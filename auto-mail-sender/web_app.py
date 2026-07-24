import os
import threading
import uuid
from dataclasses import asdict
from datetime import datetime, date, time
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for, make_response, send_from_directory
from flask_cors import CORS
import base64
import sqlite3

from auto_mailer_engine import DailyWindow, EngineConfig, load_preview, run_outreach, ProgressUpdate


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ATTACHMENT_DIR = UPLOAD_DIR / "attachments"
ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)


def parse_hhmm(value: str) -> tuple[int, int]:
    value = value.strip()
    hh, mm = value.split(":")
    hh_i = int(hh)
    mm_i = int(mm)
    if not (0 <= hh_i <= 23 and 0 <= mm_i <= 59):
        raise ValueError("Time must be in HH:MM (24h) format.")
    return hh_i, mm_i


class AppState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.worker_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.running = False
        self.started_at: Optional[str] = None
        self.last_result: Optional[Dict[str, Any]] = None
        self.last_error: Optional[str] = None
        self.last_config: Dict[str, Any] = {}
        self.last_csv_path: Optional[str] = None
        self.logs: list[str] = []
        
        # Real-time progress
        self.status = "idle"
        self.current_recipient = None
        self.next_send_at = None
        self.total_to_send = 0
        self.sent_count = 0
        self.failed_count = 0
        self.skipped_count = 0
        self.sent_emails = []
        self.failed_emails = []

    def log(self, message: str) -> None:
        with self.lock:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.logs.append(f"[{stamp}] {message}")
            self.logs = self.logs[-300:]

    def update_progress(self, up: ProgressUpdate):
        with self.lock:
            self.status = up.status
            self.current_recipient = up.current_recipient
            self.next_send_at = up.next_send_at
            self.total_to_send = up.total_to_send
            self.sent_count = up.sent_count
            self.failed_count = up.failed_count
            self.skipped_count = up.skipped_count
            if up.error:
                self.last_error = up.error
            # We don't track full lists here to keep memory low, 
            # but we can track the last few for immediate UI updates if needed.
            # For now, let's just use the counts and current recipient.


app = Flask(__name__)
app.secret_key = "local-dev-secret-change-before-public-deploy"
CORS(app)
state = AppState()


def to_json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_safe(v) for v in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def build_config(form: Dict[str, str], attachments: Optional[List[str]] = None, require_password: bool = True, tracking_base_url: str = "") -> EngineConfig:
    from_email = form.get("from_email", "").strip()
    app_password = form.get("app_password", "").strip()
    smtp_host = form.get("smtp_host", "smtp.gmail.com").strip()
    smtp_port = int(form.get("smtp_port", "587").strip())
    batch_id = form.get("batch_id", "").strip() or f"batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    subject_template = form.get("subject_template", "").strip()
    body_template = form.get("body_template", "").strip()
    db_path = form.get("db_path", "outreach.db").strip()
    daily_limit = int(form.get("daily_limit", "100").strip())
    delay_sec = int(form.get("delay_sec", "60").strip())
    consent_required = form.get("consent_required") == "on"
    enable_followup = form.get("enable_followup") == "on"
    followup_days = int(form.get("followup_days", "3").strip())

    if not from_email:
        raise ValueError("Sender email is required.")
    if require_password and not app_password:
        raise ValueError("Gmail app password is required.")
    if not subject_template:
        raise ValueError("Subject template is required.")
    if not body_template:
        raise ValueError("Body template is required.")
    if daily_limit <= 0 or delay_sec <= 0:
        raise ValueError("Daily limit and delay must be positive integers.")
    if followup_days < 0:
        raise ValueError("Follow-up days must be a non-negative integer.")

    sh, sm = parse_hhmm(form.get("window_start", "09:00"))
    eh, em = parse_hhmm(form.get("window_end", "17:00"))

    window = DailyWindow(
        start=datetime(2000, 1, 1, sh, sm).time(),
        end=datetime(2000, 1, 1, eh, em).time(),
    )

    return EngineConfig(
        db_path=db_path,
        batch_id=batch_id,
        from_email=from_email,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_app_password=app_password,
        subject_template=subject_template,
        body_template=body_template,
        daily_limit=daily_limit,
        delay_sec=delay_sec,
        window=window,
        consent_required=consent_required,
        enable_followup=enable_followup,
        followup_days=followup_days,
        attachments=attachments or [],
        tracking_base_url=tracking_base_url,
    )


def save_csv_upload() -> str:
    if "recipients_csv" not in request.files:
        raise ValueError("CSV upload is required.")
    file = request.files["recipients_csv"]
    if file.filename == "" or file.filename is None or not file.filename.lower().endswith(".csv"):
        raise ValueError("Please upload a valid .csv file.")
    target = UPLOAD_DIR / f"{uuid.uuid4().hex}.csv"
    file.save(target)
    return str(target)


def save_attachments_upload(batch_id: str) -> List[str]:
    if "attachments" not in request.files:
        return []
    files = request.files.getlist("attachments")
    paths = []
    # Create a subfolder for this batch to avoid collisions if needed, 
    # though EngineConfig currently takes absolute paths.
    batch_att_dir = ATTACHMENT_DIR / batch_id
    batch_att_dir.mkdir(parents=True, exist_ok=True)

    for file in files:
        if file.filename == "" or file.filename is None:
            continue
        target = batch_att_dir / file.filename
        file.save(target)
        paths.append(str(target))
    return paths


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.jpeg', mimetype='image/jpeg')


@app.route("/", methods=["GET"])
def index():
    defaults = {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": "587",
        "batch_id": f"batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        "daily_limit": "100",
        "delay_sec": "60",
        "window_start": "09:00",
        "window_end": "17:00",
        "db_path": "outreach.db",
        "subject_template": "Hello {first_name} - quick question",
        "body_template": "Hi {first_name},\n\nWould love to connect briefly about {company}.\n\nThanks,\n{sender_name}",
        "enable_followup": False,
        "followup_days": "3",
    }
    return render_template("index.html", defaults=defaults)


@app.route("/preview", methods=["POST"])
def preview():
    try:
        batch_id = request.form.get("batch_id", "preview")
        attachments = save_attachments_upload(batch_id)
        tracking_base_url = request.url_root.rstrip('/')
        engine = build_config(request.form, attachments=attachments, require_password=False, tracking_base_url=tracking_base_url)
        csv_path = save_csv_upload()
        preview_rows = load_preview(engine, csv_path, limit=5)
        state.log(f"Generated preview for batch {engine.batch_id} with {len(attachments)} attachments")
        return jsonify({"success": True, "preview": preview_rows, "attachment_count": len(attachments)})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/start", methods=["POST"])
def start():
    try:
        with state.lock:
            if state.running:
                return jsonify({"success": False, "error": "Mailer already running."}), 400

        batch_id = request.form.get("batch_id", "default")
        attachments = save_attachments_upload(batch_id)
        tracking_base_url = request.url_root.rstrip('/')
        engine = build_config(request.form, attachments=attachments, tracking_base_url=tracking_base_url)
        csv_path = save_csv_upload()

        def worker():
            with state.lock:
                state.running = True
                state.status = "running"
                state.started_at = datetime.now().isoformat(timespec="seconds")
                state.last_result = None
                state.last_error = None
                state.stop_event.clear()
                # Persist non-sensitive config for UI state
                safe_cfg = asdict(engine)
                safe_cfg["smtp_app_password"] = "***hidden***"
                state.last_config = to_json_safe(safe_cfg)
                state.last_csv_path = csv_path
            state.log(f"Starting batch {engine.batch_id}")
            try:
                result = run_outreach(engine, csv_path, stop_flag=state.stop_event, on_progress=state.update_progress)
                with state.lock:
                    state.last_result = result
                    state.status = "completed" if result.get("ok") else "error"
                state.log(f"Completed. Sent={result.get('total_sent')} Failed={result.get('total_failed')} Skipped={result.get('total_skipped')}")
            except Exception as exc:
                with state.lock:
                    state.last_error = str(exc)
                    state.status = "error"
                state.log(f"Fatal error: {exc}")
            finally:
                with state.lock:
                    state.running = False

        thread = threading.Thread(target=worker, daemon=True)
        with state.lock:
            state.worker_thread = thread
        thread.start()
        return jsonify({"success": True, "message": "Mailer started."})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/stop", methods=["POST"])
def stop():
    with state.lock:
        if not state.running:
            return jsonify({"success": False, "error": "Mailer is not running."}), 400
        state.stop_event.set()
    state.log("Stop requested by user.")
    return jsonify({"success": True, "message": "Stop signal sent."})


@app.route("/status", methods=["GET"])
def status():
    with state.lock:
        batch_id = state.last_config.get("batch_id")
        db_path = state.last_config.get("db_path", "outreach.db")
        opened_count = 0
        clicked_count = 0
        
        if batch_id:
            try:
                with sqlite3.connect(db_path) as conn:
                    q = """
                        SELECT COUNT(CASE WHEN sl.opened = 1 THEN 1 END),
                               COUNT(CASE WHEN sl.clicked > 0 THEN 1 END)
                        FROM batch_recipients br
                        JOIN send_log sl ON sl.recipient_id = br.recipient_id
                        WHERE br.batch_id = ?
                    """
                    res = conn.execute(q, (batch_id,)).fetchone()
                    if res:
                        opened_count, clicked_count = res[0], res[1]
            except Exception as e:
                pass

        payload = {
            "running": state.running,
            "status": state.status,
            "started_at": state.started_at,
            "current_recipient": state.current_recipient,
            "next_send_at": state.next_send_at,
            "total_to_send": state.total_to_send,
            "sent_count": state.sent_count,
            "failed_count": state.failed_count,
            "skipped_count": state.skipped_count,
            "opened_count": opened_count,
            "clicked_count": clicked_count,
            "last_result": state.last_result,
            "last_error": state.last_error,
            "last_config": to_json_safe(state.last_config),
            "logs": state.logs[-50:],
        }
    return jsonify(payload)


@app.route("/recipients", methods=["GET"])
def recipients():
    db_path = request.args.get("db_path", "outreach.db")
    batch_id = request.args.get("batch_id")
    if not batch_id:
        return jsonify({"success": False, "error": "batch_id required"}), 400
    
    from datetime import datetime
    day_key = datetime.now().date().isoformat()
    
    try:
        with sqlite3.connect(db_path) as conn:
            q = """
                SELECT r.email, sl.status, sl.sent_at, sl.error, sl.opened, sl.clicked
                FROM batch_recipients br
                JOIN recipients r ON r.recipient_id = br.recipient_id
                LEFT JOIN send_log sl ON sl.recipient_id = r.recipient_id AND sl.day_key = ?
                WHERE br.batch_id = ?
                ORDER BY br.source_order ASC
            """
            rows = conn.execute(q, (day_key, batch_id)).fetchall()
            data = [
                {
                    "email": r[0], 
                    "status": r[1] or "pending", 
                    "sent_at": r[2], 
                    "error": r[3],
                    "opened": bool(r[4]),
                    "clicked": int(r[5] or 0)
                }
                for r in rows
            ]
            return jsonify({"success": True, "recipients": data})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


PIXEL_GIF = base64.b64decode(b'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7')


@app.route('/track/open/<int:recipient_id>/<day_key>')
def track_open(recipient_id, day_key):
    db_path = "outreach.db"
    with state.lock:
        if state.last_config and state.last_config.get("db_path"):
            db_path = state.last_config.get("db_path")
            
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE send_log SET opened = 1, opened_at = ? WHERE recipient_id = ? AND day_key = ? AND opened = 0",
                (datetime.now().isoformat(), recipient_id, day_key)
            )
            state.log(f"Email opened by recipient ID: {recipient_id}")
    except Exception as e:
        print(f"Tracking open error: {e}")
        
    response = make_response(PIXEL_GIF)
    response.headers['Content-Type'] = 'image/gif'
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


@app.route('/track/click/<int:recipient_id>/<day_key>')
def track_click(recipient_id, day_key):
    target_url = request.args.get('url')
    if not target_url:
        return "Missing URL parameter", 400
        
    db_path = "outreach.db"
    with state.lock:
        if state.last_config and state.last_config.get("db_path"):
            db_path = state.last_config.get("db_path")
            
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE send_log SET clicked = clicked + 1, clicked_at = ? WHERE recipient_id = ? AND day_key = ?",
                (datetime.now().isoformat(), recipient_id, day_key)
            )
            state.log(f"Link clicked by recipient ID: {recipient_id} -> {target_url}")
    except Exception as e:
        print(f"Tracking click error: {e}")
        
    return redirect(target_url)


if __name__ == "__main__":
    # Using port 5001 to avoid conflicts with macOS AirPlay on port 5000.
    app.run(host="127.0.0.1", port=5001, debug=False)


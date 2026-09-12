import json
import os
import threading
import uuid
import bcrypt
import base64
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()
from dataclasses import asdict
from datetime import datetime, date, time
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for, make_response, send_from_directory, session
from flask_cors import CORS
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix

from auto_mailer_engine import DailyWindow, EngineConfig, load_preview, run_outreach, ProgressUpdate, get_db, init_db, _parse_recipients_file
from apollo_templates import get_apollo_template, list_apollo_templates, reset_template_override, save_template_override
from sequence_engine import (
    activate_sequence,
    create_sequence,
    delete_sequence,
    enroll_from_csv,
    enroll_from_data,
    get_sequence,
    get_sequence_dashboard,
    get_inbox_activity,
    get_analytics,
    list_sequences,
    pause_sequence,
    preview_sequence_steps,
    process_due_sends,
    resume_sequence,
    update_sequence,
)
from bson.objectid import ObjectId
from bson.errors import InvalidId

import tempfile

BASE_DIR = Path(__file__).resolve().parent
try:
    UPLOAD_DIR = BASE_DIR / "uploads"
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    ATTACHMENT_DIR = UPLOAD_DIR / "attachments"
    ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)
except (OSError, PermissionError):
    # Serverless fallback for read-only environments (e.g. Vercel)
    UPLOAD_DIR = Path(tempfile.gettempdir()) / "uploads"
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


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or "auto-mailer-stable-flask-secret-key-2026"
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max upload size limit
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
CORS(app)

# Initialize database indexes
try:
    init_db()
except Exception as exc:
    print(f"[db-init] {exc}")

TRACKING_BASE_URL = os.environ.get("TRACKING_BASE_URL", "").rstrip("/")
is_serverless = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))
ENABLE_INLINE_WORKER = os.environ.get("ENABLE_INLINE_WORKER", "0" if is_serverless else "1") == "1"
_worker_started = False
_worker_lock = threading.Lock()


_serverless_send_lock = threading.Lock()

def _trigger_serverless_send() -> None:
    """On serverless (Vercel), trigger due sequence sends in a non-blocking background thread."""
    def _bg_send():
        if not _serverless_send_lock.acquire(blocking=False):
            return
        try:
            process_due_sends(tracking_base_url=TRACKING_BASE_URL, max_per_run=15, sync_sleep=False)
        except Exception as exc:
            print(f"[serverless-send] {exc}")
        finally:
            _serverless_send_lock.release()

    threading.Thread(target=_bg_send, daemon=True).start()


def _start_inline_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started or not ENABLE_INLINE_WORKER:
            return
        _worker_started = True

    def worker_loop():
        while True:
            try:
                process_due_sends(tracking_base_url=TRACKING_BASE_URL, max_per_run=30)
            except Exception as exc:
                print(f"[inline-worker] {exc}")
            import time
            time.sleep(int(os.environ.get("WORKER_INTERVAL", "60")))

    t = threading.Thread(target=worker_loop, daemon=True, name="sequence-worker")
    t.start()


_start_inline_worker()

# Multi-user thread and runtime state directory
user_states: Dict[str, AppState] = {}
states_lock = threading.Lock()

def get_user_state(user_id: str) -> AppState:
    with states_lock:
        if user_id not in user_states:
            user_states[user_id] = AppState()
        return user_states[user_id]


def to_json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): to_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_safe(v) for v in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def build_config(form: Dict[str, str], user_id: str, attachments: Optional[List[str]] = None, require_password: bool = True, tracking_base_url: str = "") -> EngineConfig:
    db = get_db()
    user = db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise ValueError("User not found.")

    from_email = user.get("smtp_email", "").strip()
    app_password = user.get("smtp_app_password", "").strip()
    smtp_host = "smtp.gmail.com"
    smtp_port = 587

    if not from_email:
        from_email = "test@gmail.com"

    if require_password:
        if not user.get("smtp_email") or not user.get("smtp_app_password"):
            raise ValueError("Sender email or Gmail App Password is not configured. Please save them in the Settings tab first.")

    batch_id = form.get("batch_id", "").strip() or f"batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    subject_template = form.get("subject_template", "").strip()
    body_template = form.get("body_template", "").strip()
    daily_limit = int(form.get("daily_limit", "100").strip())
    delay_sec = int(form.get("delay_sec", "60").strip())
    consent_required = form.get("consent_required") == "on"
    enable_followup = form.get("enable_followup") == "on"
    followup_days = int(form.get("followup_days", "3").strip())

    enable_reply_tracking = form.get("enable_reply_tracking") == "on"
    imap_host = form.get("imap_host", "").strip() or user.get("imap_host", "imap.gmail.com")
    imap_port = int(form.get("imap_port", "993").strip() or user.get("imap_port", 993))
    imap_username = form.get("imap_username", "").strip() or user.get("imap_username", "").strip() or from_email
    imap_password = form.get("imap_password", "").strip() or user.get("imap_password", "").strip() or app_password

    min_delay_str = form.get("min_delay_sec", "").strip()
    max_delay_str = form.get("max_delay_sec", "").strip()
    min_delay_sec = int(min_delay_str) if min_delay_str and min_delay_str.isdigit() else None
    max_delay_sec = int(max_delay_str) if max_delay_str and max_delay_str.isdigit() else None

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
        user_id=user_id,
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
        min_delay_sec=min_delay_sec,
        max_delay_sec=max_delay_sec,
        enable_reply_tracking=enable_reply_tracking,
        imap_host=imap_host,
        imap_port=imap_port,
        imap_username=imap_username,
        imap_password=imap_password,
    )


def save_csv_upload() -> str:
    file = request.files.get("recipients_csv") or request.files.get("recipients_file")
    if not file or not file.filename:
        raise ValueError("Recipients file upload is required (.csv, .xlsx, or .xls).")
    
    ext = Path(file.filename).suffix.lower()
    if ext not in [".csv", ".xlsx", ".xls"]:
        raise ValueError(f"Unsupported file type '{ext}'. Please upload a valid .csv, .xlsx, or .xls file.")
        
    target = UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
    file.save(target)
    return str(target)


def save_attachments_upload(batch_id: str) -> List[str]:
    if "attachments" not in request.files:
        return []
    files = request.files.getlist("attachments")
    paths = []
    batch_att_dir = ATTACHMENT_DIR / batch_id
    batch_att_dir.mkdir(parents=True, exist_ok=True)

    for file in files:
        if file.filename == "" or file.filename is None:
            continue
        safe_name = secure_filename(file.filename)
        if not safe_name:
            continue
        target = batch_att_dir / safe_name
        file.save(target)
        paths.append(str(target))
    return paths


# Authentication checks
@app.before_request
def check_login():
    if not request.endpoint:
        return
    allowed_routes = ['login', 'register', 'static', 'track_open', 'track_click', 'favicon']
    if request.endpoint in allowed_routes:
        return
    if not session.get("user_id"):
        return redirect(url_for('login'))


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.jpeg', mimetype='image/jpeg', silent=True)


@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("user_id"):
        return redirect(url_for("index"))
    
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        phone = request.form.get("phone", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        confirm_password = request.form.get("confirm_password", "").strip()
        
        if not full_name or not phone or not username or not password or not confirm_password:
            flash("All fields are required.", "error")
            return render_template("register.html")
            
        if password != confirm_password:
            flash("Passwords do not match. Please try again.", "error")
            return render_template("register.html")
            
        db = get_db()
        existing = db.users.find_one({"username": username})
        if existing:
            flash("Username already exists.", "error")
            return render_template("register.html")
            
        hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        db.users.insert_one({
            "username": username,
            "full_name": full_name,
            "phone": phone,
            "password_hash": hashed,
            "created_at": datetime.utcnow()
        })
        flash("Registration successful! Please login.", "success")
        return redirect(url_for("login"))
        
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("index"))
        
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        
        db = get_db()
        user = db.users.find_one({"username": username})
        
        if user and bcrypt.checkpw(password.encode('utf-8'), user["password_hash"].encode('utf-8')):
            session["user_id"] = str(user["_id"])
            session["username"] = user["username"]
            session["full_name"] = user.get("full_name", user["username"])
            return redirect(url_for("index"))
        else:
            flash("Invalid credentials.", "error")
            
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/settings", methods=["GET", "POST"])
def settings():
    user_id = session.get("user_id")
    db = get_db()
    
    if request.method == "POST":
        smtp_email = request.form.get("smtp_email", "").strip()
        smtp_app_password = request.form.get("smtp_app_password", "").strip()
        imap_host = request.form.get("imap_host", "").strip() or "imap.gmail.com"
        imap_port = int(request.form.get("imap_port", "993").strip() or 993)
        imap_username = request.form.get("imap_username", "").strip()
        imap_password = request.form.get("imap_password", "").strip()
        
        if not smtp_email:
            return jsonify({"success": False, "error": "Sender email is required."}), 400
            
        update_doc = {
            "smtp_email": smtp_email,
            "imap_host": imap_host,
            "imap_port": imap_port,
            "imap_username": imap_username
        }
        if smtp_app_password and smtp_app_password != "********":
            update_doc["smtp_app_password"] = smtp_app_password
        if imap_password and imap_password != "********":
            update_doc["imap_password"] = imap_password
            
        db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update_doc})
        return jsonify({"success": True, "message": "Settings saved successfully."})
        
    user = db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        return jsonify({"success": False, "error": "User not found."}), 404
        
    masked_pw = "********" if user.get("smtp_app_password") else ""
    masked_imap_pw = "********" if user.get("imap_password") else ""
    return jsonify({
        "success": True,
        "username": user.get("username"),
        "full_name": user.get("full_name"),
        "phone": user.get("phone"),
        "smtp_email": user.get("smtp_email", ""),
        "smtp_app_password": masked_pw,
        "imap_host": user.get("imap_host", "imap.gmail.com"),
        "imap_port": user.get("imap_port", 993),
        "imap_username": user.get("imap_username", ""),
        "imap_password": masked_imap_pw
    })


@app.route("/check-replies", methods=["POST"])
def trigger_check_replies():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401
    
    u_state = get_user_state(user_id)
    try:
        db = get_db()
        user = db.users.find_one({"_id": ObjectId(user_id)})
        if not user:
            return jsonify({"error": "User not found"}), 404
        
        from_email = user.get("smtp_email", "").strip()
        app_password = user.get("smtp_app_password", "").strip()
        
        imap_host = request.form.get("imap_host", "").strip() or user.get("imap_host", "imap.gmail.com")
        imap_port = int(request.form.get("imap_port", "993").strip() or user.get("imap_port", 993))
        imap_username = request.form.get("imap_username", "").strip() or user.get("imap_username", "").strip() or from_email
        imap_password = request.form.get("imap_password", "").strip() or user.get("imap_password", "").strip() or app_password
        
        if not imap_username or not imap_password:
            return jsonify({"status": "error", "message": "IMAP credentials not configured. Please save your email & app password in Settings."}), 400
        
        from auto_mailer_engine import EngineConfig, check_inbox_replies
        cfg = EngineConfig(
            user_id=user_id,
            batch_id="reply_check",
            from_email=from_email,
            smtp_host="smtp.gmail.com",
            smtp_port=587,
            smtp_app_password=app_password,
            enable_reply_tracking=True,
            imap_host=imap_host,
            imap_port=imap_port,
            imap_username=imap_username,
            imap_password=imap_password
        )
        res = check_inbox_replies(cfg)
        u_state.log(f"[Inbox Scan] {res.get('message', '')}")
        return jsonify(res)
    except Exception as e:
        u_state.log(f"[Inbox Scan Error] {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/", methods=["GET"])
def index():
    defaults = {
        "daily_limit": "100",
        "delay_sec": "60",
        "window_start": "09:00",
        "window_end": "17:00",
        "enable_reply_tracking": True,
        "consent_required": True,
        "default_steps": json.dumps([
            {
                "subject": "{Hello|Hi|Hey} {first_name} - quick question",
                "subject_variants": [
                    "{Hello|Hi|Hey} {first_name} - quick question",
                    "Quick note for {first_name} at {company}",
                ],
                "body": "<p>{Hi|Hello} {first_name},</p><p>Would love to connect briefly regarding {company}.</p><p>Best,<br>{sender_name}</p>",
                "delay_days": 0,
            },
            {
                "subject": "Re: {company} - following up",
                "subject_variants": [
                    "Re: {company} - following up",
                    "Bumping this, {first_name}",
                ],
                "body": "<p>Hi {first_name},</p><p>Just bumping this in case it got buried. Happy to share more details.</p><p>Thanks,<br>{sender_name}</p>",
                "delay_days": 3,
            },
            {
                "subject": "Last try - {first_name}",
                "body": "<p>Hi {first_name},</p><p>I'll keep this short — should I close the loop on this?</p><p>{sender_name}</p>",
                "delay_days": 5,
            },
        ]),
    }
    return render_template("index.html", defaults=defaults, username=session.get("full_name") or session.get("username"))


# --- Sequence API (Apollo.io-style) ---

@app.route("/api/templates", methods=["GET"])
def api_list_templates():
    user_id = session.get("user_id")
    return jsonify({"success": True, "templates": list_apollo_templates(user_id)})


@app.route("/api/templates/<template_id>", methods=["GET"])
def api_get_template(template_id):
    user_id = session.get("user_id")
    try:
        tpl = get_apollo_template(template_id, user_id=user_id)
        return jsonify({"success": True, "template": tpl})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 404


@app.route("/api/templates/<template_id>", methods=["PUT"])
def api_save_template(template_id):
    user_id = session.get("user_id")
    try:
        data = request.get_json(force=True)
        tpl = save_template_override(
            user_id,
            template_id,
            name=data.get("name", ""),
            steps=data.get("steps", []),
            settings=data.get("settings"),
        )
        return jsonify({"success": True, "template": tpl, "message": "Template saved. Your edits will load next time."})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/templates/<template_id>/reset", methods=["POST"])
def api_reset_template(template_id):
    user_id = session.get("user_id")
    try:
        tpl = reset_template_override(user_id, template_id)
        return jsonify({"success": True, "template": tpl, "message": "Template reset to default."})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/sequences", methods=["GET"])
def api_list_sequences():
    user_id = session.get("user_id")
    return jsonify({"success": True, "sequences": list_sequences(user_id)})


@app.route("/api/sequences", methods=["POST"])
def api_create_sequence():
    user_id = session.get("user_id")
    try:
        data = request.get_json(force=True)
        seq = create_sequence(
            user_id,
            name=data.get("name", "Untitled Sequence"),
            steps=data.get("steps", []),
            settings=data.get("settings"),
        )
        return jsonify({"success": True, "sequence": seq})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/sequences/<sequence_id>", methods=["GET"])
def api_get_sequence(sequence_id):
    user_id = session.get("user_id")
    try:
        return jsonify({"success": True, "sequence": get_sequence(user_id, sequence_id)})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 404


@app.route("/api/sequences/<sequence_id>", methods=["PUT"])
def api_update_sequence(sequence_id):
    user_id = session.get("user_id")
    try:
        data = request.get_json(force=True)
        seq = update_sequence(
            user_id,
            sequence_id,
            name=data.get("name"),
            steps=data.get("steps"),
            settings=data.get("settings"),
        )
        return jsonify({"success": True, "sequence": seq})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/sequences/<sequence_id>", methods=["DELETE"])
def api_delete_sequence(sequence_id):
    user_id = session.get("user_id")
    try:
        delete_sequence(user_id, sequence_id)
        return jsonify({"success": True})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/parse-leads", methods=["POST"])
def api_parse_leads():
    csv_path = None
    try:
        csv_path = save_csv_upload()
        rows = _parse_recipients_file(csv_path)
        if not rows:
            return jsonify({"success": False, "error": "No valid lead rows found in file."}), 400
        headers = list(rows[0].keys()) if rows else []
        return jsonify({"success": True, "rows": rows, "headers": headers, "count": len(rows)})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    finally:
        if csv_path and os.path.exists(csv_path):
            try:
                os.remove(csv_path)
            except Exception:
                pass


@app.route("/api/sequences/<sequence_id>/activate", methods=["POST"])
def api_activate_sequence(sequence_id):
    user_id = session.get("user_id")
    csv_path = None
    contacts_data = None
    try:
        if request.is_json:
            data = request.get_json(silent=True) or {}
            contacts_data = data.get("contacts")
        
        if not contacts_data and (request.files.get("recipients_csv") or request.files.get("recipients_file")):
            csv_path = save_csv_upload()

        result = activate_sequence(user_id, sequence_id, csv_path=csv_path, contacts_data=contacts_data)
        return jsonify({"success": True, **result})
    except Exception as exc:
        err_str = str(exc)
        if "CREDENTIALS_MISSING" in err_str:
            return jsonify({"success": False, "code": "credentials_missing", "error": err_str.replace("CREDENTIALS_MISSING: ", "")}), 400
        return jsonify({"success": False, "error": err_str}), 400
    finally:
        if csv_path and os.path.exists(csv_path):
            try:
                os.remove(csv_path)
            except Exception:
                pass


@app.route("/api/sequences/<sequence_id>/enroll", methods=["POST"])
def api_enroll_sequence(sequence_id):
    user_id = session.get("user_id")
    csv_path = None
    try:
        if request.is_json:
            data = request.get_json(silent=True) or {}
            contacts = data.get("contacts")
            if not contacts:
                return jsonify({"success": False, "error": "No contacts provided"}), 400
            enrolled = enroll_from_data(user_id, sequence_id, contacts)
        else:
            csv_path = save_csv_upload()
            enrolled = enroll_from_csv(user_id, sequence_id, csv_path)
        return jsonify({"success": True, "enrolled": enrolled})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    finally:
        if csv_path and os.path.exists(csv_path):
            try:
                os.remove(csv_path)
            except Exception:
                pass


@app.route("/api/sequences/<sequence_id>/pause", methods=["POST"])
def api_pause_sequence(sequence_id):
    user_id = session.get("user_id")
    try:
        result = pause_sequence(user_id, sequence_id)
        return jsonify({"success": True, **result})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/sequences/<sequence_id>/resume", methods=["POST"])
def api_resume_sequence(sequence_id):
    user_id = session.get("user_id")
    try:
        result = resume_sequence(user_id, sequence_id)
        return jsonify({"success": True, **result})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/cron/send", methods=["GET", "POST"])
def api_cron_send():
    """Endpoint for Vercel Cron or frontend polling to trigger sequence sends."""
    try:
        res = process_due_sends(tracking_base_url=TRACKING_BASE_URL, max_per_run=15, sync_sleep=False)
        return jsonify({"success": True, "processed": res})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/sequences/<sequence_id>/dashboard", methods=["GET"])
def api_sequence_dashboard(sequence_id):
    user_id = session.get("user_id")
    if is_serverless or not ENABLE_INLINE_WORKER:
        _trigger_serverless_send()
    try:
        data = get_sequence_dashboard(user_id, sequence_id)
        return jsonify({"success": True, **data})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/sequences/dashboard", methods=["GET"])
def api_dashboard():
    user_id = session.get("user_id")
    if is_serverless or not ENABLE_INLINE_WORKER:
        _trigger_serverless_send()
    try:
        data = get_sequence_dashboard(user_id)
        return jsonify({"success": True, **data})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/inbox", methods=["GET"])
@app.route("/api/sequences/<sequence_id>/inbox", methods=["GET"])
def api_inbox(sequence_id=None):
    user_id = session.get("user_id")
    try:
        data = get_inbox_activity(user_id, sequence_id)
        return jsonify({"success": True, **data})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/analytics", methods=["GET"])
@app.route("/api/sequences/<sequence_id>/analytics", methods=["GET"])
def api_analytics(sequence_id=None):
    user_id = session.get("user_id")
    try:
        data = get_analytics(user_id, sequence_id)
        return jsonify({"success": True, **data})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/sequences/<sequence_id>/preview", methods=["POST"])
def api_preview_sequence(sequence_id):
    user_id = session.get("user_id")
    csv_path = None
    try:
        seq = get_sequence(user_id, sequence_id)
        csv_path = save_csv_upload()
        previews = preview_sequence_steps(user_id, seq["steps"], csv_path)
        return jsonify({"success": True, "preview": previews})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    finally:
        if csv_path and os.path.exists(csv_path):
            try:
                os.remove(csv_path)
            except Exception:
                pass


@app.route("/api/sequences/preview-draft", methods=["POST"])
def api_preview_draft():
    csv_path = None
    try:
        steps_json = request.form.get("steps_json", "[]")
        steps = json.loads(steps_json)
        csv_path = save_csv_upload()
        previews = preview_sequence_steps(session.get("user_id"), steps, csv_path)
        return jsonify({"success": True, "preview": previews})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    finally:
        if csv_path and os.path.exists(csv_path):
            try:
                os.remove(csv_path)
            except Exception:
                pass


@app.route("/preview", methods=["POST"])
def preview():
    attachments = []
    csv_path = None
    try:
        user_id = session.get("user_id")
        
        # 1. Pre-validate configuration parameters to avoid resource allocation on validation failure
        test_engine = build_config(request.form, user_id=user_id, attachments=[], require_password=False, tracking_base_url="")
        
        # 2. Validation succeeded, save files safely
        batch_id = request.form.get("batch_id", "preview")
        attachments = save_attachments_upload(batch_id)
        tracking_base_url = request.url_root.rstrip('/')
        engine = build_config(request.form, user_id=user_id, attachments=attachments, require_password=False, tracking_base_url=tracking_base_url)
        csv_path = save_csv_upload()
        preview_rows = load_preview(engine, csv_path, limit=5)
        
        u_state = get_user_state(user_id)
        u_state.log(f"Generated preview for batch {engine.batch_id} with {len(attachments)} attachments")
        return jsonify({"success": True, "preview": preview_rows, "attachment_count": len(attachments)})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    finally:
        if csv_path and os.path.exists(csv_path):
            try:
                os.remove(csv_path)
            except Exception:
                pass
        for att in attachments:
            if os.path.exists(att):
                try:
                    os.remove(att)
                except Exception:
                    pass
        if attachments:
            try:
                parent_dir = os.path.dirname(attachments[0])
                if os.path.isdir(parent_dir) and not os.listdir(parent_dir):
                    os.rmdir(parent_dir)
            except Exception:
                pass



@app.route("/start", methods=["POST"])
def start():
    try:
        user_id = session.get("user_id")
        u_state = get_user_state(user_id)
        
        with u_state.lock:
            if u_state.running:
                return jsonify({"success": False, "error": "Mailer already running."}), 400

        # 1. Pre-validate configuration parameters to avoid resource allocation on validation failure
        test_engine = build_config(request.form, user_id=user_id, attachments=[], tracking_base_url="")
        
        # 2. Validation succeeded, save files safely
        batch_id = request.form.get("batch_id", "default")
        attachments = save_attachments_upload(batch_id)
        tracking_base_url = request.url_root.rstrip('/')
        engine = build_config(request.form, user_id=user_id, attachments=attachments, tracking_base_url=tracking_base_url)
        
        try:
            csv_path = save_csv_upload()
        except Exception as csv_exc:
            for att_path in attachments:
                if os.path.exists(att_path):
                    try:
                        os.remove(att_path)
                    except Exception:
                        pass
            raise csv_exc

        def worker():
            with u_state.lock:
                u_state.running = True
                u_state.status = "running"
                u_state.started_at = datetime.now().isoformat(timespec="seconds")
                u_state.last_result = None
                u_state.last_error = None
                u_state.stop_event.clear()
                safe_cfg = asdict(engine)
                safe_cfg["smtp_app_password"] = "***hidden***"
                u_state.last_config = to_json_safe(safe_cfg)
                u_state.last_csv_path = csv_path
            u_state.log(f"Starting batch {engine.batch_id}")
            try:
                result = run_outreach(engine, csv_path, stop_flag=u_state.stop_event, on_progress=u_state.update_progress)
                with u_state.lock:
                    u_state.last_result = result
                    u_state.status = "completed" if result.get("ok") else "error"
                u_state.log(f"Completed. Sent={result.get('total_sent')} Failed={result.get('total_failed')} Skipped={result.get('total_skipped')}")
            except Exception as exc:
                with u_state.lock:
                    u_state.last_error = str(exc)
                    u_state.status = "error"
                u_state.log(f"Fatal error: {exc}")
            finally:
                with u_state.lock:
                    u_state.running = False
                # Clean up attachments now that campaign run has completed, failed, or stopped
                for att_path in engine.attachments:
                    if os.path.exists(att_path):
                        try:
                            os.remove(att_path)
                        except Exception:
                            pass
                if engine.attachments:
                    try:
                        parent_dir = os.path.dirname(engine.attachments[0])
                        if os.path.isdir(parent_dir) and not os.listdir(parent_dir):
                            os.rmdir(parent_dir)
                    except Exception:
                        pass

        thread = threading.Thread(target=worker, daemon=True)
        with u_state.lock:
            u_state.worker_thread = thread
        thread.start()
        return jsonify({"success": True, "message": "Mailer started."})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/stop", methods=["POST"])
def stop():
    user_id = session.get("user_id")
    u_state = get_user_state(user_id)
    with u_state.lock:
        if not u_state.running:
            return jsonify({"success": False, "error": "Mailer is not running."}), 400
        u_state.stop_event.set()
    u_state.log("Stop requested by user.")
    return jsonify({"success": True, "message": "Stop signal sent."})


@app.route("/status", methods=["GET"])
def status():
    user_id = session.get("user_id")
    u_state = get_user_state(user_id)
    with u_state.lock:
        batch_id = u_state.last_config.get("batch_id")
        opened_count = 0
        clicked_count = 0
        replied_count = 0
        
        try:
            db = get_db()
            if batch_id:
                rec_ids = [doc["recipient_id"] for doc in db.batch_recipients.find({"user_id": user_id, "batch_id": batch_id}, {"recipient_id": 1})]
                opened_count = db.send_log.count_documents({
                    "user_id": user_id,
                    "recipient_id": {"$in": rec_ids},
                    "opened": 1
                })
                clicked_count = db.send_log.count_documents({
                    "user_id": user_id,
                    "recipient_id": {"$in": rec_ids},
                    "clicked": {"$gt": 0}
                })
                replied_count = db.recipients.count_documents({
                    "user_id": user_id,
                    "_id": {"$in": rec_ids},
                    "replied": True
                })
            else:
                replied_count = db.recipients.count_documents({
                    "user_id": user_id,
                    "replied": True
                })
        except Exception:
            pass

        payload = {
            "running": u_state.running,
            "status": u_state.status,
            "started_at": u_state.started_at,
            "current_recipient": u_state.current_recipient,
            "next_send_at": u_state.next_send_at,
            "total_to_send": u_state.total_to_send,
            "sent_count": u_state.sent_count,
            "failed_count": u_state.failed_count,
            "skipped_count": u_state.skipped_count,
            "opened_count": opened_count,
            "clicked_count": clicked_count,
            "replied_count": replied_count,
            "last_result": u_state.last_result,
            "last_error": u_state.last_error,
            "last_config": to_json_safe(u_state.last_config),
            "logs": u_state.logs[-50:],
        }
    return jsonify(payload)


@app.route("/recipients", methods=["GET"])
def recipients():
    user_id = session.get("user_id")
    batch_id = request.args.get("batch_id")
    if not batch_id:
        return jsonify({"success": False, "error": "batch_id required"}), 400
    
    day_key = datetime.now().date().isoformat()
    try:
        db = get_db()
        pipeline = [
            {"$match": {"user_id": user_id, "batch_id": batch_id}},
            {"$sort": {"source_order": 1}},
            {
                "$lookup": {
                    "from": "recipients",
                    "localField": "recipient_id",
                    "foreignField": "_id",
                    "as": "rec_info"
                }
            },
            {"$unwind": "$rec_info"},
            {
                "$lookup": {
                    "from": "send_log",
                    "let": {"rec_id": "$recipient_id"},
                    "pipeline": [
                        {"$match": {"$expr": {"$and": [
                            {"$eq": ["$recipient_id", "$$rec_id"]},
                            {"$eq": ["$day_key", day_key]}
                        ]}}}
                    ],
                    "as": "log_info"
                }
            },
            {
                "$project": {
                    "email": "$rec_info.email",
                    "replied": "$rec_info.replied",
                    "log": {"$arrayElemAt": ["$log_info", 0]}
                }
            }
        ]
        results = db.batch_recipients.aggregate(pipeline)
        data = []
        for doc in results:
            log = doc.get("log") or {}
            is_replied = bool(doc.get("replied") or log.get("replied"))
            data.append({
                "email": doc["email"],
                "status": "replied" if is_replied else log.get("status", "pending"),
                "sent_at": log.get("sent_at"),
                "error": log.get("error"),
                "opened": bool(log.get("opened", 0)),
                "clicked": int(log.get("clicked", 0)),
                "replied": is_replied
            })
        return jsonify({"success": True, "recipients": data})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


PIXEL_GIF = base64.b64decode(b'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7')


@app.route('/track/open/<recipient_id>/<day_key>')
def track_open(recipient_id, day_key):
    try:
        db = get_db()
        now = datetime.now().isoformat()
        db.send_log.update_one(
            {"recipient_id": ObjectId(recipient_id), "day_key": day_key},
            {"$set": {"opened": 1, "opened_at": now}}
        )
        db.sequence_send_log.update_many(
            {"recipient_id": ObjectId(recipient_id), "day_key": day_key},
            {"$set": {"opened": 1, "opened_at": now}}
        )
    except (Exception, InvalidId) as e:
        print(f"Tracking open error: {e}")
        
    response = make_response(PIXEL_GIF)
    response.headers['Content-Type'] = 'image/gif'
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


@app.route('/track/click/<recipient_id>/<day_key>')
def track_click(recipient_id, day_key):
    target_url = request.args.get('url')
    if not target_url:
        return "Missing URL parameter", 400
        
    try:
        db = get_db()
        now = datetime.now().isoformat()
        db.send_log.update_one(
            {"recipient_id": ObjectId(recipient_id), "day_key": day_key},
            {"$inc": {"clicked": 1}, "$set": {"clicked_at": now}}
        )
        db.sequence_send_log.update_many(
            {"recipient_id": ObjectId(recipient_id), "day_key": day_key},
            {"$inc": {"clicked": 1}, "$set": {"clicked_at": now}}
        )
    except (Exception, InvalidId) as e:
        print(f"Tracking click error: {e}")
        
    return redirect(target_url)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False)

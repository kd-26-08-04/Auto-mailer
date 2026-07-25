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

from auto_mailer_engine import DailyWindow, EngineConfig, load_preview, run_outreach, ProgressUpdate, get_db, init_db
from bson.objectid import ObjectId
from bson.errors import InvalidId

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
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or os.urandom(24)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB max upload size limit
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
CORS(app)

# Initialize database indexes
init_db()

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
        
        if not smtp_email:
            return jsonify({"success": False, "error": "Sender email is required."}), 400
            
        update_doc = {"smtp_email": smtp_email}
        if smtp_app_password and smtp_app_password != "********":
            update_doc["smtp_app_password"] = smtp_app_password
            
        db.users.update_one({"_id": ObjectId(user_id)}, {"$set": update_doc})
        return jsonify({"success": True, "message": "Settings saved successfully."})
        
    user = db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        return jsonify({"success": False, "error": "User not found."}), 404
        
    masked_pw = "********" if user.get("smtp_app_password") else ""
    return jsonify({
        "success": True,
        "username": user.get("username"),
        "full_name": user.get("full_name"),
        "phone": user.get("phone"),
        "smtp_email": user.get("smtp_email", ""),
        "smtp_app_password": masked_pw
    })


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
        "subject_template": "Hello {first_name} - quick question",
        "body_template": "Hi {first_name},\n\nWould love to connect briefly about {company}.\n\nThanks,\n{sender_name}",
        "enable_followup": False,
        "followup_days": "3",
    }
    return render_template("index.html", defaults=defaults, username=session.get("full_name") or session.get("username"))


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
        
        if batch_id:
            try:
                db = get_db()
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
                    "log": {"$arrayElemAt": ["$log_info", 0]}
                }
            }
        ]
        results = db.batch_recipients.aggregate(pipeline)
        data = []
        for doc in results:
            log = doc.get("log") or {}
            data.append({
                "email": doc["email"],
                "status": log.get("status", "pending"),
                "sent_at": log.get("sent_at"),
                "error": log.get("error"),
                "opened": bool(log.get("opened", 0)),
                "clicked": int(log.get("clicked", 0))
            })
        return jsonify({"success": True, "recipients": data})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


PIXEL_GIF = base64.b64decode(b'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7')


@app.route('/track/open/<recipient_id>/<day_key>')
def track_open(recipient_id, day_key):
    try:
        db = get_db()
        db.send_log.update_one(
            {"recipient_id": ObjectId(recipient_id), "day_key": day_key},
            {"$set": {"opened": 1, "opened_at": datetime.now().isoformat()}}
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
        db.send_log.update_one(
            {"recipient_id": ObjectId(recipient_id), "day_key": day_key},
            {"$inc": {"clicked": 1}, "$set": {"clicked_at": datetime.now().isoformat()}}
        )
    except (Exception, InvalidId) as e:
        print(f"Tracking click error: {e}")
        
    return redirect(target_url)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=False)

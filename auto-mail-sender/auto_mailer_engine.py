import csv
import dataclasses
import json
import os
import random
import re
import smtplib
import socket
import ssl
import time
import mimetypes
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, date, time as dtime, timedelta
from email.message import EmailMessage
from typing import Any, Dict, Iterable, List, Optional, Tuple, Callable

from pymongo import MongoClient
from bson.objectid import ObjectId
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Setup MongoDB Connection (Atlas or Local)
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
_mongo_client = None

def get_mongo_client():
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(MONGO_URI)
    return _mongo_client

def get_db():
    client = get_mongo_client()
    parsed = urllib.parse.urlparse(MONGO_URI)
    db_name = parsed.path.lstrip('/') if parsed.path else "outreach_db"
    return client[db_name]


def _utc_now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _local_now() -> datetime:
    return datetime.now()


def day_key_local(dt: Optional[datetime] = None) -> str:
    dt = dt or _local_now()
    return dt.date().isoformat()


def hour_bucket_local(dt: Optional[datetime] = None) -> str:
    dt = dt or _local_now()
    return dt.strftime("%Y-%m-%d %H:00")


@dataclass(frozen=True)
class DailyWindow:
    start: dtime
    end: dtime

    def contains(self, dt: datetime) -> bool:
        start_dt = datetime.combine(dt.date(), self.start)
        end_dt = datetime.combine(dt.date(), self.end)
        # If end is earlier than start, treat as crossing midnight.
        if end_dt <= start_dt:
            end_dt = end_dt + timedelta(days=1)
            if dt < start_dt:
                dt = dt + timedelta(days=1)
        return start_dt <= dt <= end_dt

    def next_open_time(self, dt: Optional[datetime] = None) -> datetime:
        dt = dt or _local_now()
        # Move to start of today's window if we're before it.
        start_dt = datetime.combine(dt.date(), self.start)
        end_dt = datetime.combine(dt.date(), self.end)
        if end_dt <= start_dt:
            end_dt = end_dt + timedelta(days=1)
        if dt < start_dt:
            return start_dt
        # Otherwise go to next day start.
        return start_dt + timedelta(days=1)


@dataclass
class EngineConfig:
    batch_id: str
    from_email: str
    smtp_host: str
    smtp_port: int
    smtp_app_password: str
    user_id: str = "default_user"  # Scopes campaigns to this user
    db_path: str = ""              # Preserved for backward compatibility
    smtp_use_starttls: bool = True

    subject_template: str = ""
    body_template: str = ""

    daily_limit: int = 100
    delay_sec: int = 60
    window: DailyWindow = dataclasses.field(default_factory=lambda: DailyWindow(dtime(9, 0), dtime(17, 0)))

    # Follow-up Settings
    enable_followup: bool = False
    followup_days: int = 3

    # Human-like delays. If not provided, they are derived from delay_sec.
    min_delay_sec: Optional[int] = None
    max_delay_sec: Optional[int] = None

    # Guardrails
    consent_required: bool = True
    consent_field_name: str = "consent"
    consent_truthy: Tuple[str, ...] = ("true", "1", "yes", "y")

    # Retry behavior for transient errors.
    max_retries_per_recipient_per_day: int = 5
    retry_backoff_base_sec: int = 15
    retry_backoff_max_sec: int = 180

    # Randomization strength
    jitter_factor: float = 0.35

    # Attachments
    attachments: List[str] = dataclasses.field(default_factory=list)

    # Tracking base URL (e.g. http://127.0.0.1:5001)
    tracking_base_url: str = ""


@dataclass
class ProgressUpdate:
    status: str  # idle, running, sleeping, stopped, completed, error
    current_recipient: Optional[str] = None
    next_send_at: Optional[str] = None
    total_to_send: int = 0
    sent_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    error: Optional[str] = None


def init_db(db_path: str = "") -> None:
    """Initialize MongoDB indexes."""
    db = get_db()
    db.users.create_index("username", unique=True)
    db.recipients.create_index([("user_id", 1), ("email", 1)], unique=True)
    db.batch_recipients.create_index([("user_id", 1), ("batch_id", 1), ("recipient_id", 1)], unique=True)
    db.send_log.create_index([("user_id", 1), ("recipient_id", 1), ("day_key", 1)], unique=True)
    db.run_state.create_index([("user_id", 1), ("batch_id", 1), ("day_key", 1)], unique=True)


def _parse_csv_recipients(csv_path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()})
    return rows


def _render_template(template: str, data: Dict[str, Any]) -> str:
    """Simple {field} interpolation using Python's format_map."""
    class SafeDict(dict):
        def __missing__(self, key: str) -> str:
            return ""
    return template.format_map(SafeDict(data))


def _is_truthy_consent(value: Any, truthy: Iterable[str]) -> bool:
    if value is None:
        return False
    s = str(value).strip().lower()
    return s in set(t.lower() for t in truthy)


def ensure_batch(engine: EngineConfig) -> None:
    db = get_db()
    db.batches.update_one(
        {"user_id": engine.user_id, "batch_id": engine.batch_id},
        {"$setOnInsert": {"created_at": _utc_now_iso()}},
        upsert=True
    )


def upsert_batch_recipients(engine: EngineConfig, csv_path: str) -> int:
    """Imports recipients into recipients collection and maps them to batch_recipients."""
    rows = _parse_csv_recipients(csv_path)
    if not rows:
        return 0

    required = ["email"]
    for r in required:
        if r not in rows[0]:
            raise ValueError(f"CSV must include a '{r}' column.")

    db = get_db()

    # Clear existing mapping for this batch so users can re-import edits
    db.batch_recipients.delete_many({"user_id": engine.user_id, "batch_id": engine.batch_id})

    inserted = 0
    for idx, row in enumerate(rows):
        email = str(row.get("email", "")).strip()
        if not email:
            continue

        # Upsert global recipient record scoped to this user
        res = db.recipients.find_one_and_update(
            {"user_id": engine.user_id, "email": email},
            {
                "$set": {"data": row, "updated_at": _utc_now_iso()},
                "$setOnInsert": {"created_at": _utc_now_iso()}
            },
            upsert=True,
            return_document=True
        )
        recipient_id = res["_id"]

        # Map recipient to batch
        db.batch_recipients.update_one(
            {"user_id": engine.user_id, "batch_id": engine.batch_id, "recipient_id": recipient_id},
            {"$set": {"source_order": idx}},
            upsert=True
        )
        inserted += 1

    return inserted


def _get_sorted_batch_emails(engine: EngineConfig) -> List[Tuple[str, str, Dict[str, Any]]]:
    """Returns list of (recipient_id_str, email, data_dict), sorted by CSV order."""
    db = get_db()
    pipeline = [
        {"$match": {"user_id": engine.user_id, "batch_id": engine.batch_id}},
        {"$sort": {"source_order": 1}},
        {
            "$lookup": {
                "from": "recipients",
                "localField": "recipient_id",
                "foreignField": "_id",
                "as": "recipient_info"
            }
        },
        {"$unwind": "$recipient_info"}
    ]
    results = db.batch_recipients.aggregate(pipeline)
    out = []
    for doc in results:
        rec_info = doc["recipient_info"]
        out.append((str(rec_info["_id"]), str(rec_info["email"]), rec_info["data"]))
    return out


def _count_sent_today(engine: EngineConfig, day: str) -> int:
    db = get_db()
    return db.send_log.count_documents({
        "user_id": engine.user_id,
        "day_key": day,
        "status": "sent"
    })


def _derive_delay_bounds(engine: EngineConfig) -> Tuple[int, int]:
    if engine.min_delay_sec is not None and engine.max_delay_sec is not None:
        return engine.min_delay_sec, engine.max_delay_sec
    min_delay = max(1, int(engine.delay_sec * (1 - engine.jitter_factor)))
    max_delay = max(min_delay + 2, int(engine.delay_sec * (1 + engine.jitter_factor)))
    return min_delay, max_delay


def _smtp_send(engine: EngineConfig, to_email: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = engine.from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body, subtype="html")

    for path in engine.attachments:
        if not os.path.isfile(path):
            continue
        ctype, encoding = mimetypes.guess_type(path)
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)

        with open(path, "rb") as f:
            msg.add_attachment(
                f.read(),
                maintype=maintype,
                subtype=subtype,
                filename=os.path.basename(path)
            )

    context = ssl.create_default_context()
    with smtplib.SMTP(engine.smtp_host, engine.smtp_port, timeout=30) as server:
        if engine.smtp_use_starttls:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
        server.login(engine.from_email, engine.smtp_app_password)
        server.send_message(msg)


def _is_transient_error(exc: Exception) -> bool:
    transient_markers = (
        smtplib.SMTPServerDisconnected,
        smtplib.SMTPConnectError,
        smtplib.SMTPHeloError,
        smtplib.SMTPException,
        socket.timeout,
        TimeoutError,
        ConnectionError,
    )
    if isinstance(exc, transient_markers):
        return True
    msg = str(exc).lower()
    if any(k in msg for k in ["timeout", "temporar", "connection", "network", "timed out", "reset"]):
        return True
    return False


def rewrite_links(html_content: str, recipient_id: str, day_key: str, tracking_base_url: str) -> str:
    def replace_link(match):
        url = match.group(2)
        if (url.startswith("#") or url.startswith("mailto:") or 
            url.startswith("javascript:") or "track/click" in url):
            return match.group(0)
        
        encoded_url = urllib.parse.quote(url)
        new_href = f"{tracking_base_url}/track/click/{recipient_id}/{day_key}?url={encoded_url}"
        return f'{match.group(1)}="{new_href}"'

    pattern = r'(href)\s*=\s*["\']([^"\']+)["\']'
    return re.sub(pattern, replace_link, html_content)


def run_outreach(engine: EngineConfig, recipients_csv_path: str, stop_flag: Any, on_progress: Optional[Callable[[ProgressUpdate], None]] = None) -> Dict[str, Any]:
    """Main outreach loop using MongoDB."""
    init_db()
    ensure_batch(engine)
    try:
        imported = upsert_batch_recipients(engine, recipients_csv_path)
    except Exception as exc:
        if os.path.exists(recipients_csv_path):
            try:
                os.remove(recipients_csv_path)
            except Exception:
                pass
        raise exc

    if os.path.exists(recipients_csv_path):
        try:
            os.remove(recipients_csv_path)
        except Exception:
            pass

    if imported == 0:
        return {"ok": False, "reason": "No recipients imported."}

    min_delay, max_delay = _derive_delay_bounds(engine)
    db = get_db()
    day = day_key_local(_local_now())
    batch_list = _get_sorted_batch_emails(engine)

    # Load history
    sent_ever_recipient_ids = set()
    last_sent_times: Dict[str, datetime] = {}

    if not engine.enable_followup:
        sent_ever_recipient_ids = set(
            str(doc["recipient_id"])
            for doc in db.send_log.find({"user_id": engine.user_id, "status": "sent"}, {"recipient_id": 1})
        )
    else:
        pipeline = [
            {"$match": {"user_id": engine.user_id, "status": "sent", "sent_at": {"$ne": None}}},
            {"$group": {"_id": "$recipient_id", "last_sent_at": {"$max": "$sent_at"}}}
        ]
        for group in db.send_log.aggregate(pipeline):
            r_id_str = str(group["_id"])
            sent_at_str = group["last_sent_at"]
            try:
                last_sent_times[r_id_str] = datetime.fromisoformat(sent_at_str)
            except Exception:
                pass

    current_day_for_sets = None
    sent_today_recipient_ids = set()
    attempted_today = set()
    next_source_order = 0

    def refresh_day_state(day_key: str) -> None:
        nonlocal current_day_for_sets, sent_today_recipient_ids, attempted_today, next_source_order
        current_day_for_sets = day_key

        cur_state = db.run_state.find_one({"user_id": engine.user_id, "batch_id": engine.batch_id, "day_key": day_key})
        if cur_state is None:
            db.run_state.update_one(
                {"user_id": engine.user_id, "batch_id": engine.batch_id, "day_key": day_key},
                {"$set": {"next_source_order": 0, "updated_at": _utc_now_iso()}},
                upsert=True
            )
            next_source_order = 0
        else:
            next_source_order = int(cur_state["next_source_order"])

        sent_today_recipient_ids = set(
            str(doc["recipient_id"])
            for doc in db.send_log.find({"user_id": engine.user_id, "day_key": day_key, "status": "sent"}, {"recipient_id": 1})
        )
        attempted_today = set(
            str(doc["recipient_id"])
            for doc in db.send_log.find({"user_id": engine.user_id, "day_key": day_key, "status": {"$in": ["failed", "skipped"]}}, {"recipient_id": 1})
        )

    refresh_day_state(day)

    def consent_ok(data_dict: Dict[str, Any]) -> bool:
        if not engine.consent_required:
            return True
        return _is_truthy_consent(data_dict.get(engine.consent_field_name), engine.consent_truthy)

    total_sent = 0
    total_failed = 0
    total_skipped = 0
    consecutive_transient_failures = 0

    def report(status: str, current: Optional[str] = None, next_at: Optional[datetime] = None, err: Optional[str] = None):
        if not on_progress:
            return
        update = ProgressUpdate(
            status=status,
            current_recipient=current,
            next_send_at=next_at.isoformat() if next_at else None,
            total_to_send=len(batch_list),
            sent_count=total_sent,
            failed_count=total_failed,
            skipped_count=total_skipped,
            error=err
        )
        on_progress(update)

    while True:
        if getattr(stop_flag, "is_set", None) and callable(getattr(stop_flag, "is_set")) and stop_flag.is_set():
            report("stopped")
            break
        if getattr(stop_flag, "is_set", None) and not callable(getattr(stop_flag, "is_set")) and bool(stop_flag.is_set):
            report("stopped")
            break

        now = _local_now()
        if not engine.window.contains(now):
            day = day_key_local(now)
            refresh_day_state(day)
            if _count_sent_today(engine, day) >= engine.daily_limit:
                next_open = engine.window.next_open_time(now)
                report("sleeping", next_at=next_open)
                time.sleep(min(max(5, int((next_open - now).total_seconds())), 60))
                continue
            next_open = engine.window.next_open_time(now)
            report("sleeping", next_at=next_open)
            time.sleep(min(max(5, int((next_open - now).total_seconds())), 60))
            continue

        day = day_key_local(now)
        if day != current_day_for_sets:
            refresh_day_state(day)
        
        day_sent = _count_sent_today(engine, day)
        if day_sent >= engine.daily_limit:
            report("completed")
            break

        if next_source_order >= len(batch_list):
            report("completed")
            break

        recipient_id, to_email, data_dict = batch_list[next_source_order]
        report("running", current=to_email)

        # Consent guard
        if not consent_ok(data_dict):
            db.send_log.update_one(
                {"user_id": engine.user_id, "recipient_id": ObjectId(recipient_id), "day_key": day},
                {"$set": {
                    "status": "skipped",
                    "attempt_no": 0,
                    "sent_at": None,
                    "error": "consent_required_failed",
                    "updated_at": _utc_now_iso()
                }},
                upsert=True
            )
            db.run_state.update_one(
                {"user_id": engine.user_id, "batch_id": engine.batch_id, "day_key": day},
                {"$set": {"next_source_order": next_source_order + 1, "updated_at": _utc_now_iso()}}
            )
            total_skipped += 1
            next_source_order += 1
            continue

        # Skip if follow-up disabled and ever emailed
        if not engine.enable_followup and recipient_id in sent_ever_recipient_ids:
            db.run_state.update_one(
                {"user_id": engine.user_id, "batch_id": engine.batch_id, "day_key": day},
                {"$set": {"next_source_order": next_source_order + 1, "updated_at": _utc_now_iso()}}
            )
            total_skipped += 1
            next_source_order += 1
            continue

        # Follow-up guard
        if engine.enable_followup:
            if recipient_id not in last_sent_times:
                db.run_state.update_one(
                    {"user_id": engine.user_id, "batch_id": engine.batch_id, "day_key": day},
                    {"$set": {"next_source_order": next_source_order + 1, "updated_at": _utc_now_iso()}}
                )
                total_skipped += 1
                next_source_order += 1
                continue
            else:
                last_sent_time = last_sent_times[recipient_id]
                days_passed = (now.date() - last_sent_time.date()).days
                if days_passed < engine.followup_days:
                    db.run_state.update_one(
                        {"user_id": engine.user_id, "batch_id": engine.batch_id, "day_key": day},
                        {"$set": {"next_source_order": next_source_order + 1, "updated_at": _utc_now_iso()}}
                    )
                    total_skipped += 1
                    next_source_order += 1
                    continue

        # Never send twice the same day
        if recipient_id in sent_today_recipient_ids:
            db.run_state.update_one(
                {"user_id": engine.user_id, "batch_id": engine.batch_id, "day_key": day},
                {"$set": {"next_source_order": next_source_order + 1, "updated_at": _utc_now_iso()}}
            )
            next_source_order += 1
            continue

        # If already attempted and failed/skipped today, skip
        if recipient_id in attempted_today:
            db.run_state.update_one(
                {"user_id": engine.user_id, "batch_id": engine.batch_id, "day_key": day},
                {"$set": {"next_source_order": next_source_order + 1, "updated_at": _utc_now_iso()}}
            )
            next_source_order += 1
            continue

        subject = _render_template(engine.subject_template, data_dict).strip()
        if not subject:
            subject = "(no subject)"
        body = _render_template(engine.body_template, data_dict).strip()

        # Open and Click tracking
        if getattr(engine, "tracking_base_url", None):
            pixel_tag = f'<img src="{engine.tracking_base_url}/track/open/{recipient_id}/{day}" width="1" height="1" style="display:none;" />'
            body = rewrite_links(body, recipient_id, day, engine.tracking_base_url)
            body = body + pixel_tag

        # Calculate attempt number
        log_doc = db.send_log.find_one({"user_id": engine.user_id, "recipient_id": ObjectId(recipient_id), "day_key": day})
        attempt_no = int(log_doc["attempt_no"]) if log_doc and "attempt_no" in log_doc else 0
        attempt_no += 1

        try:
            _smtp_send(engine, to_email, subject, body)
            sent_at = now.isoformat(timespec="seconds")
            db.send_log.update_one(
                {"user_id": engine.user_id, "recipient_id": ObjectId(recipient_id), "day_key": day},
                {"$set": {
                    "status": "sent",
                    "attempt_no": attempt_no,
                    "sent_at": sent_at,
                    "error": None,
                    "updated_at": _utc_now_iso()
                }},
                upsert=True
            )
            sent_today_recipient_ids.add(recipient_id)
            if not engine.enable_followup:
                sent_ever_recipient_ids.add(recipient_id)
            else:
                last_sent_times[recipient_id] = now
            total_sent += 1
            consecutive_transient_failures = 0

            db.run_state.update_one(
                {"user_id": engine.user_id, "batch_id": engine.batch_id, "day_key": day},
                {"$set": {"next_source_order": next_source_order + 1, "updated_at": _utc_now_iso()}}
            )
            next_source_order += 1

        except Exception as exc:
            transient = _is_transient_error(exc)
            consecutive_transient_failures += 1 if transient else 0

            if transient and attempt_no <= engine.max_retries_per_recipient_per_day:
                backoff = min(engine.retry_backoff_max_sec, engine.retry_backoff_base_sec * (2 ** max(0, attempt_no - 2)))
                backoff = int(backoff * (1 + random.random() * 0.4))
                db.send_log.update_one(
                    {"user_id": engine.user_id, "recipient_id": ObjectId(recipient_id), "day_key": day},
                    {"$set": {
                        "status": "pending",
                        "attempt_no": attempt_no,
                        "sent_at": None,
                        "error": str(exc)[:500],
                        "updated_at": _utc_now_iso()
                    }},
                    upsert=True
                )
                report("sleeping", next_at=now + timedelta(seconds=backoff), err=str(exc))
                time.sleep(max(5, min(backoff, 60)))
                continue

            db.send_log.update_one(
                {"user_id": engine.user_id, "recipient_id": ObjectId(recipient_id), "day_key": day},
                {"$set": {
                    "status": "failed",
                    "attempt_no": attempt_no,
                    "sent_at": None,
                    "error": str(exc)[:500],
                    "updated_at": _utc_now_iso()
                }},
                upsert=True
            )
            attempted_today.add(recipient_id)
            total_failed += 1
            db.run_state.update_one(
                {"user_id": engine.user_id, "batch_id": engine.batch_id, "day_key": day},
                {"$set": {"next_source_order": next_source_order + 1, "updated_at": _utc_now_iso()}}
            )
            next_source_order += 1

        sleep_sec = random.randint(min_delay, max_delay)
        next_send = _local_now() + timedelta(seconds=sleep_sec)
        report("sleeping", next_at=next_send)
        time.sleep(sleep_sec)

    day_sent = _count_sent_today(engine, day)
    return {
        "ok": True,
        "day_key": day,
        "total_sent": day_sent,
        "total_failed": total_failed,
        "total_skipped": total_skipped,
        "imported": imported,
    }


def load_preview(engine: EngineConfig, recipients_csv_path: str, limit: int = 3) -> List[Dict[str, str]]:
    rows = _parse_csv_recipients(recipients_csv_path)
    out: List[Dict[str, str]] = []
    for row in rows[:limit]:
        if engine.consent_required and not _is_truthy_consent(row.get(engine.consent_field_name), engine.consent_truthy):
            continue
        out.append(
            {
                "to": str(row.get("email", "")).strip(),
                "subject": _render_template(engine.subject_template, row).strip(),
                "body": _render_template(engine.body_template, row).strip(),
            }
        )
    return out

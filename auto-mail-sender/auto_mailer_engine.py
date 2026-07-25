import csv
import dataclasses
import json
import os
import random
import re
import smtplib
import sqlite3
import socket
import ssl
import time
import mimetypes
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, date, time as dtime, timedelta
from email.message import EmailMessage
from typing import Any, Dict, Iterable, List, Optional, Tuple, Callable


DB_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS batches (
  batch_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS batch_recipients (
  batch_id TEXT NOT NULL,
  recipient_id INTEGER NOT NULL,
  source_order INTEGER NOT NULL,
  PRIMARY KEY (batch_id, recipient_id),
  FOREIGN KEY (batch_id) REFERENCES batches(batch_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS recipients (
  recipient_id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  data_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- Tracks sending outcomes. "sent" must never be repeated the same day for a recipient.
CREATE TABLE IF NOT EXISTS send_log (
  recipient_id INTEGER NOT NULL,
  day_key TEXT NOT NULL,               -- YYYY-MM-DD in local time
  status TEXT NOT NULL,               -- pending|sent|failed|skipped
  attempt_no INTEGER NOT NULL,
  sent_at TEXT,
  error TEXT,
  updated_at TEXT NOT NULL,
  opened INTEGER DEFAULT 0,
  opened_at TEXT,
  clicked INTEGER DEFAULT 0,
  clicked_at TEXT,
  PRIMARY KEY (recipient_id, day_key)
);

-- Persists progress for a specific batch+day so restarts resume where they left off.
CREATE TABLE IF NOT EXISTS run_state (
  batch_id TEXT NOT NULL,
  day_key TEXT NOT NULL,
  next_source_order INTEGER NOT NULL, -- index in the sorted CSV order
  updated_at TEXT NOT NULL,
  PRIMARY KEY (batch_id, day_key),
  FOREIGN KEY (batch_id) REFERENCES batches(batch_id) ON DELETE CASCADE
);
"""


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
    db_path: str
    batch_id: str
    from_email: str
    smtp_host: str
    smtp_port: int
    smtp_app_password: str
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

    # Retry behavior for transient errors (keeps the same recipient).
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


def init_db(db_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(DB_SCHEMA)
        # Migrate existing DB schema dynamically if tracking columns are missing
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(send_log)")
        columns = [col[1] for col in cursor.fetchall()]
        if "opened" not in columns:
            conn.execute("ALTER TABLE send_log ADD COLUMN opened INTEGER DEFAULT 0")
        if "opened_at" not in columns:
            conn.execute("ALTER TABLE send_log ADD COLUMN opened_at TEXT")
        if "clicked" not in columns:
            conn.execute("ALTER TABLE send_log ADD COLUMN clicked INTEGER DEFAULT 0")
        if "clicked_at" not in columns:
            conn.execute("ALTER TABLE send_log ADD COLUMN clicked_at TEXT")


def _parse_csv_recipients(csv_path: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()})
    return rows


def _render_template(template: str, data: Dict[str, Any]) -> str:
    """
    Simple {field} interpolation using Python's format_map.
    Missing keys become an empty string to keep batches robust.
    """

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
    with sqlite3.connect(engine.db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO batches(batch_id, created_at) VALUES (?, ?)", (engine.batch_id, _utc_now_iso()))


def upsert_batch_recipients(engine: EngineConfig, csv_path: str) -> int:
    """
    Imports recipients into `recipients` and attaches them to the current `batch_id`.
    Returns number of recipients imported.
    """
    rows = _parse_csv_recipients(csv_path)
    if not rows:
        return 0

    required = ["email"]
    for r in required:
        if r not in rows[0]:
            raise ValueError(f"CSV must include a '{r}' column.")

    with sqlite3.connect(engine.db_path) as conn:
        conn.execute("BEGIN")
        inserted = 0
        # Remove existing mapping for this batch (so you can re-import with edits).
        conn.execute("DELETE FROM batch_recipients WHERE batch_id = ?", (engine.batch_id,))

        for idx, row in enumerate(rows):
            email = str(row.get("email", "")).strip()
            if not email:
                continue

            # Store entire row as JSON for personalization.
            data_json = json.dumps(row, ensure_ascii=False)
            cur = conn.execute(
                "INSERT OR IGNORE INTO recipients(email, data_json, created_at) VALUES (?, ?, ?)",
                (email, data_json, _utc_now_iso()),
            )
            # If recipient existed, still refresh data_json for template updates.
            conn.execute(
                "UPDATE recipients SET data_json = ? WHERE email = ?",
                (data_json, email),
            )

            rec_id = conn.execute("SELECT recipient_id FROM recipients WHERE email = ?", (email,)).fetchone()
            if not rec_id:
                continue
            recipient_id = int(rec_id[0])

            conn.execute(
                "INSERT OR REPLACE INTO batch_recipients(batch_id, recipient_id, source_order) VALUES (?, ?, ?)",
                (engine.batch_id, recipient_id, idx),
            )
            inserted += 1
        conn.commit()
    return inserted


def _get_sorted_batch_emails(conn: sqlite3.Connection, engine: EngineConfig) -> List[Tuple[int, str, Dict[str, Any]]]:
    """
    Returns list of (recipient_id, email, data_dict), sorted by CSV source order.
    """
    q = """
      SELECT r.recipient_id, r.email, r.data_json
      FROM batch_recipients br
      JOIN recipients r ON r.recipient_id = br.recipient_id
      WHERE br.batch_id = ?
      ORDER BY br.source_order ASC
    """
    out: List[Tuple[int, str, Dict[str, Any]]] = []
    for recipient_id, email, data_json in conn.execute(q, (engine.batch_id,)):
        data_dict = json.loads(data_json)
        out.append((int(recipient_id), str(email), data_dict))
    return out


def _count_sent_today(conn: sqlite3.Connection, engine: EngineConfig, day: str) -> int:
    q = "SELECT COUNT(*) FROM send_log WHERE day_key = ? AND status = 'sent'"
    return int(conn.execute(q, (day,)).fetchone()[0])


def _derive_delay_bounds(engine: EngineConfig) -> Tuple[int, int]:
    if engine.min_delay_sec is not None and engine.max_delay_sec is not None:
        return engine.min_delay_sec, engine.max_delay_sec
    # Apply jitter range to delay_sec to look more human.
    min_delay = max(1, int(engine.delay_sec * (1 - engine.jitter_factor)))
    max_delay = max(min_delay + 2, int(engine.delay_sec * (1 + engine.jitter_factor)))
    return min_delay, max_delay


def _smtp_send(engine: EngineConfig, to_email: str, subject: str, body: str) -> None:
    # Keep it simple: send body as HTML to support rich templates and tracking
    msg = EmailMessage()
    msg["From"] = engine.from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body, subtype="html")

    # Add attachments if any
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

    # Note: Gmail SMTP requires an app password (with 2FA enabled) or OAuth2.
    context = ssl.create_default_context()
    with smtplib.SMTP(engine.smtp_host, engine.smtp_port, timeout=30) as server:
        if engine.smtp_use_starttls:
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
        server.login(engine.from_email, engine.smtp_app_password)
        server.send_message(msg)


def _is_transient_error(exc: Exception) -> bool:
    # Best-effort classification to distinguish network issues from permanent recipient rejects.
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
    # Smarter heuristic: some SMTPExceptions are permanent; without parsing codes, be conservative.
    msg = str(exc).lower()
    if any(k in msg for k in ["timeout", "temporar", "connection", "network", "timed out", "reset"]):
        return True
    return False


def rewrite_links(html_content: str, recipient_id: int, day_key: str, tracking_base_url: str) -> str:
    def replace_link(match):
        url = match.group(2)
        # Skip relative links, mailto/javascript, or already tracking URLs
        if (url.startswith("#") or url.startswith("mailto:") or 
            url.startswith("javascript:") or "track/click" in url):
            return match.group(0)
        
        encoded_url = urllib.parse.quote(url)
        new_href = f"{tracking_base_url}/track/click/{recipient_id}/{day_key}?url={encoded_url}"
        return f'{match.group(1)}="{new_href}"'

    # Matches href="url" or href='url'
    pattern = r'(href)\s*=\s*["\']([^"\']+)["\']'
    return re.sub(pattern, replace_link, html_content)


def run_outreach(engine: EngineConfig, recipients_csv_path: str, stop_flag: Any, on_progress: Optional[Callable[[ProgressUpdate], None]] = None) -> Dict[str, Any]:
    """
    Main loop. Designed to be called from a background thread.

    stop_flag: an object with attribute/value `is_set` or callable `is_set()`.
    on_progress: optional callback for real-time status updates.
    """
    init_db(engine.db_path)
    ensure_batch(engine)
    imported = upsert_batch_recipients(engine, recipients_csv_path)
    if imported == 0:
        return {"ok": False, "reason": "No recipients imported."}

    min_delay, max_delay = _derive_delay_bounds(engine)

    with sqlite3.connect(engine.db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        day = day_key_local(_local_now())
        # Fetch recipients once per run day.
        batch_list = _get_sorted_batch_emails(conn, engine)

        # Load sending history for duplicate prevention or follow-up check
        sent_ever_recipient_ids = set()
        last_sent_times: Dict[int, datetime] = {}

        if not engine.enable_followup:
            sent_ever_recipient_ids = set(
                r_id
                for (r_id,) in conn.execute(
                    "SELECT DISTINCT recipient_id FROM send_log WHERE status = 'sent'"
                ).fetchall()
            )
        else:
            for r_id, sent_at_str in conn.execute(
                "SELECT recipient_id, MAX(sent_at) FROM send_log WHERE status = 'sent' AND sent_at IS NOT NULL GROUP BY recipient_id"
            ).fetchall():
                try:
                    # Parse local ISO format
                    last_sent_times[r_id] = datetime.fromisoformat(sent_at_str)
                except Exception:
                    pass

        current_day_for_sets = None
        sent_today_recipient_ids: set[int] = set()
        attempted_today: set[int] = set()
        next_source_order = 0

        def refresh_day_state(day_key: str) -> None:
            nonlocal current_day_for_sets, sent_today_recipient_ids, attempted_today, next_source_order
            current_day_for_sets = day_key

            # Ensure run_state exists.
            cur_state = conn.execute(
                "SELECT next_source_order FROM run_state WHERE batch_id = ? AND day_key = ?",
                (engine.batch_id, day_key),
            ).fetchone()
            if cur_state is None:
                conn.execute(
                    "INSERT OR REPLACE INTO run_state(batch_id, day_key, next_source_order, updated_at) VALUES (?, ?, ?, ?)",
                    (engine.batch_id, day_key, 0, _utc_now_iso()),
                )
                conn.commit()
                next_source_order = 0
            else:
                next_source_order = int(cur_state[0])

            sent_today_recipient_ids = set(
                r_id
                for (r_id,) in conn.execute(
                    """
                    SELECT recipient_id FROM send_log WHERE day_key = ? AND status = 'sent'
                    """,
                    (day_key,),
                ).fetchall()
            )
            attempted_today = set(
                r_id
                for (r_id,) in conn.execute(
                    """
                    SELECT recipient_id FROM send_log WHERE day_key = ? AND status IN ('failed','skipped')
                    """,
                    (day_key,),
                ).fetchall()
            )

        refresh_day_state(day)

        # When consent is required, pre-filter.
        def consent_ok(data_dict: Dict[str, Any]) -> bool:
            if not engine.consent_required:
                return True
            return _is_truthy_consent(data_dict.get(engine.consent_field_name), engine.consent_truthy)

        # Main sending loop: keep running until window ends or all recipients processed.
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
            # If outside sending window, wait for next open time (but also enforce daily limit).
            if not engine.window.contains(now):
                # If daily limit reached, go to next day start; otherwise still wait for window.
                day = day_key_local(now)
                refresh_day_state(day)
                if _count_sent_today(conn, engine, day) >= engine.daily_limit:
                    next_open = engine.window.next_open_time(now)
                    report("sleeping", next_at=next_open)
                    sleep_sec = max(5, int((next_open - now).total_seconds()))
                    time.sleep(min(sleep_sec, 60))
                    continue
                next_open = engine.window.next_open_time(now)
                report("sleeping", next_at=next_open)
                sleep_sec = max(5, int((next_open - now).total_seconds()))
                time.sleep(min(sleep_sec, 60))
                continue

            # Enforce daily caps.
            day = day_key_local(now)
            if day != current_day_for_sets:
                refresh_day_state(day)
            day_sent = _count_sent_today(conn, engine, day)
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
                conn.execute(
                    """
                    INSERT OR REPLACE INTO send_log(recipient_id, day_key, status, attempt_no, sent_at, error, updated_at)
                    VALUES (?, ?, 'skipped', 0, NULL, ?, ?)
                    """,
                    (recipient_id, day, "consent_required_failed", _utc_now_iso()),
                )
                conn.execute(
                    "UPDATE run_state SET next_source_order = ?, updated_at = ? WHERE batch_id = ? AND day_key = ?",
                    (next_source_order + 1, _utc_now_iso(), engine.batch_id, day),
                )
                conn.commit()
                total_skipped += 1
                next_source_order += 1
                continue

            # Check if followup mode is disabled: skip if they have ever been emailed.
            if not engine.enable_followup and recipient_id in sent_ever_recipient_ids:
                conn.execute(
                    "UPDATE run_state SET next_source_order = ?, updated_at = ? WHERE batch_id = ? AND day_key = ?",
                    (next_source_order + 1, _utc_now_iso(), engine.batch_id, day),
                )
                conn.commit()
                total_skipped += 1
                next_source_order += 1
                continue

            # Check if followup mode is enabled: skip if they were never emailed, or if not enough days have passed.
            if engine.enable_followup:
                if recipient_id not in last_sent_times:
                    conn.execute(
                        "UPDATE run_state SET next_source_order = ?, updated_at = ? WHERE batch_id = ? AND day_key = ?",
                        (next_source_order + 1, _utc_now_iso(), engine.batch_id, day),
                    )
                    conn.commit()
                    total_skipped += 1
                    next_source_order += 1
                    continue
                else:
                    last_sent_time = last_sent_times[recipient_id]
                    days_passed = (now.date() - last_sent_time.date()).days
                    if days_passed < engine.followup_days:
                        conn.execute(
                            "UPDATE run_state SET next_source_order = ?, updated_at = ? WHERE batch_id = ? AND day_key = ?",
                            (next_source_order + 1, _utc_now_iso(), engine.batch_id, day),
                        )
                        conn.commit()
                        total_skipped += 1
                        next_source_order += 1
                        continue

            # Never send twice the same day.
            if recipient_id in sent_today_recipient_ids:
                conn.execute(
                    "UPDATE run_state SET next_source_order = ?, updated_at = ? WHERE batch_id = ? AND day_key = ?",
                    (next_source_order + 1, _utc_now_iso(), engine.batch_id, day),
                )
                conn.commit()
                next_source_order += 1
                continue

            # If we've already attempted and failed today, skip (requirement: no re-send).
            if recipient_id in attempted_today:
                conn.execute(
                    "UPDATE run_state SET next_source_order = ?, updated_at = ? WHERE batch_id = ? AND day_key = ?",
                    (next_source_order + 1, _utc_now_iso(), engine.batch_id, day),
                )
                conn.commit()
                next_source_order += 1
                continue

            subject = _render_template(engine.subject_template, data_dict).strip()
            if not subject:
                subject = "(no subject)"
            body = _render_template(engine.body_template, data_dict).strip()

            # Apply tracking pixel and rewrite links if tracking URL is provided
            if getattr(engine, "tracking_base_url", None):
                pixel_tag = f'<img src="{engine.tracking_base_url}/track/open/{recipient_id}/{day}" width="1" height="1" style="display:none;" />'
                body = rewrite_links(body, recipient_id, day, engine.tracking_base_url)
                body = body + pixel_tag

            # Attempt send
            attempt_no = int(
                conn.execute(
                    "SELECT COALESCE(MAX(attempt_no), 0) FROM send_log WHERE recipient_id = ? AND day_key = ?",
                    (recipient_id, day),
                ).fetchone()[0]
            )
            attempt_no += 1

            try:
                _smtp_send(engine, to_email, subject, body)
                sent_at = now.isoformat(timespec="seconds")
                conn.execute(
                    """
                    INSERT OR REPLACE INTO send_log(recipient_id, day_key, status, attempt_no, sent_at, error, updated_at)
                    VALUES (?, ?, 'sent', ?, ?, NULL, ?)
                    """,
                    (recipient_id, day, attempt_no, sent_at, _utc_now_iso()),
                )
                sent_today_recipient_ids.add(recipient_id)
                if not engine.enable_followup:
                    sent_ever_recipient_ids.add(recipient_id)
                else:
                    last_sent_times[recipient_id] = now
                total_sent += 1
                consecutive_transient_failures = 0

                conn.execute(
                    "UPDATE run_state SET next_source_order = ?, updated_at = ? WHERE batch_id = ? AND day_key = ?",
                    (next_source_order + 1, _utc_now_iso(), engine.batch_id, day),
                )
                conn.commit()
                next_source_order += 1

            except Exception as exc:
                transient = _is_transient_error(exc)
                consecutive_transient_failures += 1 if transient else 0

                retry_count_for_day = int(
                    conn.execute(
                        "SELECT COALESCE(MAX(attempt_no), 0) FROM send_log WHERE recipient_id = ? AND day_key = ?",
                        (recipient_id, day),
                    ).fetchone()[0]
                )
                if transient and retry_count_for_day < engine.max_retries_per_recipient_per_day:
                    backoff = min(engine.retry_backoff_max_sec, engine.retry_backoff_base_sec * (2 ** max(0, retry_count_for_day - 1)))
                    backoff = int(backoff * (1 + random.random() * 0.4))
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO send_log(recipient_id, day_key, status, attempt_no, sent_at, error, updated_at)
                        VALUES (?, ?, 'pending', ?, NULL, ?, ?)
                        """,
                        (recipient_id, day, attempt_no, str(exc)[:500], _utc_now_iso()),
                    )
                    conn.commit()
                    report("sleeping", next_at=now + timedelta(seconds=backoff), err=str(exc))
                    time.sleep(max(5, min(backoff, 60)))
                    continue

                conn.execute(
                    """
                    INSERT OR REPLACE INTO send_log(recipient_id, day_key, status, attempt_no, sent_at, error, updated_at)
                    VALUES (?, ?, 'failed', ?, NULL, ?, ?)
                    """,
                    (recipient_id, day, attempt_no, str(exc)[:500], _utc_now_iso()),
                )
                attempted_today.add(recipient_id)
                total_failed += 1
                conn.execute(
                    "UPDATE run_state SET next_source_order = ?, updated_at = ? WHERE batch_id = ? AND day_key = ?",
                    (next_source_order + 1, _utc_now_iso(), engine.batch_id, day),
                )
                conn.commit()
                next_source_order += 1

            sleep_sec = random.randint(min_delay, max_delay)
            next_send = _local_now() + timedelta(seconds=sleep_sec)
            report("sleeping", next_at=next_send)
            time.sleep(sleep_sec)

        # Gather summary.
        day_sent = _count_sent_today(conn, engine, day)
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
        # Guard missing consent if required.
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


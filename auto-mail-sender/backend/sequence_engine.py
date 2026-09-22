"""Apollo.io-style multi-step email sequences with durable MongoDB scheduling."""

from __future__ import annotations

import logging
import mimetypes
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from bson.binary import Binary
from bson.objectid import ObjectId
from pymongo import ReturnDocument, UpdateOne, InsertOne

from auto_mailer_engine import (
    DailyWindow,
    EngineConfig,
    _is_transient_error,
    _is_truthy_consent,
    _local_now,
    _parse_recipients_file,
    _render_template,
    _smtp_send,
    _utc_now_iso,
    check_inbox_replies,
    day_key_local,
    get_db,
    init_db,
    rewrite_links,
)

logger = logging.getLogger(__name__)

ALLOWED_SEQUENCE_STATUSES = frozenset({"draft", "active", "paused", "completed", "deleted"})
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
ALLOWED_ATTACHMENT_MIME_PREFIXES = (
    "image/",
    "application/pdf",
    "application/msword",
    "application/vnd.",
    "text/plain",
    "text/csv",
)


def init_sequence_db() -> None:
    init_db()

def _parse_window(settings: Dict[str, Any]) -> DailyWindow:
    start_str = settings.get("window_start", "09:00")
    end_str = settings.get("window_end", "17:00")
    sh, sm = map(int, start_str.split(":"))
    eh, em = map(int, end_str.split(":"))
    return DailyWindow(
        start=datetime(2000, 1, 1, sh, sm).time(),
        end=datetime(2000, 1, 1, eh, em).time(),
    )


def _normalize_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not steps:
        raise ValueError("At least one email step is required.")
    normalized = []
    for idx, step in enumerate(steps):
        body = str(step.get("body", "")).strip()
        if not body:
            raise ValueError(f"Step {idx + 1}: body is required.")

        variants_raw = step.get("subject_variants")
        if variants_raw and isinstance(variants_raw, list):
            variants = [str(v).strip() for v in variants_raw if str(v).strip()]
        else:
            variants = []

        subject = str(step.get("subject", "")).strip()
        if not variants and subject:
            variants = [subject]
        if not variants:
            raise ValueError(f"Step {idx + 1}: at least one subject variant is required.")

        delay_days = int(step.get("delay_days", 0 if idx == 0 else 3))
        delay_hours = int(step.get("delay_hours", 0))
        if delay_days < 0 or delay_hours < 0:
            raise ValueError(f"Step {idx + 1}: delay must be >= 0.")
        if idx == 0:
            delay_days = 0
            delay_hours = 0
        attachment_ids = []
        raw_ids = step.get("attachment_ids") or []
        if isinstance(raw_ids, list):
            for aid in raw_ids:
                aid_str = str(aid).strip()
                if aid_str:
                    attachment_ids.append(aid_str)

        normalized.append(
            {
                "step_index": idx,
                "subject": variants[0],
                "subject_variants": variants,
                "body": body,
                "delay_days": delay_days,
                "delay_hours": delay_hours,
                "attachment_ids": attachment_ids,
            }
        )
    return normalized


def _pick_subject_variant(
    step: Dict[str, Any], enrollment_id: Any, step_index: int
) -> str:
    """Deterministic A/B pick — same contact always gets the same variant for a step."""
    variants = step.get("subject_variants") or [step.get("subject", "")]
    variants = [v for v in variants if v]
    if len(variants) == 1:
        return variants[0]
    key = f"{enrollment_id}:{step_index}"
    bucket = sum(ord(c) for c in key) % len(variants)
    return variants[bucket]


def _render_step_subject(
    step: Dict[str, Any], data_dict: Dict[str, Any], enrollment_id: Any, step_index: int
) -> str:
    template = _pick_subject_variant(step, enrollment_id, step_index)
    return _render_template(template, data_dict).strip() or "(no subject)"


def _normalize_settings(settings: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    settings = settings or {}
    daily_limit = int(settings.get("daily_limit", 100))
    delay_sec = int(settings.get("delay_sec", 60))
    if daily_limit <= 0 or delay_sec <= 0:
        raise ValueError("Daily limit and delay must be positive integers.")
    # Exact user interval — no random jitter between sequence sends.
    return {
        "daily_limit": daily_limit,
        "delay_sec": delay_sec,
        "window_start": settings.get("window_start", "09:00"),
        "window_end": settings.get("window_end", "17:00"),
        "consent_required": bool(settings.get("consent_required", False)),
        "enable_reply_tracking": bool(settings.get("enable_reply_tracking", True)),
        "min_delay_sec": delay_sec,
        "max_delay_sec": delay_sec,
    }


def _seq_to_dict(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "name": doc.get("name", ""),
        "status": doc.get("status", "draft"),
        "settings": doc.get("settings", {}),
        "steps": doc.get("steps", []),
        "stats": doc.get("stats", {}),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "activated_at": doc.get("activated_at"),
        "deleted_at": doc.get("deleted_at"),
    }


def _assert_not_deleted(seq: Dict[str, Any]) -> None:
    if seq.get("status") == "deleted":
        raise ValueError("Sequence has been deleted.")


def _log_event(event: str, **fields: Any) -> None:
    parts = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    logger.info("%s %s", event, parts)
    print(f"[{event}] {parts}")


def create_sequence(
    user_id: str, name: str, steps: List[Dict[str, Any]], settings: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    init_sequence_db()
    name = name.strip()
    if not name:
        raise ValueError("Sequence name is required.")
    norm_steps = _normalize_steps(steps)
    norm_settings = _normalize_settings(settings)
    now = _utc_now_iso()
    doc = {
        "user_id": user_id,
        "name": name,
        "status": "draft",
        "settings": norm_settings,
        "steps": norm_steps,
        "stats": {"enrolled": 0, "active": 0, "completed": 0, "replied": 0, "sent_today": 0},
        "created_at": now,
        "updated_at": now,
        "activated_at": None,
    }
    res = get_db().sequences.insert_one(doc)
    doc["_id"] = res.inserted_id
    _log_event("sequence.created", sequence_id=str(doc["_id"]), user_id=user_id)
    return _seq_to_dict(doc)


def update_sequence(
    user_id: str,
    sequence_id: str,
    name: Optional[str] = None,
    steps: Optional[List[Dict[str, Any]]] = None,
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    init_sequence_db()
    db = get_db()
    seq = db.sequences.find_one({"_id": ObjectId(sequence_id), "user_id": user_id})
    if not seq:
        raise ValueError("Sequence not found.")
    _assert_not_deleted(seq)
    if seq["status"] == "active":
        raise ValueError("Pause the sequence before editing.")

    update: Dict[str, Any] = {"updated_at": _utc_now_iso()}
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("Sequence name is required.")
        update["name"] = name
    if steps is not None:
        # Preserve attachment_ids from existing steps when client omits them
        existing_by_idx = {int(s.get("step_index", i)): s for i, s in enumerate(seq.get("steps") or [])}
        for i, step in enumerate(steps):
            if not step.get("attachment_ids") and i in existing_by_idx:
                step = {**step, "attachment_ids": existing_by_idx[i].get("attachment_ids") or []}
                steps[i] = step
        update["steps"] = _normalize_steps(steps)
    if settings is not None:
        update["settings"] = _normalize_settings(settings)

    db.sequences.update_one({"_id": ObjectId(sequence_id)}, {"$set": update})
    updated = db.sequences.find_one({"_id": ObjectId(sequence_id)})
    
    # Recalculate next_send_at for all active enrollments when timing or steps change
    try:
        active_enrollments = list(db.enrollments.find({"sequence_id": ObjectId(sequence_id), "status": "active"}))
        if active_enrollments:
            norm_settings = updated.get("settings", {})
            norm_steps = updated.get("steps", [])
            now = _local_now()
            for e in active_enrollments:
                step_idx = int(e.get("current_step", 0))
                if step_idx == 0 and not e.get("last_sent_at"):
                    first_send = _schedule_first_send(norm_settings)
                    new_next = first_send.isoformat(timespec="seconds")
                else:
                    base_time = None
                    if e.get("last_sent_at"):
                        try:
                            base_time = datetime.fromisoformat(e["last_sent_at"])
                        except Exception:
                            pass
                    if not base_time:
                        base_time = now
                    next_dt = _compute_next_send_after_step(norm_settings, norm_steps, step_idx, base_time)
                    new_next = next_dt.isoformat(timespec="seconds") if next_dt else None
                
                db.enrollments.update_one(
                    {"_id": e["_id"]},
                    {"$set": {"next_send_at": new_next, "updated_at": _utc_now_iso()}}
                )
    except Exception as exc:
        print(f"[update_sequence warning] Failed to update enrollment send times: {exc}")

    _log_event("sequence.updated", sequence_id=sequence_id, user_id=user_id, status=updated.get("status"))
    return _seq_to_dict(updated)


def list_sequences(user_id: str) -> List[Dict[str, Any]]:
    init_sequence_db()
    docs = get_db().sequences.find(
        {"user_id": user_id, "status": {"$ne": "deleted"}}
    ).sort("created_at", -1)
    return [_seq_to_dict(d) for d in docs]


def get_sequence(user_id: str, sequence_id: str) -> Dict[str, Any]:
    init_sequence_db()
    doc = get_db().sequences.find_one({"_id": ObjectId(sequence_id), "user_id": user_id})
    if not doc:
        raise ValueError("Sequence not found.")
    _assert_not_deleted(doc)
    return _seq_to_dict(doc)


def _upsert_recipient(user_id: str, row: Dict[str, Any]) -> ObjectId:
    email = str(row.get("email", "")).strip().lower()
    if not email:
        raise ValueError("Each row must include an email.")
    db = get_db()
    res = db.recipients.find_one_and_update(
        {"user_id": user_id, "email": email},
        {
            "$set": {"data": row, "updated_at": _utc_now_iso()},
            "$setOnInsert": {"created_at": _utc_now_iso(), "replied": False, "do_not_contact": False},
        },
        upsert=True,
        return_document=True,
    )
    return res["_id"]


def _schedule_first_send(settings: Dict[str, Any]) -> datetime:
    # Step 0 sends immediately on launch
    return _local_now()


def _refresh_due_enrollment_times(sequence: Dict[str, Any]) -> int:
    """Ensure active enrollments have a valid send time without overwriting future step schedules."""
    settings = sequence["settings"]
    window = _parse_window(settings)
    now = _local_now()
    if not window.contains(now):
        return 0

    now_iso = now.isoformat(timespec="seconds")
    res = get_db().enrollments.update_many(
        {
            "sequence_id": sequence["_id"],
            "status": "active",
            "next_send_at": None,
        },
        {"$set": {"next_send_at": now_iso, "updated_at": _utc_now_iso()}},
    )
    return int(res.modified_count)


def enroll_from_data(user_id: str, sequence_id: str, rows: List[Dict[str, Any]]) -> int:
    """Import list of contact dicts into a sequence using fast MongoDB bulk operations. Returns number newly enrolled at Email 1."""
    init_sequence_db()
    db = get_db()
    seq_oid = ObjectId(sequence_id)
    seq = db.sequences.find_one({"_id": seq_oid, "user_id": user_id})
    if not seq:
        raise ValueError("Sequence not found.")
    _assert_not_deleted(seq)

    if not rows:
        raise ValueError("No valid recipients provided.")
    
    settings = seq["settings"]
    consent_required = settings.get("consent_required", False)
    first_send_at = _schedule_first_send(settings)
    first_send_iso = first_send_at.isoformat(timespec="seconds")
    now_iso = _utc_now_iso()

    # Step 1: Filter and sanitize rows
    valid_rows = []
    valid_emails = []
    seen_emails = set()
    for row in rows:
        email = str(row.get("email", "")).strip().lower()
        if not email or "@" not in email:
            continue
        if consent_required and not _is_truthy_consent(row.get("consent"), ("true", "1", "yes", "y")):
            continue
        if email in seen_emails:
            continue
        seen_emails.add(email)
        valid_rows.append((email, row))
        valid_emails.append(email)

    if not valid_rows:
        return 0

    # Step 2: Bulk upsert recipients
    rec_ops = []
    for email, row in valid_rows:
        rec_ops.append(
            UpdateOne(
                {"user_id": user_id, "email": email},
                {
                    "$set": {"data": row, "updated_at": now_iso},
                    "$setOnInsert": {"created_at": now_iso, "replied": False, "do_not_contact": False},
                },
                upsert=True
            )
        )
    if rec_ops:
        db.recipients.bulk_write(rec_ops, ordered=False)

    # Step 3: Fetch recipient IDs in a single query
    recipient_docs = list(db.recipients.find({"user_id": user_id, "email": {"$in": valid_emails}}, {"_id": 1, "email": 1}))
    email_to_rec_id = {doc["email"]: doc["_id"] for doc in recipient_docs}

    # Step 4: Fetch existing enrollments in a single query
    rec_ids = list(email_to_rec_id.values())
    existing_enrollments = list(db.enrollments.find(
        {"sequence_id": seq_oid, "recipient_id": {"$in": rec_ids}}
    ))
    existing_map = {e["recipient_id"]: e for e in existing_enrollments}

    # Step 5: Build bulk enrollment operations
    enrollment_ops = []
    enrolled = 0
    for email, row in valid_rows:
        rec_id = email_to_rec_id.get(email)
        if not rec_id:
            continue

        existing = existing_map.get(rec_id)
        if existing:
            if existing.get("status") in ("stopped_replied", "stopped_unsubscribed"):
                continue
            patch: Dict[str, Any] = {
                "data": row,
                "email": email,
                "updated_at": now_iso,
            }
            if existing.get("status") == "stopped_failed":
                patch["status"] = "active"
                patch["next_send_at"] = first_send_iso
            enrollment_ops.append(
                UpdateOne({"_id": existing["_id"]}, {"$set": patch})
            )
        else:
            enrollment_ops.append(
                InsertOne(
                    {
                        "user_id": user_id,
                        "sequence_id": seq_oid,
                        "recipient_id": rec_id,
                        "email": email,
                        "data": row,
                        "current_step": 0,
                        "status": "active",
                        "next_send_at": first_send_iso,
                        "last_sent_at": None,
                        "enrolled_at": now_iso,
                        "updated_at": now_iso,
                    }
                )
            )
            enrolled += 1

    if enrollment_ops:
        db.enrollments.bulk_write(enrollment_ops, ordered=False)

    if enrolled:
        db.sequences.update_one(
            {"_id": seq_oid},
            {
                "$inc": {"stats.enrolled": enrolled, "stats.active": enrolled},
                "$set": {"updated_at": now_iso},
            },
        )
    return enrolled


def enroll_from_csv(user_id: str, sequence_id: str, csv_path: str) -> int:
    """Import CSV/Excel contacts into a sequence. Returns number enrolled."""
    rows = _parse_recipients_file(csv_path)
    if not rows:
        raise ValueError("No valid recipients found in file.")
    if "email" not in rows[0]:
        raise ValueError("File must include an 'email' column header.")
    return enroll_from_data(user_id, sequence_id, rows)


def activate_sequence(user_id: str, sequence_id: str, csv_path: Optional[str] = None, contacts_data: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    init_sequence_db()
    db = get_db()
    user = db.users.find_one({"_id": ObjectId(user_id)})
    if not user or not user.get("smtp_email") or not user.get("smtp_app_password"):
        raise ValueError("CREDENTIALS_MISSING: Sender email or Gmail App Password is not configured. Please save your credentials in Settings.")

    seq = db.sequences.find_one({"_id": ObjectId(sequence_id), "user_id": user_id})
    if not seq:
        raise ValueError("Sequence not found.")
    _assert_not_deleted(seq)
    if seq.get("status") == "active":
        # Idempotent re-activate: enroll any new contacts, keep same sequence
        pass
    elif seq.get("status") not in ("draft", "paused", "completed"):
        raise ValueError(f"Cannot activate sequence in status '{seq.get('status')}'.")

    if not seq.get("steps"):
        raise ValueError("Add at least one email step before activating.")

    enrolled = 0
    if contacts_data:
        enrolled = enroll_from_data(user_id, sequence_id, contacts_data)
    elif csv_path:
        enrolled = enroll_from_csv(user_id, sequence_id, csv_path)

    active_count = db.enrollments.count_documents(
        {"sequence_id": ObjectId(sequence_id), "status": "active"}
    )
    if active_count == 0:
        raise ValueError("Upload a file or provide contacts before activating.")

    now = _utc_now_iso()
    # Recalculate step 0 send time so starting sequence sends immediately inside working hours
    first_send = _schedule_first_send(seq["settings"]).isoformat(timespec="seconds")
    db.enrollments.update_many(
        {
            "sequence_id": ObjectId(sequence_id),
            "status": "active",
            "current_step": 0,
            "last_sent_at": None,
        },
        {"$set": {"next_send_at": first_send, "updated_at": now}},
    )

    db.sequences.update_one(
        {"_id": ObjectId(sequence_id)},
        {"$set": {"status": "active", "activated_at": now, "updated_at": now}},
    )
    seq = db.sequences.find_one({"_id": ObjectId(sequence_id)})
    rescheduled = _refresh_due_enrollment_times(seq) if seq else 0
    _log_event(
        "sequence.started",
        sequence_id=sequence_id,
        user_id=user_id,
        enrolled=enrolled,
        active_contacts=active_count,
    )
    return {
        "sequence_id": sequence_id,
        "status": "active",
        "enrolled": enrolled,
        "active_contacts": active_count,
        "rescheduled": rescheduled,
    }


def pause_sequence(user_id: str, sequence_id: str) -> Dict[str, Any]:
    db = get_db()
    res = db.sequences.update_one(
        {"_id": ObjectId(sequence_id), "user_id": user_id, "status": "active"},
        {"$set": {"status": "paused", "updated_at": _utc_now_iso()}},
    )
    if res.matched_count == 0:
        raise ValueError("Active sequence not found.")
    _log_event("sequence.paused", sequence_id=sequence_id, user_id=user_id)
    return {"sequence_id": sequence_id, "status": "paused"}


def retry_failed_contacts(user_id: str, sequence_id: str) -> int:
    db = get_db()
    seq_oid = ObjectId(sequence_id)
    seq = db.sequences.find_one({"_id": seq_oid, "user_id": user_id})
    if not seq:
        raise ValueError("Sequence not found.")
    now = _local_now()
    now_iso = now.isoformat(timespec="seconds")
    res = db.enrollments.update_many(
        {"sequence_id": seq_oid, "status": "stopped_failed"},
        {"$set": {"status": "active", "next_send_at": now_iso, "updated_at": _utc_now_iso()}}
    )
    active_count = db.enrollments.count_documents({"sequence_id": seq_oid, "status": "active"})
    db.sequences.update_one(
        {"_id": seq_oid},
        {"$set": {"stats.active": active_count, "updated_at": _utc_now_iso()}}
    )
    return int(res.modified_count)


def resume_sequence(user_id: str, sequence_id: str) -> Dict[str, Any]:
    db = get_db()
    seq_oid = ObjectId(sequence_id)
    res = db.sequences.update_one(
        {"_id": seq_oid, "user_id": user_id, "status": "paused"},
        {"$set": {"status": "active", "updated_at": _utc_now_iso()}},
    )
    if res.matched_count == 0:
        raise ValueError("Paused sequence not found.")
    reactivated = retry_failed_contacts(user_id, sequence_id)
    seq = db.sequences.find_one({"_id": seq_oid, "user_id": user_id})
    rescheduled = _refresh_due_enrollment_times(seq) if seq else 0
    _log_event("sequence.resumed", sequence_id=sequence_id, user_id=user_id, reactivated=reactivated)
    return {"sequence_id": sequence_id, "status": "active", "rescheduled": rescheduled, "reactivated": reactivated}


def delete_sequence(user_id: str, sequence_id: str) -> None:
    """Soft-delete: stop future sends; idempotent if already deleted."""
    init_sequence_db()
    db = get_db()
    seq = db.sequences.find_one({"_id": ObjectId(sequence_id), "user_id": user_id})
    if not seq:
        raise ValueError("Sequence not found.")
    if seq.get("status") == "deleted":
        return
    now = _utc_now_iso()
    db.sequences.update_one(
        {"_id": ObjectId(sequence_id)},
        {"$set": {"status": "deleted", "deleted_at": now, "updated_at": now}},
    )
    # Clear next_send_at so orphaned jobs cannot fire if status check races
    db.enrollments.update_many(
        {"sequence_id": ObjectId(sequence_id), "status": "active"},
        {"$set": {"next_send_at": None, "updated_at": now}},
    )
    _log_event("sequence.deleted", sequence_id=sequence_id, user_id=user_id)


def _build_engine_config(user_id: str, sequence: Dict[str, Any], tracking_base_url: str = "") -> EngineConfig:
    db = get_db()
    user = db.users.find_one({"_id": ObjectId(user_id)})
    if not user:
        raise ValueError(f"User {user_id} not found.")

    email_provider = user.get("email_provider", "smtp")
    from_email = user.get("smtp_email", "").strip()
    app_password = user.get("smtp_app_password", "").strip()
    brevo_api_key = user.get("brevo_api_key", "").strip()
    brevo_sender_email = user.get("brevo_sender_email", "").strip() or from_email
    brevo_sender_name = user.get("brevo_sender_name", "").strip()

    if email_provider == "brevo":
        if not brevo_api_key or not brevo_sender_email:
            raise ValueError("Brevo API key and Sender Email are not configured. Please save them in Settings.")
        from_email = brevo_sender_email
    else:
        if not from_email or not app_password:
            # Fallback to Brevo if Brevo is configured
            if brevo_api_key and brevo_sender_email:
                email_provider = "brevo"
                from_email = brevo_sender_email
            else:
                raise ValueError("SMTP credentials (or Brevo API Key) are not configured. Please save them in Settings.")

    settings = sequence["settings"]
    window = _parse_window(settings)
    delay_sec = int(settings["delay_sec"])
    return EngineConfig(
        user_id=user_id,
        batch_id=f"seq-{sequence['_id']}",
        from_email=from_email,
        smtp_host="smtp.gmail.com",
        smtp_port=465,
        smtp_use_starttls=False,
        smtp_app_password=app_password,
        email_provider=email_provider,
        brevo_api_key=brevo_api_key,
        brevo_sender_email=brevo_sender_email,
        brevo_sender_name=brevo_sender_name,
        daily_limit=settings["daily_limit"],
        delay_sec=delay_sec,
        window=window,
        consent_required=settings.get("consent_required", True),
        # Exact interval — never derive random jitter for sequences
        min_delay_sec=delay_sec,
        max_delay_sec=delay_sec,
        jitter_factor=0.0,
        tracking_base_url=tracking_base_url,
        enable_reply_tracking=settings.get("enable_reply_tracking", True),
        imap_host=user.get("imap_host", "imap.gmail.com"),
        imap_port=int(user.get("imap_port", 993)),
        imap_username=user.get("imap_username", "").strip() or from_email,
        imap_password=user.get("imap_password", "").strip() or app_password,
    )


def _attachment_meta(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(doc["_id"]),
        "filename": doc.get("filename"),
        "content_type": doc.get("content_type"),
        "size": doc.get("size", 0),
        "sequence_id": str(doc.get("sequence_id")),
        "step_index": int(doc.get("step_index", 0)),
        "created_at": doc.get("created_at"),
    }


def store_step_attachment(
    user_id: str,
    sequence_id: str,
    step_index: int,
    filename: str,
    content_type: str,
    file_bytes: bytes,
) -> Dict[str, Any]:
    init_sequence_db()
    db = get_db()
    seq = db.sequences.find_one({"_id": ObjectId(sequence_id), "user_id": user_id})
    if not seq:
        raise ValueError("Sequence not found.")
    _assert_not_deleted(seq)
    if seq.get("status") == "active":
        raise ValueError("Pause the sequence before adding attachments.")

    steps = seq.get("steps") or []
    if step_index < 0 or step_index >= len(steps):
        raise ValueError("Invalid step index.")

    if not file_bytes:
        raise ValueError("Empty file.")
    if len(file_bytes) > MAX_ATTACHMENT_BYTES:
        raise ValueError(f"File too large. Max {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB.")

    safe_name = os.path.basename(filename or "attachment").strip() or "attachment"
    ctype = (content_type or "").strip() or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    if not any(ctype.startswith(p) for p in ALLOWED_ATTACHMENT_MIME_PREFIXES):
        raise ValueError(f"File type not allowed: {ctype}")

    doc = {
        "user_id": user_id,
        "sequence_id": ObjectId(sequence_id),
        "step_index": int(step_index),
        "filename": safe_name,
        "content_type": ctype,
        "size": len(file_bytes),
        "data": Binary(file_bytes),
        "created_at": _utc_now_iso(),
    }
    res = db.sequence_attachments.insert_one(doc)
    att_id = str(res.inserted_id)

    attachment_ids = list(steps[step_index].get("attachment_ids") or [])
    attachment_ids.append(att_id)
    steps[step_index]["attachment_ids"] = attachment_ids
    db.sequences.update_one(
        {"_id": ObjectId(sequence_id)},
        {"$set": {"steps": steps, "updated_at": _utc_now_iso()}},
    )
    _log_event(
        "attachment.uploaded",
        sequence_id=sequence_id,
        step_index=step_index,
        attachment_id=att_id,
        size=len(file_bytes),
    )
    return _attachment_meta({**doc, "_id": res.inserted_id})


def list_sequence_attachments(user_id: str, sequence_id: str) -> List[Dict[str, Any]]:
    init_sequence_db()
    db = get_db()
    seq = db.sequences.find_one({"_id": ObjectId(sequence_id), "user_id": user_id})
    if not seq:
        raise ValueError("Sequence not found.")
    docs = db.sequence_attachments.find(
        {"sequence_id": ObjectId(sequence_id), "user_id": user_id},
        {"data": 0},
    ).sort("created_at", 1)
    return [_attachment_meta(d) for d in docs]


def delete_step_attachment(user_id: str, sequence_id: str, attachment_id: str) -> None:
    init_sequence_db()
    db = get_db()
    seq = db.sequences.find_one({"_id": ObjectId(sequence_id), "user_id": user_id})
    if not seq:
        raise ValueError("Sequence not found.")
    _assert_not_deleted(seq)
    if seq.get("status") == "active":
        raise ValueError("Pause the sequence before removing attachments.")

    att = db.sequence_attachments.find_one(
        {"_id": ObjectId(attachment_id), "sequence_id": ObjectId(sequence_id), "user_id": user_id}
    )
    if not att:
        return  # idempotent

    db.sequence_attachments.delete_one({"_id": ObjectId(attachment_id)})
    steps = seq.get("steps") or []
    for step in steps:
        ids = [str(x) for x in (step.get("attachment_ids") or []) if str(x) != attachment_id]
        step["attachment_ids"] = ids
    db.sequences.update_one(
        {"_id": ObjectId(sequence_id)},
        {"$set": {"steps": steps, "updated_at": _utc_now_iso()}},
    )
    _log_event("attachment.deleted", sequence_id=sequence_id, attachment_id=attachment_id)


def load_step_attachments_for_send(sequence_id: ObjectId, step: Dict[str, Any]) -> List[Tuple[str, bytes, str]]:
    """Return (filename, bytes, content_type) for SMTP. Missing files are skipped with a log."""
    db = get_db()
    results: List[Tuple[str, bytes, str]] = []
    for aid in step.get("attachment_ids") or []:
        try:
            doc = db.sequence_attachments.find_one({"_id": ObjectId(str(aid)), "sequence_id": sequence_id})
        except Exception:
            doc = None
        if not doc or not doc.get("data"):
            _log_event("attachment.loaded", sequence_id=str(sequence_id), attachment_id=str(aid), ok=False)
            continue
        data = doc["data"]
        raw = bytes(data) if not isinstance(data, (bytes, bytearray)) else bytes(data)
        results.append(
            (
                doc.get("filename") or "attachment",
                raw,
                doc.get("content_type") or "application/octet-stream",
            )
        )
        _log_event(
            "attachment.loaded",
            sequence_id=str(sequence_id),
            attachment_id=str(aid),
            ok=True,
            size=len(raw),
        )
    return results


def record_email_open(send_log_id: str) -> Optional[Dict[str, Any]]:
    """Record an open against a specific sequence_send_log document. Idempotent first-open."""
    init_sequence_db()
    db = get_db()
    try:
        oid = ObjectId(send_log_id)
    except Exception:
        return None
    log = db.sequence_send_log.find_one({"_id": oid})
    if not log:
        return None
    now = _utc_now_iso()
    first_open = not bool(log.get("opened"))
    update: Dict[str, Any] = {"$inc": {"open_count": 1}, "$set": {"updated_at": now}}
    if first_open:
        update["$set"]["opened"] = 1
        update["$set"]["opened_at"] = now
    db.sequence_send_log.update_one({"_id": oid}, update)

    # Compatibility update for legacy send_log keyed by day
    if log.get("recipient_id") is not None and log.get("day_key"):
        db.send_log.update_one(
            {"recipient_id": log["recipient_id"], "day_key": log["day_key"]},
            {
                "$set": {"opened": 1, "opened_at": now if first_open else log.get("opened_at", now)},
                "$inc": {"open_count": 1},
            },
        )
    _log_event(
        "email.opened",
        send_log_id=send_log_id,
        sequence_id=str(log.get("sequence_id")),
        enrollment_id=str(log.get("enrollment_id")),
        step_index=log.get("step_index"),
        first_open=first_open,
    )
    return {
        "send_log_id": send_log_id,
        "sequence_id": str(log.get("sequence_id")),
        "enrollment_id": str(log.get("enrollment_id")),
        "step_index": log.get("step_index"),
        "first_open": first_open,
    }


def _count_user_sends_today(user_id: str, day: str) -> int:
    return get_db().sequence_send_log.count_documents(
        {"user_id": user_id, "day_key": day, "status": "sent"}
    )


def _compute_next_send_after_step(
    settings: Dict[str, Any], steps: List[Dict[str, Any]], next_step_index: int, from_dt: datetime
) -> Optional[datetime]:
    if next_step_index >= len(steps):
        return None
    delay_days = int(steps[next_step_index].get("delay_days", 0))
    delay_hours = int(steps[next_step_index].get("delay_hours", 0))
    target = from_dt + timedelta(days=delay_days, hours=delay_hours)
    window = _parse_window(settings)
    if window.contains(target):
        return target
    return window.next_open_time(target)


def _stop_enrollment(enrollment_id: ObjectId, status: str, sequence_id: ObjectId) -> None:
    db = get_db()
    # Check current status first so we only decrement stats.active if it was active or processing
    prev = db.enrollments.find_one_and_update(
        {"_id": enrollment_id, "status": {"$in": ["active", "processing"]}},
        {"$set": {"status": status, "updated_at": _utc_now_iso()}},
        return_document=ReturnDocument.BEFORE,
    )
    if not prev:
        db.enrollments.update_one(
            {"_id": enrollment_id},
            {"$set": {"status": status, "updated_at": _utc_now_iso()}},
        )
        return

    inc_fields: Dict[str, int] = {"stats.active": -1}
    if status == "stopped_replied":
        inc_fields["stats.replied"] = 1
    elif status == "completed":
        inc_fields["stats.completed"] = 1
    elif status == "stopped_failed":
        inc_fields["stats.failed"] = 1
    db.sequences.update_one({"_id": sequence_id}, {"$inc": inc_fields, "$set": {"updated_at": _utc_now_iso()}})


def process_due_sends(tracking_base_url: str = "", max_per_run: int = 50, sync_sleep: bool = True) -> Dict[str, Any]:
    """Process all due sequence sends with atomic claiming. Safe to call concurrently from multiple workers/crons."""
    init_sequence_db()
    db = get_db()
    now = _local_now()
    now_iso = now.isoformat(timespec="seconds")
    day = day_key_local(now)

    active_sequences = list(db.sequences.find({"status": "active"}))
    summary: Dict[str, Any] = {
        "processed": 0,
        "sent": 0,
        "skipped": 0,
        "failed": 0,
        "sequences": len(active_sequences),
    }
    skip_notes: List[str] = []

    user_sent_today: Dict[str, int] = {}
    user_last_send: Dict[str, datetime] = {}
    reply_checked_users: set = set()

    # Reclaim stale processing locks (e.g. crashed worker process > 10m ago)
    try:
        stale_cutoff = (_local_now() - timedelta(minutes=10)).isoformat(timespec="seconds")
        db.enrollments.update_many(
            {
                "status": "processing",
                "processing_started_at": {"$lte": stale_cutoff},
            },
            {"$set": {"status": "active"}},
        )
    except Exception:
        pass

    for seq in active_sequences:
        if summary["processed"] >= max_per_run:
            break

        user_id = seq["user_id"]
        sequence_id = seq["_id"]
        settings = seq["settings"]
        steps = seq["steps"]
        window = _parse_window(settings)

        if user_id not in user_sent_today:
            user_sent_today[user_id] = _count_user_sends_today(user_id, day)
        if user_sent_today[user_id] >= settings["daily_limit"]:
            skip_notes.append(f"{seq.get('name', sequence_id)}: daily limit reached")
            continue

        if settings.get("enable_reply_tracking") and user_id not in reply_checked_users:
            try:
                engine_cfg = _build_engine_config(user_id, seq, tracking_base_url)
                check_inbox_replies(engine_cfg)
            except Exception:
                pass
            reply_checked_users.add(user_id)

        if not window.contains(now):
            skip_notes.append(
                f"{seq.get('name', sequence_id)}: outside working hours "
                f"({settings.get('window_start', '09:00')}-{settings.get('window_end', '17:00')})"
            )
            continue

        try:
            engine = _build_engine_config(user_id, seq, tracking_base_url)
        except ValueError as exc:
            skip_notes.append(f"{seq.get('name', sequence_id)}: {exc}")
            continue

        delay_sec = int(settings.get("delay_sec", 60))

        while True:
            if summary["processed"] >= max_per_run:
                break
            if user_sent_today[user_id] >= settings["daily_limit"]:
                break

            # Re-check sequence still active (pause/delete race)
            live_seq = db.sequences.find_one({"_id": sequence_id}, {"status": 1})
            if not live_seq or live_seq.get("status") != "active":
                skip_notes.append(f"{seq.get('name', sequence_id)}: no longer active")
                break

            last = user_last_send.get(user_id)
            if last:
                elapsed = (_local_now() - last).total_seconds()
                if elapsed < delay_sec:
                    break

            # ATOMIC CLAIM: Find and lock one due enrollment
            enrollment = db.enrollments.find_one_and_update(
                {
                    "sequence_id": sequence_id,
                    "status": "active",
                    "next_send_at": {"$lte": now_iso},
                },
                {
                    "$set": {
                        "status": "processing",
                        "processing_started_at": _local_now().isoformat(timespec="seconds"),
                        "updated_at": _utc_now_iso(),
                    }
                },
                sort=[("next_send_at", 1)],
                return_document=ReturnDocument.AFTER,
            )
            if not enrollment:
                pending = db.enrollments.find_one(
                    {"sequence_id": sequence_id, "status": "active", "next_send_at": {"$gt": now_iso}},
                    sort=[("next_send_at", 1)],
                )
                if pending and pending.get("next_send_at"):
                    skip_notes.append(
                        f"{seq.get('name', sequence_id)}: next send at {pending['next_send_at']}"
                    )
                break

            summary["processed"] += 1
            enrollment_id = enrollment["_id"]
            recipient_id = enrollment["recipient_id"]
            step_index = int(enrollment["current_step"])

            if step_index >= len(steps):
                _stop_enrollment(enrollment_id, "completed", sequence_id)
                summary["skipped"] += 1
                continue

            rec = db.recipients.find_one({"_id": recipient_id})
            if rec and (rec.get("replied") or rec.get("do_not_contact")):
                _stop_enrollment(enrollment_id, "stopped_replied", sequence_id)
                summary["skipped"] += 1
                continue

            data_dict = enrollment.get("data") or (rec.get("data") if rec else {})
            if settings.get("consent_required") and not _is_truthy_consent(
                data_dict.get("consent"), ("true", "1", "yes", "y")
            ):
                _stop_enrollment(enrollment_id, "stopped_unsubscribed", sequence_id)
                summary["skipped"] += 1
                continue

            step = steps[step_index]
            subject = _render_step_subject(step, data_dict, enrollment_id, step_index)
            body = _render_template(step["body"], data_dict).strip()
            recipient_id_str = str(recipient_id)

            log_filter = {"enrollment_id": enrollment_id, "step_index": step_index}
            existing_log = db.sequence_send_log.find_one(log_filter)
            if existing_log and existing_log.get("status") == "sent":
                next_step = step_index + 1
                if next_step >= len(steps):
                    _stop_enrollment(enrollment_id, "completed", sequence_id)
                else:
                    next_at = _compute_next_send_after_step(settings, steps, next_step, now)
                    db.enrollments.update_one(
                        {"_id": enrollment_id},
                        {
                            "$set": {
                                "status": "active",
                                "current_step": next_step,
                                "next_send_at": next_at.isoformat(timespec="seconds") if next_at else None,
                                "updated_at": _utc_now_iso(),
                            }
                        },
                    )
                summary["skipped"] += 1
                continue

            # Upsert pending log first so we have a stable id for the tracking pixel
            pending_log = db.sequence_send_log.find_one_and_update(
                log_filter,
                {
                    "$set": {
                        "user_id": user_id,
                        "sequence_id": sequence_id,
                        "recipient_id": recipient_id,
                        "email": enrollment["email"],
                        "step_index": step_index,
                        "day_key": day,
                        "status": "pending",
                        "updated_at": _utc_now_iso(),
                    },
                    "$setOnInsert": {
                        "created_at": _utc_now_iso(),
                        "opened": 0,
                        "open_count": 0,
                        "clicked": 0,
                    },
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            send_log_id = str(pending_log["_id"])

            if tracking_base_url:
                body = rewrite_links(body, recipient_id_str, day, tracking_base_url)
                body += (
                    f'<img src="{tracking_base_url}/track/open/log/{send_log_id}" '
                    f'width="1" height="1" style="display:none;" alt="" />'
                )

            memory_atts = load_step_attachments_for_send(sequence_id, step)

            send_succeeded = False
            try:
                _smtp_send(
                    engine,
                    enrollment["email"],
                    subject,
                    body,
                    memory_attachments=memory_atts,
                )
                sent_at = _local_now().isoformat(timespec="seconds")
                db.sequence_send_log.update_one(
                    {"_id": pending_log["_id"]},
                    {
                        "$set": {
                            "status": "sent",
                            "sent_at": sent_at,
                            "error": None,
                            "day_key": day,
                            "updated_at": _utc_now_iso(),
                        }
                    },
                )
                try:
                    db.send_log.update_one(
                        {
                            "user_id": user_id,
                            "recipient_id": recipient_id,
                            "day_key": day,
                        },
                        {
                            "$set": {
                                "status": "sent",
                                "sent_at": sent_at,
                                "sequence_id": str(sequence_id),
                                "step_index": step_index,
                                "sequence_send_log_id": send_log_id,
                                "updated_at": _utc_now_iso(),
                            },
                            "$setOnInsert": {"attempt_no": 1, "created_at": _utc_now_iso()},
                        },
                        upsert=True,
                    )
                except Exception as log_exc:
                    pass

                user_sent_today[user_id] += 1
                user_last_send[user_id] = _local_now()
                summary["sent"] += 1
                send_succeeded = True
                _log_event(
                    "email.sent",
                    sequence_id=str(sequence_id),
                    enrollment_id=str(enrollment_id),
                    step_index=step_index,
                    send_log_id=send_log_id,
                )

                next_step = step_index + 1
                if next_step >= len(steps):
                    _stop_enrollment(enrollment_id, "completed", sequence_id)
                    db.enrollments.update_one(
                        {"_id": enrollment_id},
                        {
                            "$set": {
                                "current_step": next_step,
                                "last_sent_at": sent_at,
                                "next_send_at": None,
                                "updated_at": _utc_now_iso(),
                            }
                        },
                    )
                else:
                    next_at = _compute_next_send_after_step(settings, steps, next_step, _local_now())
                    db.enrollments.update_one(
                        {"_id": enrollment_id},
                        {
                            "$set": {
                                "status": "active",
                                "current_step": next_step,
                                "last_sent_at": sent_at,
                                "next_send_at": next_at.isoformat(timespec="seconds") if next_at else None,
                                "updated_at": _utc_now_iso(),
                            }
                        },
                    )

            except Exception as exc:
                transient = _is_transient_error(exc)
                attempt = (existing_log or pending_log or {}).get("attempt_no", 0) + 1
                db.sequence_send_log.update_one(
                    {"_id": pending_log["_id"]},
                    {
                        "$set": {
                            "status": "pending" if transient else "failed",
                            "error": str(exc)[:500],
                            "attempt_no": attempt,
                            "updated_at": _utc_now_iso(),
                        },
                    },
                )
                if not transient:
                    _stop_enrollment(enrollment_id, "stopped_failed", sequence_id)
                    summary["failed"] += 1
                    _log_event(
                        "email.failed",
                        sequence_id=str(sequence_id),
                        enrollment_id=str(enrollment_id),
                        step_index=step_index,
                    )
                else:
                    retry_at = _local_now() + timedelta(minutes=min(attempt * 2, 30))
                    db.enrollments.update_one(
                        {"_id": enrollment_id},
                        {
                            "$set": {
                                "status": "active",
                                "next_send_at": retry_at.isoformat(timespec="seconds"),
                                "updated_at": _utc_now_iso(),
                            }
                        },
                    )

            if sync_sleep and send_succeeded:
                # Sleep exactly the user-configured interval (capped for worker responsiveness)
                time.sleep(min(delay_sec, 120))

    if skip_notes and summary["sent"] == 0:
        summary["note"] = "; ".join(dict.fromkeys(skip_notes))
    return summary


def format_user_friendly_error(raw_error: Optional[str]) -> Optional[str]:
    if not raw_error:
        return None
    err = str(raw_error)
    if "E11000 duplicate key error" in err and "send_log" in err:
        return "Daily email limit reached for recipient (already sent an email to this contact today)"
    if "E11000 duplicate key error" in err:
        return "Duplicate record error (email already sent today)"
    if "535" in err or "Authentication" in err or "Username and Password not accepted" in err or "CREDENTIALS_MISSING" in err:
        return "Gmail login failed — please verify your Gmail Address and App Password in Settings"
    if "SMTPConnectError" in err or "Connection refused" in err or "timed out" in err:
        return "Gmail SMTP connection timed out or failed to connect"
    if "550" in err or "554" in err or "RecipientsFailed" in err:
        return "Email rejected by recipient server (invalid address or recipient inbox full)"
    clean = err.replace("full error: ", "").strip()
    return clean[:250]


def get_sequence_dashboard(user_id: str, sequence_id: Optional[str] = None) -> Dict[str, Any]:
    init_sequence_db()
    db = get_db()
    day = day_key_local()

    if sequence_id:
        seq_oid = ObjectId(sequence_id)
        seq = db.sequences.find_one({"_id": seq_oid, "user_id": user_id})
        if not seq:
            raise ValueError("Sequence not found.")
        sequences = [seq]
    else:
        sequences = list(db.sequences.find({"user_id": user_id, "status": {"$ne": "deleted"}}).sort("created_at", -1).limit(1))
        if not sequences:
            return {"sequence": None, "enrollments": [], "stats": {}, "logs": []}

    seq = sequences[0]
    seq_oid = seq["_id"]

    enrollments = list(
        db.enrollments.find({"sequence_id": seq_oid}).sort("enrolled_at", -1).limit(500)
    )

    sent_today = db.sequence_send_log.count_documents(
        {"sequence_id": seq_oid, "day_key": day, "status": "sent"}
    )
    opened = db.sequence_send_log.count_documents({"sequence_id": seq_oid, "opened": 1})
    clicked = db.sequence_send_log.count_documents({"sequence_id": seq_oid, "clicked": {"$gt": 0}})

    enrollment_rows = []
    for e in enrollments:
        step_idx = int(e.get("current_step", 0))
        total_steps = len(seq.get("steps", []))
        if e["status"] == "completed":
            step_label = f"Completed ({total_steps}/{total_steps})"
        elif e["status"] == "active":
            step_label = f"Step {step_idx + 1} of {total_steps}"
        else:
            step_label = e["status"].replace("_", " ").title()

        error_msg = None
        log_doc = db.sequence_send_log.find_one(
            {"enrollment_id": e["_id"]},
            sort=[("updated_at", -1)]
        )
        if log_doc and log_doc.get("error"):
            error_msg = format_user_friendly_error(log_doc.get("error"))

        enrollment_rows.append(
            {
                "email": e.get("email"),
                "status": e.get("status"),
                "current_step": step_idx,
                "total_steps": total_steps,
                "step_label": step_label,
                "next_send_at": e.get("next_send_at"),
                "last_sent_at": e.get("last_sent_at"),
                "error": error_msg,
            }
        )

    recent_logs = list(
        db.sequence_send_log.find({"sequence_id": seq_oid})
        .sort("sent_at", -1)
        .limit(50)
    )
    log_lines = []
    for log in reversed(recent_logs):
        if log.get("sent_at"):
            log_lines.append(
                f"[{log['sent_at']}] Step {log.get('step_index', 0) + 1} → {log.get('email')} ({log.get('status')})"
            )

    active = db.enrollments.count_documents({"sequence_id": seq_oid, "status": "active"})
    settings = seq.get("settings", {})
    window = _parse_window(settings)
    now = _local_now()
    in_window = window.contains(now)
    pending_next = db.enrollments.find_one(
        {"sequence_id": seq_oid, "status": "active", "next_send_at": {"$ne": None}},
        sort=[("next_send_at", 1)],
    )
    next_send_time = pending_next.get("next_send_at") if pending_next else None

    if not in_window:
        status_msg = f"Outside Working Hours ({settings.get('window_start', '09:00')} - {settings.get('window_end', '17:00')})."
        if next_send_time:
            status_msg += f" Next send scheduled at: {next_send_time}"
    elif next_send_time:
        status_msg = f"Worker Active (Working Hours: {settings.get('window_start', '09:00')} - {settings.get('window_end', '17:00')}). Next send scheduled at: {next_send_time}"
    elif active > 0:
        status_msg = f"Worker Active (Working Hours: {settings.get('window_start', '09:00')} - {settings.get('window_end', '17:00')}). Ready to process active contacts."
    else:
        status_msg = "No active contacts scheduled for send."

    if not log_lines and active:
        log_lines.append(status_msg)

    completed = db.enrollments.count_documents({"sequence_id": seq_oid, "status": "completed"})
    replied = db.enrollments.count_documents(
        {"sequence_id": seq_oid, "status": {"$in": ["stopped_replied", "stopped_unsubscribed"]}}
    )
    failed = db.enrollments.count_documents({"sequence_id": seq_oid, "status": "stopped_failed"})

    lists = {
        "active": [r for r in enrollment_rows if r["status"] == "active"],
        "completed": [r for r in enrollment_rows if r["status"] == "completed"],
        "replied": [r for r in enrollment_rows if r["status"] in ("stopped_replied", "stopped_unsubscribed")],
        "failed": [r for r in enrollment_rows if r["status"] == "stopped_failed"],
    }

    schedule_info = {
        "is_in_window": in_window,
        "window_start": settings.get("window_start", "09:00"),
        "window_end": settings.get("window_end", "17:00"),
        "next_send_at": next_send_time,
        "status_message": status_msg,
    }

    return {
        "sequence": _seq_to_dict(seq),
        "enrollments": enrollment_rows,
        "lists": lists,
        "schedule_info": schedule_info,
        "stats": {
            "sent_today": sent_today,
            "opened": opened,
            "clicked": clicked,
            "active": active,
            "completed": completed,
            "replied": replied,
            "failed": failed,
            "total_enrolled": len(enrollments),
            "enrolled": len(enrollments),
        },
        "logs": log_lines,
    }


def preview_sequence_steps(
    user_id: str, steps: List[Dict[str, Any]], csv_path: str, limit: int = 3
) -> List[Dict[str, str]]:
    norm_steps = _normalize_steps(steps)
    rows = _parse_recipients_file(csv_path)
    if not rows:
        return []
    previews = []
    for row in rows[:limit]:
        for step in norm_steps[:2]:
            fake_enrollment_id = str(row.get("email", "preview"))
            subject = _render_step_subject(step, row, fake_enrollment_id, step["step_index"])
            previews.append(
                {
                    "to": str(row.get("email", "")).strip(),
                    "step": step["step_index"] + 1,
                    "subject": subject,
                    "subject_variant": _pick_subject_variant(step, fake_enrollment_id, step["step_index"]),
                    "body": _render_template(step["body"], row).strip(),
                }
            )
    return previews


def _resolve_sequence(user_id: str, sequence_id: Optional[str] = None):
    db = get_db()
    if sequence_id:
        seq_oid = ObjectId(sequence_id)
        seq = db.sequences.find_one({"_id": seq_oid, "user_id": user_id})
        if not seq:
            raise ValueError("Sequence not found.")
        return seq, seq_oid
    sequences = list(db.sequences.find({"user_id": user_id, "status": {"$ne": "deleted"}}).sort("created_at", -1).limit(1))
    if not sequences:
        return None, None
    seq = sequences[0]
    return seq, seq["_id"]


def _contact_activity_for_sequence(seq_oid: ObjectId, enrollments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    db = get_db()
    recipient_ids = [e["recipient_id"] for e in enrollments]
    if not recipient_ids:
        return []

    send_logs = list(db.sequence_send_log.find({"sequence_id": seq_oid, "recipient_id": {"$in": recipient_ids}}))
    logs_by_recipient: Dict[Any, List[Dict[str, Any]]] = {}
    for log in send_logs:
        rid = log["recipient_id"]
        logs_by_recipient.setdefault(rid, []).append(log)

    recipients = {
        doc["_id"]: doc
        for doc in db.recipients.find({"_id": {"$in": recipient_ids}})
    }

    contacts = []
    for e in enrollments:
        rid = e["recipient_id"]
        rec = recipients.get(rid, {})
        logs = [l for l in logs_by_recipient.get(rid, []) if l.get("status") == "sent"]
        emails_sent = len(logs)
        opened = any(l.get("opened") for l in logs)
        clicked = sum(int(l.get("clicked") or 0) for l in logs)
        last_log = max(logs, key=lambda l: l.get("sent_at") or "", default=None) if logs else None
        replied = bool(
            rec.get("replied")
            or e.get("status") in ("stopped_replied", "stopped_unsubscribed")
        )

        if emails_sent == 0:
            engagement = "pending"
        elif replied:
            engagement = "replied"
        elif opened:
            engagement = "opened"
        else:
            engagement = "not_opened"

        contacts.append(
            {
                "email": e.get("email"),
                "engagement": engagement,
                "emails_sent": emails_sent,
                "opened": opened,
                "clicked": clicked,
                "replied": replied,
                "status": e.get("status"),
                "current_step": int(e.get("current_step", 0)),
                "last_sent_at": last_log.get("sent_at") if last_log else e.get("last_sent_at"),
                "last_step_sent": (last_log.get("step_index", 0) + 1) if last_log else 0,
                "next_send_at": e.get("next_send_at"),
            }
        )
    return contacts


def get_inbox_activity(user_id: str, sequence_id: Optional[str] = None) -> Dict[str, Any]:
    init_sequence_db()
    db = get_db()
    seq, seq_oid = _resolve_sequence(user_id, sequence_id)
    if not seq:
        return {"sequence": None, "contacts": [], "groups": {}}

    enrollments = list(db.enrollments.find({"sequence_id": seq_oid}).sort("enrolled_at", -1).limit(1000))
    contacts = _contact_activity_for_sequence(seq_oid, enrollments)

    groups = {
        "all_sent": [c for c in contacts if c["emails_sent"] > 0],
        "opened": [c for c in contacts if c["opened"]],
        "not_opened": [c for c in contacts if c["emails_sent"] > 0 and not c["opened"]],
        "replied": [c for c in contacts if c["replied"]],
        "no_response": [c for c in contacts if c["emails_sent"] > 0 and not c["replied"]],
        "pending": [c for c in contacts if c["emails_sent"] == 0],
    }

    return {
        "sequence": _seq_to_dict(seq),
        "contacts": contacts,
        "groups": {k: len(v) for k, v in groups.items()},
        "group_lists": groups,
    }


def get_analytics(user_id: str, sequence_id: Optional[str] = None) -> Dict[str, Any]:
    init_sequence_db()
    db = get_db()
    seq, seq_oid = _resolve_sequence(user_id, sequence_id)
    if not seq:
        return {"sequence": None, "summary": {}, "steps": [], "daily": []}

    enrollments = list(db.enrollments.find({"sequence_id": seq_oid}))
    contacts = _contact_activity_for_sequence(seq_oid, enrollments)
    send_logs = list(db.sequence_send_log.find({"sequence_id": seq_oid, "status": "sent"}))

    total_enrolled = len(enrollments)
    contacts_sent = len([c for c in contacts if c["emails_sent"] > 0])
    total_emails_sent = len(send_logs)
    opened_count = len([c for c in contacts if c["opened"]])
    clicked_count = len([c for c in contacts if c["clicked"] > 0])
    replied_count = len([c for c in contacts if c["replied"]])
    not_opened_count = len([c for c in contacts if c["emails_sent"] > 0 and not c["opened"]])

    def rate(n: int, d: int) -> float:
        return round((n / d * 100) if d else 0, 1)

    step_stats = []
    total_steps = len(seq.get("steps", []))
    for i in range(total_steps):
        step_logs = [l for l in send_logs if int(l.get("step_index", -1)) == i]
        step_sent = len(step_logs)
        step_opened = sum(1 for l in step_logs if l.get("opened"))
        step_clicked = sum(1 for l in step_logs if int(l.get("clicked") or 0) > 0)
        step_stats.append(
            {
                "step": i + 1,
                "sent": step_sent,
                "opened": step_opened,
                "clicked": step_clicked,
                "open_rate": rate(step_opened, step_sent),
                "click_rate": rate(step_clicked, step_sent),
            }
        )

    daily_map: Dict[str, int] = {}
    for log in send_logs:
        day = log.get("day_key") or (log.get("sent_at") or "")[:10]
        if day:
            daily_map[day] = daily_map.get(day, 0) + 1
    daily = [{"day": k, "sent": v} for k, v in sorted(daily_map.items())][-14:]

    return {
        "sequence": _seq_to_dict(seq),
        "summary": {
            "total_enrolled": total_enrolled,
            "contacts_sent": contacts_sent,
            "total_emails_sent": total_emails_sent,
            "opened": opened_count,
            "not_opened": not_opened_count,
            "clicked": clicked_count,
            "replied": replied_count,
            "open_rate": rate(opened_count, contacts_sent),
            "click_rate": rate(clicked_count, contacts_sent),
            "reply_rate": rate(replied_count, contacts_sent),
            "no_response": contacts_sent - replied_count,
        },
        "steps": step_stats,
        "daily": daily,
        "funnel": [
            {"label": "Enrolled", "count": total_enrolled},
            {"label": "Emailed", "count": contacts_sent},
            {"label": "Opened", "count": opened_count},
            {"label": "Clicked", "count": clicked_count},
            {"label": "Replied", "count": replied_count},
        ],
    }

"""Bundled sequence templates — shipped inside this project for deployment."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from bson.objectid import ObjectId

from auto_mailer_engine import get_db, init_db
from sequence_engine import _normalize_settings, _normalize_steps

MAIL_HEADER = re.compile(r"^## Mail (\d+) — Day (\d+)\s*$", re.MULTILINE)
SUBJECT_LINE = re.compile(r"^\*\*Subject:\*\*\s*(.+)$", re.MULTILINE)

# Bundled with the app — works on Render/server without external Apollo folder
SEQUENCES_DIR = Path(__file__).resolve().parent / "sequences"


def _apollo_to_template(text: str) -> str:
    return text.replace("{{", "{").replace("}}", "}")


def _text_to_html(text: str) -> str:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    parts = []
    for para in paragraphs:
        para = _apollo_to_template(para)
        parts.append(f"<p>{para.replace(chr(10), '<br>')}</p>")
    return "".join(parts)


def parse_apollo_markdown(content: str, slug: str = "") -> Dict[str, Any]:
    title_match = re.search(r"^#\s*Sequence:\s*(.+)$", content, re.MULTILINE)
    name = title_match.group(1).strip() if title_match else slug.replace("-", " ").title()

    delay_hint = ""
    delay_match = re.search(r"Delay:\s*\*\*([^*]+)\*\*", content)
    if delay_match:
        delay_hint = delay_match.group(1).strip()

    headers = MAIL_HEADER.findall(content)
    sections = MAIL_HEADER.split(content)
    steps: List[Dict[str, Any]] = []
    prev_day = 0

    i = 1
    while i + 2 < len(sections):
        mail_num = int(sections[i])
        day_num = int(sections[i + 1])
        body_block = sections[i + 2]
        i += 3

        delay_days = day_num - prev_day if mail_num > 1 else 0
        prev_day = day_num

        subject_match = SUBJECT_LINE.search(body_block)
        subject = _apollo_to_template(subject_match.group(1).strip()) if subject_match else ""

        body_text = body_block
        if subject_match:
            body_text = body_block[subject_match.end() :].strip()
        body_text = re.sub(r"\n---\s*$", "", body_text).strip()

        steps.append(
            {
                "subject": subject,
                "subject_variants": [subject],
                "body": _text_to_html(body_text),
                "delay_days": delay_days,
                "delay_hours": 0,
                "mail_number": mail_num,
                "schedule_day": day_num,
            }
        )

    return {
        "id": slug,
        "name": name,
        "delay_hint": delay_hint,
        "step_count": len(steps),
        "steps": steps,
        "settings": {
            "daily_limit": 100,
            "delay_sec": 60,
            "window_start": "09:00",
            "window_end": "17:00",
            "consent_required": False,
            "enable_reply_tracking": True,
        },
        "source": "bundled",
    }


def _load_bundled_template(template_id: str) -> Dict[str, Any]:
    path = SEQUENCES_DIR / f"{template_id}.md"
    if not path.is_file():
        raise ValueError(f"Template '{template_id}' not found.")
    content = path.read_text(encoding="utf-8")
    return parse_apollo_markdown(content, slug=template_id)


def _init_template_db() -> None:
    init_db()
    get_db().template_overrides.create_index([("user_id", 1), ("template_id", 1)], unique=True)


def list_apollo_templates(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    if not SEQUENCES_DIR.is_dir():
        return []

    overrides = {}
    if user_id:
        _init_template_db()
        for doc in get_db().template_overrides.find({"user_id": user_id}):
            overrides[doc["template_id"]] = doc

    templates = []
    for path in sorted(SEQUENCES_DIR.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        try:
            content = path.read_text(encoding="utf-8")
            parsed = parse_apollo_markdown(content, slug=path.stem)
            override = overrides.get(parsed["id"])
            templates.append(
                {
                    "id": parsed["id"],
                    "name": override.get("name", parsed["name"]) if override else parsed["name"],
                    "delay_hint": parsed.get("delay_hint", ""),
                    "step_count": len(override["steps"]) if override else parsed["step_count"],
                    "file": path.name,
                    "customized": bool(override),
                }
            )
        except Exception:
            continue
    return templates


def get_apollo_template(template_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    if user_id:
        _init_template_db()
        override = get_db().template_overrides.find_one({"user_id": user_id, "template_id": template_id})
        if override:
            return {
                "id": template_id,
                "name": override.get("name", template_id),
                "delay_hint": override.get("delay_hint", ""),
                "step_count": len(override.get("steps", [])),
                "steps": override["steps"],
                "settings": override.get("settings", {}),
                "source": "customized",
                "updated_at": override.get("updated_at"),
            }

    return _load_bundled_template(template_id)


def save_template_override(
    user_id: str,
    template_id: str,
    name: str,
    steps: List[Dict[str, Any]],
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _init_template_db()
    if not (SEQUENCES_DIR / f"{template_id}.md").is_file():
        raise ValueError(f"Template '{template_id}' not found.")

    norm_steps = _normalize_steps(steps)
    norm_settings = _normalize_settings(settings or {})
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    base = _load_bundled_template(template_id)
    doc = {
        "user_id": user_id,
        "template_id": template_id,
        "name": name.strip() or base["name"],
        "delay_hint": base.get("delay_hint", ""),
        "steps": norm_steps,
        "settings": norm_settings,
        "updated_at": now,
    }
    get_db().template_overrides.update_one(
        {"user_id": user_id, "template_id": template_id},
        {"$set": doc, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return get_apollo_template(template_id, user_id=user_id)


def reset_template_override(user_id: str, template_id: str) -> Dict[str, Any]:
    _init_template_db()
    get_db().template_overrides.delete_one({"user_id": user_id, "template_id": template_id})
    return _load_bundled_template(template_id)

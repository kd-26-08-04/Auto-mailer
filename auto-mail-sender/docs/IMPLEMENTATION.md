# Implementation Guide

Technical documentation for developers: architecture, file structure, APIs, database, and credential flow.

---

## Table of contents

1. [Architecture overview](#architecture-overview)
2. [Project structure](#project-structure)
3. [How sending works](#how-sending-works)
4. [Database collections](#database-collections)
5. [API reference](#api-reference)
6. [Credentials & keys flow](#credentials--keys-flow)
7. [Email tracking](#email-tracking)
8. [Reply detection](#reply-detection)
9. [Sequence templates](#sequence-templates)
10. [Extending the app](#extending-the-app)

---

## Architecture overview

```
┌─────────────┐     CSV upload      ┌──────────────┐
│   Browser   │ ──────────────────► │  Flask Web   │
│   (UI)      │ ◄── status/inbox ── │  web_app.py  │
└─────────────┘                     └──────┬───────┘
                                           │
                                    MongoDB (Atlas)
                                           ▲
┌─────────────┐     poll every 60s         │
│  sequence_  │ ─── process_due_sends ─────┘
│  worker.py  │
└──────┬──────┘
       │ SMTP (Gmail)
       ▼
   Recipients
```

| Component | File | Role |
|-----------|------|------|
| Web UI | `web_app.py` + `templates/index.html` | Login, sequences, inbox, analytics |
| Sequence engine | `sequence_engine.py` | CRUD, enrollment, scheduling, worker logic |
| Mail engine | `auto_mailer_engine.py` | SMTP, IMAP, tracking pixel, CSV parse |
| Templates | `apollo_templates.py` + `sequences/*.md` | Bundled niche templates |
| Worker | `sequence_worker.py` | Standalone cron process for production |
| Entry (prod) | `wsgi.py` | Gunicorn WSGI entry |

**Two processes in production:**
- **Web** — serves UI only (`ENABLE_INLINE_WORKER=0`)
- **Worker** — sends emails on schedule (must run 24/7)

---

## Project structure

```
auto-mail-sender/
├── web_app.py              # Flask routes, auth, API
├── wsgi.py                 # Gunicorn entry
├── auto_mailer_engine.py   # SMTP, IMAP, tracking, CSV
├── sequence_engine.py      # Sequences, enrollments, worker logic
├── sequence_worker.py      # Background worker (cron)
├── apollo_templates.py     # Template loader + user overrides
├── sequences/              # Bundled .md templates (10 niches)
│   ├── healthy-food.md
│   ├── cosmetics.md
│   └── ...
├── templates/
│   ├── index.html          # Main app UI
│   ├── login.html
│   └── register.html
├── static/styles.css
├── docs/
│   ├── DEPLOYMENT_AND_USAGE.md
│   └── IMPLEMENTATION.md
├── tests/
│   ├── test_sequences.py
│   └── test_extended.py
├── render.yaml             # Render deploy config
├── run_mailer.bat          # Windows local launcher
├── requirements.txt
└── .env.example
```

---

## How sending works

1. User **launches sequence** with CSV → contacts enrolled in MongoDB
2. Each enrollment gets `next_send_at`, `current_step`, `status: active`
3. **Worker** runs every 60s, calls `process_due_sends()`:
   - Finds active sequences
   - Finds enrollments where `next_send_at <= now`
   - Respects working hours, daily limit, delay between sends
   - Picks A/B subject variant (deterministic per contact)
   - Sends via Gmail SMTP
   - Advances to next step, sets new `next_send_at` (+ delay_days/hours)
   - Stops contact on reply or failure
4. UI polls dashboard/inbox/analytics — no browser needed for sending

---

## Database collections

| Collection | Purpose |
|------------|---------|
| `users` | Login, SMTP/IMAP credentials per user |
| `sequences` | Sequence definition (name, steps, settings, status) |
| `enrollments` | Contact ↔ sequence mapping, current step, next_send_at |
| `recipients` | Global contact records (email, CSV data, replied flag) |
| `sequence_send_log` | Per-step send log (sent, opened, clicked) |
| `template_overrides` | User-edited template versions |
| `send_log` | Legacy + tracking compatibility |
| `batch_recipients` | Legacy batch campaigns |

### Sequence step schema

```json
{
  "step_index": 0,
  "subject": "Hello {first_name}",
  "subject_variants": ["Hello {first_name}", "Quick note for {first_name}"],
  "body": "<p>Hi {first_name},</p>",
  "delay_days": 0,
  "delay_hours": 0
}
```

### Enrollment schema

```json
{
  "user_id": "...",
  "sequence_id": ObjectId,
  "recipient_id": ObjectId,
  "email": "john@acme.com",
  "current_step": 1,
  "status": "active",
  "next_send_at": "2026-08-31T10:00:00",
  "last_sent_at": "2026-08-24T10:00:00"
}
```

---

## API reference

All API routes require login (session cookie) except tracking pixels.

### Auth

| Method | Route | Description |
|--------|-------|-------------|
| GET/POST | `/register` | Create account |
| GET/POST | `/login` | Login |
| GET | `/logout` | Logout |

### Templates

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/templates` | List bundled templates (+ customized flag) |
| GET | `/api/templates/<id>` | Load template (user override if saved) |
| PUT | `/api/templates/<id>` | Save user template edits |
| POST | `/api/templates/<id>/reset` | Reset to bundled default |

### Sequences

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/sequences` | List sequences |
| POST | `/api/sequences` | Create sequence |
| GET | `/api/sequences/<id>` | Get sequence |
| PUT | `/api/sequences/<id>` | Update sequence |
| DELETE | `/api/sequences/<id>` | Delete sequence |
| POST | `/api/sequences/<id>/activate` | Launch (+ CSV upload) |
| POST | `/api/sequences/<id>/enroll` | Add more CSV contacts |
| POST | `/api/sequences/<id>/pause` | Pause |
| POST | `/api/sequences/<id>/resume` | Resume |
| GET | `/api/sequences/dashboard` | Dashboard data |
| GET | `/api/sequences/<id>/dashboard` | Dashboard for sequence |

### Inbox & Analytics

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/api/inbox` | Inbox activity (latest sequence) |
| GET | `/api/sequences/<id>/inbox` | Inbox for specific sequence |
| GET | `/api/analytics` | Analytics (latest sequence) |
| GET | `/api/sequences/<id>/analytics` | Analytics for specific sequence |

### Tracking (public, no auth)

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/track/open/<recipient_id>/<day_key>` | Open pixel |
| GET | `/track/click/<recipient_id>/<day_key>?url=...` | Click redirect |

### Settings

| Method | Route | Description |
|--------|-------|-------------|
| GET/POST | `/settings` | User profile + Gmail/IMAP credentials |
| POST | `/check-replies` | Manual IMAP reply scan |

---

## Credentials & keys flow

### Environment variables (server-level)

Set once per deployment in `.env` or Render dashboard:

```
MONGO_URI          → MongoDB Atlas (see DEPLOYMENT guide)
FLASK_SECRET_KEY   → python -c "import secrets; print(secrets.token_hex(32))"
TRACKING_BASE_URL  → https://your-app.onrender.com
```

**Where to get MongoDB URI:**
1. [MongoDB Atlas](https://www.mongodb.com/atlas) → Cluster → Connect → Driver
2. Format: `mongodb+srv://USER:PASS@cluster.mongodb.net/outreach_db`

### User credentials (app Settings tab)

Stored in MongoDB `users` collection — **not** in environment variables:

| Field | Source | Used for |
|-------|--------|----------|
| `smtp_email` | User enters in Settings | From address |
| `smtp_app_password` | Gmail App Password | SMTP send |
| `imap_host` | Default `imap.gmail.com` | Reply scan |
| `imap_username` | Same as Gmail | Reply scan |
| `imap_password` | Same App Password | Reply scan |

**Where to get Gmail App Password:**
1. [Google Account Security](https://myaccount.google.com/security)
2. Enable 2-Step Verification
3. [App Passwords](https://myaccount.google.com/apppasswords) → Create → copy 16 chars

### What is NOT needed

- No Stripe, SendGrid, or AWS keys
- No Apollo.io API key
- No OAuth setup (uses App Password, not Google OAuth)

---

## Email tracking

### Open tracking

- 1×1 tracking pixel appended to HTML body
- URL: `{TRACKING_BASE_URL}/track/open/{recipient_id}/{day_key}`
- Updates `sequence_send_log.opened = 1`

### Click tracking

- Links rewritten to: `{TRACKING_BASE_URL}/track/click/{recipient_id}/{day_key}?url=...`
- Increments `sequence_send_log.clicked`

### Requirements

- `TRACKING_BASE_URL` must be publicly reachable
- Emails must be HTML (Quill editor produces HTML)

---

## Reply detection

Function: `check_inbox_replies()` in `auto_mailer_engine.py`

1. Connects to Gmail IMAP (readonly)
2. Scans last 150 inbox messages (last 14 days)
3. **Only matches senders in `recipients` collection** (CSV uploads)
4. Marks `recipients.replied = true`, stops sequence for that contact

Triggered by:
- **Scan Replies** button (manual)
- Worker automatically (every worker cycle if enabled)
- Not a full inbox UI — only outreach contacts

---

## Sequence templates

### Bundled files

Location: `sequences/*.md`

Parsed by `apollo_templates.py`:
- `{{first_name}}` → `{first_name}` (app template format)
- Plain text → HTML paragraphs
- Day 0/7/14/21/28 → `delay_days` between steps

### User overrides

Saved to MongoDB `template_overrides` (per user, per template_id).

Load order:
1. User override in MongoDB (if exists)
2. Bundled `.md` file

---

## Extending the app

### Add a new template

1. Create `sequences/my-niche.md` following existing format:
   ```markdown
   # Sequence: My Niche
   Delay: **Day 0 / 7 / 14 / 21 / 28**
   ---
   ## Mail 1 — Day 0
   **Subject:** Hello {{first_name}}
   Hi {{first_name}}, ...
   ```
2. Restart app — appears in template dropdown automatically

### Add a new sequence step field

1. Update `_normalize_steps()` in `sequence_engine.py`
2. Update UI in `templates/index.html` step builder
3. Update `process_due_sends()` if needed at send time

### Change worker frequency

```env
WORKER_INTERVAL=30   # check every 30 seconds
WORKER_MAX_SENDS=100 # send up to 100 per cycle
```

### Run worker as cron (alternative to always-on process)

```bash
# Render cron job — every minute
python sequence_worker.py --once
```

---

## Security notes

- Passwords hashed with bcrypt (`users.password_hash`)
- SMTP passwords stored plaintext in MongoDB — consider encryption for production
- Session protected by `FLASK_SECRET_KEY`
- Tracking routes are public (required for pixel/links) — no PII exposed in URLs
- IMAP runs readonly
- Never commit `.env` or credentials to git

---

## Related docs

- [DEPLOYMENT_AND_USAGE.md](./DEPLOYMENT_AND_USAGE.md) — deploy + user guide + where to get keys (non-technical)

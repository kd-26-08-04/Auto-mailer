# Deployment & Usage Guide

Complete guide to deploy **EISTATECH Auto Mailer** and run Apollo-style email sequences.

---

## Table of contents

1. [What this app does](#what-this-app-does)
2. [Prerequisites](#prerequisites)
3. [Where to get keys & credentials](#where-to-get-keys--credentials)
4. [Local setup (Windows)](#local-setup-windows)
5. [Deploy to Render (production)](#deploy-to-render-production)
6. [Environment variables](#environment-variables)
7. [How to use the app](#how-to-use-the-app)
8. [CSV format](#csv-format)
9. [Sequence templates](#sequence-templates)
10. [Troubleshooting](#troubleshooting)
11. [Running tests](#running-tests)

---

## What this app does

- **Multi-step email sequences** (like Apollo.io) — Mail 1 → wait 7 days → Mail 2 → …
- **CSV contact import** — upload contacts, no manual entry
- **Background worker** — sends continue even if you close the browser
- **Open / click / reply tracking** — only for contacts you emailed (not your whole Gmail inbox)
- **Inbox & Analytics** — see who opened, replied, or never opened
- **10 bundled templates** — Healthy Food, Cosmetics, Agency Owners, etc.

---

## Prerequisites

| Requirement | Notes |
|-------------|--------|
| Python 3.10+ | Tested on 3.10 |
| MongoDB | Local install or [MongoDB Atlas](https://www.mongodb.com/atlas) (free tier works) |
| Gmail account | With 2-Step Verification enabled |
| Gmail App Password | For SMTP send + IMAP reply scan |
| Render account | Optional, for cloud deployment |

---

## Where to get keys & credentials

### 1. MongoDB (`MONGO_URI`)

**Option A — MongoDB Atlas (recommended for production)**

1. Go to [https://www.mongodb.com/atlas](https://www.mongodb.com/atlas)
2. Create a free account → **Create cluster** (M0 free tier)
3. **Database Access** → Add user (username + password)
4. **Network Access** → Add IP `0.0.0.0/0` (allow from anywhere, needed for Render)
5. **Connect** → **Drivers** → copy connection string:
   ```
   mongodb+srv://USERNAME:PASSWORD@cluster0.xxxxx.mongodb.net/outreach_db
   ```
6. Replace `<password>` with your actual password

**Option B — Local MongoDB**

```
MONGO_URI=mongodb://localhost:27017
```

Install: [https://www.mongodb.com/try/download/community](https://www.mongodb.com/try/download/community)

---

### 2. Gmail App Password (SMTP + IMAP)

Used in the app **Settings** tab (stored per user in MongoDB).

1. Enable **2-Step Verification** on your Google Account:  
   [https://myaccount.google.com/security](https://myaccount.google.com/security)
2. Go to **App passwords**:  
   [https://myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Create app name: `Auto Mailer`
4. Copy the **16-character password** (e.g. `abcd efgh ijkl mnop`)
5. In the app → **Settings** → paste:
   - **Sender Gmail**: your email (e.g. `colabwithreshmi@gmail.com`)
   - **App Password**: the 16-char password

> Gmail App Password is **not** an environment variable — each user saves it in Settings after login.

---

### 3. Flask secret key (`FLASK_SECRET_KEY`)

Used to encrypt login sessions.

**Generate locally:**

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Copy output into `.env` or Render environment variables.

---

### 4. Tracking URL (`TRACKING_BASE_URL`)

Required for **open** and **click** tracking pixels/links.

| Environment | Value |
|-------------|--------|
| Local | `http://127.0.0.1:5001` |
| Render | `https://your-web-service-name.onrender.com` |

Must be the **public URL** where your web app is reachable (no trailing slash).

---

## Local setup (Windows)

### Step 1 — Clone and install

```powershell
cd C:\projects\Auto-mailer\auto-mail-sender
python -m pip install -r requirements.txt
```

### Step 2 — Configure environment

```powershell
copy .env.example .env
# Edit .env with your MONGO_URI, FLASK_SECRET_KEY, TRACKING_BASE_URL
```

### Step 3 — Start the app (easiest)

Double-click **`run_mailer.bat`** or run manually:

**Terminal 1 — Background worker (required for sending):**
```powershell
cd C:\projects\Auto-mailer\auto-mail-sender
python sequence_worker.py
```

**Terminal 2 — Web app:**
```powershell
cd C:\projects\Auto-mailer\auto-mail-sender
set ENABLE_INLINE_WORKER=0
python web_app.py
```

Open: [http://127.0.0.1:5001](http://127.0.0.1:5001)

> Keep the **worker terminal open**. The web UI can be closed after launching a sequence.

---

## Deploy to Render (production)

The project includes `render.yaml` for one-click deploy with **two services**:

| Service | Purpose |
|---------|---------|
| `auto-mail-sender` | Web UI (Gunicorn) |
| `sequence-worker` | Background email sender (runs 24/7) |

### Step 1 — Push to GitHub

Ensure your repo contains the `auto-mail-sender` folder with all files including `sequences/`.

### Step 2 — Create Render account

[https://render.com](https://render.com) → Sign up → connect GitHub.

### Step 3 — New Blueprint

1. **New** → **Blueprint**
2. Connect repo
3. Render reads `render.yaml` and creates both services

### Step 4 — Set environment variables

In Render dashboard, for **both** web and worker services:

| Variable | Value |
|----------|--------|
| `MONGO_URI` | Your MongoDB Atlas connection string |
| `TRACKING_BASE_URL` | `https://your-web-app.onrender.com` |
| `FLASK_SECRET_KEY` | Random 64-char hex string (web service only) |

Already set in `render.yaml`:
- `ENABLE_INLINE_WORKER=0` (web uses separate worker)
- `WORKER_INTERVAL=60` (worker checks every 60 seconds)

### Step 5 — Verify

1. Open your Render web URL
2. Register → Login → Settings → save Gmail credentials
3. Create sequence → upload CSV → Launch
4. Check worker logs in Render dashboard for send activity

### Render notes

- Web service may spin down on free tier after inactivity — **worker must stay running** for sends
- Filesystem is ephemeral — all data persists in MongoDB only
- Bind is handled by Gunicorn: `0.0.0.0:$PORT`

---

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MONGO_URI` | Yes | `mongodb://localhost:27017` | MongoDB connection string |
| `FLASK_SECRET_KEY` | Production | random | Session encryption |
| `TRACKING_BASE_URL` | Recommended | empty | Public app URL for tracking |
| `ENABLE_INLINE_WORKER` | No | `1` | Set `0` when using separate worker |
| `WORKER_INTERVAL` | No | `60` | Seconds between worker runs |
| `WORKER_MAX_SENDS` | No | `50` | Max emails per worker cycle |

---

## How to use the app

### First-time setup

1. **Register** at `/register` (full name, phone, username, password)
2. **Login** at `/login`
3. **Settings** → save Gmail + App Password
4. Ready to send

### Create and launch a sequence

1. **Sequences** → **New Sequence**
2. **Choose template** (e.g. Healthy Food) or build custom steps
3. Edit email subjects, bodies, delays (days + hours)
4. Set **working hours** (e.g. 09:00–17:00)
5. **Upload CSV** with contacts
6. **Launch Sequence**

The background worker handles all sends automatically.

### Dashboard

- Live stats: Sent, Opened, Clicked, Replied
- Tabs: In Progress, Completed, Replied, Failed
- Activity log

### Inbox

Shows activity for **contacts you emailed only**:

| Tab | Meaning |
|-----|---------|
| Opened | Opened at least one email |
| Not Opened | Sent but never opened |
| Replied | Replied to your outreach |
| No Response | Sent, no reply yet |
| Not Sent Yet | Enrolled, waiting |

Click **Scan Replies** to check Gmail for replies from CSV contacts only.

### Analytics

- Open rate, click rate, reply rate
- Funnel: Enrolled → Emailed → Opened → Clicked → Replied
- Sends per day (last 14 days)
- Performance by sequence step

### Pause / resume

Open sequence → **Pause** or **Resume** (worker respects paused status).

### Edit templates

Load template → edit steps → **Save Template** (stored in MongoDB per user).

---

## CSV format

**Required column:**

```csv
email
john@company.com
```

**Recommended columns** (used in email templates):

```csv
email,first_name,company,sender_name,consent
john@acme.com,John,Acme Corp,Reshmi,true
```

| Column | Used for |
|--------|----------|
| `email` | Required — recipient address |
| `first_name` | `{first_name}` in templates |
| `company` | `{company}` in templates |
| `consent` | Must be `true`/`yes`/`1` if consent checkbox enabled |

Supports: `.csv`, `.xlsx`, `.xls`

Example file: `example_recipients.csv`

---

## Sequence templates

Bundled in `sequences/` folder (ships with deploy):

- agency-owners, cosmetics, cookware, healthy-food, healthy-snack
- protein-atta, protein-powder, red-light-mask, supplements, tea

Default schedule: **Day 0 → 7 → 14 → 21 → 28** (7 days between each email).

Variables in templates: `{first_name}`, `{company}` (from CSV).

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Emails not sending | Is `sequence_worker.py` running? Check worker logs |
| "SMTP credentials not configured" | Settings → save Gmail + App Password |
| Opens/clicks not tracked | Set `TRACKING_BASE_URL` to public URL |
| Replies not detected | Settings → Gmail saved; click **Scan Replies** |
| MongoDB connection error | Check `MONGO_URI`; Atlas IP whitelist `0.0.0.0/0` |
| Template dropdown empty | Ensure `sequences/` folder exists in deploy |
| Worker sends duplicates | Only run ONE worker (don't use inline worker + separate worker) |

---

## Running tests

```powershell
cd C:\projects\Auto-mailer\auto-mail-sender
python -m pip install -r requirements.txt
python tests/test_sequences.py      # 14 tests — core + API
python tests/test_extended.py       # 5 tests — inbox, analytics, templates
node ui-test.mjs                    # Browser UI smoke test
```

All tests require MongoDB running (local or Atlas).

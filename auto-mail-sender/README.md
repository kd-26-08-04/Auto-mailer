# EISTATECH Auto Mailer - Decoupled Architecture

This repository is split into two independent services:

## 📂 Repository Structure

- `frontend/` - React SPA (Vite) for the User Dashboard & Outreach Management UI.
- `backend/` - Python Flask REST API, MongoDB Integration, and Background Email Sequence Worker.

---

## 🌐 Hosting & Deployment Instructions

### 1. Frontend Hosting (Vercel / Netlify / Cloudflare Pages)
- **Root Directory**: `frontend`
- **Build Command**: `npm run build`
- **Output Directory**: `dist`
- **Environment Variable**: Set `VITE_API_BASE_URL` to your live Backend URL (e.g. `https://your-backend.onrender.com`).

### 2. Backend Hosting (Render / Railway / AWS / VPS)
- **Root Directory**: `backend`
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `gunicorn main:app`
- **Background Worker**: `python sequence_worker.py`
- **Environment Variables**:
  - `MONGO_URI`: MongoDB Connection String
  - `FRONTEND_URL`: URL of your deployed React Frontend (e.g. `https://your-app.vercel.app`) for CORS.
  - `FLASK_SECRET_KEY`: Secret key for session encryption.
  - `TRACKING_BASE_URL`: Live URL for tracking email opens/clicks.
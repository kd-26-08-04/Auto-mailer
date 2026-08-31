#!/usr/bin/env python3
"""Standalone background worker for Apollo-style email sequences.

Run continuously (local/production):
    python sequence_worker.py

Or as a one-shot cron job (Render cron every minute):
    python sequence_worker.py --once

Environment:
    MONGO_URI          - MongoDB connection string
    TRACKING_BASE_URL  - Public app URL for open/click tracking (e.g. https://your-app.onrender.com)
    WORKER_INTERVAL    - Seconds between runs (default: 60)
    WORKER_MAX_SENDS   - Max sends per run (default: 50)
"""

import argparse
import os
import sys
import time
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from sequence_engine import init_sequence_db, process_due_sends


def run_once(tracking_base_url: str, max_sends: int) -> dict:
    init_sequence_db()
    result = process_due_sends(tracking_base_url=tracking_base_url, max_per_run=max_sends)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] Worker: {result}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Sequence email worker")
    parser.add_argument("--once", action="store_true", help="Run once and exit (for cron)")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("WORKER_INTERVAL", "60")))
    parser.add_argument("--max-sends", type=int, default=int(os.environ.get("WORKER_MAX_SENDS", "50")))
    args = parser.parse_args()

    tracking_base_url = os.environ.get("TRACKING_BASE_URL", "").rstrip("/")
    if not tracking_base_url:
        print("Warning: TRACKING_BASE_URL not set — open/click tracking disabled.", file=sys.stderr)

    if args.once:
        run_once(tracking_base_url, args.max_sends)
        return

    print(f"Sequence worker started (interval={args.interval}s, max_sends={args.max_sends})")
    while True:
        try:
            run_once(tracking_base_url, args.max_sends)
        except Exception as exc:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{stamp}] Worker error: {exc}", file=sys.stderr)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

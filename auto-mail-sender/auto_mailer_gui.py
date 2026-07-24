import threading
import tkinter as tk
import os
from tkinter import filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from tkinter.ttk import Checkbutton, Entry, Frame, Label, Button
from datetime import datetime

from auto_mailer_engine import DailyWindow, EngineConfig, load_preview, run_outreach


DEFAULT_DB_PATH = "outreach.db"


def _parse_hhmm(value: str):
    value = value.strip()
    hh, mm = value.split(":")
    hh_i = int(hh)
    mm_i = int(mm)
    if not (0 <= hh_i <= 23 and 0 <= mm_i <= 59):
        raise ValueError("Time must be in HH:MM (24h).")
    return hh_i, mm_i


class AutoMailerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Auto Mailer (Gmail SMTP, rate-limited)")

        self.stop_event = threading.Event()
        self.worker_thread = None

        self.csv_path_var = tk.StringVar()
        self.from_email_var = tk.StringVar()
        self.app_pass_var = tk.StringVar()
        self.batch_id_var = tk.StringVar(value=f"batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        self.subject_var = tk.StringVar(value="Hello {first_name} — quick question")
        self.db_path_var = tk.StringVar(value=DEFAULT_DB_PATH)

        self.daily_limit_var = tk.StringVar(value="100")
        self.delay_sec_var = tk.StringVar(value="60")
        self.window_start_var = tk.StringVar(value="09:00")
        self.window_end_var = tk.StringVar(value="17:00")

        self.smtp_host_var = tk.StringVar(value="smtp.gmail.com")
        self.smtp_port_var = tk.StringVar(value="587")

        self.consent_required_var = tk.BooleanVar(value=True)
        self.log_text = None

        self._build_ui()

    def _build_ui(self):
        # ttk.Frame uses "padding" instead of tkinter's padx/pady constructor args.
        frm = Frame(self.root, padding=10)
        frm.pack(fill="both", expand=True)

        row = 0
        Label(frm, text="Gmail From Email").grid(row=row, column=0, sticky="w")
        Entry(frm, textvariable=self.from_email_var, width=45).grid(row=row, column=1, sticky="we", padx=5)
        row += 1

        Label(frm, text="Gmail App Password").grid(row=row, column=0, sticky="w")
        Entry(frm, textvariable=self.app_pass_var, show="*", width=45).grid(row=row, column=1, sticky="we", padx=5)
        row += 1

        Label(frm, text="SMTP Host").grid(row=row, column=0, sticky="w")
        Entry(frm, textvariable=self.smtp_host_var, width=20).grid(row=row, column=1, sticky="w", padx=5)
        Label(frm, text="Port").grid(row=row, column=1, sticky="w", padx=(200, 0))
        Entry(frm, textvariable=self.smtp_port_var, width=10).grid(row=row, column=1, sticky="w", padx=(240, 0))
        row += 1

        Label(frm, text="Batch Name (for resume)").grid(row=row, column=0, sticky="w")
        Entry(frm, textvariable=self.batch_id_var, width=45).grid(row=row, column=1, sticky="we", padx=5)
        row += 1

        Label(frm, text="Recipients CSV (must include `email` + consent column if enabled)").grid(row=row, column=0, sticky="w")
        Entry(frm, textvariable=self.csv_path_var, width=45).grid(row=row, column=1, sticky="we", padx=5)
        Button(frm, text="Browse", command=self._pick_csv).grid(row=row, column=2, sticky="e")
        row += 1

        cb = Checkbutton(frm, text="Require consent=true in CSV", variable=self.consent_required_var)
        cb.grid(row=row, column=1, sticky="w")
        row += 1

        Label(frm, text="Subject (template with {field})").grid(row=row, column=0, sticky="w")
        Entry(frm, textvariable=self.subject_var, width=45).grid(row=row, column=1, sticky="we", padx=5)
        row += 1

        Label(frm, text="Body (template with {field})").grid(row=row, column=0, sticky="nw", pady=(10, 0))
        self.body_text = ScrolledText(frm, height=8, width=55)
        self.body_text.grid(row=row, column=1, columnspan=2, sticky="we", padx=5, pady=(10, 0))
        self.body_text.insert("1.0", "Hi {first_name},\n\nI’m reaching out because I think {company} might be a great fit for what we’re building. Would you be open to a 10-minute chat this week?\n\nThanks,\n{sender_name}")
        row += 1

        Label(frm, text="Daily Limit (mails/day)").grid(row=row, column=0, sticky="w")
        Entry(frm, textvariable=self.daily_limit_var, width=10).grid(row=row, column=1, sticky="w", padx=5)
        Label(frm, text="Delay (seconds)").grid(row=row, column=1, sticky="w", padx=(170, 0))
        Entry(frm, textvariable=self.delay_sec_var, width=10).grid(row=row, column=1, sticky="w", padx=(350, 0))
        row += 1

        Label(frm, text="Send Window Start (HH:MM 24h)").grid(row=row, column=0, sticky="w")
        Entry(frm, textvariable=self.window_start_var, width=10).grid(row=row, column=1, sticky="w", padx=5)
        Label(frm, text="End").grid(row=row, column=1, sticky="w", padx=(170, 0))
        Entry(frm, textvariable=self.window_end_var, width=10).grid(row=row, column=1, sticky="w", padx=(215, 0))
        row += 1

        Label(frm, text="SQLite DB Path").grid(row=row, column=0, sticky="w")
        Entry(frm, textvariable=self.db_path_var, width=20).grid(row=row, column=1, sticky="w", padx=5)
        row += 1

        btn_frame = Frame(frm)
        btn_frame.grid(row=row, column=0, columnspan=3, sticky="we", pady=(10, 0))

        Button(btn_frame, text="Preview (first 3)", command=self._on_preview).pack(side="left", padx=(0, 10))
        # Use classic tk.Button for color options (ttk.Button doesn't support bg/fg kwargs).
        tk.Button(btn_frame, text="Start", command=self._on_start, bg="#1f7a1f", fg="white").pack(side="left", padx=(0, 10))
        tk.Button(btn_frame, text="Stop", command=self._on_stop, bg="#7a1f1f", fg="white").pack(side="left")
        row += 1

        self.log_text = ScrolledText(frm, height=12, width=85, state="disabled")
        self.log_text.grid(row=row, column=0, columnspan=3, sticky="we", pady=(10, 0))

    def _pick_csv(self):
        path = filedialog.askopenfilename(
            title="Select recipients CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if path:
            self.csv_path_var.set(path)

    def _log(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _validate_inputs(self) -> EngineConfig:
        csv_path = self.csv_path_var.get().strip()
        if not csv_path:
            raise ValueError("Please select a recipients CSV.")
        from_email = self.from_email_var.get().strip()
        if not from_email:
            raise ValueError("Please enter From email.")
        app_pass = self.app_pass_var.get().strip()
        if not app_pass:
            raise ValueError("Please enter Gmail App Password.")

        daily_limit = int(self.daily_limit_var.get().strip())
        delay_sec = int(self.delay_sec_var.get().strip())
        if daily_limit <= 0 or delay_sec <= 0:
            raise ValueError("Daily limit and delay must be positive integers.")

        sh, sm = _parse_hhmm(self.window_start_var.get())
        eh, em = _parse_hhmm(self.window_end_var.get())
        window = DailyWindow(start=datetime(2000, 1, 1, sh, sm).time(), end=datetime(2000, 1, 1, eh, em).time())

        smtp_port = int(self.smtp_port_var.get().strip())

        subject_template = self.subject_var.get()
        body_template = self.body_text.get("1.0", "end").strip()
        if not body_template:
            raise ValueError("Body template is empty.")

        return EngineConfig(
            db_path=self.db_path_var.get().strip(),
            batch_id=self.batch_id_var.get().strip(),
            from_email=from_email,
            smtp_host=self.smtp_host_var.get().strip(),
            smtp_port=smtp_port,
            smtp_app_password=app_pass,
            subject_template=subject_template,
            body_template=body_template,
            daily_limit=daily_limit,
            delay_sec=delay_sec,
            window=window,
            consent_required=self.consent_required_var.get(),
        )

    def _on_preview(self):
        try:
            engine = self._validate_inputs()
            self._log("Previewing first recipients...")
            preview = load_preview(engine, self.csv_path_var.get().strip(), limit=3)
            if not preview:
                self._log("No preview recipients matched (consent filter might be excluding them).")
                return
            for i, item in enumerate(preview, 1):
                self._log(f"--- Preview {i} ---")
                self._log(f"To: {item['to']}")
                self._log(f"Subject: {item['subject']}")
                self._log(f"Body:\n{item['body']}\n")
        except Exception as e:
            messagebox.showerror("Preview error", str(e))

    def _on_start(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showinfo("Already running", "Mailer is already running.")
            return

        self.stop_event.clear()
        try:
            engine = self._validate_inputs()
        except Exception as e:
            messagebox.showerror("Input error", str(e))
            return

        recipients_csv_path = self.csv_path_var.get().strip()
        self._log(f"Starting batch '{engine.batch_id}'...")

        def worker():
            try:
                result = run_outreach(engine, recipients_csv_path, stop_flag=self.stop_event)
                self._log(f"Finished. Sent: {result.get('total_sent')} Failed: {result.get('total_failed')} Skipped: {result.get('total_skipped')}")
            except Exception as e:
                self._log(f"Fatal error: {e}")
            finally:
                # Ensure the stop flag is not interpreted as "keep running".
                pass

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _on_stop(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self._log("Stopping...")
            self.stop_event.set()


def main():
    # Silences macOS system Tk deprecation warning in terminal output.
    os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")
    root = tk.Tk()
    app = AutoMailerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()


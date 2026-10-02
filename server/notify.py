"""Email when a job finishes (optional): the summary per run, links, plan and spreadsheet attached.

Configured only by environment variables, never in code:
  ROOMSCAN_SMTP_USER      the sending account, e.g. you@gmail.com
  ROOMSCAN_SMTP_PASSWORD  for Gmail an App Password (https://myaccount.google.com/apppasswords),
                          not the account password
  ROOMSCAN_SMTP_HOST      default smtp.gmail.com
  ROOMSCAN_SMTP_PORT      default 587 (STARTTLS)
  ROOMSCAN_SMTP_FROM      default ROOMSCAN_SMTP_USER
  ROOMSCAN_WEB_URL        the site, for the "open the results" link (default http://localhost:5173)
  ROOMSCAN_API_URL        this server as the recipient can reach it, for download links
                          (default http://localhost:8000)
Without user and password, email is off and the web app hides the email box.
"""
from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import threading
import traceback
from email.message import EmailMessage
from pathlib import Path

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}$")
MAX_ATTACH = 8 * 1024 * 1024  # bytes per message, below Gmail's 25 MB


def enabled() -> bool:
    return bool(os.environ.get("ROOMSCAN_SMTP_USER") and os.environ.get("ROOMSCAN_SMTP_PASSWORD"))


def valid_email(addr: str | None) -> bool:
    return bool(addr) and bool(EMAIL_RE.match(addr.strip()))


def _summary(j: dict, out_dir: Path, api: str, web: str) -> tuple[str, str, list[tuple[str, bytes, str]]]:
    ok = j["status"] == "done"
    lines, attach, size = [], [], 0
    n_rooms = 0
    for r in j.get("runs", []):
        title = r.get("title") or r.get("label")
        if r.get("status") != "done":
            lines.append(f"- {title}: failed ({(r.get('error') or 'error')[:200]})")
            continue
        n_rooms += int(r.get("n_rooms") or 0)
        odir = out_dir / r.get("prefix", "") if r.get("prefix") else out_dir
        fp = ""
        try:
            res = json.loads((odir / "result.json").read_text(encoding="utf-8"))
            f = res["property"]["footprint_area"]
            fp = f", footprint {f['value']:.1f} m2 (90 % range {f['ci90'][0]:.1f}-{f['ci90'][1]:.1f})"
        except Exception:  # noqa: BLE001 - the email still goes out without it
            pass
        lines.append(f"- {title}: {r.get('n_rooms', 0)} room(s){fp}")
        for k, fn in (("plan_png", "plan.png"), ("result_xlsx", "result.xlsx")):
            url = (r.get("outputs") or {}).get(k)
            if url:
                lines.append(f"    {fn}: {api.rstrip('/')}{url}")
            p = odir / fn
            if p.is_file() and size + p.stat().st_size <= MAX_ATTACH:
                data = p.read_bytes()
                size += len(data)
                name = f"{(r.get('label') or 'run')}_{fn}"
                attach.append((name, data, "image/png" if fn.endswith(".png") else
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
    subject = (f"roomscan: your floor plan is ready ({n_rooms} room(s))" if ok
               else "roomscan: processing failed")
    body = "\n".join([
        "Your roomscan job has finished." if ok else "Your roomscan job could not finish.",
        "", *lines, "",
        f"Open the results: {web.rstrip('/')}/  (job {j['job_id']})",
        *([f"Problems: {j['error']}"] if j.get("error") else []),
        "", "The plan and the spreadsheet are attached where they fit. Links work while the server keeps the "
        "results (a few days).",
    ])
    return subject, body, attach


def send_job_email(j: dict, to: str, out_dir: Path) -> None:
    """Send the job's email now (blocking). Raises on SMTP errors."""
    api = os.environ.get("ROOMSCAN_API_URL", "http://localhost:8000")
    web = os.environ.get("ROOMSCAN_WEB_URL", "http://localhost:5173")
    subject, body, attach = _summary(j, Path(out_dir), api, web)
    user = os.environ["ROOMSCAN_SMTP_USER"]
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ.get("ROOMSCAN_SMTP_FROM", user), to
    msg.set_content(body)
    for name, data, ctype in attach:
        main, sub = ctype.split("/", 1)
        msg.add_attachment(data, maintype=main, subtype=sub, filename=name)
    host = os.environ.get("ROOMSCAN_SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("ROOMSCAN_SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, os.environ["ROOMSCAN_SMTP_PASSWORD"])
        s.send_message(msg)


def notify_async(j: dict, out_dir: Path, on_result=None) -> None:
    """Email the job's address in the background, if email is on and an address was given.
    on_result(status: str) records "sent" or "failed: ..." on the job."""
    to = j.get("notify_email")
    if not (to and enabled()):
        return

    def run():
        try:
            send_job_email(j, to, out_dir)
            status = "sent"
        except Exception as e:  # noqa: BLE001 - a mail problem must never fail the job
            traceback.print_exc()
            status = f"failed: {type(e).__name__}: {str(e)[:200]}"
        if on_result:
            on_result(status)

    threading.Thread(target=run, name="roomscan-email", daemon=True).start()

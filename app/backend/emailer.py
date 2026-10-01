"""PDD Stage 8 (Output data / Business Validation): email the zipped output +
validation summary after every run, success or mismatch - see settings.smtp.

Failure to send never fails the run - this is called after run.result is
already set, so a bad SMTP config just means a missing email, not a lost run.
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path

from backend.settings import settings


def send_run_summary(
    run,
    *,
    flow_title: str,
    zip_path: Path,
    summary_path: Path,
    recon_label: str,
    succeeded: int,
    failed_count: int,
) -> None:
    smtp = settings.smtp
    if not smtp.configured:
        return

    ok = recon_label in ("pass", "n/a (partial run)", "skipped", "n/a")
    subject = (
        f"[VAHAN] {flow_title} - run {run.id} - "
        + ("OK" if ok else "MISMATCH - needs review")
    )
    body = (
        f"{flow_title}\n"
        f"Run ID: {run.id}\n"
        f"Succeeded: {succeeded}   Failed: {failed_count}\n"
        f"Reconciliation: {recon_label}\n\n"
        "Full details are in the attached execution summary and output zip."
    )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp.sender
    msg["To"] = ", ".join(smtp.recipients)
    msg.set_content(body)

    _MAX_ATTACH_BYTES = 20 * 1024 * 1024  # most SMTP relays reject well below 25MB
    skipped_large = []
    for path in (summary_path, zip_path):
        p = Path(path)
        if not p.exists():
            continue
        if p.stat().st_size > _MAX_ATTACH_BYTES:
            skipped_large.append(p)
            continue
        msg.add_attachment(
            p.read_bytes(),
            maintype="application",
            subtype="octet-stream",
            filename=p.name,
        )
    if skipped_large:
        msg.set_content(
            body + "\n\nToo large to attach (left on disk instead):\n"
            + "\n".join(f"  {p} ({p.stat().st_size / 1024 / 1024:.1f} MB)" for p in skipped_large)
        )

    try:
        with smtplib.SMTP(smtp.host, smtp.port, timeout=30) as server:
            if smtp.starttls:
                server.starttls()
            if smtp.username:
                server.login(smtp.username, smtp.password)
            server.send_message(msg)
        run.log(f"Summary emailed to {', '.join(smtp.recipients)}")
    except Exception as e:  # noqa: BLE001 - never fail the run over email
        run.log(f"Could not send summary email: {e}", "warn")

"""Outgoing email. Uses SMTP when configured; otherwise logs the message.

Returns True only if the message was actually handed to an SMTP server, so
callers can tell users honestly whether an email went out.
"""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from flask import current_app

log = logging.getLogger("lawpilot.mail")


def send_mail(to: str, subject: str, body: str) -> bool:
    cfg = current_app.config["LP"]
    if not cfg.smtp_host:
        log.warning("SMTP not configured; email to %s not sent.\nSubject: %s\n%s", to, subject, body)
        return False
    msg = EmailMessage()
    msg["From"] = cfg.smtp_from or cfg.smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            if cfg.smtp_user:
                smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        log.error("Failed to send email to %s: %s", to, exc)
        return False

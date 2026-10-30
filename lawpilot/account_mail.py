"""Account emails: welcome (temporary password + set-password link) and password reset."""
from __future__ import annotations

import html
import secrets
from typing import Tuple

# No look-alike characters (0/O, 1/l/I), so a temporary password can be typed from an email.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"


def temporary_password() -> str:
    """Like 'Kx7m-Qp2v-9RtL': 12 random characters (about 70 bits), always mixing letters and digits."""
    while True:
        groups = ["".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(3)]
        pw = "-".join(groups)
        if any(c.isdigit() for c in pw) and any(c.isalpha() for c in pw):
            return pw


def _button_email(heading: str, intro_html: str, button: str, link: str, footer: str) -> str:
    return f"""<!doctype html><html><body style="margin:0;background:#f3ede0;font-family:Georgia,'Times New Roman',serif;color:#1e1b16">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:32px 12px"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#fffcf5;border:1px solid #dccfb6;border-top:4px solid #8e1c1c;border-radius:10px">
<tr><td style="padding:28px 32px">
<div style="font-size:22px;font-weight:bold;margin-bottom:4px">LawPilot</div>
<div style="font-family:Arial,sans-serif;font-size:12px;color:#675f52;margin-bottom:22px">Legal research &amp; practice management</div>
<h1 style="font-size:20px;margin:0 0 14px">{heading}</h1>
<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.6">{intro_html}</div>
<p style="margin:24px 0"><a href="{html.escape(link, quote=True)}" style="display:inline-block;background:#8e1c1c;color:#fffaf0;text-decoration:none;font-family:Arial,sans-serif;font-weight:bold;padding:12px 22px;border-radius:8px">{button}</a></p>
<div style="font-family:Arial,sans-serif;font-size:12px;color:#675f52;line-height:1.5">If the button does not work, copy this link into your browser:<br>
<span style="word-break:break-all">{html.escape(link)}</span><br><br>{footer}</div>
</td></tr></table>
<div style="font-family:Arial,sans-serif;font-size:11px;color:#8a8275;margin-top:14px">सत्यमेव जयते · Truth alone triumphs</div>
</td></tr></table></body></html>"""


def welcome(name: str, email: str, temp_password: str, link: str, hours: int) -> Tuple[str, str, str]:
    """Subject, plain text and HTML for a new account."""
    subject = "Welcome to LawPilot: set your password"
    text = (f"Hello {name},\n\n"
            f"Your LawPilot account for {email} is ready.\n\n"
            f"Temporary password: {temp_password}\n\n"
            f"Set your own password here (link valid for {hours} hours):\n{link}\n\n"
            "You can also sign in with the temporary password; you will be asked to choose your own straight away.\n\n"
            "If you did not create this account, ignore this email and the temporary password will expire.")
    intro = (f"<p>Hello {html.escape(name)},</p><p>Your LawPilot account for <strong>{html.escape(email)}</strong> is ready.</p>"
             f"<p>Temporary password:<br><code style=\"display:inline-block;margin-top:6px;font-size:18px;letter-spacing:1px;"
             f"background:#ece4d2;padding:8px 12px;border-radius:6px\">{html.escape(temp_password)}</code></p>"
             f"<p>Choose your own password now (the link is valid for {hours} hours). You can also sign in with the "
             f"temporary password; you will be asked to choose your own straight away.</p>")
    return subject, text, _button_email("Set your password", intro, "Set my password", link,
                                        "If you did not create this account, ignore this email; the temporary "
                                        "password will expire on its own.")


def reset(name: str, link: str) -> Tuple[str, str, str]:
    subject = "Reset your LawPilot password"
    text = (f"Hello {name},\n\nUse this link within one hour to reset your password:\n{link}\n\n"
            "If you did not ask for this, ignore this email.")
    intro = f"<p>Hello {html.escape(name)},</p><p>Use the button below within one hour to choose a new password.</p>"
    return subject, text, _button_email("Reset your password", intro, "Choose a new password", link,
                                        "If you did not ask for this, ignore this email; your password stays the same.")

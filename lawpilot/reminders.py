"""Hearing reminder emails. Run once a day (cron / Task Scheduler / Render cron job):

    python -m lawpilot.reminders

Emails every member of a matter who has hearing reminders enabled about the
matter's hearings tomorrow. Requires SMTP settings.
"""
from __future__ import annotations

from datetime import date, timedelta

from lawpilot import create_app
from lawpilot.db import get_db, query_all
from lawpilot.mailer import send_mail


def send_hearing_reminders() -> int:
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    rows = query_all(
        "SELECT h.date, h.purpose, h.court_room, m.id AS matter_id, m.title, m.court, u.email, u.name, u.prefs "
        "FROM hearings h JOIN matters m ON m.id = h.matter_id JOIN matter_members mm ON mm.matter_id = m.id "
        "JOIN users u ON u.id = mm.user_id WHERE substr(h.date, 1, 10) = ? AND u.status = 'active' "
        "AND m.status = 'open'", (tomorrow,))
    sent = 0
    for r in rows:
        if not (r["prefs"] or {}).get("notify_hearings", True):
            continue
        when = r["date"][11:16] if len(r["date"]) > 10 else ""
        body = (f"Dear {r['name']},\n\nReminder: {r['title']} is listed tomorrow ({tomorrow}{' at ' + when if when else ''})"
                f"{' before ' + r['court'] if r['court'] else ''}.\n"
                + (f"Purpose: {r['purpose']}\n" if r["purpose"] else "")
                + (f"Court room / item: {r['court_room']}\n" if r["court_room"] else ""))
        if send_mail(r["email"], f"Hearing tomorrow: {r['title']}", body):
            sent += 1
    return sent


if __name__ == "__main__":
    app = create_app()
    with app.app_context():
        get_db()
        print(f"Sent {send_hearing_reminders()} reminder(s).")

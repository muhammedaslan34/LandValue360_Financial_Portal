from __future__ import annotations

import smtplib
import sys
from email.message import EmailMessage

from landvalue360_portal.config import get_settings


def main() -> int:
    settings = get_settings()
    recipient = sys.argv[1] if len(sys.argv) > 1 else settings.smtp_username
    if not recipient:
        print("No recipient provided")
        return 1
    if settings.email_backend != "smtp":
        print(f"EMAIL_BACKEND={settings.email_backend!r} (expected 'smtp')")
        return 1
    if not settings.smtp_host:
        print("SMTP host is not configured")
        return 1

    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = recipient
    msg["Subject"] = "LV360 SMTP probe"
    msg.set_content("If you received this, Brevo SMTP is working from the server.")

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password or "")
            smtp.send_message(msg)
    except Exception as exc:
        print(f"SMTP FAILED: {type(exc).__name__}: {exc}")
        return 1

    print(f"SMTP OK: sent probe to {recipient}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

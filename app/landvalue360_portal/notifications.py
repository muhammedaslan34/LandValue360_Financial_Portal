from __future__ import annotations

import smtplib
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import EmailTemplate, Notification, NotificationOutbox, Profile, User


class _SafeFormat(dict):
    def __missing__(self, key: str) -> str:
        return ""


def notify_user(db: Session, user: User, *, kind: str, title: str, body: str, link: str | None = None, email_template: str | None = None) -> None:
    db.add(Notification(user_id=user.id, kind=kind, title=title, body=body, link=link, created_by=user.id, updated_by=user.id))
    if email_template:
        profile = db.scalar(select(Profile).where(Profile.user_id == user.id))
        language = profile.preferred_language if profile and profile.preferred_language in {"ar", "en"} else "ar"
        db.add(NotificationOutbox(
            recipient=user.email,
            template_code=email_template,
            payload={"title": title, "body": body, "link": link or "", "language": language},
            created_by=user.id,
            updated_by=user.id,
        ))


def _render_message(db: Session, row: NotificationOutbox) -> tuple[str, str]:
    payload = _SafeFormat({key: "" if value is None else str(value) for key, value in (row.payload or {}).items()})
    language = payload.get("language") or "ar"
    template = db.scalar(select(EmailTemplate).where(EmailTemplate.code == row.template_code, EmailTemplate.active.is_(True)))
    if not template:
        subject = payload.get("title") or row.template_code
        body = payload.get("body") or ""
        if payload.get("link"):
            body += f"\n{payload['link']}"
        return subject, body
    subject_template = template.subject_en if language == "en" else template.subject_ar
    body_template = template.body_en if language == "en" else template.body_ar
    return subject_template.format_map(payload), body_template.format_map(payload)


def deliver_pending(db: Session, limit: int = 50) -> int:
    settings = get_settings()
    rows = list(db.scalars(select(NotificationOutbox).where(NotificationOutbox.status == "PENDING").order_by(NotificationOutbox.created_at).limit(limit)).all())
    delivered = 0
    for row in rows:
        row.attempts += 1
        try:
            subject, body = _render_message(db, row)
            if settings.email_backend == "console":
                print(f"[EMAIL] to={row.recipient} subject={subject!r} body={body!r}")
            else:
                if not settings.smtp_host:
                    raise RuntimeError("SMTP host is not configured")
                msg = EmailMessage()
                msg["From"] = settings.smtp_from
                msg["To"] = row.recipient
                msg["Subject"] = subject
                msg.set_content(body)
                with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
                    if settings.smtp_starttls:
                        smtp.starttls()
                    if settings.smtp_username:
                        smtp.login(settings.smtp_username, settings.smtp_password or "")
                    smtp.send_message(msg)
            row.status = "SENT"
            row.last_error = None
            delivered += 1
        except Exception as exc:
            row.status = "FAILED" if row.attempts >= 5 else "PENDING"
            row.last_error = str(exc)[:2000]
            print(f"[EMAIL FAILED] to={row.recipient} template={row.template_code} attempts={row.attempts} error={exc}")
    db.flush()
    return delivered

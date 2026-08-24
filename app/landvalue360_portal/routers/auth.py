from __future__ import annotations

from datetime import timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import AccessSession, LoginAttempt, OneTimeToken, PrivacyRequest, User, UserConsent, utcnow
from ..notifications import notify_user
from ..schemas import LoginIn, RegisterIn
from ..security import SESSION_COOKIE, create_session, csrf_protect, current_session, current_user, hash_password, new_token, revoke_session, token_hash, verify_password, user_permission_codes, user_role_codes
from ..services import create_personal_landowner, seed_defaults, audit
from ..web import templates

router = APIRouter()

def _aware(value):
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"title": "تسجيل الدخول"})


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html", {"title": "إنشاء حساب"})


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_page(request: Request):
    return templates.TemplateResponse(request, "forgot.html", {"title": "استعادة كلمة المرور"})


@router.get("/reset-password", response_class=HTMLResponse)
def reset_page(request: Request, token: str = ""):
    return templates.TemplateResponse(request, "reset.html", {"title": "تعيين كلمة مرور", "token": token})


@router.get("/change-password", response_class=HTMLResponse)
def change_password_page(request: Request, user: User = Depends(current_user)):
    return templates.TemplateResponse(request, "change_password.html", {"title": "تغيير كلمة المرور", "user": user})


@router.post("/api/auth/register")
def register(payload: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    seed_defaults(db)
    try:
        user, org = create_personal_landowner(
            db, email=str(payload.email), password=payload.password, full_name=payload.full_name,
            organization_name=payload.organization_name, country=payload.country, phone=payload.phone,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not user.email_verified_at:
        verify_raw = new_token()
        db.add(OneTimeToken(user_id=user.id, kind="VERIFY_EMAIL", token_hash=token_hash(verify_raw), expires_at=utcnow() + timedelta(hours=24), created_by=user.id, updated_by=user.id))
        notify_user(db, user, kind="EMAIL_VERIFICATION", title="تأكيد البريد الإلكتروني", body="يرجى تأكيد بريدك الإلكتروني.", link=f"{get_settings().base_url}/verify-email?token={verify_raw}", email_template="VERIFY_EMAIL")
        db.commit()
        return {"ok": True, "redirect": "/login?verification_required=1", "verification_required": True}
    raw, session = create_session(db, user, request=request)
    db.commit()
    response.set_cookie(SESSION_COOKIE, raw, httponly=True, secure=get_settings().cookie_secure, samesite="lax", max_age=get_settings().session_hours * 3600)
    return {"ok": True, "redirect": "/portal", "csrf_token": session.csrf_token}


@router.post("/api/auth/login")
def login(payload: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    identifier = str(payload.email).lower()
    ip = request.client.host if request.client else None
    now = utcnow()
    window = now - timedelta(minutes=15)
    recent = db.scalar(select(func.count(LoginAttempt.id)).where(LoginAttempt.attempted_at >= window, ((LoginAttempt.identifier == identifier) | (LoginAttempt.ip_address == ip)))) or 0
    if recent >= 20:
        raise HTTPException(status_code=429, detail="محاولات كثيرة. حاول لاحقاً.")
    user = db.scalar(select(User).where(func.lower(User.email) == identifier, User.deleted_at.is_(None)))
    if not user or not verify_password(user.password_hash, payload.password):
        db.add(LoginAttempt(identifier=identifier, ip_address=ip, success=False, attempted_at=now))
        if user:
            user.failed_login_count += 1
            if user.failed_login_count >= 8:
                user.locked_until = now + timedelta(minutes=30)
        db.commit()
        raise HTTPException(status_code=401, detail="البريد أو كلمة المرور غير صحيحة")
    if not user.active or user.suspended or (user.locked_until and _aware(user.locked_until) > now):
        raise HTTPException(status_code=403, detail="الحساب غير متاح حالياً")
    if not user.email_verified_at and not get_settings().auto_verify_email:
        raise HTTPException(status_code=403, detail="يجب تأكيد البريد الإلكتروني أولاً")
    db.add(LoginAttempt(identifier=identifier, ip_address=ip, success=True, attempted_at=now))
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    raw, session = create_session(db, user, request=request)
    audit(db, user=user, action="LOGIN", entity_type="USER", entity_id=user.id, ip=request.client.host if request.client else None)
    db.commit()
    response.set_cookie(SESSION_COOKIE, raw, httponly=True, secure=get_settings().cookie_secure, samesite="lax", max_age=get_settings().session_hours * 3600)
    return {
        "ok": True,
        "redirect": "/change-password" if user.must_change_password else "/portal",
        "csrf_token": session.csrf_token,
        "must_change_password": bool(user.must_change_password),
    }


@router.post("/api/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db), _=Depends(csrf_protect)):
    revoke_session(db, request.cookies.get(SESSION_COOKIE))
    db.commit()
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@router.get("/verify-email")
def verify_email(token: str, db: Session = Depends(get_db)):
    row = db.scalar(select(OneTimeToken).where(OneTimeToken.kind == "VERIFY_EMAIL", OneTimeToken.token_hash == token_hash(token), OneTimeToken.used_at.is_(None)))
    if not row or _aware(row.expires_at) <= utcnow():
        raise HTTPException(status_code=400, detail="الرابط غير صالح أو منتهي")
    user = db.get(User, row.user_id)
    user.email_verified_at = utcnow(); row.used_at = utcnow(); db.commit()
    return RedirectResponse("/login?verified=1", status_code=303)


@router.post("/api/auth/forgot-password")
def forgot_password(request: Request, payload: dict, db: Session = Depends(get_db)):
    email = str(payload.get("email") or "").lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email, User.deleted_at.is_(None)))
    if user:
        raw = new_token()
        db.add(OneTimeToken(user_id=user.id, kind="RESET_PASSWORD", token_hash=token_hash(raw), expires_at=utcnow() + timedelta(hours=1), created_by=user.id, updated_by=user.id))
        notify_user(db, user, kind="PASSWORD_RESET", title="استعادة كلمة المرور", body="استخدم الرابط الآمن لتعيين كلمة مرور جديدة.", link=f"{get_settings().base_url}/reset-password?token={raw}", email_template="PASSWORD_RESET")
        db.commit()
    return {"ok": True, "message": "إذا كان البريد مسجلاً فسيصلك رابط الاستعادة."}


@router.post("/api/auth/reset-password")
def reset_password(payload: dict, db: Session = Depends(get_db)):
    raw = str(payload.get("token") or "")
    password = str(payload.get("password") or "")
    if len(password) < 10:
        raise HTTPException(status_code=422, detail="كلمة المرور يجب ألا تقل عن 10 أحرف")
    row = db.scalar(select(OneTimeToken).where(OneTimeToken.kind == "RESET_PASSWORD", OneTimeToken.token_hash == token_hash(raw), OneTimeToken.used_at.is_(None)))
    if not row or _aware(row.expires_at) <= utcnow():
        raise HTTPException(status_code=400, detail="الرابط غير صالح أو منتهي")
    user = db.get(User, row.user_id)
    user.password_hash = hash_password(password)
    user.password_changed_at = utcnow()
    user.must_change_password = False
    row.used_at = utcnow()
    for session in db.scalars(select(AccessSession).where(AccessSession.user_id == user.id, AccessSession.revoked_at.is_(None))).all():
        session.revoked_at = utcnow()
    audit(db, user=user, action="PASSWORD_RESET", entity_type="USER", entity_id=user.id)
    db.commit()
    return {"ok": True}


@router.post("/api/auth/change-password")
def change_password(
    payload: dict, request: Request, user: User = Depends(current_user),
    session=Depends(current_session), db: Session = Depends(get_db), _=Depends(csrf_protect),
):
    current_password = str(payload.get("current_password") or "")
    new_password = str(payload.get("new_password") or "")
    confirm_password = str(payload.get("confirm_password") or "")
    if not verify_password(user.password_hash, current_password):
        raise HTTPException(status_code=422, detail="كلمة المرور الحالية غير صحيحة")
    if len(new_password) < 10:
        raise HTTPException(status_code=422, detail="كلمة المرور الجديدة يجب ألا تقل عن 10 أحرف")
    if new_password != confirm_password:
        raise HTTPException(status_code=422, detail="تأكيد كلمة المرور غير مطابق")
    if new_password == current_password:
        raise HTTPException(status_code=422, detail="يجب اختيار كلمة مرور جديدة مختلفة")
    user.password_hash = hash_password(new_password)
    user.password_changed_at = utcnow()
    user.must_change_password = False
    user.failed_login_count = 0
    user.locked_until = None
    for row in db.scalars(select(AccessSession).where(
        AccessSession.user_id == user.id, AccessSession.id != session.id, AccessSession.revoked_at.is_(None),
    )).all():
        row.revoked_at = utcnow()
    audit(
        db, user=user, action="PASSWORD_CHANGED", entity_type="USER", entity_id=user.id,
        ip=request.client.host if request.client else None,
    )
    db.commit()
    return {"ok": True, "redirect": "/portal"}


@router.get("/api/auth/me")
def me(user: User = Depends(current_user), session=Depends(current_session), db: Session = Depends(get_db)):
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "csrf_token": session.csrf_token,
        "roles": sorted(user_role_codes(db, user.id)),
        "permissions": sorted(user_permission_codes(db, user.id)),
        "must_change_password": bool(user.must_change_password),
        "password_changed_at": user.password_changed_at,
    }


@router.get("/api/auth/sessions")
def sessions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = list(db.scalars(select(AccessSession).where(AccessSession.user_id == user.id, AccessSession.revoked_at.is_(None)).order_by(AccessSession.created_at.desc())).all())
    return [{"id": r.id, "created_at": r.created_at, "expires_at": r.expires_at, "ip_address": r.ip_address, "user_agent": r.user_agent} for r in rows]

@router.delete("/api/auth/sessions/{session_id}")
def revoke_named_session(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    row = db.get(AccessSession, session_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    row.revoked_at = utcnow(); db.commit(); return {"ok": True}

@router.post("/api/account/privacy-requests", status_code=201)
def privacy_request(payload: dict, user: User = Depends(current_user), db: Session = Depends(get_db), _=Depends(csrf_protect)):
    kind = str(payload.get("request_type") or "").upper()
    if kind not in {"EXPORT", "DELETE_ACCOUNT"}:
        raise HTTPException(status_code=422, detail="Invalid privacy request")
    row = PrivacyRequest(user_id=user.id, request_type=kind, status="OPEN", notes=str(payload.get("notes") or ""), created_by=user.id, updated_by=user.id)
    db.add(row); db.commit(); return {"id": row.id, "status": row.status}

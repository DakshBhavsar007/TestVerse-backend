"""
app/routers/auth_router.py

POST /auth/register              — create account (unverified), send OTP
POST /auth/verify-otp            — verify OTP → activate account, get JWT
POST /auth/resend-otp            — resend OTP to email
POST /auth/login                 — OAuth2 form login, get JWT
GET  /auth/me                    — current user info (protected)
POST /auth/google                — Google OAuth sign-in with role
POST /auth/forgot-password       — send reset link (handles Google accounts)
GET  /auth/verify-reset-token/{} — check reset token validity
POST /auth/reset-password        — set new password via token
PATCH /auth/update-profile       — update name / email
POST /auth/change-password       — change password (requires current password)
"""

import os
import secrets
import random
from typing import Optional
from datetime import datetime, timezone, timedelta

import sendgrid
from sendgrid.helpers.mail import Mail

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field

from app.database import get_db
from app.utils.auth import create_access_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["Auth"])

# ─── Helpers ──────────────────────────────────────────────────────────────────

VALID_ROLES = {"admin", "developer", "viewer"}

FRONTEND_URL    = os.getenv("FRONTEND_URL", "http://localhost:5173")
SENDGRID_KEY    = os.getenv("SENDGRID_API_KEY", "")
FROM_EMAIL      = os.getenv("FROM_EMAIL", "noreply@testverse.com")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")


def _safe(user: dict) -> dict:
    return {
        "id":         str(user.get("_id") or user.get("id", "")),
        "email":      user["email"],
        "name":       user.get("name", ""),
        "created_at": str(user.get("created_at", "")),
    }


async def _find_by_email(email: str):
    db = get_db()
    if db is None:
        return None
    return await db.users.find_one({"email": email.lower()})


def _send_email(to: str, subject: str, html: str):
    """Fire-and-forget SendGrid email. Prints to console in dev mode."""
    if not SENDGRID_KEY:
        print(f"\n📧 [DEV EMAIL] To: {to}\nSubject: {subject}\n{html}\n")
        return
    try:
        sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_KEY)
        sg.send(Mail(from_email=FROM_EMAIL, to_emails=to, subject=subject, html_content=html))
    except Exception as e:
        print(f"⚠️  SendGrid error: {e}")


def _otp_email_html(name: str, otp: str) -> str:
    return f"""
    <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:32px;
                background:#0f1623;color:#fff;border-radius:12px;">
      <h2 style="color:#6366f1;">🔐 Verify your TestVerse account</h2>
      <p style="color:#9ca3af;">Hi {name}, thanks for signing up!</p>
      <p style="color:#9ca3af;">Use the OTP below to verify your email address.
         It expires in <strong>10 minutes</strong>.</p>
      <div style="text-align:center;margin:32px 0;">
        <span style="display:inline-block;letter-spacing:12px;font-size:36px;
                     font-weight:700;color:#6366f1;background:rgba(99,102,241,0.1);
                     padding:16px 28px;border-radius:12px;border:1px solid rgba(99,102,241,0.3);">
          {otp}
        </span>
      </div>
      <p style="color:#6b7280;font-size:13px;">
        If you didn't create a TestVerse account, you can safely ignore this email.
      </p>
    </div>
    """


def _reset_email_html(reset_url: str) -> str:
    return f"""
    <div style="font-family:sans-serif;max-width:480px;margin:auto;padding:32px;
                background:#0f1623;color:#fff;border-radius:12px;">
      <h2 style="color:#6366f1;">🔑 Reset Your Password</h2>
      <p style="color:#9ca3af;">You requested a password reset for your TestVerse account.</p>
      <a href="{reset_url}"
         style="display:inline-block;margin:24px 0;padding:12px 28px;
                background:linear-gradient(135deg,#6366f1,#8b5cf6);
                color:#fff;border-radius:8px;text-decoration:none;font-weight:600;">
        Reset Password
      </a>
      <p style="color:#6b7280;font-size:13px;">
        This link expires in <strong>1 hour</strong>.
        If you didn't request this, you can safely ignore this email.
      </p>
    </div>
    """


# ─── Pydantic Models ───────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email:    EmailStr
    password: str  = Field(..., min_length=8)
    name:     str  = Field(..., min_length=1)
    role:     Optional[str] = Field("developer", description="admin | developer | viewer")


class VerifyOTPRequest(BaseModel):
    email: EmailStr
    otp:   str = Field(..., min_length=6, max_length=6)


class ResendOTPRequest(BaseModel):
    email: EmailStr


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str  = "bearer"
    user:         dict


class GoogleAuthRequest(BaseModel):
    token: str
    role:  Optional[str] = Field("developer")


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token:        str
    new_password: str = Field(..., min_length=8)


class UpdateProfileRequest(BaseModel):
    name:  Optional[str]      = Field(None, min_length=1)
    email: Optional[EmailStr] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password:     str = Field(..., min_length=8)


# ─── Register (sends OTP, account inactive until verified) ────────────────────

@router.post("/register", status_code=202)
async def register(req: RegisterRequest):
    """
    Create a pending (unverified) account and send a 6-digit OTP.
    The account is activated only after /verify-otp succeeds.
    """
    if await _find_by_email(req.email):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered.")

    db = get_db()
    role = req.role if req.role in VALID_ROLES else "developer"
    otp  = str(random.randint(100000, 999999))
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    # Store pending user
    pending = {
        "email":           req.email.lower(),
        "name":            req.name,
        "hashed_password": hash_password(req.password),
        "role":            role,
        "created_at":      datetime.now(timezone.utc),
        "is_active":       False,   # ← inactive until OTP verified
        "auth_provider":   "email",
    }

    if db is not None:
        # Upsert so resend-before-verify doesn't duplicate
        await db.users.update_one(
            {"email": req.email.lower()},
            {"$set": pending},
            upsert=True
        )
        # Store OTP
        await db.email_otps.replace_one(
            {"email": req.email.lower()},
            {"email": req.email.lower(), "otp": otp, "expires_at": expires_at, "verified": False},
            upsert=True,
        )

    _send_email(
        to=req.email,
        subject="TestVerse — Your verification code",
        html=_otp_email_html(req.name, otp),
    )

    return {"message": "otp_sent", "email": req.email.lower()}


# ─── Verify OTP ───────────────────────────────────────────────────────────────

@router.post("/verify-otp", response_model=TokenResponse)
async def verify_otp(req: VerifyOTPRequest):
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")

    record = await db.email_otps.find_one({"email": req.email.lower(), "verified": False})

    if not record:
        raise HTTPException(400, "No pending verification for this email.")

    # Check expiry
    expires_at = record["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(400, "OTP has expired. Please request a new one.")

    if record["otp"] != req.otp.strip():
        raise HTTPException(400, "Incorrect OTP. Please try again.")

    # Activate the user
    await db.users.update_one(
        {"email": req.email.lower()},
        {"$set": {"is_active": True, "email_verified": True}}
    )

    # Mark OTP used
    await db.email_otps.update_one(
        {"email": req.email.lower()},
        {"$set": {"verified": True}}
    )

    # Fetch user & assign role
    user = await _find_by_email(req.email)
    if not user:
        raise HTTPException(500, "User not found after verification.")

    user_id = str(user.get("_id") or user.get("id", ""))
    role    = user.get("role", "developer")

    await db.role_assignments.replace_one(
        {"user_id": user_id},
        {"user_id": user_id, "role": role},
        upsert=True,
    )

    token = create_access_token({"sub": user["email"], "name": user.get("name", ""), "id": user_id})
    return TokenResponse(access_token=token, user=_safe(user))


# ─── Resend OTP ───────────────────────────────────────────────────────────────

@router.post("/resend-otp")
async def resend_otp(req: ResendOTPRequest):
    db = get_db()
    user = await _find_by_email(req.email)

    if not user:
        raise HTTPException(404, "No account found for this email.")
    if user.get("is_active") and user.get("email_verified"):
        raise HTTPException(400, "Email is already verified.")

    otp        = str(random.randint(100000, 999999))
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    if db is not None:
        await db.email_otps.replace_one(
            {"email": req.email.lower()},
            {"email": req.email.lower(), "otp": otp, "expires_at": expires_at, "verified": False},
            upsert=True,
        )

    _send_email(
        to=req.email,
        subject="TestVerse — Your new verification code",
        html=_otp_email_html(user.get("name", "there"), otp),
    )

    return {"message": "otp_resent"}


# ─── Login ────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(form: OAuth2PasswordRequestForm = Depends()):
    user = await _find_by_email(form.username)

    if not user or not verify_password(form.password, user.get("hashed_password", "")):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Block Google-only accounts from password login
    if user.get("auth_provider") == "google" and not user.get("hashed_password"):
        raise HTTPException(400, "This account uses Google Sign-In. Please use the Google button.")

    # Block unverified accounts
    if not user.get("is_active", True):
        raise HTTPException(
            403,
            "email_not_verified",   # frontend checks this string to show OTP screen
        )

    user_id = str(user.get("_id") or user.get("id", ""))
    token   = create_access_token({"sub": user["email"], "name": user.get("name", ""), "id": user_id})

    db = get_db()
    if db is not None:
        await db.users.update_one(
            {"_id": user.get("_id")},
            {"$set": {"last_login": datetime.now(timezone.utc)}}
        )

    return TokenResponse(access_token=token, user=_safe(user))


# ─── Me ───────────────────────────────────────────────────────────────────────

@router.get("/me")
async def me(current_user: dict = Depends(get_current_user)):
    db = get_db()
    if db is not None:
        email = current_user.get("email") or current_user.get("sub", "")
        if email:
            user = await db.users.find_one({"email": email.lower()})
            if user:
                return {"user": _safe(user)}
    return {"user": {
        "id":    current_user.get("id") or current_user.get("sub"),
        "email": current_user.get("email") or current_user.get("sub"),
        "name":  current_user.get("name", ""),
    }}


# ─── Google OAuth ─────────────────────────────────────────────────────────────

@router.post("/google", response_model=TokenResponse)
async def google_auth(req: GoogleAuthRequest):
    """Verify Google ID token, create/login user, assign role, return JWT."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(500, "Google OAuth not configured on server.")

    role = req.role if req.role in VALID_ROLES else "developer"

    # Verify Google token
    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests
        user_info = google_id_token.verify_oauth2_token(
            req.token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
    except ValueError as e:
        raise HTTPException(401, f"Invalid Google token: {e}")

    email = user_info["email"].lower()
    name  = user_info.get("name", email.split("@")[0])
    db    = get_db()

    user = await _find_by_email(email)

    if not user:
        # New user — create and auto-verify (Google already verified the email)
        new_user = {
            "email":           email,
            "name":            name,
            "hashed_password": "",
            "google_id":       user_info["sub"],
            "avatar":          user_info.get("picture", ""),
            "created_at":      datetime.now(timezone.utc),
            "is_active":       True,
            "email_verified":  True,
            "auth_provider":   "google",
        }
        if db is not None:
            result = await db.users.insert_one(new_user)
            new_user["id"] = str(result.inserted_id)
            await db.role_assignments.replace_one(
                {"user_id": new_user["id"]},
                {"user_id": new_user["id"], "role": role},
                upsert=True,
            )
        user = new_user
    else:
        # Existing user — preserve role, update metadata
        user["id"] = str(user.get("_id") or user.get("id", ""))
        if db is not None:
            await db.users.update_one(
                {"email": email},
                {"$set": {
                    "last_login":    datetime.now(timezone.utc),
                    "avatar":        user_info.get("picture", ""),
                    "auth_provider": "google",
                }}
            )

    user_id = str(user.get("id") or user.get("_id", ""))
    token   = create_access_token({"sub": email, "name": name, "id": user_id})
    return TokenResponse(access_token=token, user=_safe(user))


# ─── Forgot Password ──────────────────────────────────────────────────────────

@router.post("/forgot-password")
async def forgot_password(req: ForgotPasswordRequest):
    db   = get_db()
    user = await _find_by_email(req.email)

    # Always 200 to prevent email enumeration
    if not user:
        return {"message": "If that email exists, a reset link has been sent."}

    # Google-only account
    if user.get("auth_provider") == "google" and not user.get("hashed_password"):
        return {
            "message": "google_account",
            "detail":  "This email is linked to a Google account. Please sign in with Google.",
        }

    reset_token = secrets.token_urlsafe(32)
    expires_at  = datetime.now(timezone.utc) + timedelta(hours=1)

    if db is not None:
        await db.password_resets.replace_one(
            {"email": req.email.lower()},
            {"email": req.email.lower(), "token": reset_token,
             "expires_at": expires_at, "used": False},
            upsert=True,
        )

    reset_url = f"{FRONTEND_URL}/reset-password?token={reset_token}"
    _send_email(
        to=req.email,
        subject="TestVerse — Reset Your Password",
        html=_reset_email_html(reset_url),
    )

    return {"message": "If that email exists, a reset link has been sent."}


# ─── Verify Reset Token ───────────────────────────────────────────────────────

@router.get("/verify-reset-token/{token}")
async def verify_reset_token(token: str):
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")

    record = await db.password_resets.find_one({"token": token, "used": False})
    if not record:
        return {"valid": False, "reason": "Invalid or already used."}

    expires_at = record["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        return {"valid": False, "reason": "Link has expired."}

    return {"valid": True}


# ─── Reset Password ───────────────────────────────────────────────────────────

@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest):
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")

    record = await db.password_resets.find_one({"token": req.token, "used": False})
    if not record:
        raise HTTPException(400, "Invalid or expired reset link.")

    expires_at = record["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(400, "Reset link has expired. Please request a new one.")

    await db.users.update_one(
        {"email": record["email"]},
        {"$set": {"hashed_password": hash_password(req.new_password)}}
    )
    await db.password_resets.update_one(
        {"token": req.token},
        {"$set": {"used": True}}
    )

    return {"message": "Password reset successfully. You can now log in."}


# ─── Profile Update ───────────────────────────────────────────────────────────

@router.patch("/update-profile")
async def update_profile(
    req: UpdateProfileRequest,
    current_user: dict = Depends(get_current_user),
):
    db    = get_db()
    email = current_user.get("email") or current_user.get("sub")

    if db is None:
        raise HTTPException(500, "Database not available")

    updates = {}
    if req.name:
        updates["name"] = req.name
    if req.email and req.email.lower() != email:
        if await db.users.find_one({"email": req.email.lower()}):
            raise HTTPException(409, "Email already in use")
        updates["email"] = req.email.lower()

    if not updates:
        raise HTTPException(400, "No changes provided")

    await db.users.update_one({"email": email}, {"$set": updates})
    updated = await db.users.find_one({"email": updates.get("email", email)})
    return {"success": True, "user": _safe(updated)}


# ─── Change Password ──────────────────────────────────────────────────────────

@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user),
):
    db    = get_db()
    email = current_user.get("email") or current_user.get("sub")

    if db is None:
        raise HTTPException(500, "Database not available")

    user = await _find_by_email(email)
    if not user:
        raise HTTPException(404, "User not found")
    if not verify_password(req.current_password, user["hashed_password"]):
        raise HTTPException(400, "Current password is incorrect")

    await db.users.update_one(
        {"email": email},
        {"$set": {"hashed_password": hash_password(req.new_password)}}
    )
    return {"success": True, "message": "Password changed successfully"}

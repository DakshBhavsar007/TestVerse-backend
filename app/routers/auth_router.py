"""
app/routers/auth_router.py
POST /auth/register  — create account, get JWT
POST /auth/login     — OAuth2 form login, get JWT
GET  /auth/me        — current user info (protected)
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from app.database import get_db
from app.utils.auth import create_access_token, get_current_user, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["Auth"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    name: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


def _safe(user: dict) -> dict:
    return {
        "id": str(user.get("_id") or user.get("id", "")),
        "email": user["email"],
        "name": user.get("name", ""),
        "created_at": str(user.get("created_at", "")),
    }


async def _find_by_email(email: str):
    db = get_db()
    if db is None:
        return None
    return await db.users.find_one({"email": email.lower()})


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(req: RegisterRequest):
    if await _find_by_email(req.email):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered.")
    user = {
        "email": req.email.lower(),
        "name": req.name,
        "hashed_password": hash_password(req.password),
        "created_at": datetime.now(timezone.utc),
        "is_active": True,
    }
    db = get_db()
    if db is not None:
        result = await db.users.insert_one(user)
        user["id"] = str(result.inserted_id)
    else:
        user["id"] = "dev-in-memory"
    token = create_access_token({"sub": user["email"], "name": user["name"]})
    return TokenResponse(access_token=token, user=_safe(user))


@router.post("/login", response_model=TokenResponse)
async def login(form: OAuth2PasswordRequestForm = Depends()):
    user = await _find_by_email(form.username)
    if not user or not verify_password(form.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token({"sub": user["email"], "name": user.get("name", "")})
    return TokenResponse(access_token=token, user=_safe(user))


@router.get("/me")
async def me(current_user: dict = Depends(get_current_user)):
    return {"user": current_user}


# ─── Profile Update Endpoints ──────────────────────────────────────────────────

class UpdateProfileRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1)
    email: Optional[EmailStr] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


@router.patch("/update-profile")
async def update_profile(
    req: UpdateProfileRequest,
    current_user: dict = Depends(get_current_user)
):
    """Update the current user's name and/or email."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")
    email = current_user.get("email") or current_user.get("sub")

    if db is None:
        raise HTTPException(500, "Database not available")

    updates = {}
    if req.name:
        updates["name"] = req.name
    if req.email and req.email.lower() != email:
        existing = await db.users.find_one({"email": req.email.lower()})
        if existing:
            raise HTTPException(409, "Email already in use")
        updates["email"] = req.email.lower()

    if not updates:
        raise HTTPException(400, "No changes provided")

    await db.users.update_one({"email": email}, {"$set": updates})
    updated = await db.users.find_one({"email": updates.get("email", email)})
    return {"success": True, "user": _safe(updated)}


@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user)
):
    """Change password after verifying the current password."""
    db = get_db()
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

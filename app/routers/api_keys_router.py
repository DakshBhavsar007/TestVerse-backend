"""
app/routers/api_keys_router.py
Phase 6a — API Keys: generate, list, revoke.

NOTE: current_user["sub"] is the user's EMAIL (see auth.py).
MongoDB collection: api_keys
  { key_id, user_id (=email), name, key_hash, key_preview, created_at, last_used, active }
"""
import hashlib
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from ..utils.auth import get_current_user
from ..database import get_db

router = APIRouter(prefix="/apikeys", tags=["API Keys"])

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
KEY_PREFIX = "tv_"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()

def _preview(raw: str) -> str:
    return raw[:12] + "..." + raw[-4:]


# ── API Key auth dependency ────────────────────────────────────────────────────

async def get_user_from_api_key(api_key: str = Security(API_KEY_HEADER)) -> dict | None:
    """Resolve a user from X-API-Key header. Returns user dict or None."""
    if not api_key or not api_key.startswith(KEY_PREFIX):
        return None
    db = get_db()
    key_hash = _hash_key(api_key)
    record = await db.api_keys.find_one({"key_hash": key_hash, "active": True}, {"_id": 0})
    if not record:
        return None
    await db.api_keys.update_one({"key_hash": key_hash}, {"$set": {"last_used": _now()}})
    # Return dict matching JWT payload shape (sub = email)
    return {"sub": record["user_id"], "email": record["user_id"], "name": record.get("user_name", "")}


# ── Pydantic ───────────────────────────────────────────────────────────────────

class CreateKeyRequest(BaseModel):
    name: str = "My API Key"


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/")
async def list_keys(current_user: dict = Depends(get_current_user)):
    """List all active API keys for the current user."""
    db = get_db()
    cursor = db.api_keys.find(
        {"user_id": current_user["sub"], "active": True},
        {"_id": 0, "key_hash": 0}
    )
    keys = await cursor.to_list(length=50)
    return {"keys": keys}


@router.post("/")
async def create_key(
    body: CreateKeyRequest,
    current_user: dict = Depends(get_current_user),
):
    """Generate a new API key. The full key is returned ONCE — store it safely."""
    db = get_db()
    count = await db.api_keys.count_documents({"user_id": current_user["sub"], "active": True})
    if count >= 10:
        raise HTTPException(status_code=400, detail="Maximum of 10 API keys reached. Revoke one first.")

    raw_key = KEY_PREFIX + secrets.token_urlsafe(32)
    key_id  = str(uuid.uuid4())
    now     = _now()

    record = {
        "key_id":      key_id,
        "user_id":     current_user["sub"],   # = email
        "user_name":   current_user.get("name", ""),
        "name":        body.name.strip() or "My API Key",
        "key_hash":    _hash_key(raw_key),
        "key_preview": _preview(raw_key),
        "created_at":  now,
        "last_used":   None,
        "active":      True,
    }
    await db.api_keys.insert_one(record)

    return {
        "success":    True,
        "key_id":     key_id,
        "name":       record["name"],
        "key":        raw_key,          # shown ONCE only
        "preview":    _preview(raw_key),
        "created_at": now,
        "message":    "Store this key safely — it will not be shown again.",
    }


@router.delete("/{key_id}")
async def revoke_key(
    key_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Revoke an API key (soft-delete)."""
    db = get_db()
    result = await db.api_keys.update_one(
        {"key_id": key_id, "user_id": current_user["sub"]},
        {"$set": {"active": False}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Key not found")
    return {"success": True, "revoked": key_id}


@router.get("/usage")
async def key_usage_stats(current_user: dict = Depends(get_current_user)):
    """Summary stats for all the user's API keys."""
    db = get_db()
    cursor = db.api_keys.find({"user_id": current_user["sub"]}, {"_id": 0, "key_hash": 0})
    keys = await cursor.to_list(length=50)
    active  = sum(1 for k in keys if k.get("active"))
    revoked = sum(1 for k in keys if not k.get("active"))
    used    = sum(1 for k in keys if k.get("last_used"))
    return {
        "total": len(keys), "active": active,
        "revoked": revoked, "ever_used": used,
        "keys": [k for k in keys if k.get("active")],
    }

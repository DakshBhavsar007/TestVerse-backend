"""
app/utils/auth.py — JWT helpers + FastAPI dependency
pip install python-jose[cryptography] passlib[bcrypt] python-multipart
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, APIKeyHeader
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    payload = data.copy()
    payload["exp"] = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return jwt.encode(payload, get_settings().app_secret_key, algorithm=ALGORITHM)

def verify_token(token: str) -> dict:
    try:
        return jwt.decode(token, get_settings().app_secret_key, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    api_key: Optional[str] = Depends(api_key_header)
) -> dict:
    from app.database import get_db
    if api_key:
        import hashlib
        from datetime import datetime, timezone

        db = get_db()
        if db is not None and api_key.startswith("tv_"):
            key_hash = hashlib.sha256(api_key.encode()).hexdigest()
            record = await db.api_keys.find_one({"key_hash": key_hash, "active": True})
            if record:
                # Update last_used
                await db.api_keys.update_one(
                    {"key_hash": key_hash},
                    {"$set": {"last_used": datetime.now(timezone.utc).isoformat()}}
                )
                return {
                    "sub": record["user_id"],
                    "email": record["user_id"],
                    "name": record.get("user_name", ""),
                    "id": str(record.get("_id", "")) # Provide a generic id to prevent breakage
                }
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API Key",
        )

    if token:
        try:
            payload = verify_token(token)
            db = get_db()
            if db is not None:
                user = await db.users.find_one({"email": payload.get("sub", "").lower()})
                if user and not user.get("is_active", True):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Account is deactivated. Contact Support.",
                    )
            return payload
        except HTTPException as e:
            if getattr(e, "status_code", None) == 403:
                raise e
            pass
        except Exception as e:
            print(f"Token verification error: {e}")
            pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated. Provide a valid Bearer token or X-API-Key.",
        headers={"WWW-Authenticate": "Bearer"},
    )

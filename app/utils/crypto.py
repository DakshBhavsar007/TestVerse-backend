"""
app/utils/crypto.py — Fernet credential encryption
pip install cryptography
Generate key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
Set as CREDENTIAL_ENCRYPTION_KEY in .env
"""
from app.config import get_settings

try:
    from cryptography.fernet import Fernet, InvalidToken
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


def _fernet():
    key = get_settings().credential_encryption_key
    if not key or not _AVAILABLE:
        return None
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        return None


def encrypt_credential(plain: str) -> str:
    f = _fernet()
    if f is None:
        # Dev fallback — warn but don't crash
        if get_settings().environment != "production":
            return plain
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY not set — required in production")
    return f.encrypt(plain.encode()).decode()


def decrypt_credential(value: str) -> str:
    f = _fernet()
    if f is None:
        return value  # dev: was never encrypted
    try:
        return f.decrypt(value.encode()).decode()
    except InvalidToken:
        raise ValueError("Credential decryption failed — wrong key or tampered token")


def scrub(value: str) -> None:
    del value

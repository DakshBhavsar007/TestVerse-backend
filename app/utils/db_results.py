"""
app/utils/db_results.py — MongoDB persistence for test results.
Automatically falls back to in-memory dict when MongoDB is unavailable.
Phase 3: share_token auto-generated on every save.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional
from app.database import get_db

_mem: dict = {}  # in-memory fallback


def _clean(doc: dict) -> dict:
    if doc and "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


async def save_result(test_id: str, data: dict) -> None:
    data["test_id"] = test_id
    data["saved_at"] = datetime.now(timezone.utc).isoformat()

    # ── Phase 3: ensure share_token exists ────────────────────────────────────
    if not data.get("share_token"):
        data["share_token"] = str(uuid.uuid4())

    db = get_db()
    if db is not None:
        await db.test_results.replace_one({"test_id": test_id}, data, upsert=True)
    else:
        _mem[test_id] = data


async def get_result(test_id: str) -> Optional[dict]:
    db = get_db()
    if db is not None:
        doc = await db.test_results.find_one({"test_id": test_id})
        return _clean(doc) if doc else None
    return _mem.get(test_id)


async def get_result_by_share_token(token: str) -> Optional[dict]:
    """Phase 3: Look up a result by its public share token."""
    db = get_db()
    if db is not None:
        doc = await db.test_results.find_one({"share_token": token})
        return _clean(doc) if doc else None
    # In-memory fallback
    for result in _mem.values():
        if result.get("share_token") == token:
            return result
    return None


async def list_results(user_id: Optional[str] = None, limit: int = 20) -> list:
    db = get_db()
    if db is not None:
        query = {"user_id": user_id} if user_id else {}
        cursor = db.test_results.find(query).sort("saved_at", -1).limit(limit)
        return [_clean(doc) async for doc in cursor]
    results = list(_mem.values())
    if user_id:
        results = [r for r in results if r.get("user_id") == user_id]
    return sorted(results, key=lambda r: r.get("saved_at", ""), reverse=True)[:limit]


async def delete_result(test_id: str) -> bool:
    db = get_db()
    if db is not None:
        res = await db.test_results.delete_one({"test_id": test_id})
        return res.deleted_count > 0
    if test_id in _mem:
        del _mem[test_id]
        return True
    return False
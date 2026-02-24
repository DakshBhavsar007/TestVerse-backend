"""
app/routers/share_router.py
Phase 3 — Public share endpoint. No authentication required.
Serves a stripped-down result payload via share token.
"""
from fastapi import APIRouter, HTTPException
from app.utils.db_results import get_result_by_share_token

router = APIRouter(prefix="/share", tags=["Share"])

# Fields to EXCLUDE from the public payload (strip sensitive user info)
_PRIVATE_FIELDS = {"user_id", "saved_at", "_id"}


def _public_result(result: dict) -> dict:
    return {k: v for k, v in result.items() if k not in _PRIVATE_FIELDS}


@router.get("/{token}")
async def get_shared_result(token: str):
    """
    Public endpoint — no auth.
    Returns a sanitised test result for the given share token.
    """
    result = await get_result_by_share_token(token)
    if not result:
        raise HTTPException(status_code=404, detail="Shared report not found or link is invalid.")
    return _public_result(result)

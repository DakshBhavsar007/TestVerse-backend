"""
History router — list and delete past test results.
Phase 4: Added /history/trend and /history/urls endpoints.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from ..utils.db_results import delete_result, list_results, get_result
from ..utils.auth import get_current_user

router = APIRouter(prefix="/history", tags=["History"])


@router.get("/urls")
async def get_tested_urls(current_user: dict = Depends(get_current_user)):
    """
    Phase 4 — Returns unique URLs the user has tested.
    Used by the Trends page URL picker dropdown.
    """
    all_results = await list_results(user_id=current_user.get("sub"), limit=500)
    seen = {}
    for r in all_results:
        url = r.get("url")
        if not url:
            continue
        if url not in seen:
            seen[url] = {
                "url": url,
                "last_score": r.get("overall_score"),
                "last_tested": (r.get("started_at") or r.get("saved_at") or "")[:10],
                "test_count": 0,
            }
        seen[url]["test_count"] += 1
    return {"urls": list(seen.values())}


@router.get("/trend")
async def get_trend(
    url: str = Query(..., description="URL to get score trend for"),
    limit: int = Query(30, le=100),
    current_user: dict = Depends(get_current_user),
):
    """
    Phase 4 — Returns score history for a specific URL, oldest→newest.
    Used by TrendChart in Trends.jsx.
    """
    all_results = await list_results(user_id=current_user.get("sub"), limit=500)

    url_norm = url.rstrip("/").lower()
    filtered = [
        r for r in all_results
        if r.get("url", "").rstrip("/").lower() == url_norm
        and r.get("overall_score") is not None
        and r.get("status") == "completed"
    ]

    # Sort oldest first for chart display
    filtered.sort(key=lambda r: r.get("started_at") or r.get("saved_at") or "")
    filtered = filtered[-limit:]

    trend = [
        {
            "test_id": r["test_id"],
            "score":   r["overall_score"],
            "date":    (r.get("started_at") or r.get("saved_at") or "")[:16].replace("T", " "),
        }
        for r in filtered
    ]

    return {"url": url, "trend": trend, "total": len(trend)}


@router.get("/")
async def list_tests(
    limit: int = Query(20, ge=1, le=100),
    skip: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
):
    """List all past test results for the current user."""
    results = await list_results(user_id=current_user.get("sub"))
    total = len(results)
    paged = results[skip: skip + limit]
    return {"success": True, "total": total, "results": paged}


@router.delete("/{test_id}")
async def delete_test(
    test_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Delete a test result by ID (must be the owner)."""
    result = await get_result(test_id)
    if not result:
        raise HTTPException(status_code=404, detail="Test not found")
    if result.get("user_id") != current_user.get("sub"):
        raise HTTPException(status_code=403, detail="Not your test result")
    await delete_result(test_id)
    return {"success": True, "message": f"Test {test_id} deleted"}
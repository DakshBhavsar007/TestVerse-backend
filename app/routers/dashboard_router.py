"""
app/routers/dashboard_router.py
Provides aggregated stats for the Dashboard page.
"""
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from fastapi import APIRouter, Depends

from app.utils.auth import get_current_user
from app.utils.db_results import list_results

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


def _score_band(score):
    if score is None:
        return "unknown"
    if score >= 80:
        return "excellent"
    if score >= 60:
        return "good"
    if score >= 40:
        return "fair"
    return "poor"


@router.get("/stats")
async def dashboard_stats(current_user: dict = Depends(get_current_user)):
    """
    Returns aggregated stats for the authenticated user's dashboard:
    - total tests, completed, failed, running
    - average overall score
    - score distribution (excellent/good/fair/poor)
    - top tested URLs (by frequency)
    - recent 5 tests
    - tests over time (last 30 days, grouped by day)
    """
    user_id = current_user.get("sub")
    results = await list_results(user_id=user_id)

    total = len(results)
    completed = [r for r in results if r.get("status") == "completed"]
    failed    = [r for r in results if r.get("status") == "failed"]
    running   = [r for r in results if r.get("status") == "running"]

    # Average score (only completed with a score)
    scored = [r for r in completed if r.get("overall_score") is not None]
    avg_score = round(sum(r["overall_score"] for r in scored) / len(scored)) if scored else None

    # Best and worst scoring URLs
    best = max(scored, key=lambda r: r["overall_score"], default=None)
    worst = min(scored, key=lambda r: r["overall_score"], default=None)

    # Score distribution
    distribution = defaultdict(int)
    for r in scored:
        distribution[_score_band(r["overall_score"])] += 1

    # Top URLs by test count
    url_counts = defaultdict(int)
    url_latest_score = {}
    for r in results:
        u = r.get("url", "")
        if u:
            url_counts[u] += 1
            if r.get("overall_score") is not None:
                url_latest_score[u] = r["overall_score"]

    top_urls = sorted(url_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    top_urls_data = [
        {"url": url, "count": count, "latest_score": url_latest_score.get(url)}
        for url, count in top_urls
    ]

    # Recent 5 tests (already sorted newest-first by list_results)
    recent = results[:5]
    recent_data = [
        {
            "test_id": r.get("test_id"),
            "url": r.get("url"),
            "status": r.get("status"),
            "overall_score": r.get("overall_score"),
            "started_at": r.get("started_at"),
        }
        for r in recent
    ]

    # Tests per day — last 30 days
    now = datetime.now(timezone.utc)
    day_counts = defaultdict(int)
    day_avg_score = defaultdict(list)

    for r in results:
        started = r.get("started_at")
        if not started:
            continue
        try:
            dt = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            if (now - dt).days <= 30:
                day_key = dt.strftime("%Y-%m-%d")
                day_counts[day_key] += 1
                if r.get("overall_score") is not None:
                    day_avg_score[day_key].append(r["overall_score"])
        except Exception:
            continue

    # Fill all 30 days (so chart has no gaps)
    timeline = []
    for i in range(29, -1, -1):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        scores_that_day = day_avg_score.get(day, [])
        timeline.append({
            "date": day,
            "tests": day_counts.get(day, 0),
            "avg_score": round(sum(scores_that_day) / len(scores_that_day)) if scores_that_day else None,
        })

    return {
        "total": total,
        "completed": len(completed),
        "failed": len(failed),
        "running": len(running),
        "avg_score": avg_score,
        "best": {"url": best.get("url"), "score": best.get("overall_score")} if best else None,
        "worst": {"url": worst.get("url"), "score": worst.get("overall_score")} if worst else None,
        "score_distribution": dict(distribution),
        "top_urls": top_urls_data,
        "recent_tests": recent_data,
        "timeline": timeline,
    }

"""
app/routers/reporting_router.py
Phase 7B/C: Advanced Reporting
- CSV/JSON export of test history
- Custom dashboard configurations (saved widget layouts)
- Scheduled report delivery via email/webhook
"""
import uuid, csv, io, json
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from app.database import get_db
from app.utils.auth import get_current_user

router = APIRouter(prefix="/reporting", tags=["Reporting"])


# ─── Models ────────────────────────────────────────────────────────────────────

class DashboardWidget(BaseModel):
    widget_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: str          # score_ring | bar_chart | stat_card | table | trend_line
    title: str
    metric: str        # avg_score | uptime | tests_run | failures | response_time
    time_range_days: int = 7
    url_filter: Optional[str] = None
    position: dict = Field(default_factory=lambda: {"x": 0, "y": 0, "w": 4, "h": 2})


class SaveDashboardRequest(BaseModel):
    name: str
    widgets: List[DashboardWidget]
    is_default: bool = False


class ScheduledReportRequest(BaseModel):
    name: str
    cron: str = "0 9 * * 1"          # Default: Monday 9am
    format: str = "csv"               # csv | json | pdf
    delivery: str = "email"           # email | webhook
    destination: str                  # email address or webhook URL
    url_filter: Optional[str] = None
    include_sections: List[str] = Field(default_factory=lambda: [
        "summary", "top_failures", "score_trends", "ssl_expiry"
    ])


# ─── Export endpoints ──────────────────────────────────────────────────────────

@router.get("/export/csv")
async def export_csv(
    days: int = Query(30, ge=1, le=365),
    url_filter: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """Export test history as CSV."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")

    query = {"user_id": user_id}
    if url_filter:
        query["url"] = {"$regex": url_filter, "$options": "i"}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query["created_at"] = {"$gte": cutoff}

    if db:
        results = await db.test_results.find(query).sort("created_at", -1).to_list(5000)
    else:
        results = []

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "test_id", "url", "score", "status", "ttfb_ms", "load_time_ms",
        "ssl_valid", "ssl_days_remaining", "broken_links", "js_errors",
        "missing_images", "mobile_ok", "created_at"
    ])
    for r in results:
        writer.writerow([
            r.get("test_id", ""),
            r.get("url", ""),
            r.get("score", ""),
            r.get("status", ""),
            r.get("ttfb_ms", ""),
            r.get("load_time_ms", ""),
            r.get("ssl_valid", ""),
            r.get("ssl_days_remaining", ""),
            len(r.get("broken_links", [])),
            len(r.get("js_errors", [])),
            len(r.get("missing_images", [])),
            r.get("mobile_ok", ""),
            r.get("created_at", ""),
        ])

    output.seek(0)
    filename = f"testverse_export_{datetime.now().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/export/json")
async def export_json(
    days: int = Query(30, ge=1, le=365),
    url_filter: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user)
):
    """Export test history as JSON."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")

    query = {"user_id": user_id}
    if url_filter:
        query["url"] = {"$regex": url_filter, "$options": "i"}
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query["created_at"] = {"$gte": cutoff}

    results = await db.test_results.find(query).sort("created_at", -1).to_list(5000) if db else []

    # Sanitize _id
    clean = []
    for r in results:
        r.pop("_id", None)
        if isinstance(r.get("created_at"), datetime):
            r["created_at"] = r["created_at"].isoformat()
        clean.append(r)

    payload = json.dumps({"exported_at": datetime.now().isoformat(), "count": len(clean), "results": clean})
    filename = f"testverse_export_{datetime.now().strftime('%Y%m%d')}.json"
    return StreamingResponse(
        iter([payload]),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/summary")
async def get_report_summary(
    days: int = Query(30, ge=1, le=365),
    current_user: dict = Depends(get_current_user)
):
    """Get aggregated summary stats for reporting."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")

    if db is None:
        return {"success": True, "summary": {}}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    results = await db.test_results.find({
        "user_id": user_id,
        "created_at": {"$gte": cutoff}
    }).to_list(5000)

    if not results:
        return {"success": True, "summary": {"total": 0}}

    scores = [r["score"] for r in results if r.get("score") is not None]
    failures = [r for r in results if r.get("status") == "failed"]
    urls = list(set(r.get("url", "") for r in results))

    # Top failing URLs
    from collections import Counter
    fail_counts = Counter(r.get("url", "") for r in failures)
    top_failures = [{"url": u, "count": c} for u, c in fail_counts.most_common(5)]

    # SSL expiring soon
    ssl_warnings = []
    for r in results:
        days_left = r.get("ssl_days_remaining")
        if days_left is not None and days_left < 30:
            ssl_warnings.append({"url": r.get("url"), "days_remaining": days_left})

    return {
        "success": True,
        "summary": {
            "total_tests": len(results),
            "unique_urls": len(urls),
            "avg_score": round(sum(scores) / len(scores), 1) if scores else None,
            "min_score": min(scores) if scores else None,
            "max_score": max(scores) if scores else None,
            "failure_rate": round(len(failures) / len(results) * 100, 1),
            "top_failures": top_failures,
            "ssl_warnings": ssl_warnings[:5],
            "period_days": days,
        }
    }


# ─── Custom Dashboards ────────────────────────────────────────────────────────

@router.post("/dashboards")
async def save_dashboard(
    req: SaveDashboardRequest,
    current_user: dict = Depends(get_current_user)
):
    """Save a custom dashboard layout."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")

    user_id = current_user.get("id") or current_user.get("sub")
    dashboard_id = str(uuid.uuid4())

    # If marking as default, unset others
    if req.is_default:
        await db.custom_dashboards.update_many(
            {"user_id": user_id},
            {"$set": {"is_default": False}}
        )

    doc = {
        "dashboard_id": dashboard_id,
        "user_id": user_id,
        "name": req.name,
        "widgets": [w.dict() for w in req.widgets],
        "is_default": req.is_default,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    await db.custom_dashboards.insert_one(doc)

    return {"success": True, "dashboard_id": dashboard_id, "message": f"Dashboard '{req.name}' saved"}


@router.get("/dashboards")
async def list_dashboards(current_user: dict = Depends(get_current_user)):
    """List all saved dashboards."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")
    if db is None:
        return {"success": True, "dashboards": []}

    docs = await db.custom_dashboards.find({"user_id": user_id}).sort("created_at", -1).to_list(50)
    return {
        "success": True,
        "dashboards": [
            {
                "dashboard_id": d["dashboard_id"],
                "name": d["name"],
                "widget_count": len(d.get("widgets", [])),
                "is_default": d.get("is_default", False),
                "created_at": d["created_at"].isoformat(),
            }
            for d in docs
        ]
    }


@router.get("/dashboards/{dashboard_id}")
async def get_dashboard(dashboard_id: str, current_user: dict = Depends(get_current_user)):
    """Get a specific dashboard with full widget config."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")
    if db is None:
        raise HTTPException(500, "Database not available")

    doc = await db.custom_dashboards.find_one({"dashboard_id": dashboard_id, "user_id": user_id})
    if not doc:
        raise HTTPException(404, "Dashboard not found")

    doc.pop("_id", None)
    doc["created_at"] = doc["created_at"].isoformat()
    return {"success": True, "dashboard": doc}


@router.delete("/dashboards/{dashboard_id}")
async def delete_dashboard(dashboard_id: str, current_user: dict = Depends(get_current_user)):
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")
    if db:
        await db.custom_dashboards.delete_one({"dashboard_id": dashboard_id, "user_id": user_id})
    return {"success": True, "message": "Dashboard deleted"}


# ─── Scheduled Reports ────────────────────────────────────────────────────────

@router.post("/scheduled-reports")
async def create_scheduled_report(
    req: ScheduledReportRequest,
    current_user: dict = Depends(get_current_user)
):
    """Create a scheduled report delivery."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")

    user_id = current_user.get("id") or current_user.get("sub")
    report_id = str(uuid.uuid4())

    doc = {
        "report_id": report_id,
        "user_id": user_id,
        "name": req.name,
        "cron": req.cron,
        "format": req.format,
        "delivery": req.delivery,
        "destination": req.destination,
        "url_filter": req.url_filter,
        "include_sections": req.include_sections,
        "enabled": True,
        "last_sent": None,
        "send_count": 0,
        "created_at": datetime.now(timezone.utc),
    }
    await db.scheduled_reports.insert_one(doc)

    return {"success": True, "report_id": report_id, "message": f"Scheduled report '{req.name}' created"}


@router.get("/scheduled-reports")
async def list_scheduled_reports(current_user: dict = Depends(get_current_user)):
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")
    if db is None:
        return {"success": True, "reports": []}

    docs = await db.scheduled_reports.find({"user_id": user_id}).to_list(50)
    return {
        "success": True,
        "reports": [
            {
                "report_id": d["report_id"],
                "name": d["name"],
                "cron": d["cron"],
                "format": d["format"],
                "delivery": d["delivery"],
                "destination": d["destination"],
                "enabled": d.get("enabled", True),
                "last_sent": d["last_sent"].isoformat() if d.get("last_sent") else None,
                "send_count": d.get("send_count", 0),
            }
            for d in docs
        ]
    }


@router.delete("/scheduled-reports/{report_id}")
async def delete_scheduled_report(report_id: str, current_user: dict = Depends(get_current_user)):
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")
    if db:
        await db.scheduled_reports.delete_one({"report_id": report_id, "user_id": user_id})
    return {"success": True, "message": "Scheduled report deleted"}

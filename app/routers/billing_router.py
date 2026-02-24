"""
app/routers/billing_router.py
Phase 7B/C: Cost Management & Billing
- Usage tracking per user/team
- Plan tiers (Free, Pro, Enterprise)
- Rate limiting enforcement by tier
- Overage alerts
"""
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app.database import get_db
from app.utils.auth import get_current_user

router = APIRouter(prefix="/billing", tags=["Billing"])


# ─── Plan Definitions ──────────────────────────────────────────────────────────

PLANS = {
    "free": {
        "name": "Free",
        "price_usd": 0,
        "limits": {
            "tests_per_month": 50,
            "schedules": 2,
            "team_members": 1,
            "api_keys": 1,
            "bulk_urls_per_batch": 5,
            "monitors": 2,
            "report_history_days": 7,
            "crawl_pages": 10,
        },
        "features": ["basic_tests", "history", "pdf_export"],
    },
    "pro": {
        "name": "Pro",
        "price_usd": 29,
        "limits": {
            "tests_per_month": 1000,
            "schedules": 20,
            "team_members": 5,
            "api_keys": 10,
            "bulk_urls_per_batch": 50,
            "monitors": 20,
            "report_history_days": 90,
            "crawl_pages": 100,
        },
        "features": ["basic_tests", "history", "pdf_export", "teams", "slack",
                     "bulk", "schedules", "api_keys", "notifications", "templates"],
    },
    "enterprise": {
        "name": "Enterprise",
        "price_usd": 99,
        "limits": {
            "tests_per_month": -1,        # unlimited
            "schedules": -1,
            "team_members": -1,
            "api_keys": -1,
            "bulk_urls_per_batch": 500,
            "monitors": -1,
            "report_history_days": 365,
            "crawl_pages": 500,
        },
        "features": ["all", "rbac", "audit_logs", "sla", "whitelabel",
                     "compliance", "advanced_reporting", "custom_dashboards"],
    },
}


# ─── Models ────────────────────────────────────────────────────────────────────

class ChangePlanRequest(BaseModel):
    plan: str = Field(..., pattern="^(free|pro|enterprise)$")


class UsageRecord(BaseModel):
    resource: str   # tests | schedules | api_keys | monitors | bulk_jobs
    count: int = 1


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def get_user_plan(user_id: str) -> str:
    db = get_db()
    if db is None:
        return "pro"  # dev mode
    sub = await db.subscriptions.find_one({"user_id": user_id})
    return sub["plan"] if sub else "free"


async def get_monthly_usage(user_id: str, db) -> dict:
    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    usage_doc = await db.usage_records.find_one({
        "user_id": user_id,
        "month": month_start.strftime("%Y-%m")
    })
    return usage_doc.get("usage", {}) if usage_doc else {}


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/plans")
async def list_plans():
    """List all available plans and their limits."""
    return {"success": True, "plans": PLANS}


@router.get("/my-plan")
async def get_my_plan(current_user: dict = Depends(get_current_user)):
    """Get current user's plan, usage, and limits."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")

    plan_name = await get_user_plan(user_id)
    plan = PLANS[plan_name]

    usage = {}
    if db:
        usage = await get_monthly_usage(user_id, db)

        # Also get live counts from DB for accuracy
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        tests_this_month = await db.test_results.count_documents({
            "user_id": user_id,
            "created_at": {"$gte": month_start}
        })
        schedules_count = await db.schedules.count_documents({"user_id": user_id})
        api_keys_count = await db.api_keys.count_documents({"user_id": user_id, "active": True})
        monitors_count = await db.monitors.count_documents({"user_id": user_id, "enabled": True})

        usage = {
            "tests_this_month": tests_this_month,
            "schedules": schedules_count,
            "api_keys": api_keys_count,
            "monitors": monitors_count,
        }

    # Calculate percentage used for each limit
    usage_pct = {}
    for resource, used in usage.items():
        limit_key = resource if resource in plan["limits"] else None
        if limit_key:
            limit = plan["limits"][limit_key]
            usage_pct[resource] = {
                "used": used,
                "limit": limit,
                "pct": round(used / limit * 100, 1) if limit > 0 else 0,
                "unlimited": limit == -1,
            }

    return {
        "success": True,
        "plan": plan_name,
        "plan_details": plan,
        "usage": usage_pct,
        "billing_period": datetime.now(timezone.utc).strftime("%B %Y"),
    }


@router.post("/change-plan")
async def change_plan(
    req: ChangePlanRequest,
    current_user: dict = Depends(get_current_user)
):
    """Change user's subscription plan."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")

    user_id = current_user.get("id") or current_user.get("sub")
    current_plan = await get_user_plan(user_id)

    if req.plan == current_plan:
        raise HTTPException(400, f"Already on {req.plan} plan")

    await db.subscriptions.replace_one(
        {"user_id": user_id},
        {
            "user_id": user_id,
            "plan": req.plan,
            "previous_plan": current_plan,
            "changed_at": datetime.now(timezone.utc),
            "billing_start": datetime.now(timezone.utc),
        },
        upsert=True
    )

    # Log the plan change
    await db.billing_events.insert_one({
        "event_id": str(uuid.uuid4()),
        "user_id": user_id,
        "type": "plan_change",
        "from_plan": current_plan,
        "to_plan": req.plan,
        "timestamp": datetime.now(timezone.utc),
    })

    return {
        "success": True,
        "message": f"Plan changed from {current_plan} to {req.plan}",
        "new_plan": PLANS[req.plan],
    }


@router.get("/usage-history")
async def get_usage_history(
    months: int = 3,
    current_user: dict = Depends(get_current_user)
):
    """Get usage history over the last N months."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")

    if db is None:
        return {"success": True, "history": []}

    history = []
    now = datetime.now(timezone.utc)
    for i in range(months):
        month_dt = now - timedelta(days=30 * i)
        month_start = month_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_key = month_start.strftime("%Y-%m")

        tests_count = await db.test_results.count_documents({
            "user_id": user_id,
            "created_at": {"$gte": month_start, "$lt": month_start + timedelta(days=32)}
        })
        history.append({
            "month": month_start.strftime("%B %Y"),
            "month_key": month_key,
            "tests_run": tests_count,
        })

    return {"success": True, "history": list(reversed(history))}


@router.get("/invoices")
async def list_invoices(current_user: dict = Depends(get_current_user)):
    """List billing invoices/events."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")

    if db is None:
        return {"success": True, "invoices": []}

    events = await db.billing_events.find({"user_id": user_id}).sort("timestamp", -1).to_list(24)
    return {
        "success": True,
        "invoices": [
            {
                "event_id": e["event_id"],
                "type": e["type"],
                "from_plan": e.get("from_plan"),
                "to_plan": e.get("to_plan"),
                "timestamp": e["timestamp"].isoformat(),
            }
            for e in events
        ]
    }


@router.get("/check-limit/{resource}")
async def check_limit(
    resource: str,
    current_user: dict = Depends(get_current_user)
):
    """Check if user has exceeded their plan limit for a resource."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")
    plan_name = await get_user_plan(user_id)
    plan = PLANS[plan_name]

    limit = plan["limits"].get(resource)
    if limit is None:
        raise HTTPException(404, f"Unknown resource: {resource}")

    if limit == -1:
        return {"success": True, "allowed": True, "unlimited": True}

    # Get current count
    current = 0
    if db:
        if resource == "tests_per_month":
            month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            current = await db.test_results.count_documents({"user_id": user_id, "created_at": {"$gte": month_start}})
        elif resource == "schedules":
            current = await db.schedules.count_documents({"user_id": user_id})
        elif resource == "api_keys":
            current = await db.api_keys.count_documents({"user_id": user_id, "active": True})
        elif resource == "monitors":
            current = await db.monitors.count_documents({"user_id": user_id, "enabled": True})

    allowed = current < limit
    return {
        "success": True,
        "allowed": allowed,
        "current": current,
        "limit": limit,
        "plan": plan_name,
        "upgrade_required": not allowed,
    }

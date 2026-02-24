"""
app/routers/schedule_router.py
CRUD endpoints for managing scheduled tests.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.utils.auth import get_current_user
from app.database import get_db
from app.services.scheduler import (
    add_schedule_job, remove_schedule_job, INTERVAL_OPTIONS
)

router = APIRouter(prefix="/schedules", tags=["Schedules"])


class CreateScheduleRequest(BaseModel):
    url: str
    name: Optional[str] = None           # friendly label, defaults to URL
    interval: str = "daily"              # "6h" | "daily" | "weekly"
    notify_email: Optional[str] = None   # override; defaults to account email


class UpdateScheduleRequest(BaseModel):
    name: Optional[str] = None
    interval: Optional[str] = None
    active: Optional[bool] = None
    notify_email: Optional[str] = None


def _fmt(schedule: dict) -> dict:
    """Strip MongoDB _id before returning to client."""
    schedule.pop("_id", None)
    return schedule


@router.get("")
async def list_schedules(current_user: dict = Depends(get_current_user)):
    db = get_db()
    if db is None:
        return {"schedules": []}
    user_id = current_user.get("sub")
    cursor = db.schedules.find({"user_id": user_id}).sort("created_at", -1)
    schedules = [_fmt(s) async for s in cursor]
    return {"schedules": schedules, "total": len(schedules)}


@router.post("")
async def create_schedule(req: CreateScheduleRequest,
                           current_user: dict = Depends(get_current_user)):
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    if req.interval not in INTERVAL_OPTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid interval. Choose from: {list(INTERVAL_OPTIONS.keys())}"
        )

    user_id = current_user.get("sub")
    user_email = current_user.get("email", "")

    # Check if schedule already exists for this URL + user
    existing = await db.schedules.find_one({"url": req.url, "user_id": user_id})
    if existing:
        raise HTTPException(
            status_code=409,
            detail="A schedule already exists for this URL. Update it instead."
        )

    schedule_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    doc = {
        "schedule_id": schedule_id,
        "user_id": user_id,
        "user_email": req.notify_email or user_email,
        "url": req.url,
        "name": req.name or req.url,
        "interval": req.interval,
        "active": True,
        "created_at": now,
        "last_run": None,
        "last_score": None,
        "last_test_id": None,
        "run_count": 0,
    }

    await db.schedules.insert_one(doc)
    doc.pop("_id", None)

    # Register with APScheduler
    interval_hours = INTERVAL_OPTIONS[req.interval]
    add_schedule_job(schedule_id, interval_hours)

    return {"success": True, "schedule": doc}


@router.get("/{schedule_id}")
async def get_schedule(schedule_id: str,
                        current_user: dict = Depends(get_current_user)):
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    schedule = await db.schedules.find_one({"schedule_id": schedule_id})
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if schedule.get("user_id") != current_user.get("sub"):
        raise HTTPException(status_code=403, detail="Not your schedule")

    return _fmt(schedule)


@router.patch("/{schedule_id}")
async def update_schedule(schedule_id: str, req: UpdateScheduleRequest,
                           current_user: dict = Depends(get_current_user)):
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    schedule = await db.schedules.find_one({"schedule_id": schedule_id})
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if schedule.get("user_id") != current_user.get("sub"):
        raise HTTPException(status_code=403, detail="Not your schedule")

    if req.interval and req.interval not in INTERVAL_OPTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid interval. Choose from: {list(INTERVAL_OPTIONS.keys())}"
        )

    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()

    await db.schedules.update_one({"schedule_id": schedule_id}, {"$set": updates})

    # Re-register job if interval or active status changed
    if "interval" in updates or "active" in updates:
        if updates.get("active") is False:
            remove_schedule_job(schedule_id)
        else:
            interval = updates.get("interval", schedule.get("interval", "daily"))
            interval_hours = INTERVAL_OPTIONS[interval]
            add_schedule_job(schedule_id, interval_hours)

    updated = await db.schedules.find_one({"schedule_id": schedule_id})
    return {"success": True, "schedule": _fmt(updated)}


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: str,
                           current_user: dict = Depends(get_current_user)):
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    schedule = await db.schedules.find_one({"schedule_id": schedule_id})
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if schedule.get("user_id") != current_user.get("sub"):
        raise HTTPException(status_code=403, detail="Not your schedule")

    await db.schedules.delete_one({"schedule_id": schedule_id})
    remove_schedule_job(schedule_id)

    return {"success": True, "deleted": schedule_id}


@router.post("/{schedule_id}/run-now")
async def run_now(schedule_id: str,
                  current_user: dict = Depends(get_current_user)):
    """Trigger a scheduled test immediately (outside its normal interval)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    schedule = await db.schedules.find_one({"schedule_id": schedule_id})
    if not schedule:
        raise HTTPException(status_code=404, detail="Schedule not found")
    if schedule.get("user_id") != current_user.get("sub"):
        raise HTTPException(status_code=403, detail="Not your schedule")

    import asyncio
    from app.services.scheduler import _run_scheduled_test
    asyncio.create_task(_run_scheduled_test(schedule_id))

    return {"success": True, "message": "Test triggered — check history shortly"}

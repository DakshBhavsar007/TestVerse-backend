"""
app/services/scheduler.py
Manages scheduled tests using APScheduler (AsyncIOScheduler).
Schedules are stored in MongoDB and reloaded on startup.
"""
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

scheduler = AsyncIOScheduler(timezone="UTC")

# Interval options (hours)
INTERVAL_OPTIONS = {
    "6h":     6,
    "daily":  24,
    "weekly": 168,
}


# ── Trigger a test run ─────────────────────────────────────────────────────────
async def _run_scheduled_test(schedule_id: str):
    """Called by APScheduler — fetches schedule, runs test, sends email."""
    from app.database import get_db
    from app.utils.db_results import save_result, get_result
    from app.services.score_calculator import score_and_summarize
    from app.services.email_service import (
        send_scheduled_complete, send_test_failed, send_score_drop
    )
    from app.config import get_settings

    settings = get_settings()
    db = get_db()
    if db is None:
        return

    # Load schedule
    schedule = await db.schedules.find_one({"schedule_id": schedule_id, "active": True})
    if not schedule:
        return

    url = schedule["url"]
    user_email = schedule.get("user_email", "")
    schedule_name = schedule.get("name", url)
    app_url = settings.app_url

    # Get previous score for drop detection
    prev_score = schedule.get("last_score")

    # Build a test_id and run the test inline
    tid = str(uuid.uuid4())

    # Import and run the full test suite
    try:
        from app.routers.test_router import _run_all
        # We don't have credentials for scheduled basic tests
        await _run_all(tid, url, username=None, enc_pw=None,
                       user_id=schedule.get("user_id"))

        # Wait for it to complete (it saves to db)
        for _ in range(120):  # up to 2 minutes
            await asyncio.sleep(1)
            result = await get_result(tid)
            if result and result.get("status") in ("completed", "failed"):
                break

        result = await get_result(tid)
        if not result:
            return

        new_score = result.get("overall_score")
        summary = result.get("summary")
        status = result.get("status")

        # Update schedule record
        await db.schedules.update_one(
            {"schedule_id": schedule_id},
            {"$set": {
                "last_run": datetime.now(timezone.utc).isoformat(),
                "last_score": new_score,
                "last_test_id": tid,
                "run_count": schedule.get("run_count", 0) + 1,
            }}
        )

        # Send emails
        if user_email:
            if status == "failed":
                await send_test_failed(
                    user_email, url,
                    result.get("error", "Unknown error"), tid, app_url
                )
            else:
                # Always send scheduled complete email
                await send_scheduled_complete(
                    user_email, url, new_score, summary, tid, schedule_name, app_url
                )
                # Also send score drop alert if applicable
                if (prev_score is not None and new_score is not None
                        and new_score < prev_score - 5):
                    await send_score_drop(
                        user_email, url, prev_score, new_score, tid, app_url
                    )

    except Exception as e:
        print(f"❌ Scheduled test failed for {url}: {e}")
        if user_email:
            await send_test_failed(user_email, url, str(e), tid, app_url)


# ── Public API ─────────────────────────────────────────────────────────────────

def add_schedule_job(schedule_id: str, interval_hours: int):
    """Add or replace an APScheduler job for a schedule."""
    job_id = f"schedule_{schedule_id}"
    # Remove existing job if any
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    scheduler.add_job(
        _run_scheduled_test,
        trigger=IntervalTrigger(hours=interval_hours),
        args=[schedule_id],
        id=job_id,
        replace_existing=True,
        misfire_grace_time=300,  # 5 min grace
    )
    print(f"✅ Scheduled job added: {job_id} every {interval_hours}h")


def remove_schedule_job(schedule_id: str):
    job_id = f"schedule_{schedule_id}"
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)
        print(f"🗑️  Removed scheduled job: {job_id}")


async def load_schedules_from_db():
    """On startup: reload all active schedules from MongoDB into APScheduler."""
    from app.database import get_db
    db = get_db()
    if db is None:
        return

    count = 0
    async for schedule in db.schedules.find({"active": True}):
        interval_hours = INTERVAL_OPTIONS.get(schedule.get("interval", "daily"), 24)
        add_schedule_job(schedule["schedule_id"], interval_hours)
        count += 1

    print(f"✅ Loaded {count} scheduled test(s) from database")


def start_scheduler():
    if not scheduler.running:
        scheduler.start()
        print("✅ APScheduler started")


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        print("❌ APScheduler stopped")

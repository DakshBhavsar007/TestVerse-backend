"""
app/services/notification_service.py
Called after every test run to decide which emails to send.
Handles: test complete, score drop, test failed triggers.
"""
from typing import Optional
from app.config import get_settings

settings = get_settings()


async def notify_on_complete(
    user_id: str,
    url: str,
    test_id: str,
    status: str,
    score: Optional[int],
    summary: Optional[str],
    error: Optional[str] = None,
):
    """
    Main entry point — called from test_router after every test finishes.
    Looks up the user's email, finds previous score, sends appropriate emails.
    """
    if not settings.sendgrid_api_key:
        return  # Email not configured, skip silently

    try:
        from app.database import get_db
        from app.utils.db_results import list_results
        from app.services.email_service import (
            send_test_complete, send_test_failed, send_score_drop
        )

        db = get_db()
        app_url = settings.app_url

        # Get user email
        user_email = None
        if db is not None and user_id:
            from bson import ObjectId
            try:
                user = await db.users.find_one({"_id": ObjectId(user_id)})
                if not user:
                    # Try sub field format
                    user = await db.users.find_one({"sub": user_id})
                if user:
                    user_email = user.get("email")
            except Exception:
                # sub might not be ObjectId
                user = await db.users.find_one({"sub": user_id})
                if user:
                    user_email = user.get("email")

        if not user_email:
            return  # Can't send without an email address

        # ── Test failed ────────────────────────────────────────────────────────
        if status == "failed":
            await send_test_failed(
                user_email, url, error or "Unknown error", test_id, app_url
            )
            return

        # ── Test complete ──────────────────────────────────────────────────────
        await send_test_complete(user_email, url, score, summary, test_id, app_url)

        # ── Score drop check ───────────────────────────────────────────────────
        # Find the previous completed test for same URL to compare scores
        if score is not None and db is not None:
            prev_tests = await list_results(user_id=user_id)
            prev_completed = [
                r for r in prev_tests
                if r.get("url") == url
                and r.get("status") == "completed"
                and r.get("overall_score") is not None
                and r.get("test_id") != test_id
            ]
            if prev_completed:
                prev_score = prev_completed[0].get("overall_score")  # most recent first
                if prev_score is not None and score < prev_score - 5:
                    await send_score_drop(
                        user_email, url, prev_score, score, test_id, app_url
                    )

    except Exception as e:
        # Never let notification errors crash the test flow
        print(f"⚠️  Notification error for test {test_id}: {e}")

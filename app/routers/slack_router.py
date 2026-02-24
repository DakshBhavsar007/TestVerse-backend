"""
app/routers/slack_router.py
Phase 5 — Slack integration: save webhook, test ping, configure notifications.

MongoDB collection: slack_configs
  { user_id, webhook_url, notify_on_complete, notify_on_score_drop,
    score_threshold, created_at, updated_at }
"""
import httpx
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ..utils.auth import get_current_user
from ..database import get_db

router = APIRouter(prefix="/slack", tags=["Slack"])

# ── Pydantic models ────────────────────────────────────────────────────────────

class SlackConfigRequest(BaseModel):
    webhook_url: str
    notify_on_complete: bool = True
    notify_on_score_drop: bool = True
    score_threshold: int = 60          # alert when score drops below this

class SlackConfigUpdate(BaseModel):
    webhook_url: Optional[str] = None
    notify_on_complete: Optional[bool] = None
    notify_on_score_drop: Optional[bool] = None
    score_threshold: Optional[int] = None

# ── Helper ─────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

async def _send_slack_message(webhook_url: str, payload: dict) -> dict:
    """Send a message to Slack webhook. Returns {ok, error}."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code == 200 and resp.text == "ok":
                return {"ok": True}
            return {"ok": False, "error": f"Slack returned {resp.status_code}: {resp.text}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/config")
async def get_slack_config(current_user: dict = Depends(get_current_user)):
    """Get current user's Slack configuration."""
    db = get_db()
    config = await db.slack_configs.find_one(
        {"user_id": current_user["sub"]}, {"_id": 0}
    )
    if not config:
        return {"configured": False, "config": None}

    # Mask the webhook URL for security (show only last 8 chars)
    safe = dict(config)
    if safe.get("webhook_url"):
        safe["webhook_url_masked"] = "••••••••••••" + safe["webhook_url"][-8:]
        safe["webhook_url"] = safe["webhook_url"]  # keep full for editing
    return {"configured": True, "config": safe}


@router.post("/config")
async def save_slack_config(
    body: SlackConfigRequest,
    current_user: dict = Depends(get_current_user),
):
    """Save or update Slack webhook configuration."""
    db = get_db()

    if not body.webhook_url.startswith("https://hooks.slack.com/"):
        raise HTTPException(
            status_code=400,
            detail="Invalid Slack webhook URL. Must start with https://hooks.slack.com/"
        )

    if body.score_threshold < 0 or body.score_threshold > 100:
        raise HTTPException(status_code=400, detail="Score threshold must be 0–100")

    now = _now()
    config = {
        "user_id": current_user["sub"],
        "webhook_url": body.webhook_url,
        "notify_on_complete": body.notify_on_complete,
        "notify_on_score_drop": body.notify_on_score_drop,
        "score_threshold": body.score_threshold,
        "updated_at": now,
    }

    existing = await db.slack_configs.find_one({"user_id": current_user["sub"]})
    if existing:
        await db.slack_configs.update_one(
            {"user_id": current_user["sub"]},
            {"$set": config}
        )
    else:
        config["created_at"] = now
        await db.slack_configs.insert_one(config)

    config.pop("_id", None)
    return {"success": True, "config": config}


@router.post("/test")
async def test_slack_webhook(current_user: dict = Depends(get_current_user)):
    """Send a test ping to the configured Slack webhook."""
    db = get_db()
    config = await db.slack_configs.find_one(
        {"user_id": current_user["sub"]}, {"_id": 0}
    )
    if not config:
        raise HTTPException(status_code=404, detail="No Slack config found. Save a webhook first.")

    payload = {
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "✅ *TestVerse — Slack connected!*\n"
                        f"Your webhook is configured for `{current_user.get('email', 'your account')}`.\n"
                        "You'll receive notifications here when tests complete or scores drop."
                    )
                }
            },
            {"type": "divider"},
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"⚙️ Notify on complete: *{'on' if config.get('notify_on_complete') else 'off'}* · "
                                f"Score drop alerts: *{'on' if config.get('notify_on_score_drop') else 'off'}* · "
                                f"Threshold: *{config.get('score_threshold', 60)}*"
                    }
                ]
            }
        ]
    }

    result = await _send_slack_message(config["webhook_url"], payload)
    if not result["ok"]:
        raise HTTPException(status_code=502, detail=f"Slack ping failed: {result['error']}")

    return {"success": True, "message": "Test ping sent to Slack!"}


@router.delete("/config")
async def delete_slack_config(current_user: dict = Depends(get_current_user)):
    """Remove Slack integration."""
    db = get_db()
    await db.slack_configs.delete_one({"user_id": current_user["sub"]})
    return {"success": True, "message": "Slack integration removed"}


# ── Utility function used by test_router when a test completes ─────────────────

async def notify_slack_on_complete(user_id: str, test_result: dict):
    """
    Called from test_router after a test finishes.
    Sends Slack notification if user has configured it.
    """
    db = get_db()
    config = await db.slack_configs.find_one({"user_id": user_id}, {"_id": 0})
    if not config or not config.get("webhook_url"):
        return

    score = test_result.get("overall_score")
    url = test_result.get("url", "Unknown URL")
    test_id = test_result.get("test_id", "")
    status = test_result.get("status", "completed")

    # Check if we should notify
    should_notify_complete = config.get("notify_on_complete", True)
    should_notify_drop = config.get("notify_on_score_drop", True)
    threshold = config.get("score_threshold", 60)

    score_dropped = score is not None and score < threshold

    if not should_notify_complete and not (should_notify_drop and score_dropped):
        return

    # Build score color emoji
    if score is None:
        score_emoji = "⚪"
        score_label = "N/A"
    elif score >= 80:
        score_emoji = "🟢"
        score_label = "Excellent"
    elif score >= 60:
        score_emoji = "🟡"
        score_label = "Good"
    elif score >= 40:
        score_emoji = "🟠"
        score_label = "Fair"
    else:
        score_emoji = "🔴"
        score_label = "Poor"

    alert_line = (
        f"\n⚠️ *Score dropped below threshold ({threshold})!*"
        if score_dropped and should_notify_drop else ""
    )

    from ..config import get_settings
    settings = get_settings()
    report_url = f"{settings.app_url}/result/{test_id}"

    payload = {
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*TestVerse — Test Complete* {score_emoji}\n"
                        f"*URL:* `{url}`\n"
                        f"*Score:* *{score if score is not None else '—'}/100* — {score_label}"
                        f"{alert_line}"
                    )
                }
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Full Report →"},
                        "url": report_url,
                        "style": "primary"
                    }
                ]
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"TestVerse · {test_id[:8]}…"}
                ]
            }
        ]
    }

    await _send_slack_message(config["webhook_url"], payload)

"""
app/routers/notifications_router.py
Phase 7A: Advanced Notifications
- Email alerts for test failures
- Webhook integrations
- Custom notification rules/thresholds
"""
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, HttpUrl, Field
from enum import Enum
from app.database import get_db
from app.utils.auth import get_current_user
import httpx
import uuid

router = APIRouter(prefix="/notifications", tags=["Notifications"])


class NotificationChannel(str, Enum):
    EMAIL = "email"
    WEBHOOK = "webhook"
    SLACK = "slack"


class NotificationTrigger(str, Enum):
    TEST_COMPLETE = "test_complete"
    TEST_FAILED = "test_failed"
    SCORE_DROP = "score_drop"
    SCORE_BELOW_THRESHOLD = "score_below_threshold"
    UPTIME_DOWN = "uptime_down"
    SSL_EXPIRING = "ssl_expiring"
    SLOW_RESPONSE = "slow_response"


class NotificationStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


# ─── Request/Response Models ───────────────────────────────────────────────────

class NotificationRule(BaseModel):
    rule_id: Optional[str] = None
    user_id: str
    team_id: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=100)
    enabled: bool = True
    
    # Trigger conditions
    trigger: NotificationTrigger
    url_pattern: Optional[str] = Field(None, description="Match URLs (regex or glob)")
    
    # Thresholds
    score_threshold: Optional[int] = Field(None, ge=0, le=100)
    response_time_ms: Optional[int] = Field(None, ge=0)
    ssl_days_threshold: Optional[int] = Field(None, ge=0)
    
    # Channels
    channels: List[NotificationChannel] = [NotificationChannel.EMAIL]
    
    # Webhooks
    webhook_url: Optional[HttpUrl] = None
    webhook_headers: Optional[Dict[str, str]] = None
    
    # Email
    email_recipients: Optional[List[str]] = None  # Additional emails
    
    # Slack
    slack_channel: Optional[str] = None
    
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CreateRuleRequest(BaseModel):
    name: str = Field(..., min_length=1)
    trigger: NotificationTrigger
    url_pattern: Optional[str] = None
    score_threshold: Optional[int] = None
    response_time_ms: Optional[int] = None
    ssl_days_threshold: Optional[int] = None
    channels: List[NotificationChannel] = [NotificationChannel.EMAIL]
    webhook_url: Optional[HttpUrl] = None
    webhook_headers: Optional[Dict[str, str]] = None
    email_recipients: Optional[List[str]] = None
    slack_channel: Optional[str] = None
    team_id: Optional[str] = None


class UpdateRuleRequest(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    trigger: Optional[NotificationTrigger] = None
    url_pattern: Optional[str] = None
    score_threshold: Optional[int] = None
    response_time_ms: Optional[int] = None
    ssl_days_threshold: Optional[int] = None
    channels: Optional[List[NotificationChannel]] = None
    webhook_url: Optional[HttpUrl] = None
    webhook_headers: Optional[Dict[str, str]] = None
    email_recipients: Optional[List[str]] = None
    slack_channel: Optional[str] = None


class NotificationLog(BaseModel):
    log_id: str
    rule_id: str
    user_id: str
    test_id: str
    trigger: NotificationTrigger
    channel: NotificationChannel
    status: NotificationStatus
    recipient: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    response: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    sent_at: datetime
    delivered_at: Optional[datetime] = None


class TestNotificationRequest(BaseModel):
    rule_id: str
    test_payload: Optional[Dict[str, Any]] = None


# ─── Helper Functions ──────────────────────────────────────────────────────────

async def send_webhook(url: str, payload: dict, headers: Optional[dict] = None) -> dict:
    """Send webhook notification."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                json=payload,
                headers=headers or {"Content-Type": "application/json"}
            )
            return {
                "success": resp.status_code in range(200, 300),
                "status_code": resp.status_code,
                "response": resp.text[:500]
            }
    except Exception as e:
        return {"success": False, "error": str(e)}


async def evaluate_rule(rule: dict, test_result: dict) -> bool:
    """Check if test result matches rule conditions."""
    trigger = rule["trigger"]
    
    # URL pattern matching
    if rule.get("url_pattern"):
        import re
        pattern = rule["url_pattern"].replace("*", ".*")
        if not re.search(pattern, test_result.get("url", "")):
            return False
    
    # Trigger-specific conditions
    if trigger == NotificationTrigger.TEST_FAILED.value:
        return test_result.get("status") == "failed"
    
    if trigger == NotificationTrigger.TEST_COMPLETE.value:
        return test_result.get("status") == "completed"
    
    if trigger == NotificationTrigger.SCORE_BELOW_THRESHOLD.value:
        threshold = rule.get("score_threshold")
        score = test_result.get("overall_score")
        if threshold and score is not None:
            return score < threshold
    
    if trigger == NotificationTrigger.UPTIME_DOWN.value:
        uptime = test_result.get("uptime", {})
        return uptime.get("status") == "fail"
    
    if trigger == NotificationTrigger.SSL_EXPIRING.value:
        ssl = test_result.get("ssl", {})
        days = ssl.get("days_until_expiry")
        threshold = rule.get("ssl_days_threshold", 30)
        if days is not None:
            return days < threshold
    
    if trigger == NotificationTrigger.SLOW_RESPONSE.value:
        uptime = test_result.get("uptime", {})
        response_time = uptime.get("response_time_ms")
        threshold = rule.get("response_time_ms", 3000)
        if response_time is not None:
            return response_time > threshold
    
    return False


async def process_notification(
    rule: dict,
    test_result: dict,
    background_tasks: BackgroundTasks
):
    """Process notification for a triggered rule."""
    db = get_db()
    if db is None:
        return
    
    user_id = rule["user_id"]
    rule_id = rule["rule_id"]
    test_id = test_result["test_id"]
    
    # Build payload
    payload = {
        "test_id": test_id,
        "url": test_result.get("url"),
        "status": test_result.get("status"),
        "score": test_result.get("overall_score"),
        "trigger": rule["trigger"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    # Send to each channel
    for channel in rule.get("channels", []):
        log_id = str(uuid.uuid4())
        
        log_entry = {
            "log_id": log_id,
            "rule_id": rule_id,
            "user_id": user_id,
            "test_id": test_id,
            "trigger": rule["trigger"],
            "channel": channel,
            "status": NotificationStatus.PENDING.value,
            "payload": payload,
            "sent_at": datetime.now(timezone.utc),
        }
        
        try:
            if channel == NotificationChannel.WEBHOOK.value:
                webhook_url = rule.get("webhook_url")
                if webhook_url:
                    result = await send_webhook(
                        str(webhook_url),
                        payload,
                        rule.get("webhook_headers")
                    )
                    log_entry["status"] = (
                        NotificationStatus.SENT.value if result["success"]
                        else NotificationStatus.FAILED.value
                    )
                    log_entry["response"] = result
                    log_entry["recipient"] = str(webhook_url)
            
            elif channel == NotificationChannel.EMAIL.value:
                # Use existing email service
                from app.services.email_service import send_test_complete, send_test_failed
                from app.config import get_settings
                
                settings = get_settings()
                recipients = rule.get("email_recipients", [])
                
                # Get user email
                user = await db.users.find_one({"_id": user_id}) or await db.users.find_one({"sub": user_id})
                if user:
                    recipients.append(user["email"])
                
                for email in recipients:
                    if test_result.get("status") == "failed":
                        await send_test_failed(
                            email,
                            test_result["url"],
                            test_result.get("error", "Unknown error"),
                            test_id,
                            settings.app_url
                        )
                    else:
                        await send_test_complete(
                            email,
                            test_result["url"],
                            test_result.get("overall_score"),
                            test_result.get("summary"),
                            test_id,
                            settings.app_url
                        )
                
                log_entry["status"] = NotificationStatus.SENT.value
                log_entry["recipient"] = ", ".join(recipients)
            
            elif channel == NotificationChannel.SLACK.value:
                # Integrate with existing Slack service
                slack_channel = rule.get("slack_channel")
                if slack_channel:
                    # Look up Slack config
                    slack_config = await db.slack_configs.find_one({"user_id": user_id})
                    if slack_config:
                        # Use existing Slack notification
                        from app.services.notification_service import notify_slack
                        await notify_slack(
                            slack_config["webhook_url"],
                            test_result["url"],
                            test_result.get("overall_score"),
                            test_id,
                            slack_channel
                        )
                        log_entry["status"] = NotificationStatus.SENT.value
                        log_entry["recipient"] = slack_channel
        
        except Exception as e:
            log_entry["status"] = NotificationStatus.FAILED.value
            log_entry["error"] = str(e)
            print(f"❌ Notification failed: {e}")
        
        finally:
            log_entry["delivered_at"] = datetime.now(timezone.utc)
            await db.notification_logs.insert_one(log_entry)


async def check_and_notify(test_result: dict, background_tasks: BackgroundTasks):
    """Main entry point: check all rules and send matching notifications."""
    db = get_db()
    if db is None:
        return
    
    user_id = test_result.get("user_id")
    if not user_id:
        return
    
    # Find all enabled rules for this user
    rules = await db.notification_rules.find({
        "user_id": user_id,
        "enabled": True
    }).to_list(100)
    
    for rule in rules:
        if await evaluate_rule(rule, test_result):
            background_tasks.add_task(process_notification, rule, test_result, background_tasks)
            print(f"🔔 Notification triggered: {rule['name']} for test {test_result['test_id']}")


# ─── API Endpoints ─────────────────────────────────────────────────────────────

@router.post("/rules", status_code=201)
async def create_rule(
    req: CreateRuleRequest,
    current_user: dict = Depends(get_current_user)
):
    """Create a new notification rule."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")
    
    user_id = current_user.get("id") or current_user.get("sub")
    rule_id = str(uuid.uuid4())
    
    rule = {
        "rule_id": rule_id,
        "user_id": user_id,
        "team_id": req.team_id,
        "name": req.name,
        "enabled": True,
        "trigger": req.trigger.value,
        "url_pattern": req.url_pattern,
        "score_threshold": req.score_threshold,
        "response_time_ms": req.response_time_ms,
        "ssl_days_threshold": req.ssl_days_threshold,
        "channels": [c.value for c in req.channels],
        "webhook_url": str(req.webhook_url) if req.webhook_url else None,
        "webhook_headers": req.webhook_headers,
        "email_recipients": req.email_recipients,
        "slack_channel": req.slack_channel,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    
    await db.notification_rules.insert_one(rule)
    
    return {
        "success": True,
        "message": "Notification rule created",
        "rule_id": rule_id,
        "rule": rule,
    }


@router.get("/rules")
async def list_rules(
    team_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """List all notification rules for current user."""
    db = get_db()
    if db is None:
        return {"success": True, "rules": []}
    
    user_id = current_user.get("id") or current_user.get("sub")
    query = {"user_id": user_id}
    if team_id:
        query["team_id"] = team_id
    
    rules = await db.notification_rules.find(query).to_list(100)
    
    return {
        "success": True,
        "total": len(rules),
        "rules": [
            {
                "rule_id": r["rule_id"],
                "name": r["name"],
                "enabled": r["enabled"],
                "trigger": r["trigger"],
                "channels": r.get("channels", []),
                "created_at": r["created_at"].isoformat(),
            }
            for r in rules
        ],
    }


@router.get("/rules/{rule_id}")
async def get_rule(
    rule_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get a specific notification rule."""
    db = get_db()
    if db is None:
        raise HTTPException(404, "Rule not found")
    
    user_id = current_user.get("id") or current_user.get("sub")
    rule = await db.notification_rules.find_one({
        "rule_id": rule_id,
        "user_id": user_id
    })
    
    if not rule:
        raise HTTPException(404, "Rule not found")
    
    return {"success": True, "rule": rule}


@router.patch("/rules/{rule_id}")
async def update_rule(
    rule_id: str,
    req: UpdateRuleRequest,
    current_user: dict = Depends(get_current_user)
):
    """Update a notification rule."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")
    
    user_id = current_user.get("id") or current_user.get("sub")
    
    update_data = req.model_dump(exclude_unset=True)
    if "channels" in update_data:
        update_data["channels"] = [c.value for c in update_data["channels"]]
    if "trigger" in update_data:
        update_data["trigger"] = update_data["trigger"].value
    if "webhook_url" in update_data and update_data["webhook_url"]:
        update_data["webhook_url"] = str(update_data["webhook_url"])
    
    update_data["updated_at"] = datetime.now(timezone.utc)
    
    result = await db.notification_rules.update_one(
        {"rule_id": rule_id, "user_id": user_id},
        {"$set": update_data}
    )
    
    if result.matched_count == 0:
        raise HTTPException(404, "Rule not found")
    
    return {"success": True, "message": "Rule updated"}


@router.delete("/rules/{rule_id}")
async def delete_rule(
    rule_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Delete a notification rule."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")
    
    user_id = current_user.get("id") or current_user.get("sub")
    result = await db.notification_rules.delete_one({
        "rule_id": rule_id,
        "user_id": user_id
    })
    
    if result.deleted_count == 0:
        raise HTTPException(404, "Rule not found")
    
    return {"success": True, "message": "Rule deleted"}


@router.post("/test-rule")
async def test_rule(
    req: TestNotificationRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Test a notification rule with sample data."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")
    
    user_id = current_user.get("id") or current_user.get("sub")
    rule = await db.notification_rules.find_one({
        "rule_id": req.rule_id,
        "user_id": user_id
    })
    
    if not rule:
        raise HTTPException(404, "Rule not found")
    
    # Create test payload
    test_payload = req.test_payload or {
        "test_id": "test_" + str(uuid.uuid4())[:8],
        "url": "https://example.com",
        "status": "completed",
        "overall_score": 75,
    }
    
    await process_notification(rule, test_payload, background_tasks)
    
    return {
        "success": True,
        "message": "Test notification sent",
        "payload": test_payload,
    }


@router.get("/logs")
async def get_notification_logs(
    rule_id: Optional[str] = None,
    limit: int = 50,
    current_user: dict = Depends(get_current_user)
):
    """Get notification delivery logs."""
    db = get_db()
    if db is None:
        return {"success": True, "logs": []}
    
    user_id = current_user.get("id") or current_user.get("sub")
    query = {"user_id": user_id}
    if rule_id:
        query["rule_id"] = rule_id
    
    logs = await db.notification_logs.find(query).sort("sent_at", -1).limit(limit).to_list(limit)
    
    return {
        "success": True,
        "total": len(logs),
        "logs": [
            {
                "log_id": log["log_id"],
                "rule_id": log["rule_id"],
                "test_id": log["test_id"],
                "trigger": log["trigger"],
                "channel": log["channel"],
                "status": log["status"],
                "recipient": log.get("recipient"),
                "sent_at": log["sent_at"].isoformat(),
                "error": log.get("error"),
            }
            for log in logs
        ],
    }

"""
app/routers/compliance_router.py
Phase 7B/C: Compliance & Security
- API key rotation policies
- GDPR: data export, right to deletion
- Data retention configuration
- Security audit trail
- Encrypted secrets management
"""
import uuid, json, hashlib, secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app.database import get_db
from app.utils.auth import get_current_user

router = APIRouter(prefix="/compliance", tags=["Compliance"])


# ─── Models ────────────────────────────────────────────────────────────────────

class KeyRotationPolicyRequest(BaseModel):
    rotation_days: int = Field(90, ge=7, le=365, description="Days before API keys must be rotated")
    notify_days_before: int = Field(14, ge=1, le=30, description="Days before expiry to send warning")
    auto_revoke: bool = False


class DataRetentionRequest(BaseModel):
    test_results_days: int = Field(90, ge=7, le=365)
    audit_logs_days: int = Field(365, ge=30, le=730)
    notification_logs_days: int = Field(30, ge=7, le=180)


class GDPRExportRequest(BaseModel):
    include_test_results: bool = True
    include_schedules: bool = True
    include_api_keys: bool = False   # previews only, never full keys
    include_audit_logs: bool = True
    include_notifications: bool = True


# ─── API Key Rotation ──────────────────────────────────────────────────────────

@router.get("/key-rotation-policy")
async def get_key_rotation_policy(current_user: dict = Depends(get_current_user)):
    """Get current API key rotation policy."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")

    if db is None:
        return {"success": True, "policy": {"rotation_days": 90, "notify_days_before": 14, "auto_revoke": False}}

    policy = await db.key_rotation_policies.find_one({"user_id": user_id})
    if not policy:
        return {"success": True, "policy": {"rotation_days": 90, "notify_days_before": 14, "auto_revoke": False}, "is_default": True}

    policy.pop("_id", None)
    return {"success": True, "policy": policy}


@router.post("/key-rotation-policy")
async def set_key_rotation_policy(
    req: KeyRotationPolicyRequest,
    current_user: dict = Depends(get_current_user)
):
    """Set API key rotation policy."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")

    user_id = current_user.get("id") or current_user.get("sub")

    policy = {
        "user_id": user_id,
        "rotation_days": req.rotation_days,
        "notify_days_before": req.notify_days_before,
        "auto_revoke": req.auto_revoke,
        "updated_at": datetime.now(timezone.utc),
    }
    await db.key_rotation_policies.replace_one({"user_id": user_id}, policy, upsert=True)

    # Apply to existing keys: set expiry dates
    expiry = datetime.now(timezone.utc) + timedelta(days=req.rotation_days)
    await db.api_keys.update_many(
        {"user_id": user_id, "active": True, "expires_at": None},
        {"$set": {"expires_at": expiry}}
    )

    return {"success": True, "message": "Key rotation policy updated", "policy": policy}


@router.get("/key-rotation-status")
async def get_key_rotation_status(current_user: dict = Depends(get_current_user)):
    """List all API keys and their rotation status."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")

    if db is None:
        return {"success": True, "keys": []}

    policy = await db.key_rotation_policies.find_one({"user_id": user_id})
    notify_days = policy["notify_days_before"] if policy else 14

    keys = await db.api_keys.find({"user_id": user_id, "active": True}).to_list(100)
    now = datetime.now(timezone.utc)

    result = []
    for k in keys:
        expires_at = k.get("expires_at")
        days_until_expiry = None
        status = "active"

        if expires_at:
            delta = (expires_at - now).days
            days_until_expiry = delta
            if delta < 0:
                status = "expired"
            elif delta <= notify_days:
                status = "expiring_soon"

        result.append({
            "key_id": k["key_id"],
            "name": k.get("name", ""),
            "key_preview": k.get("key_preview", ""),
            "created_at": k["created_at"].isoformat() if k.get("created_at") else None,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "days_until_expiry": days_until_expiry,
            "status": status,
            "last_used": k.get("last_used"),
        })

    return {
        "success": True,
        "keys": result,
        "has_expired": any(k["status"] == "expired" for k in result),
        "has_expiring_soon": any(k["status"] == "expiring_soon" for k in result),
    }


@router.post("/rotate-key/{key_id}")
async def rotate_api_key(key_id: str, current_user: dict = Depends(get_current_user)):
    """Rotate a specific API key (revoke old, create new)."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")

    user_id = current_user.get("id") or current_user.get("sub")
    old_key = await db.api_keys.find_one({"key_id": key_id, "user_id": user_id})
    if not old_key:
        raise HTTPException(404, "Key not found")

    # Revoke old key
    await db.api_keys.update_one({"key_id": key_id}, {"$set": {"active": False, "revoked_at": datetime.now(timezone.utc)}})

    # Generate new key
    new_key_value = "tv_" + secrets.token_urlsafe(32)
    new_key_id = str(uuid.uuid4())
    key_hash = hashlib.sha256(new_key_value.encode()).hexdigest()

    policy = await db.key_rotation_policies.find_one({"user_id": user_id})
    rotation_days = policy["rotation_days"] if policy else 90

    new_doc = {
        "key_id": new_key_id,
        "user_id": user_id,
        "name": old_key.get("name", "") + " (rotated)",
        "key_hash": key_hash,
        "key_preview": new_key_value[:12] + "...",
        "active": True,
        "created_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(days=rotation_days),
        "rotated_from": key_id,
        "last_used": None,
    }
    await db.api_keys.insert_one(new_doc)

    return {
        "success": True,
        "message": "Key rotated successfully",
        "new_key": new_key_value,  # shown once
        "new_key_id": new_key_id,
        "expires_at": new_doc["expires_at"].isoformat(),
    }


# ─── Data Retention ────────────────────────────────────────────────────────────

@router.get("/data-retention")
async def get_data_retention(current_user: dict = Depends(get_current_user)):
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")

    if db is None:
        return {"success": True, "policy": {"test_results_days": 90, "audit_logs_days": 365, "notification_logs_days": 30}}

    policy = await db.data_retention_policies.find_one({"user_id": user_id})
    if not policy:
        return {"success": True, "policy": {"test_results_days": 90, "audit_logs_days": 365, "notification_logs_days": 30}, "is_default": True}

    policy.pop("_id", None)
    return {"success": True, "policy": policy}


@router.post("/data-retention")
async def set_data_retention(
    req: DataRetentionRequest,
    current_user: dict = Depends(get_current_user)
):
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")

    user_id = current_user.get("id") or current_user.get("sub")
    policy = {
        "user_id": user_id,
        "test_results_days": req.test_results_days,
        "audit_logs_days": req.audit_logs_days,
        "notification_logs_days": req.notification_logs_days,
        "updated_at": datetime.now(timezone.utc),
    }
    await db.data_retention_policies.replace_one({"user_id": user_id}, policy, upsert=True)
    return {"success": True, "message": "Data retention policy updated", "policy": policy}


@router.post("/purge-old-data")
async def purge_old_data(current_user: dict = Depends(get_current_user)):
    """Manually trigger data purge based on retention policy."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")

    user_id = current_user.get("id") or current_user.get("sub")
    policy = await db.data_retention_policies.find_one({"user_id": user_id})

    result_days = policy["test_results_days"] if policy else 90
    audit_days = policy["audit_logs_days"] if policy else 365
    notif_days = policy["notification_logs_days"] if policy else 30

    now = datetime.now(timezone.utc)
    results_cutoff = now - timedelta(days=result_days)
    audit_cutoff = now - timedelta(days=audit_days)
    notif_cutoff = now - timedelta(days=notif_days)

    r1 = await db.test_results.delete_many({"user_id": user_id, "created_at": {"$lt": results_cutoff}})
    r2 = await db.audit_logs.delete_many({"user_id": user_id, "timestamp": {"$lt": audit_cutoff}})
    r3 = await db.notification_logs.delete_many({"user_id": user_id, "sent_at": {"$lt": notif_cutoff}})

    return {
        "success": True,
        "purged": {
            "test_results": r1.deleted_count,
            "audit_logs": r2.deleted_count,
            "notification_logs": r3.deleted_count,
        },
        "purged_at": now.isoformat(),
    }


# ─── GDPR ──────────────────────────────────────────────────────────────────────

@router.post("/gdpr/export")
async def gdpr_export(
    req: GDPRExportRequest,
    current_user: dict = Depends(get_current_user)
):
    """GDPR: Export all personal data for a user."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")

    export = {
        "export_id": str(uuid.uuid4()),
        "user_id": user_id,
        "user_email": current_user.get("email"),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "data": {}
    }

    if db:
        if req.include_test_results:
            results = await db.test_results.find({"user_id": user_id}).to_list(10000)
            for r in results:
                r.pop("_id", None)
                if isinstance(r.get("created_at"), datetime):
                    r["created_at"] = r["created_at"].isoformat()
            export["data"]["test_results"] = results

        if req.include_schedules:
            schedules = await db.schedules.find({"user_id": user_id}).to_list(1000)
            for s in schedules:
                s.pop("_id", None)
            export["data"]["schedules"] = schedules

        if req.include_api_keys:
            keys = await db.api_keys.find({"user_id": user_id}).to_list(100)
            export["data"]["api_keys"] = [
                {"key_id": k["key_id"], "name": k.get("name"), "key_preview": k.get("key_preview"), "created_at": str(k.get("created_at"))}
                for k in keys
            ]

        if req.include_audit_logs:
            logs = await db.audit_logs.find({"user_id": user_id}).to_list(10000)
            for l in logs:
                l.pop("_id", None)
                if isinstance(l.get("timestamp"), datetime):
                    l["timestamp"] = l["timestamp"].isoformat()
            export["data"]["audit_logs"] = logs

    payload = json.dumps(export, indent=2, default=str)
    filename = f"testverse_gdpr_export_{user_id[:8]}_{datetime.now().strftime('%Y%m%d')}.json"

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        iter([payload]),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.delete("/gdpr/delete-account")
async def gdpr_delete_account(
    confirm: str,
    current_user: dict = Depends(get_current_user)
):
    """GDPR: Right to erasure — delete all user data."""
    if confirm != "DELETE_MY_ACCOUNT":
        raise HTTPException(400, "Must pass confirm=DELETE_MY_ACCOUNT to proceed")

    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")

    user_id = current_user.get("id") or current_user.get("sub")

    collections = [
        "test_results", "schedules", "api_keys", "audit_logs",
        "notification_logs", "notification_rules", "monitors",
        "monitor_checks", "incidents", "sla_reports", "templates",
        "role_assignments", "slack_configs", "custom_dashboards",
        "scheduled_reports", "subscriptions", "data_retention_policies",
        "key_rotation_policies", "bulk_batches", "whitelabel_configs",
    ]

    deleted = {}
    for col in collections:
        r = await db[col].delete_many({"user_id": user_id})
        deleted[col] = r.deleted_count

    # Remove from teams
    await db.team_members.delete_many({"user_id": user_id})

    # Delete user account
    await db.users.delete_one({"_id": user_id})

    return {
        "success": True,
        "message": "All personal data has been permanently deleted.",
        "deleted_records": deleted,
    }


@router.get("/security-summary")
async def get_security_summary(current_user: dict = Depends(get_current_user)):
    """Security health check — key rotation, data retention, recent logins."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")

    summary = {
        "key_rotation_policy": False,
        "data_retention_policy": False,
        "active_api_keys": 0,
        "expired_keys": 0,
        "recent_logins": [],
        "score": 0,
    }

    if db is not None:
        policy = await db.key_rotation_policies.find_one({"user_id": user_id})
        summary["key_rotation_policy"] = policy is not None

        retention = await db.data_retention_policies.find_one({"user_id": user_id})
        summary["data_retention_policy"] = retention is not None

        now = datetime.now(timezone.utc)
        summary["active_api_keys"] = await db.api_keys.count_documents({"user_id": user_id, "active": True})
        summary["expired_keys"] = await db.api_keys.count_documents({
            "user_id": user_id, "active": True, "expires_at": {"$lt": now}
        })

        logins = await db.audit_logs.find(
            {"user_id": user_id, "action": "user_login"}
        ).sort("timestamp", -1).limit(5).to_list(5)
        summary["recent_logins"] = [
            {"timestamp": l["timestamp"].isoformat(), "ip": l.get("ip_address")}
            for l in logins
        ]

    # Simple security score
    score = 50
    if summary["key_rotation_policy"]: score += 20
    if summary["data_retention_policy"]: score += 15
    if summary["expired_keys"] == 0: score += 15
    summary["score"] = min(score, 100)

    return {"success": True, "summary": summary}

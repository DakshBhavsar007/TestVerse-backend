"""
app/routers/rbac_router.py
Phase 7A: Role-Based Access Control
- Admin, Developer, Viewer roles
- Permission management per team
- Audit logs for sensitive actions
"""
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from app.database import get_db
from app.utils.auth import get_current_user
from enum import Enum

router = APIRouter(prefix="/rbac", tags=["RBAC"])


class UserRole(str, Enum):
    ADMIN = "admin"
    DEVELOPER = "developer"
    VIEWER = "viewer"


class Permission(str, Enum):
    # Test permissions
    RUN_TESTS = "run_tests"
    VIEW_TESTS = "view_tests"
    DELETE_TESTS = "delete_tests"
    EXPORT_TESTS = "export_tests"
    
    # Schedule permissions
    CREATE_SCHEDULES = "create_schedules"
    EDIT_SCHEDULES = "edit_schedules"
    DELETE_SCHEDULES = "delete_schedules"
    
    # Team permissions
    MANAGE_TEAM = "manage_team"
    INVITE_MEMBERS = "invite_members"
    REMOVE_MEMBERS = "remove_members"
    CHANGE_ROLES = "change_roles"
    
    # API permissions
    MANAGE_API_KEYS = "manage_api_keys"
    VIEW_API_KEYS = "view_api_keys"
    
    # System permissions
    VIEW_AUDIT_LOGS = "view_audit_logs"
    MANAGE_BILLING = "manage_billing"
    WHITELABEL_CONFIG = "whitelabel_config"


# Role -> Permissions mapping
ROLE_PERMISSIONS = {
    UserRole.ADMIN: [p for p in Permission],  # All permissions
    UserRole.DEVELOPER: [
        Permission.RUN_TESTS,
        Permission.VIEW_TESTS,
        Permission.DELETE_TESTS,
        Permission.EXPORT_TESTS,
        Permission.CREATE_SCHEDULES,
        Permission.EDIT_SCHEDULES,
        Permission.DELETE_SCHEDULES,
        Permission.VIEW_API_KEYS,
        Permission.MANAGE_API_KEYS,
    ],
    UserRole.VIEWER: [
        Permission.VIEW_TESTS,
        Permission.EXPORT_TESTS,
    ],
}


class AuditAction(str, Enum):
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    TEST_RUN = "test_run"
    TEST_DELETE = "test_delete"
    SCHEDULE_CREATE = "schedule_create"
    SCHEDULE_DELETE = "schedule_delete"
    TEAM_MEMBER_ADD = "team_member_add"
    TEAM_MEMBER_REMOVE = "team_member_remove"
    ROLE_CHANGE = "role_change"
    API_KEY_CREATE = "api_key_create"
    API_KEY_REVOKE = "api_key_revoke"
    SETTINGS_CHANGE = "settings_change"


# ─── Request/Response Models ───────────────────────────────────────────────────

class RoleAssignment(BaseModel):
    user_id: str
    team_id: Optional[str] = None
    role: UserRole
    assigned_by: str
    assigned_at: datetime


class AssignRoleRequest(BaseModel):
    user_id: str = Field(..., description="User to assign role to")
    team_id: Optional[str] = Field(None, description="Team context (optional)")
    role: UserRole = Field(..., description="Role to assign")


class CheckPermissionRequest(BaseModel):
    permission: Permission
    team_id: Optional[str] = None


class AuditLogEntry(BaseModel):
    log_id: str
    user_id: str
    user_email: str
    action: AuditAction
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    team_id: Optional[str] = None
    details: Optional[dict] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    timestamp: datetime


class AuditLogRequest(BaseModel):
    action: AuditAction
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    team_id: Optional[str] = None
    details: Optional[dict] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


# ─── Helper Functions ──────────────────────────────────────────────────────────

async def get_user_role(user_id: str, team_id: Optional[str] = None) -> UserRole:
    """Get user's role (team-specific or global)."""
    db = get_db()
    if db is None:
        return UserRole.ADMIN  # Default in dev mode
    
    # Check team-specific role first
    if team_id:
        member = await db.team_members.find_one({
            "user_id": user_id,
            "team_id": team_id
        })
        if member and member.get("role"):
            return UserRole(member["role"])
    
    # Check global role assignment
    role_doc = await db.role_assignments.find_one({"user_id": user_id})
    if role_doc:
        return UserRole(role_doc["role"])
    
    # Default role for new users
    return UserRole.DEVELOPER


def has_permission(role: UserRole, permission: Permission) -> bool:
    """Check if role has a specific permission."""
    return permission in ROLE_PERMISSIONS.get(role, [])


def require_permission(permission: Permission, team_id: Optional[str] = None):
    """
    Factory that returns a FastAPI dependency function.
    Usage: Depends(require_permission(Permission.CHANGE_ROLES))
    """
    async def _check(user: dict = Depends(get_current_user)) -> dict:
        user_id = user.get("id") or user.get("sub")
        role = await get_user_role(user_id, team_id)

        if not has_permission(role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied. Required: {permission.value}"
            )
        return user

    return _check


async def log_audit(
    user_id: str,
    user_email: str,
    action: AuditAction,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    team_id: Optional[str] = None,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None
):
    """Log an audit event."""
    db = get_db()
    if db is None:
        return
    
    import uuid
    log_entry = {
        "log_id": str(uuid.uuid4()),
        "user_id": user_id,
        "user_email": user_email,
        "action": action.value,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "team_id": team_id,
        "details": details or {},
        "ip_address": ip_address,
        "user_agent": user_agent,
        "timestamp": datetime.now(timezone.utc),
    }
    
    await db.audit_logs.insert_one(log_entry)
    print(f"📝 Audit: {user_email} -> {action.value}")


# ─── API Endpoints ─────────────────────────────────────────────────────────────

@router.get("/my-role")
async def get_my_role(
    team_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Get current user's role and permissions."""
    user_id = current_user.get("id") or current_user.get("sub")
    role = await get_user_role(user_id, team_id)
    permissions = ROLE_PERMISSIONS.get(role, [])
    
    return {
        "success": True,
        "role": role.value,
        "permissions": [p.value for p in permissions],
        "team_id": team_id,
    }


@router.post("/check-permission")
async def check_permission(
    req: CheckPermissionRequest,
    current_user: dict = Depends(get_current_user)
):
    """Check if current user has a specific permission."""
    user_id = current_user.get("id") or current_user.get("sub")
    role = await get_user_role(user_id, req.team_id)
    has_perm = has_permission(role, req.permission)
    
    return {
        "success": True,
        "has_permission": has_perm,
        "role": role.value,
        "permission": req.permission.value,
    }


@router.post("/assign-role")
async def assign_role(
    req: AssignRoleRequest,
    current_user: dict = Depends(require_permission(Permission.CHANGE_ROLES))
):
    """Assign a role to a user (admin only)."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")
    
    assigner_id = current_user.get("id") or current_user.get("sub")
    
    assignment = {
        "user_id": req.user_id,
        "team_id": req.team_id,
        "role": req.role.value,
        "assigned_by": assigner_id,
        "assigned_at": datetime.now(timezone.utc),
    }
    
    # If team_id provided, update team_members; otherwise global role
    if req.team_id:
        await db.team_members.update_one(
            {"user_id": req.user_id, "team_id": req.team_id},
            {"$set": {"role": req.role.value}},
            upsert=False
        )
    else:
        await db.role_assignments.replace_one(
            {"user_id": req.user_id},
            assignment,
            upsert=True
        )
    
    # Audit log
    await log_audit(
        user_id=assigner_id,
        user_email=current_user.get("email", ""),
        action=AuditAction.ROLE_CHANGE,
        resource_type="user",
        resource_id=req.user_id,
        team_id=req.team_id,
        details={"new_role": req.role.value}
    )
    
    return {
        "success": True,
        "message": f"Role {req.role.value} assigned to user {req.user_id}",
        "assignment": assignment,
    }


@router.get("/team-roles/{team_id}")
async def get_team_roles(
    team_id: str,
    current_user: dict = Depends(get_current_user)
):
    """List all role assignments for a team."""
    db = get_db()
    if db is None:
        return {"success": True, "roles": []}
    
    # Verify user is team member
    user_id = current_user.get("id") or current_user.get("sub")
    member = await db.team_members.find_one({
        "user_id": user_id,
        "team_id": team_id
    })
    if not member:
        raise HTTPException(403, "Not a team member")
    
    members = await db.team_members.find({"team_id": team_id}).to_list(100)
    
    return {
        "success": True,
        "team_id": team_id,
        "roles": [
            {
                "user_id": m["user_id"],
                "email": m.get("email"),
                "role": m.get("role", "developer"),
                "joined_at": m.get("joined_at"),
            }
            for m in members
        ],
    }


@router.post("/audit-log")
async def create_audit_log(
    req: AuditLogRequest,
    current_user: dict = Depends(get_current_user)
):
    """Create an audit log entry (used internally by other services)."""
    user_id = current_user.get("id") or current_user.get("sub")
    user_email = current_user.get("email", "")
    
    await log_audit(
        user_id=user_id,
        user_email=user_email,
        action=req.action,
        resource_type=req.resource_type,
        resource_id=req.resource_id,
        team_id=req.team_id,
        details=req.details,
        ip_address=req.ip_address,
        user_agent=req.user_agent,
    )
    
    return {"success": True, "message": "Audit log created"}


@router.get("/audit-logs")
async def get_audit_logs(
    team_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 50,
    current_user: dict = Depends(require_permission(Permission.VIEW_AUDIT_LOGS))
):
    """Get audit logs (admin only)."""
    db = get_db()
    if db is None:
        return {"success": True, "logs": []}
    
    query = {}
    if team_id:
        query["team_id"] = team_id
    if action:
        query["action"] = action
    
    logs = await db.audit_logs.find(query).sort("timestamp", -1).limit(limit).to_list(limit)
    
    return {
        "success": True,
        "total": len(logs),
        "logs": [
            {
                "log_id": log["log_id"],
                "user_email": log["user_email"],
                "action": log["action"],
                "resource_type": log.get("resource_type"),
                "resource_id": log.get("resource_id"),
                "timestamp": log["timestamp"].isoformat(),
                "details": log.get("details", {}),
            }
            for log in logs
        ],
    }


@router.get("/permissions-list")
async def list_all_permissions():
    """List all available permissions (reference)."""
    return {
        "success": True,
        "permissions": [
            {
                "name": p.value,
                "admin": p in ROLE_PERMISSIONS[UserRole.ADMIN],
                "developer": p in ROLE_PERMISSIONS[UserRole.DEVELOPER],
                "viewer": p in ROLE_PERMISSIONS[UserRole.VIEWER],
            }
            for p in Permission
        ],
        "roles": {
            "admin": "Full system access",
            "developer": "Run tests, manage schedules, API keys",
            "viewer": "Read-only access to tests and reports",
        }
    }

@router.get("/team-members")
async def get_team_members(
    current_user: dict = Depends(get_current_user)
):
    """List all users with their global roles. Requires manage_team permission."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")
    role = await get_user_role(user_id)

    # Only admins and developers can view team members
    if role == UserRole.VIEWER:
        raise HTTPException(status_code=403, detail="Permission denied")

    if db is None:
        return {"success": True, "members": []}

    # Fetch all users
    users = await db.users.find({}, {"hashed_password": 0}).to_list(200)
    members = []
    for u in users:
        uid = str(u.get("id") or u.get("_id", ""))
        role_doc = await db.role_assignments.find_one({"user_id": uid})
        user_role = role_doc["role"] if role_doc else "developer"
        members.append({
            "user_id": uid,
            "email": u.get("email", ""),
            "name": u.get("name") or u.get("email", "Unknown"),
            "role": user_role,
            "joined_at": u.get("created_at", "").isoformat() if hasattr(u.get("created_at", ""), "isoformat") else str(u.get("created_at", "")),
        })

    return {"success": True, "members": members}

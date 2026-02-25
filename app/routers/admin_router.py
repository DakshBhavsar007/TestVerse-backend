from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
from app.database import get_db
from app.utils.auth import get_current_user
from bson import ObjectId

router = APIRouter(prefix="/admin", tags=["Admin Dashboard"])

async def require_system_admin(current_user: dict = Depends(get_current_user)):
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")
    if "@" in user_id:
        u = await db.users.find_one({"email": user_id.lower()})
        if u:
            user_id = str(u["_id"])
            
    role_doc = await db.role_assignments.find_one({"user_id": user_id})
    if not role_doc or role_doc.get("role") != "admin":
        raise HTTPException(status_code=403, detail="System Admin access required")
    return current_user

# --- Overview ---
@router.get("/overview")
async def get_overview(admin=Depends(require_system_admin)):
    db = get_db()
    users_count = await db.users.count_documents({})
    teams_count = await db.teams.count_documents({})
    completed_tests = await db.test_results.count_documents({})
    active_schedules = await db.schedules.count_documents({"active": True})
    return {
        "users": users_count,
        "teams": teams_count,
        "completed_tests": completed_tests,
        "active_schedules": active_schedules
    }

# --- Users ---
@router.get("/users")
async def get_all_users(admin=Depends(require_system_admin)):
    db = get_db()
    users = await db.users.find({}, {"hashed_password": 0}).to_list(500)
    result = []
    for u in users:
        uid = str(u["_id"])
        role_doc = await db.role_assignments.find_one({"user_id": uid})
        u["id"] = uid
        u["role"] = role_doc["role"] if role_doc else "developer"
        u["is_active"] = u.get("is_active", True)
        u["created_at"] = str(u.get("created_at", ""))
        u["last_login"] = str(u.get("last_login", "")) if u.get("last_login") else ""
        u.pop("_id", None)
        result.append(u)
    return {"users": result}

class UserUpdateRequest(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None

@router.patch("/users/{user_id}")
async def update_user(user_id: str, req: UserUpdateRequest, admin=Depends(require_system_admin)):
    db = get_db()
    
    # Protect against self-deactivation
    current_uid = admin.get("id") or admin.get("sub")
    if req.is_active is False:
        user_doc = await db.users.find_one({"_id": ObjectId(user_id)})
        if user_doc and (user_doc.get("email") == admin.get("sub") or str(user_doc["_id"]) == current_uid):
            raise HTTPException(400, "Cannot deactivate your own account.")
            
    updates = {}
    if req.name is not None:
        updates["name"] = req.name
    if req.is_active is not None:
        updates["is_active"] = req.is_active
        
    if updates:
        await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": updates})
        
    if req.role in ["admin", "developer", "viewer"]:
        await db.role_assignments.replace_one(
            {"user_id": user_id},
            {"user_id": user_id, "role": req.role},
            upsert=True
        )
    return {"success": True}

@router.delete("/users/{user_id}")
async def delete_user(user_id: str, admin=Depends(require_system_admin)):
    db = get_db()
    
    current_uid = admin.get("id") or admin.get("sub")
    user_doc = await db.users.find_one({"_id": ObjectId(user_id)})
    if user_doc and (user_doc.get("email") == admin.get("sub") or str(user_doc["_id"]) == current_uid):
        raise HTTPException(400, "Cannot delete your own account.")
        
    await db.users.delete_one({"_id": ObjectId(user_id)})
    await db.role_assignments.delete_one({"user_id": user_id})
    return {"success": True}

# --- Teams ---
@router.get("/teams")
async def get_all_teams(admin=Depends(require_system_admin)):
    db = get_db()
    teams = await db.teams.find({}, {"_id": 0}).to_list(500)
    for t in teams:
        members_count = await db.team_members.count_documents({"team_id": t["team_id"]})
        t["members_count"] = members_count
    return {"teams": teams}

@router.delete("/teams/{team_id}")
async def delete_system_team(team_id: str, admin=Depends(require_system_admin)):
    db = get_db()
    await db.teams.delete_one({"team_id": team_id})
    await db.team_members.delete_many({"team_id": team_id})
    return {"success": True}

"""
app/routers/teams_router.py
Phase 5 — Teams: create team, invite members, manage roles.

MongoDB collections:
  teams        — { team_id, name, owner_id, created_at }
  team_members — { team_id, user_id, email, role, invited_at, accepted }
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from ..utils.auth import get_current_user
from ..database import get_db

router = APIRouter(prefix="/teams", tags=["Teams"])

# ── Pydantic models ────────────────────────────────────────────────────────────

class CreateTeamRequest(BaseModel):
    name: str

class InviteMemberRequest(BaseModel):
    email: EmailStr
    role: str = "viewer"   # "admin" | "viewer"

class UpdateRoleRequest(BaseModel):
    role: str              # "admin" | "viewer"

# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

async def _get_team_or_404(team_id: str):
    db = get_db()
    team = await db.teams.find_one({"team_id": team_id}, {"_id": 0})
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    return team

async def _require_admin(team_id: str, user_id: str):
    """Raise 403 if user is not owner or admin of this team."""
    db = get_db()
    team = await _get_team_or_404(team_id)
    if team["owner_id"] == user_id:
        return team
    member = await db.team_members.find_one(
        {"team_id": team_id, "user_id": user_id, "role": "admin", "accepted": True},
        {"_id": 0}
    )
    if not member:
        raise HTTPException(status_code=403, detail="Admin access required")
    return team

# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/")
async def create_team(
    body: CreateTeamRequest,
    current_user: dict = Depends(get_current_user),
):
    """Create a new team. The creator becomes owner."""
    db = get_db()
    user_id = current_user["sub"]

    # One team per user for now (can be relaxed)
    existing = await db.teams.find_one({"owner_id": user_id}, {"_id": 0})
    if existing:
        raise HTTPException(status_code=400, detail="You already own a team. Delete it first.")

    team_id = str(uuid.uuid4())
    team = {
        "team_id": team_id,
        "name": body.name.strip(),
        "owner_id": user_id,
        "owner_email": current_user.get("email", ""),
        "created_at": _now(),
    }
    await db.teams.insert_one(team)

    # Add owner as member with role "owner"
    await db.team_members.insert_one({
        "team_id": team_id,
        "user_id": user_id,
        "email": current_user.get("email", ""),
        "role": "owner",
        "invited_at": _now(),
        "accepted": True,
    })

    team.pop("_id", None)
    return {"success": True, "team": team}


@router.get("/mine")
async def get_my_team(current_user: dict = Depends(get_current_user)):
    """Get the team the user owns OR is a member of."""
    db = get_db()
    user_id = current_user["sub"]

    # Check if owner
    team = await db.teams.find_one({"owner_id": user_id}, {"_id": 0})

    if not team:
        # Check membership
        membership = await db.team_members.find_one(
            {"user_id": user_id, "accepted": True}, {"_id": 0}
        )
        if membership:
            team = await db.teams.find_one(
                {"team_id": membership["team_id"]}, {"_id": 0}
            )

    if not team:
        return {"team": None, "members": []}

    # Load members
    members_cursor = db.team_members.find(
        {"team_id": team["team_id"]}, {"_id": 0}
    )
    members = await members_cursor.to_list(length=100)

    return {"team": team, "members": members}


@router.post("/{team_id}/invite")
async def invite_member(
    team_id: str,
    body: InviteMemberRequest,
    current_user: dict = Depends(get_current_user),
):
    """Invite a user to the team by email. Admin/owner only."""
    db = get_db()
    user_id = current_user["sub"]
    await _require_admin(team_id, user_id)

    if body.role not in ("admin", "viewer"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'viewer'")

    # Check if already invited
    existing = await db.team_members.find_one(
        {"team_id": team_id, "email": body.email}, {"_id": 0}
    )
    if existing:
        raise HTTPException(status_code=400, detail="This email is already a team member")

    # Try to find user_id from users collection
    invited_user = await db.users.find_one({"email": body.email}, {"_id": 0, "user_id": 1})
    invited_user_id = invited_user["user_id"] if invited_user else None

    member = {
        "team_id": team_id,
        "user_id": invited_user_id,
        "email": body.email,
        "role": body.role,
        "invited_at": _now(),
        "accepted": bool(invited_user_id),  # auto-accept if user exists
    }
    await db.team_members.insert_one(member)
    member.pop("_id", None)

    return {"success": True, "member": member}


@router.patch("/{team_id}/members/{email}/role")
async def update_member_role(
    team_id: str,
    email: str,
    body: UpdateRoleRequest,
    current_user: dict = Depends(get_current_user),
):
    """Change a member's role. Admin/owner only."""
    db = get_db()
    await _require_admin(team_id, current_user["sub"])

    if body.role not in ("admin", "viewer"):
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'viewer'")

    result = await db.team_members.update_one(
        {"team_id": team_id, "email": email},
        {"$set": {"role": body.role}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Member not found")

    return {"success": True, "email": email, "new_role": body.role}


@router.delete("/{team_id}/members/{email}")
async def remove_member(
    team_id: str,
    email: str,
    current_user: dict = Depends(get_current_user),
):
    """Remove a member from the team. Admin/owner only. Cannot remove owner."""
    db = get_db()
    team = await _require_admin(team_id, current_user["sub"])

    if email == team["owner_email"]:
        raise HTTPException(status_code=400, detail="Cannot remove the team owner")

    result = await db.team_members.delete_one({"team_id": team_id, "email": email})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Member not found")

    return {"success": True, "removed": email}


@router.delete("/{team_id}")
async def delete_team(
    team_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Delete a team entirely. Owner only."""
    db = get_db()
    team = await _get_team_or_404(team_id)

    if team["owner_id"] != current_user["sub"]:
        raise HTTPException(status_code=403, detail="Only the owner can delete the team")

    await db.teams.delete_one({"team_id": team_id})
    await db.team_members.delete_many({"team_id": team_id})

    return {"success": True, "message": "Team deleted"}

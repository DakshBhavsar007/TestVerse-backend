"""
TestVerse Phase 8B — Collaboration & Workflow Router
Features:
  1. Comments & annotations on test results
  2. Test approval workflows
  3. Activity feed / audit log
"""
import uuid
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.utils.auth import get_current_user
from app.utils.db_results import get_result
from app.database import get_db

router = APIRouter(prefix="/collab", tags=["Phase 8B — Collaboration"])


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _log_activity(
    db,
    user_id: str,
    user_email: str,
    action: str,
    entity_type: str,
    entity_id: str,
    detail: str = "",
    user_name: str = "Unknown",
):
    """Insert one activity log entry."""
    await db.activity_feed.insert_one({
        "activity_id": str(uuid.uuid4()),
        "user_id": user_id,
        "user_email": user_email,
        "user_name": user_name,
        "action": action,           # e.g. "comment_added", "approval_requested"
        "entity_type": entity_type, # "test_result" | "comment"
        "entity_id": entity_id,
        "detail": detail,
        "timestamp": now_iso(),
    })


# ─────────────────────────────────────────────────────────────
# 1. COMMENTS & ANNOTATIONS
# ─────────────────────────────────────────────────────────────

class CommentRequest(BaseModel):
    text: str
    annotation_key: Optional[str] = None   # e.g. "ssl", "speed" — pin to a check


class CommentEditRequest(BaseModel):
    text: str


@router.post("/test/{test_id}/comments")
async def add_comment(
    test_id: str,
    body: CommentRequest,
    current_user: dict = Depends(get_current_user),
):
    """Add a comment (optionally annotated to a specific check) on a test result."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    result = await get_result(test_id)
    if not result:
        raise HTTPException(status_code=404, detail="Test not found")

    comment = {
        "comment_id": str(uuid.uuid4()),
        "test_id": test_id,
        "user_id": current_user.get("id") or current_user.get("sub"),
        "user_email": current_user.get("sub", "unknown"),
        "user_name": current_user.get("name", "Unknown"),
        "text": body.text.strip(),
        "annotation_key": body.annotation_key,
        "created_at": now_iso(),
        "updated_at": None,
        "deleted": False,
    }
    await db.comments.insert_one(comment)
    comment.pop("_id", None)

    await _log_activity(
        db,
        user_id=current_user.get("id") or current_user.get("sub"),
        user_email=current_user.get("sub", "unknown"),
        user_name=current_user.get("name", "Unknown"),
        action="comment_added",
        entity_type="test_result",
        entity_id=test_id,
        detail=f"Commented on test {test_id}" + (f" [{body.annotation_key}]" if body.annotation_key else ""),
    )
    return {"success": True, "comment": comment}


@router.get("/test/{test_id}/comments")
async def list_comments(
    test_id: str,
    current_user: dict = Depends(get_current_user),
):
    """List all comments for a test result."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    cursor = db.comments.find(
        {"test_id": test_id, "deleted": False},
        {"_id": 0}
    ).sort("created_at", 1)
    comments = await cursor.to_list(length=200)
    return {"success": True, "total": len(comments), "comments": comments}


@router.patch("/comments/{comment_id}")
async def edit_comment(
    comment_id: str,
    body: CommentEditRequest,
    current_user: dict = Depends(get_current_user),
):
    """Edit your own comment."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    comment = await db.comments.find_one({"comment_id": comment_id, "deleted": False})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    
    current_uid = current_user.get("id") or current_user.get("sub")
    if comment["user_id"] != current_uid and comment["user_id"] != current_user.get("sub"):
        raise HTTPException(status_code=403, detail="You can only edit your own comments")

    await db.comments.update_one(
        {"comment_id": comment_id},
        {"$set": {"text": body.text.strip(), "updated_at": now_iso()}}
    )
    return {"success": True, "message": "Comment updated"}


@router.delete("/comments/{comment_id}")
async def delete_comment(
    comment_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Soft-delete your own comment."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    comment = await db.comments.find_one({"comment_id": comment_id, "deleted": False})
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
        
    current_uid = current_user.get("id") or current_user.get("sub")
    if comment["user_id"] != current_uid and comment["user_id"] != current_user.get("sub"):
        raise HTTPException(status_code=403, detail="You can only delete your own comments")

    await db.comments.update_one(
        {"comment_id": comment_id},
        {"$set": {"deleted": True, "updated_at": now_iso()}}
    )
    await _log_activity(
        db,
        user_id=current_uid,
        user_email=current_user.get("sub", "unknown"),
        user_name=current_user.get("name", "Unknown"),
        action="comment_deleted",
        entity_type="comment",
        entity_id=comment_id,
        detail=f"Deleted comment on test {comment['test_id']}",
    )
    return {"success": True, "message": "Comment deleted"}


# ─────────────────────────────────────────────────────────────
# 2. APPROVAL WORKFLOWS
# ─────────────────────────────────────────────────────────────

class ApprovalRequest(BaseModel):
    reviewers: List[str]        # list of reviewer user emails
    note: Optional[str] = None  # optional message to reviewers


class ApprovalDecision(BaseModel):
    decision: str               # "approved" | "rejected"
    note: Optional[str] = None


@router.post("/test/{test_id}/approval/request")
async def request_approval(
    test_id: str,
    body: ApprovalRequest,
    current_user: dict = Depends(get_current_user),
):
    """Request approval for a test result before deploying/acting on it."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    result = await get_result(test_id)
    if not result:
        raise HTTPException(status_code=404, detail="Test not found")

    # Cancel any existing open approval for this test
    await db.approvals.update_many(
        {"test_id": test_id, "status": "pending"},
        {"$set": {"status": "superseded", "updated_at": now_iso()}}
    )

    approval = {
        "approval_id": str(uuid.uuid4()),
        "test_id": test_id,
        "requested_by": current_user.get("id") or current_user.get("sub"),
        "requested_by_email": current_user.get("sub", "unknown"),
        "reviewers": body.reviewers,
        "note": body.note or "",
        "status": "pending",        # pending | approved | rejected | superseded
        "decisions": [],            # list of {reviewer, decision, note, decided_at}
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    await db.approvals.insert_one(approval)
    approval.pop("_id", None)

    await _log_activity(
        db,
        user_id=current_user.get("id") or current_user.get("sub"),
        user_email=current_user.get("sub", "unknown"),
        user_name=current_user.get("name", "Unknown"),
        action="approval_requested",
        entity_type="test_result",
        entity_id=test_id,
        detail=f"Approval requested from: {', '.join(body.reviewers)}",
    )
    return {"success": True, "approval": approval}


@router.get("/test/{test_id}/approval")
async def get_approval(
    test_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get the current approval status for a test result."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    approval = await db.approvals.find_one(
        {"test_id": test_id, "status": "pending"},
        {"_id": 0}
    )
    if not approval:
        # Return latest non-pending one
        approval = await db.approvals.find_one(
            {"test_id": test_id},
            {"_id": 0},
            sort=[("created_at", -1)]
        )
    return {"success": True, "approval": approval}


@router.post("/approval/{approval_id}/decide")
async def decide_approval(
    approval_id: str,
    body: ApprovalDecision,
    current_user: dict = Depends(get_current_user),
):
    """Approve or reject a test result (reviewer only)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    if body.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="Decision must be 'approved' or 'rejected'")

    approval = await db.approvals.find_one({"approval_id": approval_id})
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")
    if approval["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Approval is already {approval['status']}")

    reviewer_email = current_user.get("sub", "")
    current_uid = current_user.get("id") or reviewer_email
    if reviewer_email not in approval["reviewers"] and current_uid != approval["requested_by"]:
        raise HTTPException(status_code=403, detail="You are not a reviewer for this approval")

    decision_entry = {
        "reviewer": current_uid,
        "reviewer_email": reviewer_email,
        "decision": body.decision,
        "note": body.note or "",
        "decided_at": now_iso(),
    }

    # Add decision and update overall status
    new_decisions = approval.get("decisions", []) + [decision_entry]
    # If anyone rejects, reject whole approval; if all approve, approve it
    rejected = any(d["decision"] == "rejected" for d in new_decisions)
    all_approved = all(
        any(d["reviewer_email"] == r and d["decision"] == "approved" for d in new_decisions)
        for r in approval["reviewers"]
    )
    new_status = "rejected" if rejected else "approved" if all_approved else "pending"

    await db.approvals.update_one(
        {"approval_id": approval_id},
        {"$set": {
            "decisions": new_decisions,
            "status": new_status,
            "updated_at": now_iso(),
        }}
    )

    await _log_activity(
        db,
        user_id=current_uid,
        user_email=reviewer_email,
        user_name=current_user.get("name", "Unknown"),
        action=f"approval_{body.decision}",
        entity_type="test_result",
        entity_id=approval["test_id"],
        detail=f"Test {approval['test_id']} was {body.decision}" + (f": {body.note}" if body.note else ""),
    )
    return {"success": True, "status": new_status, "decision": decision_entry}


@router.get("/approvals/pending")
async def my_pending_approvals(current_user: dict = Depends(get_current_user)):
    """Get all approvals waiting for the current user's decision."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    email = current_user.get("email", "")
    cursor = db.approvals.find(
        {"status": "pending", "reviewers": email},
        {"_id": 0}
    ).sort("created_at", -1)
    approvals = await cursor.to_list(length=50)
    return {"success": True, "total": len(approvals), "approvals": approvals}


# ─────────────────────────────────────────────────────────────
# 3. ACTIVITY FEED / AUDIT LOG
# ─────────────────────────────────────────────────────────────

@router.get("/activity")
async def get_activity_feed(
    limit: int = 50,
    skip: int = 0,
    entity_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    """Get activity feed — optionally filtered by a specific test/entity."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    query: dict = {}
    # If not filtering by entity, show only this user's activity
    if entity_id:
        query["entity_id"] = entity_id
    else:
        query["user_id"] = current_user["sub"]

    cursor = db.activity_feed.find(query, {"_id": 0}).sort("timestamp", -1).skip(skip).limit(limit)
    activities = await cursor.to_list(length=limit)
    total = await db.activity_feed.count_documents(query)

    return {
        "success": True,
        "total": total,
        "skip": skip,
        "limit": limit,
        "activities": activities,
    }


@router.get("/activity/team")
async def get_team_activity(
    limit: int = 100,
    current_user: dict = Depends(get_current_user),
):
    """Get all recent activity across all users (team-wide feed)."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    cursor = db.activity_feed.find({}, {"_id": 0}).sort("timestamp", -1).limit(limit)
    activities = await cursor.to_list(length=limit)
    return {"success": True, "total": len(activities), "activities": activities}

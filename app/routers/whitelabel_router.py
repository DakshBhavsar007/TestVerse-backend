"""
app/routers/whitelabel_router.py
Phase 6c — White-label: store custom branding per user.

MongoDB collection: whitelabel_configs
  { user_id, company_name, logo_url, primary_color, accent_color,
    report_footer, hide_testverse_branding, updated_at }
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ..utils.auth import get_current_user
from ..database import get_db

router = APIRouter(prefix="/whitelabel", tags=["White Label"])

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

class WhiteLabelConfig(BaseModel):
    company_name: str = ""
    logo_url: Optional[str] = ""
    primary_color: str = "#6366f1"
    accent_color: str = "#8b5cf6"
    report_footer: str = ""
    hide_testverse_branding: bool = False

@router.get("/config")
async def get_config(current_user: dict = Depends(get_current_user)):
    db = get_db()
    config = await db.whitelabel_configs.find_one(
        {"user_id": current_user["sub"]}, {"_id": 0}
    )
    if not config:
        return {"configured": False, "config": None}
    return {"configured": True, "config": config}

@router.post("/config")
async def save_config(
    body: WhiteLabelConfig,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    now = _now()
    config = {
        "user_id":                  current_user["sub"],
        "company_name":             body.company_name.strip(),
        "logo_url":                 (body.logo_url or "").strip(),
        "primary_color":            body.primary_color or "#6366f1",
        "accent_color":             body.accent_color or "#8b5cf6",
        "report_footer":            body.report_footer.strip(),
        "hide_testverse_branding":  body.hide_testverse_branding,
        "updated_at":               now,
    }
    existing = await db.whitelabel_configs.find_one({"user_id": current_user["sub"]})
    if existing:
        await db.whitelabel_configs.update_one(
            {"user_id": current_user["sub"]}, {"$set": config}
        )
    else:
        config["created_at"] = now
        await db.whitelabel_configs.insert_one(config)
    config.pop("_id", None)
    return {"success": True, "config": config}

@router.delete("/config")
async def delete_config(current_user: dict = Depends(get_current_user)):
    db = get_db()
    await db.whitelabel_configs.delete_one({"user_id": current_user["sub"]})
    return {"success": True, "message": "White-label config removed"}

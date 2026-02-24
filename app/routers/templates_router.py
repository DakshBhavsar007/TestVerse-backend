"""
app/routers/templates_router.py
Phase 7A: Test Templates & Presets
- Save test configurations
- Quick-start templates
- Import/export test suites
"""
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, SecretStr
from enum import Enum
from app.database import get_db
from app.utils.auth import get_current_user
import uuid
import json
import io

router = APIRouter(prefix="/templates", tags=["Templates"])


class TemplateType(str, Enum):
    BASIC = "basic"
    LOGIN = "login"
    API = "api"
    CUSTOM = "custom"


class TemplateVisibility(str, Enum):
    PRIVATE = "private"
    TEAM = "team"
    PUBLIC = "public"


# ─── Request/Response Models ───────────────────────────────────────────────────

class TestTemplate(BaseModel):
    template_id: Optional[str] = None
    user_id: str
    team_id: Optional[str] = None
    
    # Metadata
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    type: TemplateType = TemplateType.BASIC
    visibility: TemplateVisibility = TemplateVisibility.PRIVATE
    tags: List[str] = []
    
    # Test configuration
    url: Optional[str] = None
    
    # Login config (if type == login)
    login_url: Optional[str] = None
    username_selector: Optional[str] = None
    password_selector: Optional[str] = None
    submit_selector: Optional[str] = None
    success_indicator: Optional[str] = None
    
    # Advanced options
    crawl_enabled: bool = True
    max_crawl_pages: int = 10
    check_broken_links: bool = True
    check_images: bool = True
    check_js_errors: bool = True
    check_mobile: bool = True
    check_ssl: bool = True
    
    # Scheduling
    schedule_enabled: bool = False
    schedule_cron: Optional[str] = None
    
    # Notifications
    notify_on_complete: bool = True
    notify_on_failure: bool = True
    notify_on_score_drop: bool = True
    
    # Usage stats
    use_count: int = 0
    last_used: Optional[datetime] = None
    
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CreateTemplateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    type: TemplateType = TemplateType.BASIC
    visibility: TemplateVisibility = TemplateVisibility.PRIVATE
    tags: List[str] = []
    url: Optional[str] = None
    login_url: Optional[str] = None
    username_selector: Optional[str] = None
    password_selector: Optional[str] = None
    submit_selector: Optional[str] = None
    success_indicator: Optional[str] = None
    crawl_enabled: bool = True
    max_crawl_pages: int = 10
    check_broken_links: bool = True
    check_images: bool = True
    check_js_errors: bool = True
    check_mobile: bool = True
    check_ssl: bool = True
    schedule_enabled: bool = False
    schedule_cron: Optional[str] = None
    notify_on_complete: bool = True
    notify_on_failure: bool = True
    notify_on_score_drop: bool = True
    team_id: Optional[str] = None


class UpdateTemplateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    visibility: Optional[TemplateVisibility] = None
    tags: Optional[List[str]] = None
    url: Optional[str] = None
    login_url: Optional[str] = None
    username_selector: Optional[str] = None
    password_selector: Optional[str] = None
    submit_selector: Optional[str] = None
    success_indicator: Optional[str] = None
    crawl_enabled: Optional[bool] = None
    max_crawl_pages: Optional[int] = None
    check_broken_links: Optional[bool] = None
    check_images: Optional[bool] = None
    check_js_errors: Optional[bool] = None
    check_mobile: Optional[bool] = None
    check_ssl: Optional[bool] = None
    schedule_enabled: Optional[bool] = None
    schedule_cron: Optional[str] = None
    notify_on_complete: Optional[bool] = None
    notify_on_failure: Optional[bool] = None
    notify_on_score_drop: Optional[bool] = None


class ApplyTemplateRequest(BaseModel):
    template_id: str
    url: str  # Override URL
    username: Optional[str] = None  # For login templates
    password: Optional[SecretStr] = None


class ExportFormat(str, Enum):
    JSON = "json"
    YAML = "yaml"


# ─── Helper Functions ──────────────────────────────────────────────────────────

async def increment_use_count(template_id: str):
    """Increment template usage counter."""
    db = get_db()
    if db is None:
        return
    
    await db.templates.update_one(
        {"template_id": template_id},
        {
            "$inc": {"use_count": 1},
            "$set": {"last_used": datetime.now(timezone.utc)}
        }
    )


def template_to_test_config(template: dict, url: str, username: Optional[str] = None, password: Optional[str] = None) -> dict:
    """Convert template to test configuration."""
    config = {
        "url": url,
        "crawl_enabled": template.get("crawl_enabled", True),
        "max_crawl_pages": template.get("max_crawl_pages", 10),
        "checks": {
            "uptime": True,
            "speed": True,
            "ssl": template.get("check_ssl", True),
            "broken_links": template.get("check_broken_links", True),
            "images": template.get("check_images", True),
            "js_errors": template.get("check_js_errors", True),
            "mobile": template.get("check_mobile", True),
        }
    }
    
    # Add login config if template is login type
    if template.get("type") == TemplateType.LOGIN.value and username and password:
        config["login"] = {
            "enabled": True,
            "login_url": template.get("login_url"),
            "username": username,
            "password": password,
            "username_selector": template.get("username_selector"),
            "password_selector": template.get("password_selector"),
            "submit_selector": template.get("submit_selector"),
            "success_indicator": template.get("success_indicator"),
        }
    
    return config


# ─── Built-in Templates ────────────────────────────────────────────────────────

BUILTIN_TEMPLATES = [
    {
        "template_id": "builtin_basic",
        "name": "Basic Website Check",
        "description": "Quick check for uptime, speed, SSL, and broken links",
        "type": TemplateType.BASIC.value,
        "visibility": TemplateVisibility.PUBLIC.value,
        "tags": ["basic", "quick", "starter"],
        "check_broken_links": True,
        "check_images": True,
        "check_js_errors": True,
        "check_mobile": True,
        "check_ssl": True,
        "crawl_enabled": False,
    },
    {
        "template_id": "builtin_comprehensive",
        "name": "Comprehensive Scan",
        "description": "Deep crawl with all checks enabled",
        "type": TemplateType.BASIC.value,
        "visibility": TemplateVisibility.PUBLIC.value,
        "tags": ["comprehensive", "deep", "thorough"],
        "check_broken_links": True,
        "check_images": True,
        "check_js_errors": True,
        "check_mobile": True,
        "check_ssl": True,
        "crawl_enabled": True,
        "max_crawl_pages": 50,
    },
    {
        "template_id": "builtin_speed",
        "name": "Speed & Performance",
        "description": "Focus on load times and performance metrics",
        "type": TemplateType.BASIC.value,
        "visibility": TemplateVisibility.PUBLIC.value,
        "tags": ["speed", "performance", "optimization"],
        "check_broken_links": False,
        "check_images": False,
        "check_js_errors": True,
        "check_mobile": True,
        "check_ssl": False,
        "crawl_enabled": False,
    },
    {
        "template_id": "builtin_security",
        "name": "Security Audit",
        "description": "SSL certificate validation and security checks",
        "type": TemplateType.BASIC.value,
        "visibility": TemplateVisibility.PUBLIC.value,
        "tags": ["security", "ssl", "audit"],
        "check_broken_links": False,
        "check_images": False,
        "check_js_errors": True,
        "check_mobile": False,
        "check_ssl": True,
        "crawl_enabled": True,
        "max_crawl_pages": 20,
    },
    {
        "template_id": "builtin_login",
        "name": "Login Flow Test",
        "description": "Test authentication and post-login functionality",
        "type": TemplateType.LOGIN.value,
        "visibility": TemplateVisibility.PUBLIC.value,
        "tags": ["login", "auth", "protected"],
        "check_broken_links": True,
        "check_images": True,
        "check_js_errors": True,
        "check_mobile": False,
        "check_ssl": True,
        "crawl_enabled": False,
    },
]


# ─── API Endpoints ─────────────────────────────────────────────────────────────

@router.post("/", status_code=201)
async def create_template(
    req: CreateTemplateRequest,
    current_user: dict = Depends(get_current_user)
):
    """Create a new test template."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")
    
    user_id = current_user.get("id") or current_user.get("sub")
    template_id = str(uuid.uuid4())
    
    template = {
        "template_id": template_id,
        "user_id": user_id,
        "team_id": req.team_id,
        "name": req.name,
        "description": req.description,
        "type": req.type.value,
        "visibility": req.visibility.value,
        "tags": req.tags,
        "url": req.url,
        "login_url": req.login_url,
        "username_selector": req.username_selector,
        "password_selector": req.password_selector,
        "submit_selector": req.submit_selector,
        "success_indicator": req.success_indicator,
        "crawl_enabled": req.crawl_enabled,
        "max_crawl_pages": req.max_crawl_pages,
        "check_broken_links": req.check_broken_links,
        "check_images": req.check_images,
        "check_js_errors": req.check_js_errors,
        "check_mobile": req.check_mobile,
        "check_ssl": req.check_ssl,
        "schedule_enabled": req.schedule_enabled,
        "schedule_cron": req.schedule_cron,
        "notify_on_complete": req.notify_on_complete,
        "notify_on_failure": req.notify_on_failure,
        "notify_on_score_drop": req.notify_on_score_drop,
        "use_count": 0,
        "last_used": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    
    await db.templates.insert_one(template)
    
    return {
        "success": True,
        "message": "Template created",
        "template_id": template_id,
        "template": template,
    }


@router.get("/")
async def list_templates(
    type: Optional[TemplateType] = None,
    team_id: Optional[str] = None,
    include_public: bool = True,
    current_user: dict = Depends(get_current_user)
):
    """List templates (user's + team's + public)."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")
    
    templates = []
    
    # Add built-in public templates
    if include_public:
        templates.extend(BUILTIN_TEMPLATES)
    
    if db is not None:
        # User's private templates
        query = {"user_id": user_id}
        if type:
            query["type"] = type.value
        user_templates = await db.templates.find(query).to_list(100)
        templates.extend(user_templates)
        
        # Team templates
        if team_id:
            team_templates = await db.templates.find({
                "team_id": team_id,
                "visibility": TemplateVisibility.TEAM.value
            }).to_list(100)
            templates.extend(team_templates)
        
        # Public templates from other users
        if include_public:
            public_templates = await db.templates.find({
                "visibility": TemplateVisibility.PUBLIC.value,
                "user_id": {"$ne": user_id}
            }).to_list(100)
            templates.extend(public_templates)
    
    return {
        "success": True,
        "total": len(templates),
        "templates": [
            {
                "template_id": t["template_id"],
                "name": t["name"],
                "description": t.get("description"),
                "type": t["type"],
                "visibility": t.get("visibility", "public"),
                "tags": t.get("tags", []),
                "use_count": t.get("use_count", 0),
                "is_builtin": t["template_id"].startswith("builtin_"),
            }
            for t in templates
        ],
    }


@router.get("/{template_id}")
async def get_template(
    template_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get a specific template."""
    # Check built-in templates first
    for builtin in BUILTIN_TEMPLATES:
        if builtin["template_id"] == template_id:
            return {"success": True, "template": builtin}
    
    db = get_db()
    if db is None:
        raise HTTPException(404, "Template not found")
    
    user_id = current_user.get("id") or current_user.get("sub")
    
    # Find template (user's own, team, or public)
    template = await db.templates.find_one({
        "$or": [
            {"template_id": template_id, "user_id": user_id},
            {"template_id": template_id, "visibility": TemplateVisibility.PUBLIC.value},
            {"template_id": template_id, "visibility": TemplateVisibility.TEAM.value},
        ]
    })
    
    if not template:
        raise HTTPException(404, "Template not found")
    
    return {"success": True, "template": template}


@router.patch("/{template_id}")
async def update_template(
    template_id: str,
    req: UpdateTemplateRequest,
    current_user: dict = Depends(get_current_user)
):
    """Update a template (owner only)."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")
    
    user_id = current_user.get("id") or current_user.get("sub")
    
    update_data = req.model_dump(exclude_unset=True)
    if "visibility" in update_data:
        update_data["visibility"] = update_data["visibility"].value
    update_data["updated_at"] = datetime.now(timezone.utc)
    
    result = await db.templates.update_one(
        {"template_id": template_id, "user_id": user_id},
        {"$set": update_data}
    )
    
    if result.matched_count == 0:
        raise HTTPException(404, "Template not found or access denied")
    
    return {"success": True, "message": "Template updated"}


@router.delete("/{template_id}")
async def delete_template(
    template_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Delete a template (owner only)."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")
    
    user_id = current_user.get("id") or current_user.get("sub")
    result = await db.templates.delete_one({
        "template_id": template_id,
        "user_id": user_id
    })
    
    if result.deleted_count == 0:
        raise HTTPException(404, "Template not found or access denied")
    
    return {"success": True, "message": "Template deleted"}


@router.post("/apply")
async def apply_template(
    req: ApplyTemplateRequest,
    current_user: dict = Depends(get_current_user)
):
    """Apply a template to create a test configuration."""
    # Get template
    template = None
    for builtin in BUILTIN_TEMPLATES:
        if builtin["template_id"] == req.template_id:
            template = builtin
            break
    
    if not template:
        db = get_db()
        if db is not None:
            user_id = current_user.get("id") or current_user.get("sub")
            template = await db.templates.find_one({
                "$or": [
                    {"template_id": req.template_id, "user_id": user_id},
                    {"template_id": req.template_id, "visibility": TemplateVisibility.PUBLIC.value},
                    {"template_id": req.template_id, "visibility": TemplateVisibility.TEAM.value},
                ]
            })
    
    if not template:
        raise HTTPException(404, "Template not found")
    
    # Convert to test config
    password = req.password.get_secret_value() if req.password else None
    config = template_to_test_config(template, req.url, req.username, password)
    
    # Increment usage
    if not req.template_id.startswith("builtin_"):
        await increment_use_count(req.template_id)
    
    return {
        "success": True,
        "message": "Template applied",
        "config": config,
    }


@router.post("/export")
async def export_templates(
    template_ids: List[str],
    format: ExportFormat = ExportFormat.JSON,
    current_user: dict = Depends(get_current_user)
):
    """Export templates to JSON/YAML."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")
    
    user_id = current_user.get("id") or current_user.get("sub")
    templates = await db.templates.find({
        "template_id": {"$in": template_ids},
        "user_id": user_id
    }).to_list(100)
    
    if not templates:
        raise HTTPException(404, "No templates found")
    
    # Clean MongoDB _id
    for t in templates:
        if "_id" in t:
            del t["_id"]
        # Convert datetime to ISO string
        for field in ["created_at", "updated_at", "last_used"]:
            if field in t and t[field]:
                t[field] = t[field].isoformat()
    
    export_data = {
        "version": "1.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "templates": templates
    }
    
    if format == ExportFormat.JSON:
        content = json.dumps(export_data, indent=2)
        media_type = "application/json"
        filename = "templates_export.json"
    else:  # YAML
        import yaml
        content = yaml.dump(export_data, default_flow_style=False)
        media_type = "application/x-yaml"
        filename = "templates_export.yaml"
    
    return StreamingResponse(
        io.BytesIO(content.encode()),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.post("/import")
async def import_templates(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    """Import templates from JSON/YAML file."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")
    
    user_id = current_user.get("id") or current_user.get("sub")
    
    # Read file
    content = await file.read()
    
    try:
        if file.filename.endswith(".json"):
            data = json.loads(content)
        elif file.filename.endswith((".yaml", ".yml")):
            import yaml
            data = yaml.safe_load(content)
        else:
            raise HTTPException(400, "Unsupported file format. Use JSON or YAML")
    except Exception as e:
        raise HTTPException(400, f"Failed to parse file: {str(e)}")
    
    templates = data.get("templates", [])
    if not templates:
        raise HTTPException(400, "No templates found in file")
    
    imported = []
    for template in templates:
        # Generate new IDs
        template["template_id"] = str(uuid.uuid4())
        template["user_id"] = user_id
        template["created_at"] = datetime.now(timezone.utc)
        template["updated_at"] = datetime.now(timezone.utc)
        template["use_count"] = 0
        template["last_used"] = None
        
        await db.templates.insert_one(template)
        imported.append(template["template_id"])
    
    return {
        "success": True,
        "message": f"Imported {len(imported)} templates",
        "template_ids": imported,
    }


@router.get("/stats/popular")
async def get_popular_templates(limit: int = 10):
    """Get most used templates (public only)."""
    db = get_db()
    
    # Start with built-in templates
    popular = sorted(BUILTIN_TEMPLATES, key=lambda x: x.get("use_count", 0), reverse=True)[:limit]
    
    if db is not None:
        public_templates = await db.templates.find({
            "visibility": TemplateVisibility.PUBLIC.value
        }).sort("use_count", -1).limit(limit).to_list(limit)
        popular.extend(public_templates)
    
    return {
        "success": True,
        "templates": [
            {
                "template_id": t["template_id"],
                "name": t["name"],
                "description": t.get("description"),
                "use_count": t.get("use_count", 0),
                "tags": t.get("tags", []),
            }
            for t in popular[:limit]
        ],
    }

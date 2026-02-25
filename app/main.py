"""
TestVerse FastAPI Application — main entry point (v3.1 - Phase 8A)
Phase 8A: AI Intelligence — Test Suggestions, Anomaly Detection, NL→Test, Chat
"""
import os, sys, asyncio
from contextlib import asynccontextmanager

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .database import connect_db, close_db
from .routers.test_router import router as test_router
from .routers.history_router import router as history_router
from .routers.auth_router import router as auth_router
from .routers.dashboard_router import router as dashboard_router
from .routers.schedule_router import router as schedule_router
from .routers.share_router import router as share_router
from .routers.pdf_router import router as pdf_router
from .routers.teams_router import router as teams_router
from .routers.slack_router import router as slack_router
from .routers.api_keys_router import router as api_keys_router
from .routers.bulk_router import router as bulk_router
from .routers.whitelabel_router import router as whitelabel_router
# Phase 7A routers
from .routers.rbac_router import router as rbac_router
from .routers.notifications_router import router as notifications_router
from .routers.templates_router import router as templates_router
from .routers.monitoring_router import router as monitoring_router
from .routers.reporting_router import router as reporting_router
from .routers.billing_router import router as billing_router
from .routers.compliance_router import router as compliance_router
from .routers.assertions_router import router as assertions_router
# Phase 8A routers
from .routers.ai_router import router as ai_router
# Phase 8B routers
from .routers.collaboration_router import router as collaboration_router
# Phase 8C routers
from .routers.cicd_router import router as cicd_router
# Phase 8E routers
from .routers.openapi_router import router as openapi_router

from .config import get_settings
from .middleware.rate_limit import RateLimitMiddleware

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(settings.reports_dir, exist_ok=True)
    try:
        await connect_db()
        from .database import get_db
        db = get_db()
        if db is not None:
            # Existing indexes
            await db.users.create_index("email", unique=True)
            await db.schedules.create_index("schedule_id", unique=True)
            await db.schedules.create_index("user_id")
            await db.schedules.create_index([("url", 1), ("user_id", 1)])
            await db.test_results.create_index("share_token", unique=True, sparse=True)
            await db.teams.create_index("team_id", unique=True)
            await db.teams.create_index("owner_id")
            await db.team_members.create_index([("team_id", 1), ("email", 1)], unique=True)
            await db.team_members.create_index("user_id")
            await db.slack_configs.create_index("user_id", unique=True)
            await db.api_keys.create_index("key_hash", unique=True)
            await db.api_keys.create_index([("user_id", 1), ("active", 1)])
            await db.bulk_batches.create_index("batch_id", unique=True)
            await db.bulk_batches.create_index("user_id")
            await db.whitelabel_configs.create_index("user_id", unique=True)
            # Phase 7A indexes
            await db.role_assignments.create_index("user_id", unique=True)
            await db.audit_logs.create_index([("user_id", 1), ("timestamp", -1)])
            await db.audit_logs.create_index("timestamp")
            await db.notification_rules.create_index("rule_id", unique=True)
            await db.notification_rules.create_index([("user_id", 1), ("enabled", 1)])
            await db.notification_logs.create_index([("user_id", 1), ("sent_at", -1)])
            await db.templates.create_index("template_id", unique=True)
            await db.templates.create_index([("user_id", 1), ("visibility", 1)])
            await db.monitors.create_index("monitor_id", unique=True)
            await db.monitors.create_index([("user_id", 1), ("enabled", 1)])
            await db.monitor_checks.create_index([("monitor_id", 1), ("timestamp", -1)])
            await db.incidents.create_index([("monitor_id", 1), ("status", 1)])
            await db.sla_reports.create_index("monitor_id")
            # Phase 8B indexes
            await db.comments.create_index([("test_id", 1), ("deleted", 1)])
            await db.comments.create_index("comment_id", unique=True)
            await db.approvals.create_index("approval_id", unique=True)
            await db.approvals.create_index([("test_id", 1), ("status", 1)])
            await db.approvals.create_index([("reviewers", 1), ("status", 1)])
            await db.activity_feed.create_index([("user_id", 1), ("timestamp", -1)])
            await db.activity_feed.create_index([("entity_id", 1), ("timestamp", -1)])
            await db.activity_feed.create_index("timestamp")
            await db.activity_feed.create_index("user_name")  # for faster name lookups
            # Phase 8C indexes
            await db.cicd_configs.create_index([("user_id", 1), ("provider", 1)], unique=True)
            await db.cicd_triggers.create_index([("user_id", 1), ("triggered_at", -1)])
            await db.cicd_triggers.create_index("trigger_id", unique=True)
            await db.jira_configs.create_index("user_id", unique=True)
            await db.imported_tests.create_index("import_id", unique=True)
            await db.imported_tests.create_index([("user_id", 1), ("imported_at", -1)])
    except Exception as e:
        print(f"⚠️  MongoDB not available — using in-memory store: {e}")

    from .services.scheduler import start_scheduler, load_schedules_from_db
    start_scheduler()
    try:
        await load_schedules_from_db()
    except Exception as e:
        print(f"⚠️  Could not load schedules: {e}")

    yield

    from .services.scheduler import stop_scheduler
    stop_scheduler()
    await close_db()


app = FastAPI(
    title="TestVerse API",
    description=(
        "🧪 **TestVerse** — Enterprise Website Testing Platform\n\n"
        "Features:\n"
        "- Comprehensive website health checks\n"
        "- Role-based access control (RBAC)\n"
        "- Advanced notification system\n"
        "- Test templates & automation\n"
        "- Performance monitoring & SLA tracking\n"
        "- 🤖 AI Intelligence (Phase 8A)\n"
        "- 🤝 Collaboration & Workflow (Phase 8B)\n"
        "- 🔗 CI/CD Integrations (Phase 8C)\n"
    ),
    version="3.3.0",
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url="/redoc" if settings.environment != "production" else None,
    lifespan=lifespan,
)

app.add_middleware(RateLimitMiddleware)

# Build allowed origins — Vercel frontend is always included regardless of environment.
# Add EXTRA_ALLOWED_ORIGINS env var (comma-separated) for preview/staging URLs.
_base_origins = [
    "https://testverse-frontend.vercel.app",
    "https://yourdomain.com",
]
_dev_origins = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
]
_extra = os.getenv("EXTRA_ALLOWED_ORIGINS", "")
_extra_origins = [o.strip() for o in _extra.split(",") if o.strip()]

ALLOWED_ORIGINS = _base_origins + _extra_origins + (
    _dev_origins if settings.environment != "production" else []
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth_router)
app.include_router(test_router)
app.include_router(history_router)
app.include_router(dashboard_router)
app.include_router(schedule_router)
app.include_router(share_router)
app.include_router(pdf_router)
app.include_router(teams_router)
app.include_router(slack_router)
app.include_router(api_keys_router)
app.include_router(bulk_router)
app.include_router(whitelabel_router)
# Phase 7A
app.include_router(rbac_router)
app.include_router(notifications_router)
app.include_router(templates_router)
app.include_router(monitoring_router)
app.include_router(reporting_router)
app.include_router(billing_router)
app.include_router(compliance_router)
app.include_router(assertions_router)
# Phase 8A
app.include_router(ai_router)
# Phase 8B
app.include_router(collaboration_router)
# Phase 8C
app.include_router(cicd_router)
# Phase 8E
app.include_router(openapi_router)

os.makedirs(settings.reports_dir, exist_ok=True)
app.mount("/reports", StaticFiles(directory=settings.reports_dir), name="reports")


@app.get("/", tags=["Health"])
async def root():
    return {"service": "TestVerse API", "version": "3.1.0", "status": "running", "docs": "/docs"}


@app.api_route("/health", methods=["GET", "HEAD"], tags=["Health"])
async def health():
    from .database import get_db
    from .services.scheduler import scheduler
    return {
        "status": "ok",
        "database": "connected" if get_db() is not None else "in-memory fallback",
        "environment": settings.environment,
        "scheduler": "running" if scheduler.running else "stopped",
        "scheduled_jobs": len(scheduler.get_jobs()),
        "phase": "8E — OpenAPI Import + Profile",
        "ai_enabled": bool(os.getenv("OPENAI_API_KEY")),
    }

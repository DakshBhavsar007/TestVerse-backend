"""
app/routers/monitoring_router.py
Phase 7A: Performance & Monitoring
- Response time tracking
- Uptime monitoring
- SLA reporting
- Performance trends
"""
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from enum import Enum
from app.database import get_db
from app.utils.auth import get_current_user
import uuid

router = APIRouter(prefix="/monitoring", tags=["Monitoring"])


class MonitorStatus(str, Enum):
    UP = "up"
    DOWN = "down"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class IncidentSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SLAStatus(str, Enum):
    MET = "met"
    AT_RISK = "at_risk"
    BREACHED = "breached"


# ─── Request/Response Models ───────────────────────────────────────────────────

class Monitor(BaseModel):
    monitor_id: Optional[str] = None
    user_id: str
    team_id: Optional[str] = None
    
    # Configuration
    name: str = Field(..., min_length=1, max_length=100)
    url: str
    enabled: bool = True
    interval_minutes: int = Field(5, ge=1, le=1440)  # 1 min to 24 hours
    
    # Thresholds
    response_time_threshold_ms: int = Field(3000, ge=100)
    uptime_threshold_percent: float = Field(99.9, ge=0, le=100)
    
    # Status
    current_status: MonitorStatus = MonitorStatus.UNKNOWN
    last_check_at: Optional[datetime] = None
    last_check_response_ms: Optional[float] = None
    last_incident_at: Optional[datetime] = None
    
    # Stats (rolling 24h)
    uptime_24h: Optional[float] = None
    avg_response_24h: Optional[float] = None
    checks_24h: int = 0
    failures_24h: int = 0
    
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CreateMonitorRequest(BaseModel):
    name: str = Field(..., min_length=1)
    url: str
    interval_minutes: int = Field(5, ge=1, le=1440)
    response_time_threshold_ms: int = Field(3000, ge=100)
    uptime_threshold_percent: float = Field(99.9, ge=0, le=100)
    team_id: Optional[str] = None


class UpdateMonitorRequest(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = None
    response_time_threshold_ms: Optional[int] = None
    uptime_threshold_percent: Optional[float] = None


class MonitorCheck(BaseModel):
    check_id: str
    monitor_id: str
    timestamp: datetime
    status: MonitorStatus
    response_time_ms: Optional[float] = None
    status_code: Optional[int] = None
    error: Optional[str] = None
    ssl_valid: Optional[bool] = None
    ssl_expires_at: Optional[datetime] = None


class Incident(BaseModel):
    incident_id: str
    monitor_id: str
    user_id: str
    severity: IncidentSeverity
    status: str  # "open", "acknowledged", "resolved"
    title: str
    description: Optional[str] = None
    started_at: datetime
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    affected_checks: int = 0


class SLAReport(BaseModel):
    report_id: str
    monitor_id: str
    period_start: datetime
    period_end: datetime
    
    # Metrics
    total_checks: int
    successful_checks: int
    failed_checks: int
    uptime_percent: float
    downtime_minutes: int
    avg_response_time_ms: float
    p95_response_time_ms: float
    p99_response_time_ms: float
    
    # SLA compliance
    sla_target: float
    sla_status: SLAStatus
    incidents: int
    mttr_minutes: Optional[float] = None  # Mean Time To Recovery
    
    generated_at: datetime


class PerformanceMetrics(BaseModel):
    timestamp: datetime
    response_time_ms: float
    status_code: Optional[int]
    status: MonitorStatus


# ─── Helper Functions ──────────────────────────────────────────────────────────

async def calculate_uptime_24h(monitor_id: str) -> Dict[str, Any]:
    """Calculate 24h uptime statistics."""
    db = get_db()
    if db is None:
        return {"uptime": 100.0, "checks": 0, "failures": 0, "avg_response": 0}
    
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    checks = await db.monitor_checks.find({
        "monitor_id": monitor_id,
        "timestamp": {"$gte": cutoff}
    }).to_list(1000)
    
    if not checks:
        return {"uptime": 100.0, "checks": 0, "failures": 0, "avg_response": 0}
    
    total = len(checks)
    failures = sum(1 for c in checks if c["status"] != MonitorStatus.UP.value)
    response_times = [c["response_time_ms"] for c in checks if c.get("response_time_ms")]
    
    uptime = ((total - failures) / total * 100) if total > 0 else 100.0
    avg_response = sum(response_times) / len(response_times) if response_times else 0
    
    return {
        "uptime": round(uptime, 2),
        "checks": total,
        "failures": failures,
        "avg_response": round(avg_response, 2)
    }


async def create_incident(
    monitor_id: str,
    user_id: str,
    severity: IncidentSeverity,
    title: str,
    description: Optional[str] = None
) -> str:
    """Create a new incident."""
    db = get_db()
    if db is None:
        return ""
    
    incident_id = str(uuid.uuid4())
    incident = {
        "incident_id": incident_id,
        "monitor_id": monitor_id,
        "user_id": user_id,
        "severity": severity.value,
        "status": "open",
        "title": title,
        "description": description,
        "started_at": datetime.now(timezone.utc),
        "acknowledged_at": None,
        "resolved_at": None,
        "duration_minutes": None,
        "affected_checks": 1,
    }
    
    await db.incidents.insert_one(incident)
    print(f"🚨 Incident created: {title}")
    return incident_id


async def resolve_incident(incident_id: str):
    """Mark incident as resolved."""
    db = get_db()
    if db is None:
        return
    
    incident = await db.incidents.find_one({"incident_id": incident_id})
    if not incident or incident["status"] == "resolved":
        return
    
    started = incident["started_at"]
    now = datetime.now(timezone.utc)
    duration = int((now - started).total_seconds() / 60)
    
    await db.incidents.update_one(
        {"incident_id": incident_id},
        {
            "$set": {
                "status": "resolved",
                "resolved_at": now,
                "duration_minutes": duration,
            }
        }
    )
    print(f"✅ Incident resolved: {incident_id} ({duration}m)")


async def perform_health_check(monitor: dict) -> MonitorCheck:
    """Perform actual health check."""
    import httpx
    
    check_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc)
    
    try:
        start = datetime.now()
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(monitor["url"])
        end = datetime.now()
        
        response_time = (end - start).total_seconds() * 1000
        threshold = monitor.get("response_time_threshold_ms", 3000)
        
        if response.status_code >= 500:
            status = MonitorStatus.DOWN
        elif response.status_code >= 400:
            status = MonitorStatus.DEGRADED
        elif response_time > threshold:
            status = MonitorStatus.DEGRADED
        else:
            status = MonitorStatus.UP
        
        check = MonitorCheck(
            check_id=check_id,
            monitor_id=monitor["monitor_id"],
            timestamp=timestamp,
            status=status,
            response_time_ms=response_time,
            status_code=response.status_code,
            error=None,
            ssl_valid=response.url.scheme == "https",
        )
    
    except Exception as e:
        check = MonitorCheck(
            check_id=check_id,
            monitor_id=monitor["monitor_id"],
            timestamp=timestamp,
            status=MonitorStatus.DOWN,
            response_time_ms=None,
            status_code=None,
            error=str(e),
            ssl_valid=None,
        )
    
    return check


async def generate_sla_report(
    monitor_id: str,
    period_days: int = 30
) -> Optional[SLAReport]:
    """Generate SLA compliance report."""
    db = get_db()
    if db is None:
        return None
    
    monitor = await db.monitors.find_one({"monitor_id": monitor_id})
    if not monitor:
        return None
    
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=period_days)
    
    # Get all checks in period
    checks = await db.monitor_checks.find({
        "monitor_id": monitor_id,
        "timestamp": {"$gte": period_start, "$lte": period_end}
    }).to_list(10000)
    
    if not checks:
        return None
    
    total = len(checks)
    successful = sum(1 for c in checks if c["status"] == MonitorStatus.UP.value)
    failed = total - successful
    
    response_times = sorted([c["response_time_ms"] for c in checks if c.get("response_time_ms")])
    avg_response = sum(response_times) / len(response_times) if response_times else 0
    p95_idx = int(len(response_times) * 0.95) if response_times else 0
    p99_idx = int(len(response_times) * 0.99) if response_times else 0
    p95 = response_times[p95_idx] if p95_idx < len(response_times) else 0
    p99 = response_times[p99_idx] if p99_idx < len(response_times) else 0
    
    uptime_percent = (successful / total * 100) if total > 0 else 100.0
    downtime_minutes = int(failed * monitor["interval_minutes"])
    
    # Get incidents
    incidents = await db.incidents.find({
        "monitor_id": monitor_id,
        "started_at": {"$gte": period_start, "$lte": period_end}
    }).to_list(1000)
    
    resolved_incidents = [i for i in incidents if i.get("duration_minutes")]
    mttr = sum(i["duration_minutes"] for i in resolved_incidents) / len(resolved_incidents) if resolved_incidents else None
    
    # Determine SLA status
    sla_target = monitor.get("uptime_threshold_percent", 99.9)
    if uptime_percent >= sla_target:
        sla_status = SLAStatus.MET
    elif uptime_percent >= sla_target - 0.5:
        sla_status = SLAStatus.AT_RISK
    else:
        sla_status = SLAStatus.BREACHED
    
    report = SLAReport(
        report_id=str(uuid.uuid4()),
        monitor_id=monitor_id,
        period_start=period_start,
        period_end=period_end,
        total_checks=total,
        successful_checks=successful,
        failed_checks=failed,
        uptime_percent=round(uptime_percent, 3),
        downtime_minutes=downtime_minutes,
        avg_response_time_ms=round(avg_response, 2),
        p95_response_time_ms=round(p95, 2),
        p99_response_time_ms=round(p99, 2),
        sla_target=sla_target,
        sla_status=sla_status,
        incidents=len(incidents),
        mttr_minutes=round(mttr, 2) if mttr else None,
        generated_at=datetime.now(timezone.utc),
    )
    
    # Save report
    await db.sla_reports.insert_one(report.model_dump())
    
    return report


# ─── API Endpoints ─────────────────────────────────────────────────────────────

@router.post("/monitors", status_code=201)
async def create_monitor(
    req: CreateMonitorRequest,
    current_user: dict = Depends(get_current_user)
):
    """Create a new monitor."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")
    
    user_id = current_user.get("id") or current_user.get("sub")
    monitor_id = str(uuid.uuid4())
    
    monitor = {
        "monitor_id": monitor_id,
        "user_id": user_id,
        "team_id": req.team_id,
        "name": req.name,
        "url": req.url,
        "enabled": True,
        "interval_minutes": req.interval_minutes,
        "response_time_threshold_ms": req.response_time_threshold_ms,
        "uptime_threshold_percent": req.uptime_threshold_percent,
        "current_status": MonitorStatus.UNKNOWN.value,
        "last_check_at": None,
        "last_check_response_ms": None,
        "last_incident_at": None,
        "uptime_24h": None,
        "avg_response_24h": None,
        "checks_24h": 0,
        "failures_24h": 0,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    
    await db.monitors.insert_one(monitor)
    
    return {
        "success": True,
        "message": "Monitor created",
        "monitor_id": monitor_id,
        "monitor": monitor,
    }


@router.get("/monitors")
async def list_monitors(
    team_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """List all monitors for user."""
    db = get_db()
    if db is None:
        return {"success": True, "monitors": []}
    
    user_id = current_user.get("id") or current_user.get("sub")
    query = {"user_id": user_id}
    if team_id:
        query["team_id"] = team_id
    
    monitors = await db.monitors.find(query).to_list(100)
    
    return {
        "success": True,
        "total": len(monitors),
        "monitors": [
            {
                "monitor_id": m["monitor_id"],
                "name": m["name"],
                "url": m["url"],
                "enabled": m["enabled"],
                "current_status": m["current_status"],
                "uptime_24h": m.get("uptime_24h"),
                "avg_response_24h": m.get("avg_response_24h"),
                "last_check_at": m["last_check_at"].isoformat() if m.get("last_check_at") else None,
            }
            for m in monitors
        ],
    }


@router.get("/monitors/{monitor_id}")
async def get_monitor(
    monitor_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get monitor details."""
    db = get_db()
    if db is None:
        raise HTTPException(404, "Monitor not found")
    
    user_id = current_user.get("id") or current_user.get("sub")
    monitor = await db.monitors.find_one({
        "monitor_id": monitor_id,
        "user_id": user_id
    })
    
    if not monitor:
        raise HTTPException(404, "Monitor not found")
    
    # Calculate fresh stats
    stats = await calculate_uptime_24h(monitor_id)
    monitor.update(stats)
    
    return {"success": True, "monitor": monitor}


@router.patch("/monitors/{monitor_id}")
async def update_monitor(
    monitor_id: str,
    req: UpdateMonitorRequest,
    current_user: dict = Depends(get_current_user)
):
    """Update monitor configuration."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")
    
    user_id = current_user.get("id") or current_user.get("sub")
    
    update_data = req.model_dump(exclude_unset=True)
    update_data["updated_at"] = datetime.now(timezone.utc)
    
    result = await db.monitors.update_one(
        {"monitor_id": monitor_id, "user_id": user_id},
        {"$set": update_data}
    )
    
    if result.matched_count == 0:
        raise HTTPException(404, "Monitor not found")
    
    return {"success": True, "message": "Monitor updated"}


@router.delete("/monitors/{monitor_id}")
async def delete_monitor(
    monitor_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Delete a monitor."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")
    
    user_id = current_user.get("id") or current_user.get("sub")
    result = await db.monitors.delete_one({
        "monitor_id": monitor_id,
        "user_id": user_id
    })
    
    if result.deleted_count == 0:
        raise HTTPException(404, "Monitor not found")
    
    return {"success": True, "message": "Monitor deleted"}


@router.post("/monitors/{monitor_id}/check")
async def run_health_check(
    monitor_id: str,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Manually trigger a health check."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")
    
    user_id = current_user.get("id") or current_user.get("sub")
    monitor = await db.monitors.find_one({
        "monitor_id": monitor_id,
        "user_id": user_id
    })
    
    if not monitor:
        raise HTTPException(404, "Monitor not found")
    
    # Perform check
    check = await perform_health_check(monitor)
    
    # Save check result
    await db.monitor_checks.insert_one(check.model_dump())
    
    # Update monitor status
    await db.monitors.update_one(
        {"monitor_id": monitor_id},
        {
            "$set": {
                "current_status": check.status.value,
                "last_check_at": check.timestamp,
                "last_check_response_ms": check.response_time_ms,
            }
        }
    )
    
    # Create incident if down
    if check.status == MonitorStatus.DOWN:
        await create_incident(
            monitor_id,
            user_id,
            IncidentSeverity.HIGH,
            f"{monitor['name']} is down",
            f"Health check failed: {check.error or 'No response'}"
        )
        await db.monitors.update_one(
            {"monitor_id": monitor_id},
            {"$set": {"last_incident_at": check.timestamp}}
        )
    
    return {
        "success": True,
        "check": check.model_dump(),
    }


@router.get("/monitors/{monitor_id}/history")
async def get_check_history(
    monitor_id: str,
    hours: int = 24,
    current_user: dict = Depends(get_current_user)
):
    """Get check history for monitor."""
    db = get_db()
    if db is None:
        return {"success": True, "checks": []}
    
    user_id = current_user.get("id") or current_user.get("sub")
    
    # Verify ownership
    monitor = await db.monitors.find_one({
        "monitor_id": monitor_id,
        "user_id": user_id
    })
    if not monitor:
        raise HTTPException(404, "Monitor not found")
    
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    checks = await db.monitor_checks.find({
        "monitor_id": monitor_id,
        "timestamp": {"$gte": cutoff}
    }).sort("timestamp", -1).to_list(1000)
    
    return {
        "success": True,
        "total": len(checks),
        "checks": [
            {
                "timestamp": c["timestamp"].isoformat(),
                "status": c["status"],
                "response_time_ms": c.get("response_time_ms"),
                "status_code": c.get("status_code"),
                "error": c.get("error"),
            }
            for c in checks
        ],
    }


@router.get("/monitors/{monitor_id}/incidents")
async def get_incidents(
    monitor_id: str,
    status: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Get incidents for monitor."""
    db = get_db()
    if db is None:
        return {"success": True, "incidents": []}
    
    user_id = current_user.get("id") or current_user.get("sub")
    
    # Verify ownership
    monitor = await db.monitors.find_one({
        "monitor_id": monitor_id,
        "user_id": user_id
    })
    if not monitor:
        raise HTTPException(404, "Monitor not found")
    
    query = {"monitor_id": monitor_id}
    if status:
        query["status"] = status
    
    incidents = await db.incidents.find(query).sort("started_at", -1).to_list(100)
    
    return {
        "success": True,
        "total": len(incidents),
        "incidents": [
            {
                "incident_id": i["incident_id"],
                "severity": i["severity"],
                "status": i["status"],
                "title": i["title"],
                "started_at": i["started_at"].isoformat(),
                "resolved_at": i["resolved_at"].isoformat() if i.get("resolved_at") else None,
                "duration_minutes": i.get("duration_minutes"),
            }
            for i in incidents
        ],
    }


@router.post("/incidents/{incident_id}/acknowledge")
async def acknowledge_incident(
    incident_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Acknowledge an incident."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")
    
    result = await db.incidents.update_one(
        {"incident_id": incident_id, "status": "open"},
        {
            "$set": {
                "status": "acknowledged",
                "acknowledged_at": datetime.now(timezone.utc),
            }
        }
    )
    
    if result.matched_count == 0:
        raise HTTPException(404, "Incident not found or already acknowledged")
    
    return {"success": True, "message": "Incident acknowledged"}


@router.post("/incidents/{incident_id}/resolve")
async def resolve_incident_endpoint(
    incident_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Resolve an incident."""
    await resolve_incident(incident_id)
    return {"success": True, "message": "Incident resolved"}


@router.get("/monitors/{monitor_id}/sla-report")
async def get_sla_report(
    monitor_id: str,
    period_days: int = 30,
    current_user: dict = Depends(get_current_user)
):
    """Generate SLA compliance report."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")
    
    user_id = current_user.get("id") or current_user.get("sub")
    monitor = await db.monitors.find_one({
        "monitor_id": monitor_id,
        "user_id": user_id
    })
    
    if not monitor:
        raise HTTPException(404, "Monitor not found")
    
    report = await generate_sla_report(monitor_id, period_days)
    
    if not report:
        raise HTTPException(404, "No data available for report")
    
    return {
        "success": True,
        "report": report.model_dump(),
    }


@router.get("/dashboard")
async def get_monitoring_dashboard(
    current_user: dict = Depends(get_current_user)
):
    """Get overview dashboard for all monitors."""
    db = get_db()
    if db is None:
        return {"success": True, "dashboard": {}}
    
    user_id = current_user.get("id") or current_user.get("sub")
    monitors = await db.monitors.find({"user_id": user_id}).to_list(100)
    
    total_monitors = len(monitors)
    monitors_up = sum(1 for m in monitors if m["current_status"] == MonitorStatus.UP.value)
    monitors_down = sum(1 for m in monitors if m["current_status"] == MonitorStatus.DOWN.value)
    monitors_degraded = sum(1 for m in monitors if m["current_status"] == MonitorStatus.DEGRADED.value)
    
    # Get open incidents
    open_incidents = await db.incidents.find({
        "user_id": user_id,
        "status": {"$in": ["open", "acknowledged"]}
    }).to_list(100)
    
    return {
        "success": True,
        "dashboard": {
            "total_monitors": total_monitors,
            "monitors_up": monitors_up,
            "monitors_down": monitors_down,
            "monitors_degraded": monitors_degraded,
            "open_incidents": len(open_incidents),
            "overall_health": "healthy" if monitors_down == 0 else "degraded" if monitors_degraded > 0 else "critical",
        },
    }

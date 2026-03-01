"""
app/routers/feature_router.py
─────────────────────────────────────────────────────────────────────────────
Human-Like Feature Testing Router — Phase 8F  (Enterprise-only)

Access: Enterprise plan subscribers only.
Billing changes: System admin only (enforced here and on Billing page).

Endpoints:
  POST /feature-test/run           → start test, returns { job_id }
  GET  /feature-test/{job_id}      → poll status/result
  WS   /feature-test/{job_id}/ws  → live-stream progress

In-memory job store (survives process restart only — sufficient for demo).
"""

import asyncio
import json
import time
import uuid
import ipaddress
import socket
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, field_validator

from app.utils.auth import get_current_user
from app.routers.billing_router import get_user_plan
from app.routers.rbac_router import get_user_role, UserRole

router = APIRouter(prefix="/feature-test", tags=["Feature Testing"])

# ── SSRF guard ─────────────────────────────────────────────────────────────────
_BLOCKED_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
]


def _ssrf_safe(url: str) -> bool:
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        try:
            ip = ipaddress.ip_address(p.hostname)
            return not any(ip in n for n in _BLOCKED_NETS)
        except ValueError:
            pass
        for *_, sa in socket.getaddrinfo(p.hostname, None):
            if any(ipaddress.ip_address(sa[0]) in n for n in _BLOCKED_NETS):
                return False
        return True
    except Exception:
        return False


# ── In-memory job store ────────────────────────────────────────────────────────
# { job_id: { status, result, log, created_at } }
_JOBS: Dict[str, Dict[str, Any]] = {}


# ── WebSocket manager ──────────────────────────────────────────────────────────
class _WSManager:
    def __init__(self):
        self._conns: Dict[str, List[WebSocket]] = {}

    async def connect(self, jid: str, ws: WebSocket):
        await ws.accept()
        self._conns.setdefault(jid, []).append(ws)

    def disconnect(self, jid: str, ws: WebSocket):
        if ws in self._conns.get(jid, []):
            self._conns[jid].remove(ws)

    async def send(self, jid: str, data: dict):
        dead = []
        for ws in list(self._conns.get(jid, [])):
            try:
                await ws.send_text(json.dumps(data, default=str))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(jid, ws)


_ws_mgr = _WSManager()


# ── Request model ──────────────────────────────────────────────────────────────
class FeatureTestRequest(BaseModel):
    url:      str
    email:    Optional[str] = None
    password: Optional[str] = None
    features: Optional[List[str]] = None  # filter to specific feature keys

    @field_validator("url")
    @classmethod
    def must_http(cls, v: str) -> str:
        if urlparse(v).scheme not in ("http", "https"):
            raise ValueError("URL must start with http:// or https://")
        return v


# ── Enterprise guard ───────────────────────────────────────────────────────────
async def _require_enterprise(current_user: dict = Depends(get_current_user)) -> dict:
    """Raise 403 if the user is not on the Enterprise plan."""
    user_id = current_user.get("id") or current_user.get("sub")
    plan = await get_user_plan(user_id)
    if plan != "enterprise":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Human-Like Feature Testing is an Enterprise feature. "
                "Please upgrade your plan to access this feature. "
                "Contact your system administrator to manage billing."
            ),
        )
    return current_user


# ── Background runner ──────────────────────────────────────────────────────────
async def _run_job(job_id: str, url: str, email: Optional[str],
                   password: Optional[str], features: Optional[List[str]]):
    from app.services.feature_tester import run_feature_tests

    job = _JOBS[job_id]
    job["status"] = "running"

    def _progress(msg: str, feature_result: Optional[Dict] = None):
        job["log"].append({"ts": datetime.now(timezone.utc).isoformat(), "msg": msg})
        payload: Dict[str, Any] = {
            "type":    "progress",
            "job_id":  job_id,
            "message": msg,
            "done":    False,
        }
        if feature_result:
            payload["feature_result"] = feature_result
            # Immediately store partial result for polling
            job.setdefault("partial_results", [])
            # Replace if already present, otherwise append
            existing = next((i for i, r in enumerate(job["partial_results"])
                             if r.get("feature") == feature_result.get("feature")), None)
            if existing is not None:
                job["partial_results"][existing] = feature_result
            else:
                job["partial_results"].append(feature_result)

        # Fire-and-forget WS broadcast
        asyncio.create_task(_ws_mgr.send(job_id, payload))

    try:
        result = await run_feature_tests(
            url=url,
            email=email,
            password=password,
            progress_cb=_progress,
            features_filter=features,
        )
        job["status"] = "completed"
        job["result"] = result
        job["finished_at"] = datetime.now(timezone.utc).isoformat()

    except Exception as exc:
        job["status"] = "failed"
        job["error"]  = str(exc)
        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        result = None

    finally:
        # Scrub credentials immediately
        if password:
            try:
                del password
            except Exception:
                pass

    await _ws_mgr.send(job_id, {
        "type":    "done",
        "job_id":  job_id,
        "status":  job["status"],
        "done":    True,
        "result":  job.get("result"),
        "error":   job.get("error"),
    })


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/run")
async def start_feature_test(
    req: FeatureTestRequest,
    current_user: dict = Depends(_require_enterprise),
):
    """Start a human-like feature test. Enterprise subscribers only."""
    if not _ssrf_safe(req.url):
        raise HTTPException(400, "URL blocked (internal/private addresses not allowed).")

    job_id = str(uuid.uuid4())
    _JOBS[job_id] = {
        "job_id":     job_id,
        "status":     "queued",
        "url":        req.url,
        "user_id":    current_user.get("id") or current_user.get("sub"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "log":        [],
        "result":     None,
        "error":      None,
        "partial_results": [],
    }

    asyncio.create_task(_run_job(
        job_id=job_id,
        url=req.url,
        email=req.email,
        password=req.password,
        features=req.features,
    ))

    return {"job_id": job_id, "message": "Feature test started", "status": "queued"}


@router.get("/{job_id}")
async def get_feature_test(
    job_id: str,
    current_user: dict = Depends(_require_enterprise),
):
    """Poll for test status / result."""
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.get("user_id") and job["user_id"] != (current_user.get("id") or current_user.get("sub")):
        raise HTTPException(403, "Not your test")

    resp: Dict[str, Any] = {
        "job_id":    job_id,
        "status":    job["status"],
        "log":       job["log"],
        "created_at": job["created_at"],
    }
    if job["status"] == "running":
        resp["partial_results"] = job.get("partial_results", [])
    if job["status"] == "completed":
        resp.update(job.get("result", {}))
    if job["status"] == "failed":
        resp["error"] = job.get("error")
    return resp


@router.websocket("/{job_id}/ws")
async def ws_feature_test(job_id: str, websocket: WebSocket):
    """Live-stream test progress."""
    await _ws_mgr.connect(job_id, websocket)

    # Send snapshot immediately
    job = _JOBS.get(job_id)
    if job:
        snapshot: Dict[str, Any] = {
            "type":    "snapshot",
            "job_id":  job_id,
            "status":  job["status"],
            "log":     job["log"],
            "partial_results": job.get("partial_results", []),
            "done":    job["status"] in ("completed", "failed"),
        }
        if job["status"] == "completed":
            snapshot["result"] = job.get("result")
        if job["status"] == "failed":
            snapshot["error"] = job.get("error")
        try:
            await websocket.send_text(json.dumps(snapshot, default=str))
        except Exception:
            pass

        if job["status"] in ("completed", "failed"):
            _ws_mgr.disconnect(job_id, websocket)
            await websocket.close()
            return

    try:
        while True:
            await websocket.receive_text()   # keep alive; server pushes data
    except WebSocketDisconnect:
        _ws_mgr.disconnect(job_id, websocket)


# ── Admin: list all jobs (system admin only) ───────────────────────────────────
@router.get("/admin/jobs")
async def admin_list_jobs(
    current_user: dict = Depends(get_current_user),
):
    """System admin: view all feature test jobs."""
    user_id = current_user.get("id") or current_user.get("sub")
    role = await get_user_role(user_id)
    if role != UserRole.ADMIN:
        raise HTTPException(403, "System Admin access required")

    jobs_summary = [
        {
            "job_id":     j["job_id"],
            "status":     j["status"],
            "url":        j["url"],
            "user_id":    j["user_id"],
            "created_at": j["created_at"],
            "finished_at": j.get("finished_at"),
            "overall_score": j.get("result", {}).get("overall_score") if j.get("result") else None,
        }
        for j in _JOBS.values()
    ]
    return {"jobs": sorted(jobs_summary, key=lambda x: x["created_at"], reverse=True)}

"""
app/routers/bulk_router.py
Phase 6b — Bulk URL testing: submit a batch, track per-URL progress.

MongoDB collection: bulk_batches
  { batch_id, user_id, urls, statuses, created_at, completed_at }
"""
import asyncio
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ..utils.auth import get_current_user
from ..database import get_db

router = APIRouter(prefix="/bulk", tags=["Bulk Testing"])

MAX_URLS = 20   # max URLs per batch

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

# ── Pydantic ───────────────────────────────────────────────────────────────────

class BulkRunRequest(BaseModel):
    urls: List[str]
    label: str = ""   # optional batch label

# ── Background runner ──────────────────────────────────────────────────────────

async def _run_single(batch_id: str, url: str, user_id: str, test_id: str):
    """Run one URL test and update the batch record with results."""
    from ..utils.db_results import save_result, get_result
    import time, requests

    db = get_db()

    async def _update(status: str, score=None, error=None):
        await db.bulk_batches.update_one(
            {"batch_id": batch_id, "statuses.test_id": test_id},
            {"$set": {
                "statuses.$.status": status,
                "statuses.$.score": score,
                "statuses.$.error": error,
                "statuses.$.finished_at": _now(),
            }}
        )

    try:
        await db.bulk_batches.update_one(
            {"batch_id": batch_id, "statuses.test_id": test_id},
            {"$set": {"statuses.$.status": "running"}}
        )

        # ── Run basic checks ───────────────────────────────────────────────────
        result = {
            "test_id": test_id,
            "url": url,
            "user_id": user_id,
            "status": "running",
            "started_at": _now(),
            "batch_id": batch_id,
        }
        await save_result(test_id, result.copy())

        # Speed check
        try:
            start = time.monotonic()
            resp = requests.get(url, timeout=20, headers={"User-Agent": "TestVerse/1.0"}, allow_redirects=True)
            ms = round((time.monotonic() - start) * 1000, 2)
            size_kb = round(len(resp.content) / 1024, 2)
            if ms < 1000:   speed_score = 95
            elif ms < 2500: speed_score = 70
            elif ms < 5000: speed_score = 45
            else:           speed_score = 20
            result["speed"] = {"status": "pass" if speed_score >= 70 else "warning", "score": speed_score, "load_time_ms": ms, "page_size_kb": size_kb}
        except Exception as e:
            result["speed"] = {"status": "fail", "score": 0, "error": str(e)}

        # SSL check
        try:
            import ssl, socket
            from urllib.parse import urlparse
            parsed = urlparse(url)
            hostname = parsed.hostname
            if parsed.scheme == "https":
                ctx = ssl.create_default_context()
                with ctx.wrap_socket(socket.socket(), server_hostname=hostname) as s:
                    s.settimeout(10)
                    s.connect((hostname, 443))
                    cert = s.getpeercert()
                    exp_str = cert.get("notAfter", "")
                    from datetime import datetime
                    exp_dt = datetime.strptime(exp_str, "%b %d %H:%M:%S %Y %Z") if exp_str else None
                    days = (exp_dt - datetime.utcnow()).days if exp_dt else None
                    result["ssl"] = {"status": "pass", "valid": True, "days_until_expiry": days, "score": 100}
            else:
                result["ssl"] = {"status": "warning", "valid": False, "score": 20, "message": "Not HTTPS"}
        except Exception as e:
            result["ssl"] = {"status": "fail", "valid": False, "score": 0, "error": str(e)}

        # SEO + security headers (lightweight)
        try:
            from app.services.advanced_checks import check_seo, check_security_headers
            seo_r = await asyncio.get_event_loop().run_in_executor(None, lambda: check_seo(url) if callable(check_seo) else {})
            sec_r = await asyncio.get_event_loop().run_in_executor(None, lambda: check_security_headers(url) if callable(check_security_headers) else {})
            result["seo"] = seo_r if isinstance(seo_r, dict) else {}
            result["security_headers"] = sec_r if isinstance(sec_r, dict) else {}
        except Exception:
            result["seo"] = {"status": "skip"}
            result["security_headers"] = {"status": "skip"}

        # Score
        scores = []
        for key in ["speed", "ssl", "seo", "security_headers"]:
            s = result.get(key, {})
            if isinstance(s, dict) and s.get("score") is not None:
                scores.append(s["score"])
        overall = round(sum(scores) / len(scores)) if scores else None

        result["overall_score"] = overall
        result["status"] = "completed"
        result["finished_at"] = _now()
        await save_result(test_id, result.copy())
        await _update("completed", score=overall)

    except Exception as e:
        await _update("failed", error=str(e))


async def _run_batch(batch_id: str, urls: List[str], user_id: str):
    """Run all URLs in the batch with a small concurrency limit."""
    sem = asyncio.Semaphore(3)   # max 3 concurrent tests

    async def _limited(url, test_id):
        async with sem:
            await _run_single(batch_id, url, user_id, test_id)

    db = get_db()
    # Get test_ids from the batch record
    batch = await db.bulk_batches.find_one({"batch_id": batch_id}, {"_id": 0})
    if not batch:
        return

    tasks = [
        _limited(s["url"], s["test_id"])
        for s in batch.get("statuses", [])
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    # Mark batch complete
    await db.bulk_batches.update_one(
        {"batch_id": batch_id},
        {"$set": {"status": "completed", "completed_at": _now()}}
    )

# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/run")
async def run_bulk(
    body: BulkRunRequest,
    current_user: dict = Depends(get_current_user),
):
    """Submit a batch of URLs for testing."""
    urls = [u.strip() for u in body.urls if u.strip()]
    if not urls:
        raise HTTPException(status_code=400, detail="No URLs provided")
    if len(urls) > MAX_URLS:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_URLS} URLs per batch")

    # Deduplicate
    seen = set()
    unique = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    urls = unique

    db = get_db()
    batch_id = str(uuid.uuid4())
    now = _now()

    statuses = [
        {
            "test_id":     str(uuid.uuid4()),
            "url":         url,
            "status":      "pending",
            "score":       None,
            "error":       None,
            "finished_at": None,
        }
        for url in urls
    ]

    batch = {
        "batch_id":    batch_id,
        "user_id":     current_user["sub"],
        "label":       body.label or f"Batch {now[:10]}",
        "urls":        urls,
        "statuses":    statuses,
        "status":      "running",
        "created_at":  now,
        "completed_at": None,
        "total":       len(urls),
    }
    await db.bulk_batches.insert_one(batch)

    # Fire off background task
    asyncio.create_task(_run_batch(batch_id, urls, current_user["sub"]))

    return {
        "success":  True,
        "batch_id": batch_id,
        "total":    len(urls),
        "message":  f"Batch started — testing {len(urls)} URL(s)",
    }


@router.get("/{batch_id}")
async def get_batch(
    batch_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get batch status and per-URL results."""
    db = get_db()
    batch = await db.bulk_batches.find_one(
        {"batch_id": batch_id, "user_id": current_user["sub"]},
        {"_id": 0}
    )
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")

    statuses = batch.get("statuses", [])
    completed = sum(1 for s in statuses if s["status"] == "completed")
    failed    = sum(1 for s in statuses if s["status"] == "failed")
    running   = sum(1 for s in statuses if s["status"] == "running")
    pending   = sum(1 for s in statuses if s["status"] == "pending")

    scores = [s["score"] for s in statuses if s.get("score") is not None]
    avg_score = round(sum(scores) / len(scores)) if scores else None

    return {
        **batch,
        "progress": {
            "completed": completed,
            "failed":    failed,
            "running":   running,
            "pending":   pending,
            "avg_score": avg_score,
            "pct":       round((completed + failed) / max(len(statuses), 1) * 100),
        }
    }


@router.get("/")
async def list_batches(current_user: dict = Depends(get_current_user)):
    """List all batches for the current user, newest first."""
    db = get_db()
    cursor = db.bulk_batches.find(
        {"user_id": current_user["sub"]},
        {"_id": 0, "statuses": 0}
    ).sort("created_at", -1).limit(20)
    batches = await cursor.to_list(length=20)
    return {"batches": batches}


@router.delete("/{batch_id}")
async def delete_batch(
    batch_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Delete a batch record."""
    db = get_db()
    result = await db.bulk_batches.delete_one(
        {"batch_id": batch_id, "user_id": current_user["sub"]}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Batch not found")
    return {"success": True}

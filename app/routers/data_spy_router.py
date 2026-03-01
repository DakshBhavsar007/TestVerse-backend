"""
app/routers/data_spy_router.py
─────────────────────────────────────────────────────────────────────────────
Data Spy Router — Pro + Enterprise only

Visits a website with Playwright and audits:
  1. Data explicitly asked from users (form inputs, sign-up fields)
  2. Data collected silently without permission (trackers, cookies,
     fingerprinting scripts, hidden fields, network beacons)

Endpoints:
  POST /data-spy/scan        → { job_id }
  GET  /data-spy/{job_id}    → poll status / result
"""

import asyncio
import ipaddress
import socket
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator

from app.utils.auth import get_current_user
from app.routers.billing_router import get_user_plan

router = APIRouter(prefix="/data-spy", tags=["Data Spy"])

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

# ── Plan guard ─────────────────────────────────────────────────────────────────
async def _require_pro_or_enterprise(current_user: dict = Depends(get_current_user)) -> dict:
    user_id = current_user.get("id") or current_user.get("sub")
    plan = await get_user_plan(user_id)
    if plan not in ("pro", "enterprise"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Data Spy is a Pro/Enterprise feature. Please upgrade your plan.",
        )
    return current_user

# ── In-memory job store ────────────────────────────────────────────────────────
_JOBS: Dict[str, Dict[str, Any]] = {}

# ── Known silent tracker domains ───────────────────────────────────────────────
TRACKER_DOMAINS = {
    "google-analytics.com", "googletagmanager.com", "googlesyndication.com",
    "doubleclick.net", "facebook.net", "facebook.com", "fbcdn.net",
    "hotjar.com", "mixpanel.com", "amplitude.com", "segment.com", "segment.io",
    "intercom.io", "intercom.com", "fullstory.com", "logrocket.com",
    "clarity.ms", "mouseflow.com", "crazyegg.com", "heatmap.com",
    "twitter.com", "ads-twitter.com", "linkedin.com", "snap.com",
    "tiktok.com", "byteoverseas.com", "pinterest.com",
    "quantserve.com", "scorecardresearch.com", "comscore.com",
    "adobedtm.com", "omtrdc.net", "mktoresp.com", "hubspot.com",
    "bing.com", "bat.bing.com", "adnxs.com", "rubiconproject.com",
    "pubmatic.com", "criteo.com", "outbrain.com", "taboola.com",
    "cloudfront.net", "nr-data.net", "newrelic.com", "datadog-browser-agent.com",
    "sentry.io", "bugsnag.com", "rollbar.com",
}

FINGERPRINT_SIGNALS = [
    "canvas", "webgl", "AudioContext", "navigator.plugins",
    "screen.width", "screen.height", "devicePixelRatio",
    "navigator.hardwareConcurrency", "navigator.deviceMemory",
    "getBattery", "getGamepads", "RTCPeerConnection",
    "FontFaceSet", "speechSynthesis",
]

# ── Request model ──────────────────────────────────────────────────────────────
class ScanRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def must_http(cls, v: str) -> str:
        if urlparse(v).scheme not in ("http", "https"):
            raise ValueError("URL must start with http:// or https://")
        return v

# ── Core scan logic ────────────────────────────────────────────────────────────
async def _run_scan(job_id: str, url: str):
    from playwright.async_api import async_playwright

    job = _JOBS[job_id]
    job["status"] = "running"

    def log(msg: str):
        job["log"].append(msg)

    try:
        log(f"▸ Launching browser for {url}")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--single-process",
                ],
            )

            # Track network requests
            network_requests: List[Dict] = []
            tracker_hits: List[Dict] = []

            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
            )

            page = await context.new_page()

            # Intercept network requests
            def on_request(request):
                req_url = request.url
                parsed  = urlparse(req_url)
                domain  = parsed.netloc.lstrip("www.")
                # Check if tracker
                for tracker in TRACKER_DOMAINS:
                    if tracker in domain:
                        tracker_hits.append({
                            "domain":  domain,
                            "url":     req_url[:120],
                            "method":  request.method,
                            "tracker": tracker,
                        })
                        break
                network_requests.append({"domain": domain, "url": req_url[:100]})

            page.on("request", on_request)

            log(f"▸ Navigating to {url}…")
            try:
                await page.goto(url, timeout=20000, wait_until="domcontentloaded")
                await asyncio.sleep(3)  # let JS execute
            except Exception as e:
                log(f"⚠️ Navigation warning: {str(e)[:80]}")

            log("▸ Analysing form inputs and explicit data collection…")

            # ── 1. Explicit data (forms & inputs) ──────────────────────────────
            explicit_data: List[Dict] = []

            inputs = await page.query_selector_all("input, textarea, select")
            for inp in inputs:
                try:
                    inp_type  = (await inp.get_attribute("type") or "text").lower()
                    inp_name  = await inp.get_attribute("name") or ""
                    inp_id    = await inp.get_attribute("id") or ""
                    inp_ph    = await inp.get_attribute("placeholder") or ""
                    label_txt = inp_name or inp_id or inp_ph or inp_type

                    if inp_type == "hidden":
                        continue  # handled separately

                    risk = "info"
                    if inp_type in ("password",):
                        risk = "medium"
                    if any(kw in label_txt.lower() for kw in ["ssn", "social", "credit", "card", "cvv", "passport", "dob", "birth"]):
                        risk = "high"
                    if any(kw in label_txt.lower() for kw in ["email", "phone", "address", "zip", "postcode"]):
                        risk = "medium"

                    explicit_data.append({
                        "name":   f"Input: {label_txt[:60]}",
                        "value":  f'type="{inp_type}"',
                        "risk":   risk,
                        "detail": f"Field name/id: '{label_txt}'. This site asks you to enter this data explicitly.",
                        "category": "explicit_forms",
                    })
                except Exception:
                    continue

            log(f"✅ Found {len(explicit_data)} explicit input field(s)")

            # ── 2. Hidden fields ────────────────────────────────────────────────
            log("▸ Scanning for hidden form fields…")
            hidden_fields: List[Dict] = []
            hidden_inputs = await page.query_selector_all('input[type="hidden"]')
            for h in hidden_inputs:
                try:
                    h_name  = await h.get_attribute("name") or ""
                    h_value = await h.get_attribute("value") or ""
                    if h_name:
                        hidden_fields.append({
                            "name":   f"Hidden: {h_name[:60]}",
                            "value":  h_value[:40] if h_value else "(empty)",
                            "risk":   "medium",
                            "detail": f"Hidden field '{h_name}' with value '{h_value[:40]}' — submitted with forms without your knowledge.",
                            "category": "hidden_fields",
                        })
                except Exception:
                    continue
            log(f"✅ Found {len(hidden_fields)} hidden field(s)")

            # ── 3. Cookies ──────────────────────────────────────────────────────
            log("▸ Inspecting cookies…")
            cookies = await context.cookies()
            cookie_findings: List[Dict] = []
            for c in cookies:
                risk = "low"
                if not c.get("httpOnly"):
                    risk = "medium"
                if not c.get("secure"):
                    risk = "medium"
                if c.get("sameSite") == "None":
                    risk = "high"
                cookie_findings.append({
                    "name":   f"Cookie: {c['name'][:50]}",
                    "value":  c["domain"],
                    "risk":   risk,
                    "detail": (
                        f"Domain: {c['domain']} | "
                        f"HttpOnly: {c.get('httpOnly', False)} | "
                        f"Secure: {c.get('secure', False)} | "
                        f"SameSite: {c.get('sameSite', 'None')}"
                    ),
                    "category": "cookies",
                })
            log(f"✅ Found {len(cookie_findings)} cookie(s)")

            # ── 4. Trackers ─────────────────────────────────────────────────────
            log("▸ Identifying third-party trackers…")
            tracker_findings: List[Dict] = []
            seen_trackers = set()
            for t in tracker_hits:
                if t["tracker"] not in seen_trackers:
                    seen_trackers.add(t["tracker"])
                    tracker_findings.append({
                        "name":   f"Tracker: {t['tracker']}",
                        "value":  t["domain"],
                        "risk":   "high",
                        "detail": f"Third-party tracker detected: {t['tracker']}. This service collects behavioural data about your visit without explicit consent.",
                        "category": "trackers",
                    })
            log(f"{'⚠️' if tracker_findings else '✅'} Found {len(tracker_findings)} tracker(s)")

            # ── 5. Fingerprinting ───────────────────────────────────────────────
            log("▸ Detecting fingerprinting scripts…")
            page_source = await page.content()
            fp_findings: List[Dict] = []
            for signal in FINGERPRINT_SIGNALS:
                if signal in page_source:
                    fp_findings.append({
                        "name":   f"Fingerprint: {signal}",
                        "value":  "detected in page JS",
                        "risk":   "high",
                        "detail": f"The site accesses '{signal}' which is commonly used to silently fingerprint your browser and identify you across sessions without cookies.",
                        "category": "fingerprinting",
                    })
            log(f"{'⚠️' if fp_findings else '✅'} Found {len(fp_findings)} fingerprinting signal(s)")

            # ── 6. Local / Session storage ──────────────────────────────────────
            log("▸ Checking browser storage…")
            storage_findings: List[Dict] = []
            try:
                ls_keys = await page.evaluate("() => Object.keys(localStorage)")
                for key in ls_keys[:20]:
                    storage_findings.append({
                        "name":   f"localStorage: {key[:50]}",
                        "value":  "stored",
                        "risk":   "low",
                        "detail": f"Key '{key}' stored in localStorage — persists after browser close.",
                        "category": "local_storage",
                    })
                ss_keys = await page.evaluate("() => Object.keys(sessionStorage)")
                for key in ss_keys[:20]:
                    storage_findings.append({
                        "name":   f"sessionStorage: {key[:50]}",
                        "value":  "stored",
                        "risk":   "low",
                        "detail": f"Key '{key}' stored in sessionStorage — cleared when tab closes.",
                        "category": "local_storage",
                    })
            except Exception:
                pass
            log(f"✅ Found {len(storage_findings)} storage item(s)")

            # ── 7. External network requests ────────────────────────────────────
            origin_domain = urlparse(url).netloc
            external_requests: List[Dict] = []
            seen_domains: set = set()
            for req in network_requests:
                d = req["domain"]
                if d and origin_domain not in d and d not in seen_domains:
                    seen_domains.add(d)
                    is_tracker = any(t in d for t in TRACKER_DOMAINS)
                    external_requests.append({
                        "name":   f"Request to: {d[:60]}",
                        "value":  d,
                        "risk":   "high" if is_tracker else "low",
                        "detail": f"Your browser contacted '{d}' — {'a known tracker' if is_tracker else 'an external service'}.",
                        "category": "network_requests",
                    })
            log(f"✅ Found {len(external_requests)} unique external domain(s)")

            await browser.close()

            # ── Build result ────────────────────────────────────────────────────
            all_silent = hidden_fields + cookie_findings + tracker_findings + fp_findings + storage_findings + external_requests

            all_findings = explicit_data + all_silent
            high_count   = sum(1 for f in all_findings if f["risk"] == "high")
            total        = len(all_findings)

            # Privacy score: start at 100, deduct per finding
            score = 100
            score -= len(tracker_findings) * 8
            score -= len(fp_findings) * 10
            score -= len(hidden_fields) * 5
            score -= len(cookie_findings) * 2
            score -= len(external_requests) * 1
            score = max(0, min(100, score))

            summary_parts = []
            if tracker_findings:
                summary_parts.append(f"{len(tracker_findings)} tracker(s) found")
            if fp_findings:
                summary_parts.append(f"{len(fp_findings)} fingerprinting technique(s)")
            if hidden_fields:
                summary_parts.append(f"{len(hidden_fields)} hidden field(s)")
            if not summary_parts:
                summary_parts.append("No major privacy concerns detected")

            categories = {
                "explicit_forms":   explicit_data,
                "hidden_fields":    hidden_fields,
                "cookies":          cookie_findings,
                "trackers":         tracker_findings,
                "fingerprinting":   fp_findings,
                "local_storage":    storage_findings,
                "network_requests": external_requests,
            }

            job["result"] = {
                "url":             url,
                "privacy_score":   score,
                "summary":         ". ".join(summary_parts) + f". Total {total} data points found.",
                "explicit_data":   explicit_data,
                "silent_data":     all_silent,
                "categories":      categories,
                "explicit_count":  len(explicit_data),
                "silent_count":    len(all_silent),
                "high_risk_count": high_count,
                "total_count":     total,
                "scanned_at":      datetime.now(timezone.utc).isoformat(),
            }
            job["status"] = "completed"
            log(f"✅ Scan complete — {total} findings, privacy score: {score}/100")

    except Exception as exc:
        job["status"] = "failed"
        job["error"]  = str(exc)
        job["log"].append(f"❌ Scan error: {str(exc)[:120]}")


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/scan")
async def start_scan(
    req: ScanRequest,
    current_user: dict = Depends(_require_pro_or_enterprise),
):
    """Start a data spy scan. Pro/Enterprise only."""
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
    }

    asyncio.create_task(_run_scan(job_id, req.url))
    return {"job_id": job_id, "status": "queued", "message": "Scan started"}


@router.get("/{job_id}")
async def get_scan(
    job_id: str,
    current_user: dict = Depends(_require_pro_or_enterprise),
):
    """Poll scan status / result."""
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    user_id = current_user.get("id") or current_user.get("sub")
    if job.get("user_id") and job["user_id"] != user_id:
        raise HTTPException(403, "Not your scan")

    resp: Dict[str, Any] = {
        "job_id":  job_id,
        "status":  job["status"],
        "log":     job["log"],
    }
    if job["status"] == "completed":
        resp["result"] = job["result"]
    if job["status"] == "failed":
        resp["error"] = job.get("error")
    return resp

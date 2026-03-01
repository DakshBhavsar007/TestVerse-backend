"""
app/middleware/rate_limit.py — Sliding-window per-IP rate limiter.
Only applies to POST /run. Limit configurable via .env RATE_LIMIT_PER_MINUTE.
"""
import time
from collections import defaultdict, deque
from typing import Callable
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import get_settings

_log: dict[str, deque] = defaultdict(deque)
WINDOW = 60
LIMITED = {"/run", "/feature-test/run"}


def _ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method == "POST" and request.url.path in LIMITED:
            limit = get_settings().rate_limit_per_minute
            ip = _ip(request)
            now = time.monotonic()
            q = _log[ip]
            while q and now - q[0] > WINDOW:
                q.popleft()
            if len(q) >= limit:
                retry = int(WINDOW - (now - q[0])) + 1
                return JSONResponse(
                    status_code=429,
                    content={"detail": f"Rate limit exceeded. Max {limit}/min per IP.", "retry_after_seconds": retry},
                    headers={"Retry-After": str(retry)},
                )
            q.append(now)
        return await call_next(request)

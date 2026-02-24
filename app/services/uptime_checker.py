"""
Uptime & Speed checker using aiohttp for fast async HTTP requests.
"""
import time
import asyncio
import aiohttp
from typing import Optional
from ..models import UptimeCheck, SpeedCheck, CheckStatus
from ..config import get_settings

settings = get_settings()


async def check_uptime(url: str, session: aiohttp.ClientSession) -> UptimeCheck:
    """Check if the URL is reachable and measure response time."""
    start = time.monotonic()
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=settings.request_timeout_seconds),
            allow_redirects=True,
            ssl=False,  # SSL is checked separately
        ) as response:
            elapsed_ms = (time.monotonic() - start) * 1000
            status_code = response.status

            if status_code < 400:
                return UptimeCheck(
                    status=CheckStatus.PASS,
                    http_status_code=status_code,
                    response_time_ms=round(elapsed_ms, 2),
                    message=f"Site is up — HTTP {status_code}",
                )
            elif status_code < 500:
                return UptimeCheck(
                    status=CheckStatus.WARNING,
                    http_status_code=status_code,
                    response_time_ms=round(elapsed_ms, 2),
                    message=f"Client error — HTTP {status_code}",
                )
            else:
                return UptimeCheck(
                    status=CheckStatus.FAIL,
                    http_status_code=status_code,
                    response_time_ms=round(elapsed_ms, 2),
                    message=f"Server error — HTTP {status_code}",
                )
    except asyncio.TimeoutError:
        return UptimeCheck(
            status=CheckStatus.FAIL,
            message=f"Request timed out after {settings.request_timeout_seconds}s",
        )
    except aiohttp.ClientConnectorError as e:
        return UptimeCheck(
            status=CheckStatus.FAIL,
            message=f"Connection failed: {str(e)[:120]}",
        )
    except Exception as e:
        return UptimeCheck(
            status=CheckStatus.FAIL,
            message=f"Unexpected error: {str(e)[:120]}",
        )


async def check_speed(url: str, session: aiohttp.ClientSession) -> SpeedCheck:
    """Measure page load speed (TTFB & full download time, page size)."""
    start = time.monotonic()
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=settings.request_timeout_seconds),
            allow_redirects=True,
            ssl=False,
        ) as response:
            ttfb_ms = (time.monotonic() - start) * 1000
            content = await response.read()
            total_ms = (time.monotonic() - start) * 1000
            size_kb = len(content) / 1024

            # Thresholds
            if total_ms < 1000:
                status = CheckStatus.PASS
                msg = f"Excellent — page loaded in {total_ms:.0f}ms"
            elif total_ms < 3000:
                status = CheckStatus.WARNING
                msg = f"Acceptable — page loaded in {total_ms:.0f}ms (aim for <1s)"
            else:
                status = CheckStatus.FAIL
                msg = f"Slow — page loaded in {total_ms:.0f}ms (threshold: 3000ms)"

            return SpeedCheck(
                status=status,
                load_time_ms=round(total_ms, 2),
                ttfb_ms=round(ttfb_ms, 2),
                page_size_kb=round(size_kb, 2),
                message=msg,
            )
    except asyncio.TimeoutError:
        return SpeedCheck(
            status=CheckStatus.FAIL,
            message=f"Speed test timed out after {settings.request_timeout_seconds}s",
        )
    except Exception as e:
        return SpeedCheck(
            status=CheckStatus.FAIL,
            message=f"Speed check error: {str(e)[:120]}",
        )

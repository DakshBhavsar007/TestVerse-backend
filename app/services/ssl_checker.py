"""
SSL certificate validator using Python's ssl + socket modules.
"""
import ssl
import socket
import asyncio
from datetime import datetime, timezone
from typing import Optional
from ..models import SSLCheck, CheckStatus


async def check_ssl(url: str) -> SSLCheck:
    """Verify SSL certificate validity, expiry, and issuer."""
    try:
        # Extract hostname from URL
        from urllib.parse import urlparse
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port or 443

        if parsed.scheme != "https":
            return SSLCheck(
                status=CheckStatus.SKIP,
                valid=False,
                message="Site uses HTTP — SSL check skipped",
            )

        # Run blocking SSL in executor
        loop = asyncio.get_event_loop()
        cert_info = await loop.run_in_executor(None, _get_cert_info, hostname, port)

        if cert_info is None:
            return SSLCheck(
                status=CheckStatus.FAIL,
                valid=False,
                message="Could not retrieve SSL certificate",
            )

        # Parse expiry
        expire_str = cert_info.get("notAfter", "")
        expires_on = None
        days_until_expiry = None
        try:
            expire_dt = datetime.strptime(expire_str, "%b %d %H:%M:%S %Y %Z")
            expire_dt = expire_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            days_until_expiry = (expire_dt - now).days
            expires_on = expire_dt.strftime("%Y-%m-%d")
        except Exception:
            pass

        # Parse issuer
        issuer_dict = dict(x[0] for x in cert_info.get("issuer", []))
        issuer = issuer_dict.get("organizationName", issuer_dict.get("commonName", "Unknown"))

        # Determine status
        if days_until_expiry is None:
            status = CheckStatus.WARNING
            msg = "SSL certificate found but could not parse expiry date"
        elif days_until_expiry < 0:
            status = CheckStatus.FAIL
            msg = f"SSL certificate EXPIRED {abs(days_until_expiry)} days ago"
        elif days_until_expiry < 14:
            status = CheckStatus.FAIL
            msg = f"SSL certificate expires in {days_until_expiry} days — CRITICAL"
        elif days_until_expiry < 30:
            status = CheckStatus.WARNING
            msg = f"SSL certificate expires soon — {days_until_expiry} days left"
        else:
            status = CheckStatus.PASS
            msg = f"SSL certificate valid — expires in {days_until_expiry} days"

        return SSLCheck(
            status=status,
            valid=days_until_expiry is not None and days_until_expiry > 0,
            expires_on=expires_on,
            days_until_expiry=days_until_expiry,
            issuer=issuer,
            message=msg,
        )

    except ssl.SSLCertVerificationError as e:
        return SSLCheck(
            status=CheckStatus.FAIL,
            valid=False,
            message=f"SSL verification failed: {str(e)[:120]}",
        )
    except ConnectionRefusedError:
        return SSLCheck(
            status=CheckStatus.FAIL,
            valid=False,
            message="Connection refused on port 443",
        )
    except socket.timeout:
        return SSLCheck(
            status=CheckStatus.FAIL,
            valid=False,
            message="SSL check timed out",
        )
    except Exception as e:
        return SSLCheck(
            status=CheckStatus.FAIL,
            valid=False,
            message=f"SSL check error: {str(e)[:120]}",
        )


def _get_cert_info(hostname: str, port: int) -> Optional[dict]:
    """Blocking SSL cert retrieval (run in executor)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_OPTIONAL
    try:
        with socket.create_connection((hostname, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                return ssock.getpeercert()
    except Exception:
        return None

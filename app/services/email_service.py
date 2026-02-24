"""
app/services/email_service.py
Sends transactional emails via SendGrid API.
"""
import httpx
from datetime import datetime
from typing import Optional
from app.config import get_settings

settings = get_settings()


def _score_label(score: Optional[int]) -> str:
    if score is None: return "N/A"
    if score >= 80: return "Excellent"
    if score >= 60: return "Good"
    if score >= 40: return "Fair"
    return "Poor"


def _score_color(score: Optional[int]) -> str:
    if score is None: return "#6b7280"
    if score >= 80: return "#10b981"
    if score >= 60: return "#f59e0b"
    if score >= 40: return "#f97316"
    return "#ef4444"


def _build_html(subject: str, headline: str, body_html: str) -> str:
    """Wrap content in a clean dark-themed HTML email template."""
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0f1117;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#e2e8f0;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f1117;padding:40px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">

        <!-- Header -->
        <tr><td style="background:linear-gradient(135deg,#1e1b4b,#1a1035);border-radius:16px 16px 0 0;padding:32px 40px;text-align:center;border-bottom:1px solid rgba(99,102,241,0.3);">
          <div style="display:inline-flex;align-items:center;gap:10px;margin-bottom:8px;">
            <div style="width:32px;height:32px;background:linear-gradient(135deg,#6366f1,#8b5cf6);border-radius:8px;display:inline-block;line-height:32px;text-align:center;font-size:16px;">⚡</div>
            <span style="font-size:20px;font-weight:800;color:#e2e8f0;letter-spacing:-0.5px;">TestVerse</span>
          </div>
          <h1 style="margin:12px 0 0;font-size:22px;font-weight:700;color:#e2e8f0;letter-spacing:-0.3px;">{headline}</h1>
        </td></tr>

        <!-- Body -->
        <tr><td style="background:#13151f;padding:32px 40px;border-left:1px solid rgba(255,255,255,0.06);border-right:1px solid rgba(255,255,255,0.06);">
          {body_html}
        </td></tr>

        <!-- Footer -->
        <tr><td style="background:#0d0f18;border-radius:0 0 16px 16px;padding:20px 40px;text-align:center;border:1px solid rgba(255,255,255,0.06);border-top:none;">
          <p style="margin:0;font-size:12px;color:#374151;">
            You're receiving this because you have notifications enabled on TestVerse.<br>
            <a href="#" style="color:#6366f1;text-decoration:none;">Manage notification settings</a>
          </p>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


async def send_email(to_email: str, subject: str, html: str) -> bool:
    """Send email via SendGrid. Returns True on success."""
    if not settings.sendgrid_api_key:
        print(f"⚠️  SendGrid not configured — skipping email to {to_email}")
        return False

    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": settings.sendgrid_from_email, "name": "TestVerse"},
        "subject": subject,
        "content": [{"type": "text/html", "value": html}],
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.sendgrid.com/v3/mail/send",
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.sendgrid_api_key}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code in (200, 202):
                print(f"✅ Email sent to {to_email}: {subject}")
                return True
            else:
                print(f"❌ SendGrid error {resp.status_code}: {resp.text[:200]}")
                return False
    except Exception as e:
        print(f"❌ Email send failed: {e}")
        return False


# ── Email templates ────────────────────────────────────────────────────────────

async def send_test_complete(to_email: str, url: str, score: Optional[int],
                              summary: Optional[str], test_id: str,
                              app_url: str = "http://localhost:5173") -> bool:
    color = _score_color(score)
    label = _score_label(score)
    subject = f"✅ Test Complete — {url} scored {score}/100" if score else f"✅ Test Complete — {url}"

    body = f"""
    <div style="text-align:center;margin-bottom:28px;">
      <div style="display:inline-block;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);border-radius:16px;padding:24px 40px;">
        <div style="font-size:56px;font-weight:900;color:{color};letter-spacing:-3px;line-height:1;">{score if score is not None else "—"}</div>
        <div style="font-size:13px;color:#6b7280;margin-top:4px;">out of 100</div>
        <div style="margin-top:10px;display:inline-block;padding:4px 16px;border-radius:20px;background:{color}20;color:{color};font-size:13px;font-weight:700;">{label}</div>
      </div>
    </div>

    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
      <tr>
        <td style="padding:12px 16px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);border-radius:10px;">
          <div style="font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">URL Tested</div>
          <div style="font-size:14px;color:#c7d2fe;font-weight:500;word-break:break-all;">{url}</div>
        </td>
      </tr>
    </table>

    {"" if not summary else f'<p style="color:#9ca3af;font-size:14px;line-height:1.7;margin:0 0 24px;padding:16px;background:rgba(99,102,241,0.06);border-left:3px solid #6366f1;border-radius:0 8px 8px 0;">{summary}</p>'}

    <div style="text-align:center;">
      <a href="{app_url}/result/{test_id}" style="display:inline-block;padding:12px 28px;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;text-decoration:none;border-radius:10px;font-size:14px;font-weight:700;letter-spacing:-0.2px;">
        View Full Report →
      </a>
    </div>
    """

    html = _build_html(subject, "Your test is complete!", body)
    return await send_email(to_email, subject, html)


async def send_score_drop(to_email: str, url: str, old_score: int,
                           new_score: int, test_id: str,
                           app_url: str = "http://localhost:5173") -> bool:
    drop = old_score - new_score
    subject = f"⚠️ Score dropped {drop} points — {url}"

    body = f"""
    <p style="color:#9ca3af;font-size:14px;margin:0 0 24px;">
      A score drop was detected for <strong style="color:#e2e8f0;">{url}</strong>.
      This may indicate a regression or new issue on your site.
    </p>

    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:28px;">
      <tr>
        <td width="48%" style="text-align:center;padding:20px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.07);border-radius:12px;">
          <div style="font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px;">Previous Score</div>
          <div style="font-size:40px;font-weight:900;color:{_score_color(old_score)};letter-spacing:-2px;">{old_score}</div>
        </td>
        <td width="4%" style="text-align:center;">
          <div style="font-size:24px;color:#374151;">→</div>
        </td>
        <td width="48%" style="text-align:center;padding:20px;background:rgba(239,68,68,0.06);border:1px solid rgba(239,68,68,0.2);border-radius:12px;">
          <div style="font-size:11px;color:#6b7280;font-weight:600;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px;">New Score</div>
          <div style="font-size:40px;font-weight:900;color:{_score_color(new_score)};letter-spacing:-2px;">{new_score}</div>
        </td>
      </tr>
    </table>

    <div style="text-align:center;margin-bottom:8px;">
      <div style="display:inline-block;padding:6px 18px;border-radius:20px;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);color:#f87171;font-size:13px;font-weight:700;">
        ▼ {drop} point drop
      </div>
    </div>

    <div style="text-align:center;margin-top:24px;">
      <a href="{app_url}/result/{test_id}" style="display:inline-block;padding:12px 28px;background:linear-gradient(135deg,#ef4444,#dc2626);color:#fff;text-decoration:none;border-radius:10px;font-size:14px;font-weight:700;">
        Investigate →
      </a>
    </div>
    """

    html = _build_html(subject, "⚠️ Score Drop Detected", body)
    return await send_email(to_email, subject, html)


async def send_test_failed(to_email: str, url: str, error: str,
                            test_id: str,
                            app_url: str = "http://localhost:5173") -> bool:
    subject = f"❌ Test Failed — {url}"

    body = f"""
    <p style="color:#9ca3af;font-size:14px;margin:0 0 20px;">
      A test for <strong style="color:#e2e8f0;">{url}</strong> failed to complete.
    </p>

    <div style="padding:16px;background:rgba(239,68,68,0.06);border:1px solid rgba(239,68,68,0.2);border-radius:10px;margin-bottom:24px;">
      <div style="font-size:11px;color:#ef4444;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">Error</div>
      <div style="font-size:13px;color:#fca5a5;font-family:monospace;word-break:break-all;">{error[:300]}</div>
    </div>

    <div style="text-align:center;">
      <a href="{app_url}/result/{test_id}" style="display:inline-block;padding:12px 28px;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;text-decoration:none;border-radius:10px;font-size:14px;font-weight:700;">
        View Details →
      </a>
    </div>
    """

    html = _build_html(subject, "Test Failed", body)
    return await send_email(to_email, subject, html)


async def send_scheduled_complete(to_email: str, url: str, score: Optional[int],
                                   summary: Optional[str], test_id: str,
                                   schedule_name: str,
                                   app_url: str = "http://localhost:5173") -> bool:
    """Same as test_complete but with a 'scheduled monitor' label."""
    color = _score_color(score)
    label = _score_label(score)
    subject = f"🕐 Scheduled Check — {url} scored {score}/100" if score else f"🕐 Scheduled Check — {url}"

    body = f"""
    <div style="margin-bottom:20px;">
      <span style="display:inline-block;padding:4px 12px;border-radius:20px;background:rgba(99,102,241,0.12);border:1px solid rgba(99,102,241,0.25);color:#818cf8;font-size:12px;font-weight:600;">
        🕐 Scheduled Monitor: {schedule_name}
      </span>
    </div>

    <div style="text-align:center;margin-bottom:28px;">
      <div style="display:inline-block;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.08);border-radius:16px;padding:24px 40px;">
        <div style="font-size:56px;font-weight:900;color:{color};letter-spacing:-3px;line-height:1;">{score if score is not None else "—"}</div>
        <div style="font-size:13px;color:#6b7280;margin-top:4px;">out of 100</div>
        <div style="margin-top:10px;display:inline-block;padding:4px 16px;border-radius:20px;background:{color}20;color:{color};font-size:13px;font-weight:700;">{label}</div>
      </div>
    </div>

    {"" if not summary else f'<p style="color:#9ca3af;font-size:14px;line-height:1.7;margin:0 0 24px;padding:16px;background:rgba(99,102,241,0.06);border-left:3px solid #6366f1;border-radius:0 8px 8px 0;">{summary}</p>'}

    <div style="text-align:center;">
      <a href="{app_url}/result/{test_id}" style="display:inline-block;padding:12px 28px;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;text-decoration:none;border-radius:10px;font-size:14px;font-weight:700;">
        View Full Report →
      </a>
    </div>
    """

    html = _build_html(subject, "Scheduled check complete!", body)
    return await send_email(to_email, subject, html)

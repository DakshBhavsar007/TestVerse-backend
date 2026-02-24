"""
app/routers/pdf_router.py
Phase 3 — PDF export for test results.
Uses reportlab (works on Windows without GTK dependency).
Install: pip install reportlab
"""
import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.utils.db_results import get_result
from app.config import get_settings

router = APIRouter(prefix="/reports", tags=["Reports"])
settings = get_settings()


def _score_label(score) -> str:
    if score is None: return "N/A"
    if score >= 80: return "Excellent"
    if score >= 60: return "Good"
    if score >= 40: return "Fair"
    return "Poor"


def _score_hex(score) -> str:
    if score is None: return "#6b7280"
    if score >= 80: return "#10b981"
    if score >= 60: return "#f59e0b"
    if score >= 40: return "#f97316"
    return "#ef4444"


def _hex_to_rgb(hex_color: str):
    """Convert #rrggbb to (r, g, b) floats 0-1 for reportlab."""
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4))


def _build_pdf(result: dict, path: str):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT

    doc = SimpleDocTemplate(
        path, pagesize=A4,
        leftMargin=22 * mm, rightMargin=22 * mm,
        topMargin=22 * mm, bottomMargin=22 * mm,
    )

    styles = getSampleStyleSheet()
    story = []

    # ── Colour constants ───────────────────────────────────────────────────────
    INDIGO   = colors.HexColor("#6366f1")
    DARK_BG  = colors.HexColor("#13151f")
    MID_GRAY = colors.HexColor("#6b7280")
    LIGHT    = colors.HexColor("#e2e8f0")

    score       = result.get("overall_score")
    score_hex   = _score_hex(score)
    score_color = colors.HexColor(score_hex)
    score_label = _score_label(score)

    # ── Custom styles ──────────────────────────────────────────────────────────
    brand = ParagraphStyle("brand", fontSize=22, fontName="Helvetica-Bold",
                            textColor=INDIGO, spaceAfter=2)
    subtitle_st = ParagraphStyle("subtitle", fontSize=10, fontName="Helvetica",
                                  textColor=MID_GRAY, spaceAfter=16)
    url_st = ParagraphStyle("url", fontSize=11, fontName="Helvetica",
                             textColor=colors.HexColor("#c7d2fe"), spaceAfter=4)
    score_st = ParagraphStyle("score_num", fontSize=52, fontName="Helvetica-Bold",
                               textColor=score_color, alignment=TA_CENTER, spaceAfter=0)
    score_label_st = ParagraphStyle("score_label", fontSize=14, fontName="Helvetica-Bold",
                                     textColor=score_color, alignment=TA_CENTER, spaceAfter=16)
    section_st = ParagraphStyle("section", fontSize=11, fontName="Helvetica-Bold",
                                 textColor=INDIGO, spaceBefore=18, spaceAfter=6)
    body_st = ParagraphStyle("body", fontSize=10, fontName="Helvetica",
                              textColor=colors.HexColor("#9ca3af"), leading=15, spaceAfter=12)
    footer_st = ParagraphStyle("footer", fontSize=8, fontName="Helvetica",
                                textColor=MID_GRAY, alignment=TA_CENTER, spaceBefore=20)

    # ── Header ─────────────────────────────────────────────────────────────────
    story.append(Paragraph("TestVerse", brand))
    story.append(Paragraph("Automated Website Testing Report", subtitle_st))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1e2030")))
    story.append(Spacer(1, 6 * mm))

    # URL + meta
    story.append(Paragraph(f"URL: {result.get('url', 'N/A')}", url_st))
    started = result.get("started_at", result.get("saved_at", ""))[:19].replace("T", " ")
    story.append(Paragraph(f"Tested: {started} UTC  |  Test ID: {result.get('test_id', '')[:8]}…", subtitle_st))
    story.append(Spacer(1, 4 * mm))

    # ── Score hero ─────────────────────────────────────────────────────────────
    story.append(Paragraph(str(score) if score is not None else "—", score_st))
    story.append(Paragraph(f"{score_label}  (out of 100)", score_label_st))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1e2030")))
    story.append(Spacer(1, 4 * mm))

    # ── Summary ────────────────────────────────────────────────────────────────
    if result.get("summary"):
        story.append(Paragraph("Summary", section_st))
        story.append(Paragraph(result["summary"], body_st))

    # ── Check results table ────────────────────────────────────────────────────
    CHECK_DEFS = [
        ("speed",            "Speed"),
        ("ssl",              "SSL"),
        ("security_headers", "Security Headers"),
        ("seo",              "SEO"),
        ("accessibility",    "Accessibility"),
        ("core_web_vitals",  "Core Web Vitals"),
        ("html_validation",  "HTML Validation"),
        ("content_quality",  "Content Quality"),
        ("cookies_gdpr",     "Cookies / GDPR"),
        ("pwa",              "PWA"),
        ("functionality",    "Functionality"),
        ("broken_links",     "Broken Links"),
        ("js_errors",        "JS Errors"),
        ("images",           "Images"),
        ("mobile",           "Mobile"),
    ]

    def _check_score(data):
        if not data or not isinstance(data, dict): return "—"
        if data.get("score") is not None: return str(data["score"])
        if data.get("valid") is True: return "100"
        if data.get("valid") is False: return "0"
        if data.get("status") == "pass": return "90"
        if data.get("status") == "warning": return "60"
        if data.get("status") == "fail": return "20"
        return "—"

    def _check_status(data):
        if not data or not isinstance(data, dict): return "—"
        return data.get("status", "—").upper()

    def _check_note(data):
        if not data or not isinstance(data, dict): return ""
        return str(data.get("message", data.get("error", "")))[:80]

    rows_with_data = [(label, key) for key, label in CHECK_DEFS if result.get(key)]
    if rows_with_data:
        story.append(Paragraph("Check Results", section_st))
        table_data = [["Check", "Score", "Status", "Note"]]
        for label, key in rows_with_data:
            data = result.get(key, {})
            table_data.append([
                label,
                _check_score(data),
                _check_status(data),
                _check_note(data),
            ])

        col_widths = [52 * mm, 18 * mm, 24 * mm, 68 * mm]
        tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
        tbl.setStyle(TableStyle([
            # Header row
            ("BACKGROUND",   (0, 0), (-1, 0),  INDIGO),
            ("TEXTCOLOR",    (0, 0), (-1, 0),  colors.white),
            ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",     (0, 0), (-1, 0),  9),
            ("TOPPADDING",   (0, 0), (-1, 0),  6),
            ("BOTTOMPADDING",(0, 0), (-1, 0),  6),
            # Data rows
            ("FONTNAME",     (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE",     (0, 1), (-1, -1), 9),
            ("TOPPADDING",   (0, 1), (-1, -1), 5),
            ("BOTTOMPADDING",(0, 1), (-1, -1), 5),
            ("TEXTCOLOR",    (0, 1), (-1, -1), colors.HexColor("#d1d5db")),
            # Alternating rows
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.HexColor("#0f1117"), colors.HexColor("#13151f")]),
            # Grid
            ("GRID",         (0, 0), (-1, -1), 0.4, colors.HexColor("#1e2030")),
            ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(tbl)

    # ── AI Recommendations ─────────────────────────────────────────────────────
    recs = result.get("ai_recommendations", [])
    if recs:
        story.append(Paragraph("AI Recommendations", section_st))
        for i, rec in enumerate(recs[:8], 1):
            story.append(Paragraph(f"{i}. {rec}", body_st))

    # ── Footer ─────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 8 * mm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1e2030")))
    story.append(Paragraph(
        f"Generated by TestVerse · {started} UTC · testverse.app",
        footer_st
    ))

    doc.build(story)


@router.get("/{test_id}/pdf")
async def export_pdf(test_id: str):
    """
    Generate and return a PDF report for a test result.
    The file is cached on disk — re-requests serve the cached version.
    No auth required so share links can trigger downloads too.
    """
    result = await get_result(test_id)
    if not result:
        raise HTTPException(status_code=404, detail="Test not found")

    os.makedirs(settings.reports_dir, exist_ok=True)
    pdf_path = os.path.join(settings.reports_dir, f"{test_id}.pdf")

    # Regenerate if missing or result was updated after last PDF
    if not os.path.exists(pdf_path):
        try:
            _build_pdf(result, pdf_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"PDF generation failed: {e}")

    short_id = test_id[:8]
    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=f"testverse-{short_id}.pdf",
    )
"""
app/services/score_calculator.py
Calculates the overall health score (0–100) from all check results.
Also generates a human-readable summary string.
"""
from typing import Any, Dict, Optional


# Weights must sum to 100
WEIGHTS: Dict[str, Dict] = {
    "speed":            {"key": "score",    "weight": 15},
    "ssl":              {"key": "valid",    "weight": 10, "bool": True},
    "security_headers": {"key": "score",    "weight": 12},
    "core_web_vitals":  {"key": "score",    "weight": 12},
    "seo":              {"key": "score",    "weight": 10},
    "accessibility":    {"key": "score",    "weight": 10},
    "html_validation":  {"key": "score",    "weight": 8},
    "content_quality":  {"key": "score",    "weight": 8},
    "broken_links":     {"key": "_derived", "weight": 8},
    "cookies_gdpr":     {"key": "score",    "weight": 7},
    "pwa":              {"key": "score",    "weight": 5},
    "functionality":    {"key": "score",    "weight": 5},
}


def _extract_value(data: Dict[str, Any], cfg: Dict) -> Optional[float]:
    """Extract a 0–100 numeric value from a check result dict."""
    if not data or not isinstance(data, dict):
        return None

    # Boolean check (e.g. SSL valid/invalid → 100/0)
    if cfg.get("bool"):
        raw = data.get(cfg["key"])
        if raw is None:
            return None
        return 100.0 if raw else 0.0

    # Derived: broken links ratio
    if cfg["key"] == "_derived":
        broken = data.get("broken", [])
        total = data.get("total_checked", 1) or 1
        return max(0.0, 100.0 - (len(broken) / total) * 200)

    # Numeric score field
    val = data.get(cfg["key"])
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def calculate_score(result: Dict[str, Any]) -> int:
    """
    Compute the weighted overall health score (0–100).
    Checks that are missing or errored are skipped (weight excluded),
    so partial results still produce a fair score.
    """
    total_weight = 0.0
    weighted_sum = 0.0

    for check_name, cfg in WEIGHTS.items():
        data = result.get(check_name)
        val = _extract_value(data, cfg)
        if val is None:
            continue
        val = max(0.0, min(100.0, val))   # clamp
        total_weight += cfg["weight"]
        weighted_sum += val * cfg["weight"]

    if total_weight == 0:
        return 0
    return round(weighted_sum / total_weight)


def generate_summary(score: int, result: Dict[str, Any]) -> str:
    """Generate a short human-readable summary of the test result."""
    if score >= 80:
        label = "excellent"
    elif score >= 60:
        label = "good"
    elif score >= 40:
        label = "fair"
    else:
        label = "poor"

    issues = []

    ssl = result.get("ssl", {})
    if ssl.get("valid") is False:
        issues.append("invalid SSL certificate")
    elif ssl.get("expires_in_days") and ssl["expires_in_days"] < 14:
        issues.append(f"SSL expiring in {ssl['expires_in_days']} days")

    speed = result.get("speed", {})
    if speed.get("load_time_ms") and speed["load_time_ms"] > 3000:
        issues.append(f"slow load time ({round(speed['load_time_ms'])}ms)")

    broken = result.get("broken_links", {})
    if broken.get("broken") and len(broken["broken"]) > 0:
        issues.append(f"{len(broken['broken'])} broken link(s)")

    sec = result.get("security_headers", {})
    if sec.get("score") is not None and sec["score"] < 50:
        issues.append("weak security headers")

    seo = result.get("seo", {})
    if seo.get("score") is not None and seo["score"] < 50:
        issues.append("poor SEO")

    url = result.get("url", "the site")
    summary = f"Overall health is {label} ({score}/100)."
    if issues:
        summary += f" Key issues: {', '.join(issues)}."
    else:
        summary += " No critical issues detected."
    return summary


def score_and_summarize(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convenience function: compute score + summary and return them.
    Does NOT mutate result — caller decides when to write back.
    """
    score = calculate_score(result)
    summary = generate_summary(score, result)
    return {"overall_score": score, "summary": summary}

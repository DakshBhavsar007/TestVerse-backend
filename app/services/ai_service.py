"""
TestVerse AI Service — Phase 8A
Uses Groq API (free tier, fast, no credit card needed)
Model: llama-3.3-70b-versatile
"""
import json
import statistics
from typing import Optional
from groq import Groq
from ..config import get_settings

settings = get_settings()
MODEL = "llama-3.3-70b-versatile"


def get_client():
    return Groq(api_key=settings.groq_api_key)


def _call_groq(prompt: str) -> str:
    """Helper to call Groq and return response text."""
    client = get_client()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=2048,
        temperature=0.7,
    )
    return response.choices[0].message.content


def _parse_json_response(text: str) -> dict:
    """Strip markdown code fences and parse JSON."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


# ─────────────────────────────────────────────
# 1. TEST SUGGESTIONS
# ─────────────────────────────────────────────
async def generate_test_suggestions(history: list[dict]) -> dict:
    if not history:
        return {"suggestions": [], "summary": "No history available to analyze."}

    history_summary = []
    for h in history[:30]:
        history_summary.append({
            "url": h.get("url", ""),
            "method": h.get("method", "GET"),
            "status": h.get("status_code"),
            "response_time": h.get("response_time_ms"),
            "passed": h.get("passed", True),
            "tags": h.get("tags", []),
        })

    prompt = f"""You are a senior QA engineer analyzing API test history for TestVerse.

Recent test history:
{json.dumps(history_summary, indent=2)}

Suggest 5-8 new API tests to improve coverage. Focus on:
- Edge cases not currently tested
- Error scenarios (400, 401, 403, 404, 500)
- Security checks (auth headers, SQL injection)
- Missing HTTP methods for endpoints

Return ONLY valid JSON with no extra text:
{{
  "suggestions": [
    {{
      "title": "Test name",
      "url": "https://example.com/api/endpoint",
      "method": "GET",
      "description": "Why this test is important",
      "expected_status": 200,
      "headers": {{}},
      "body": null,
      "priority": "high",
      "category": "security"
    }}
  ],
  "summary": "Brief analysis of coverage gaps"
}}"""

    text = _call_groq(prompt)
    return _parse_json_response(text)


# ─────────────────────────────────────────────
# 2. ANOMALY DETECTION
# ─────────────────────────────────────────────
async def detect_anomalies(history: list[dict]) -> dict:
    if len(history) < 5:
        return {"anomalies": [], "summary": "Not enough data (need at least 5 results)."}

    response_times = [h.get("response_time_ms", 0) for h in history if h.get("response_time_ms")]
    statuses = [h.get("status_code", 200) for h in history if h.get("status_code")]

    stats = {}
    if response_times:
        mean_rt = statistics.mean(response_times)
        stdev_rt = statistics.stdev(response_times) if len(response_times) > 1 else 0
        stats["response_time"] = {
            "mean": round(mean_rt, 2),
            "stdev": round(stdev_rt, 2),
            "min": min(response_times),
            "max": max(response_times),
            "p95": sorted(response_times)[int(len(response_times) * 0.95)] if len(response_times) > 1 else response_times[0],
        }

    error_count = sum(1 for s in statuses if s >= 400)
    stats["error_rate"] = round(error_count / len(statuses) * 100, 1) if statuses else 0

    url_failures = {}
    for h in history:
        url = h.get("url", "")
        if url not in url_failures:
            url_failures[url] = {"total": 0, "failures": 0}
        url_failures[url]["total"] += 1
        if not h.get("passed", True):
            url_failures[url]["failures"] += 1

    high_failure_urls = [
        {"url": url, "failure_rate": round(v["failures"] / v["total"] * 100, 1), "total": v["total"]}
        for url, v in url_failures.items()
        if v["total"] >= 3 and v["failures"] / v["total"] > 0.3
    ]

    prompt = f"""You are an SRE analyzing API test results for anomalies.

Stats: {json.dumps(stats, indent=2)}
High failure URLs: {json.dumps(high_failure_urls, indent=2)}
Recent tests: {json.dumps([{{"url": h.get("url",""), "status": h.get("status_code"), "response_time_ms": h.get("response_time_ms"), "passed": h.get("passed")}} for h in history[:20]], indent=2)}

Return ONLY valid JSON with no extra text:
{{
  "anomalies": [
    {{
      "type": "slow_response|high_error_rate|sudden_failure|pattern_break|security_concern",
      "severity": "critical|warning|info",
      "title": "Short title",
      "description": "Detailed description",
      "affected_urls": ["url1"],
      "recommendation": "What to do",
      "metric": "e.g. 2300ms avg"
    }}
  ],
  "health_score": 85,
  "summary": "Overall health in 1-2 sentences"
}}"""

    text = _call_groq(prompt)
    result = _parse_json_response(text)
    result["stats"] = stats
    return result


# ─────────────────────────────────────────────
# 3. NATURAL LANGUAGE → API TEST
# ─────────────────────────────────────────────
async def nl_to_api_test(natural_language: str, base_url: Optional[str] = None) -> dict:
    context = f"Base URL: {base_url}" if base_url else "Use a realistic example URL."

    prompt = f"""Convert this to a complete API test configuration.

Request: "{natural_language}"
{context}

Return ONLY valid JSON with no extra text:
{{
  "test_name": "Descriptive test name",
  "url": "https://api.example.com/endpoint",
  "method": "GET",
  "headers": {{"Content-Type": "application/json"}},
  "body": null,
  "query_params": {{}},
  "assertions": [
    {{
      "type": "status_code",
      "expected": "200",
      "path": "",
      "description": "What this checks"
    }}
  ],
  "expected_status": 200,
  "timeout_ms": 5000,
  "tags": ["tag1"],
  "description": "What this test verifies",
  "follow_up_tests": [
    {{
      "title": "Related test",
      "description": "Why it matters"
    }}
  ]
}}"""

    text = _call_groq(prompt)
    return _parse_json_response(text)


# ─────────────────────────────────────────────
# 4. AI CHAT
# ─────────────────────────────────────────────
async def ai_chat(message: str, context: dict) -> str:
    prompt = f"""You are TestVerse AI, an expert API testing assistant.
Help users understand test results, debug failures, and improve testing strategy.
Be concise, practical, and friendly. Use markdown for formatting.

Context: {json.dumps(context, indent=2, default=str)}

User: {message}"""

    return _call_groq(prompt)

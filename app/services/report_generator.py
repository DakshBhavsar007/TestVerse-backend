"""
HTML Report Generator — produces a beautiful, downloadable HTML report.
"""
import os
from datetime import datetime
from jinja2 import Template
from ..models import TestResult, CheckStatus
from ..config import get_settings

settings = get_settings()

REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>TestVerse Report — {{ result.url }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@700;800&family=DM+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #080810;
    --surface: #0e0e1c;
    --card: #12121f;
    --card-hover: #161628;
    --border: rgba(255,255,255,0.06);
    --border-bright: rgba(255,255,255,0.12);
    --text: #e8e8f0;
    --muted: #6b6b8a;
    --dim: #3a3a56;
    --accent: #7c6df0;
    --accent-glow: rgba(124,109,240,0.3);
    --pass: #00d68f;
    --pass-bg: rgba(0,214,143,0.08);
    --fail: #ff4d6d;
    --fail-bg: rgba(255,77,109,0.08);
    --warn: #ffb020;
    --warn-bg: rgba(255,176,32,0.08);
    --skip: #4a4a6a;
    --skip-bg: rgba(74,74,106,0.15);
    --score-great: #00d68f;
    --score-ok: #ffb020;
    --score-bad: #ff4d6d;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'DM Sans', sans-serif;
    min-height: 100vh;
    overflow-x: hidden;
  }

  /* ── Background grid ── */
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background-image:
      linear-gradient(rgba(124,109,240,0.03) 1px, transparent 1px),
      linear-gradient(90deg, rgba(124,109,240,0.03) 1px, transparent 1px);
    background-size: 40px 40px;
    pointer-events: none;
    z-index: 0;
  }

  .page-wrap {
    position: relative;
    z-index: 1;
    max-width: 1100px;
    margin: 0 auto;
    padding: 48px 24px 80px;
  }

  /* ── Header ── */
  .header {
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    margin-bottom: 52px;
    padding-bottom: 40px;
    border-bottom: 1px solid var(--border);
  }

  .logo-badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    background: var(--card);
    border: 1px solid var(--border-bright);
    border-radius: 999px;
    padding: 6px 16px 6px 10px;
    margin-bottom: 28px;
    font-size: 0.75rem;
    font-family: 'DM Mono', monospace;
    color: var(--muted);
    letter-spacing: 0.05em;
  }

  .logo-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--accent);
    box-shadow: 0 0 8px var(--accent);
    animation: pulse 2s infinite;
  }

  @keyframes pulse {
    0%,100% { opacity: 1; }
    50% { opacity: 0.4; }
  }

  .header h1 {
    font-family: 'Syne', sans-serif;
    font-size: clamp(2rem, 5vw, 3.2rem);
    font-weight: 800;
    letter-spacing: -0.02em;
    line-height: 1.1;
    color: var(--text);
    margin-bottom: 12px;
  }

  .header h1 span {
    background: linear-gradient(135deg, #7c6df0, #a78bfa, #c084fc);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
  }

  .header-url {
    font-family: 'DM Mono', monospace;
    font-size: 0.85rem;
    color: var(--accent);
    background: rgba(124,109,240,0.08);
    border: 1px solid rgba(124,109,240,0.2);
    padding: 6px 16px;
    border-radius: 6px;
    margin-bottom: 16px;
    word-break: break-all;
  }

  .header-meta {
    font-size: 0.78rem;
    color: var(--muted);
    font-family: 'DM Mono', monospace;
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
    justify-content: center;
  }

  .header-meta span {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  /* ── Score ── */
  .score-section {
    display: flex;
    justify-content: center;
    margin-bottom: 52px;
  }

  .score-ring-wrap {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
  }

  /* This inner container holds the SVG + text overlay together */
  .score-ring-container {
    position: relative;
    width: 180px;
    height: 180px;
    flex-shrink: 0;
  }

  .score-ring-svg {
    position: absolute;
    top: 0; left: 0;
    transform: rotate(-90deg);
    filter: drop-shadow(0 0 20px currentColor);
    z-index: 1;
  }

  .score-inner {
    position: absolute;
    top: 0; left: 0;
    width: 160px;
    height: 160px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    z-index: 2;
    pointer-events: none;
  }

  .score-num {
    font-family: 'Syne', sans-serif;
    font-size: 2.4rem;
    font-weight: 800;
    line-height: 1;
    letter-spacing: -0.04em;
    text-shadow: 0 0 20px currentColor;
  }

  .score-denom {
    font-size: 0.68rem;
    color: var(--muted);
    font-family: 'DM Mono', monospace;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  .score-label {
    font-size: 0.75rem;
    font-family: 'DM Mono', monospace;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--muted);
  }

  /* ── Section title ── */
  .section-title {
    font-family: 'DM Mono', monospace;
    font-size: 0.7rem;
    letter-spacing: 0.15em;
    text-transform: uppercase;
    color: var(--muted);
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .section-title::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border);
  }

  /* ── Grid ── */
  .checks-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 16px;
    margin-bottom: 48px;
  }

  /* ── Card ── */
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 22px;
    transition: border-color 0.2s, background 0.2s;
    position: relative;
    overflow: hidden;
  }

  .card::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent, var(--border-bright), transparent);
  }

  .card:hover {
    border-color: var(--border-bright);
    background: var(--card-hover);
  }

  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 18px;
  }

  .card-title {
    font-family: 'DM Sans', sans-serif;
    font-weight: 600;
    font-size: 0.9rem;
    color: var(--text);
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .card-icon {
    font-size: 1rem;
    opacity: 0.9;
  }

  /* ── Badge ── */
  .badge {
    font-family: 'DM Mono', monospace;
    font-size: 0.65rem;
    font-weight: 500;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    padding: 3px 10px;
    border-radius: 4px;
    border: 1px solid;
  }

  .badge-pass   { color: var(--pass); background: var(--pass-bg); border-color: rgba(0,214,143,0.2); }
  .badge-fail   { color: var(--fail); background: var(--fail-bg); border-color: rgba(255,77,109,0.2); }
  .badge-warning { color: var(--warn); background: var(--warn-bg); border-color: rgba(255,176,32,0.2); }
  .badge-skip   { color: var(--skip); background: var(--skip-bg); border-color: rgba(74,74,106,0.3); }

  /* ── Stats ── */
  .stat-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 9px 0;
    border-bottom: 1px solid var(--border);
    font-size: 0.84rem;
  }

  .stat-row:last-of-type { border-bottom: none; }

  .stat-label { color: var(--muted); }

  .stat-value {
    font-family: 'DM Mono', monospace;
    font-size: 0.82rem;
    font-weight: 500;
    color: var(--text);
  }

  /* ── Message box ── */
  .msg-box {
    margin-top: 14px;
    padding: 10px 14px;
    background: rgba(255,255,255,0.02);
    border-left: 2px solid var(--accent);
    border-radius: 0 8px 8px 0;
    font-size: 0.82rem;
    color: var(--muted);
    line-height: 1.5;
  }

  .msg-box.pass   { border-left-color: var(--pass); }
  .msg-box.fail   { border-left-color: var(--fail); }
  .msg-box.warning { border-left-color: var(--warn); }

  /* ── Broken links list ── */
  .broken-list {
    list-style: none;
    margin-top: 14px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .broken-item {
    padding: 8px 10px;
    background: var(--fail-bg);
    border: 1px solid rgba(255,77,109,0.12);
    border-radius: 8px;
    font-size: 0.75rem;
    line-height: 1.5;
    word-break: break-all;
  }

  .broken-item-code {
    display: inline-block;
    background: rgba(255,77,109,0.2);
    color: var(--fail);
    font-family: 'DM Mono', monospace;
    font-size: 0.7rem;
    padding: 1px 6px;
    border-radius: 3px;
    margin-bottom: 4px;
  }

  .broken-item-url { color: var(--text); margin-bottom: 2px; }
  .broken-item-src { color: var(--muted); font-size: 0.7rem; }

  /* ── Login result ── */
  .login-result {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 14px;
    border-radius: 10px;
    margin-top: 4px;
  }

  .login-result.pass {
    background: var(--pass-bg);
    border: 1px solid rgba(0,214,143,0.15);
  }

  .login-result.fail {
    background: var(--fail-bg);
    border: 1px solid rgba(255,77,109,0.15);
  }

  .login-icon { font-size: 1.4rem; }

  .login-msg {
    font-size: 0.84rem;
    color: var(--text);
    line-height: 1.4;
  }

  /* ── JS error items ── */
  .js-error-item {
    margin-top: 8px;
    padding: 8px 10px;
    background: var(--fail-bg);
    border: 1px solid rgba(255,77,109,0.1);
    border-radius: 6px;
    font-family: 'DM Mono', monospace;
    font-size: 0.72rem;
    color: var(--fail);
    word-break: break-all;
    line-height: 1.5;
  }

  /* ── Crawled pages table ── */
  .table-wrap {
    overflow-x: auto;
    border: 1px solid var(--border);
    border-radius: 16px;
    background: var(--card);
  }

  table.pages-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.8rem;
  }

  .pages-table thead tr {
    border-bottom: 1px solid var(--border-bright);
  }

  .pages-table th {
    padding: 12px 16px;
    text-align: left;
    font-family: 'DM Mono', monospace;
    font-size: 0.65rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
    font-weight: 500;
    white-space: nowrap;
  }

  .pages-table td {
    padding: 11px 16px;
    border-bottom: 1px solid var(--border);
    vertical-align: middle;
    word-break: break-all;
  }

  .pages-table tbody tr:last-child td { border-bottom: none; }

  .pages-table tbody tr:hover td {
    background: rgba(255,255,255,0.015);
  }

  .page-link {
    color: var(--accent);
    text-decoration: none;
    font-size: 0.78rem;
  }
  .page-link:hover { text-decoration: underline; }

  .page-title { color: var(--muted); font-size: 0.75rem; }

  .code-pill {
    display: inline-block;
    font-family: 'DM Mono', monospace;
    font-size: 0.72rem;
    padding: 2px 8px;
    border-radius: 4px;
    font-weight: 500;
  }

  .code-2xx { background: rgba(0,214,143,0.1); color: var(--pass); }
  .code-3xx { background: rgba(255,176,32,0.1); color: var(--warn); }
  .code-4xx, .code-5xx { background: rgba(255,77,109,0.1); color: var(--fail); }
  .code-err { background: var(--skip-bg); color: var(--muted); }

  .speed-val {
    font-family: 'DM Mono', monospace;
    font-size: 0.75rem;
    color: var(--muted);
    white-space: nowrap;
  }

  .depth-pill {
    display: inline-block;
    background: rgba(255,255,255,0.04);
    border: 1px solid var(--border);
    border-radius: 4px;
    font-family: 'DM Mono', monospace;
    font-size: 0.7rem;
    padding: 1px 8px;
    color: var(--muted);
  }

  /* ── Summary bar ── */
  .summary-bar {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    justify-content: center;
    margin-bottom: 48px;
  }

  .summary-chip {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 14px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--card);
    font-size: 0.78rem;
  }

  .chip-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  /* ── Footer ── */
  .footer {
    text-align: center;
    padding-top: 40px;
    border-top: 1px solid var(--border);
    color: var(--muted);
    font-size: 0.75rem;
    font-family: 'DM Mono', monospace;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .footer strong { color: var(--dim); }

  /* ── Post-login UI section ── */
  .post-login-summary {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 12px;
    margin-bottom: 20px;
  }

  .pl-stat-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px;
    text-align: center;
  }

  .pl-stat-num {
    font-family: 'Syne', sans-serif;
    font-size: 2rem;
    font-weight: 800;
    line-height: 1;
    margin-bottom: 4px;
  }

  .pl-stat-label {
    font-family: 'DM Mono', monospace;
    font-size: 0.65rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
  }

  .pl-landing {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 14px 18px;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    margin-bottom: 20px;
  }

  .pl-landing-icon { font-size: 1.4rem; }

  .pl-landing-title {
    font-weight: 600;
    font-size: 0.9rem;
    margin-bottom: 2px;
  }

  .pl-landing-url {
    font-family: 'DM Mono', monospace;
    font-size: 0.72rem;
    color: var(--accent);
    word-break: break-all;
  }

  .actions-table-wrap {
    overflow-x: auto;
    border: 1px solid var(--border);
    border-radius: 16px;
    background: var(--card);
    margin-bottom: 16px;
  }

  table.actions-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.8rem;
  }

  .actions-table thead tr { border-bottom: 1px solid var(--border-bright); }

  .actions-table th {
    padding: 12px 16px;
    text-align: left;
    font-family: 'DM Mono', monospace;
    font-size: 0.65rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--muted);
    font-weight: 500;
    white-space: nowrap;
  }

  .actions-table td {
    padding: 10px 16px;
    border-bottom: 1px solid var(--border);
    vertical-align: middle;
  }

  .actions-table tbody tr:last-child td { border-bottom: none; }
  .actions-table tbody tr:hover td { background: rgba(255,255,255,0.015); }

  .action-type-pill {
    display: inline-block;
    font-family: 'DM Mono', monospace;
    font-size: 0.68rem;
    padding: 2px 8px;
    border-radius: 4px;
    white-space: nowrap;
    background: rgba(255,255,255,0.04);
    border: 1px solid var(--border);
    color: var(--muted);
  }

  .action-label {
    color: var(--text);
    font-size: 0.82rem;
    max-width: 220px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .action-note {
    color: var(--muted);
    font-size: 0.75rem;
    max-width: 260px;
  }

  /* ── Responsive ── */
  @media (max-width: 600px) {
    .checks-grid { grid-template-columns: 1fr; }
    .header-meta { flex-direction: column; gap: 4px; }
  }
</style>
</head>
<body>
<div class="page-wrap">

  <!-- Header -->
  <header class="header">
    <div class="logo-badge">
      <span class="logo-dot"></span>
      TESTVERSE AUDIT REPORT
    </div>
    <h1>Website <span>Health Check</span></h1>
    <div class="header-url">{{ result.url }}</div>
    <div class="header-meta">
      <span>📅 {{ generated_at }}</span>
      <span>🆔 {{ result.test_id[:8] }}…</span>
      <span>🔖 {{ result.test_type | upper }} TEST</span>
    </div>
  </header>

  <!-- Score ring -->
  {% if result.overall_score is not none %}
  {% set sc = result.overall_score %}
  {% set color = '#00d68f' if sc >= 80 else ('#ffb020' if sc >= 50 else '#ff4d6d') %}
  {% set ring_class = 'great' if sc >= 80 else ('ok' if sc >= 50 else 'bad') %}
  {% set circumference = 402.12 %}
  {% set dash = (sc / 100) * circumference %}
  <div class="score-section">
    <div class="score-ring-wrap">
      <div class="score-ring-container">
        <svg class="score-ring-svg" width="180" height="180" style="color:{{ color }}">
          <circle cx="90" cy="90" r="64" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="10"/>
          <circle cx="90" cy="90" r="64" fill="none" stroke="{{ color }}" stroke-width="10"
            stroke-linecap="round"
            stroke-dasharray="{{ dash }} {{ circumference }}"/>
        </svg>
        <div class="score-inner" style="width:180px;height:180px;">
          <span class="score-num" style="color:{{ color }}">{{ sc }}</span>
          <span class="score-denom">/ 100</span>
        </div>
      </div>
      <div class="score-label">Overall Health Score</div>
    </div>
  </div>

  <!-- Summary chips -->
  <div class="summary-bar">
    {% if result.uptime %}<div class="summary-chip"><span class="chip-dot" style="background:{% if result.uptime.status.value == 'pass' %}var(--pass){% elif result.uptime.status.value == 'fail' %}var(--fail){% else %}var(--warn){% endif %}"></span>Uptime {{ result.uptime.status.value | upper }}</div>{% endif %}
    {% if result.speed %}<div class="summary-chip"><span class="chip-dot" style="background:{% if result.speed.status.value == 'pass' %}var(--pass){% elif result.speed.status.value == 'fail' %}var(--fail){% else %}var(--warn){% endif %}"></span>Speed {{ result.speed.status.value | upper }}</div>{% endif %}
    {% if result.ssl %}<div class="summary-chip"><span class="chip-dot" style="background:{% if result.ssl.status.value == 'pass' %}var(--pass){% elif result.ssl.status.value == 'fail' %}var(--fail){% else %}var(--warn){% endif %}"></span>SSL {{ result.ssl.status.value | upper }}</div>{% endif %}
    {% if result.broken_links %}<div class="summary-chip"><span class="chip-dot" style="background:{% if result.broken_links.status.value == 'pass' %}var(--pass){% elif result.broken_links.status.value == 'fail' %}var(--fail){% else %}var(--warn){% endif %}"></span>Links {{ result.broken_links.status.value | upper }}</div>{% endif %}
    {% if result.mobile_responsiveness %}<div class="summary-chip"><span class="chip-dot" style="background:{% if result.mobile_responsiveness.status.value == 'pass' %}var(--pass){% elif result.mobile_responsiveness.status.value == 'fail' %}var(--fail){% else %}var(--warn){% endif %}"></span>Mobile {{ result.mobile_responsiveness.status.value | upper }}</div>{% endif %}
    {% if result.login_success is not none %}<div class="summary-chip"><span class="chip-dot" style="background:{% if result.login_success %}var(--pass){% else %}var(--fail){% endif %}"></span>Login {% if result.login_success %}PASS{% else %}FAIL{% endif %}</div>{% endif %}
  </div>
  {% endif %}

  <!-- Checks grid -->
  <div class="section-title">Check Results</div>
  <div class="checks-grid">

    {% if result.uptime %}
    <div class="card">
      <div class="card-header">
        <div class="card-title"><span class="card-icon">🌐</span> Uptime</div>
        <span class="badge badge-{{ result.uptime.status.value }}">{{ result.uptime.status.value }}</span>
      </div>
      {% if result.uptime.http_status_code %}<div class="stat-row"><span class="stat-label">HTTP Status</span><span class="stat-value">{{ result.uptime.http_status_code }}</span></div>{% endif %}
      {% if result.uptime.response_time_ms %}<div class="stat-row"><span class="stat-label">Response Time</span><span class="stat-value">{{ result.uptime.response_time_ms }} ms</span></div>{% endif %}
      <div class="msg-box {{ result.uptime.status.value }}">{{ result.uptime.message }}</div>
    </div>
    {% endif %}

    {% if result.speed %}
    <div class="card">
      <div class="card-header">
        <div class="card-title"><span class="card-icon">⚡</span> Speed</div>
        <span class="badge badge-{{ result.speed.status.value }}">{{ result.speed.status.value }}</span>
      </div>
      {% if result.speed.load_time_ms %}<div class="stat-row"><span class="stat-label">Load Time</span><span class="stat-value">{{ result.speed.load_time_ms }} ms</span></div>{% endif %}
      {% if result.speed.ttfb_ms %}<div class="stat-row"><span class="stat-label">TTFB</span><span class="stat-value">{{ result.speed.ttfb_ms }} ms</span></div>{% endif %}
      {% if result.speed.page_size_kb %}<div class="stat-row"><span class="stat-label">Page Size</span><span class="stat-value">{{ result.speed.page_size_kb }} KB</span></div>{% endif %}
      <div class="msg-box {{ result.speed.status.value }}">{{ result.speed.message }}</div>
    </div>
    {% endif %}

    {% if result.ssl %}
    <div class="card">
      <div class="card-header">
        <div class="card-title"><span class="card-icon">🔒</span> SSL Certificate</div>
        <span class="badge badge-{{ result.ssl.status.value }}">{{ result.ssl.status.value }}</span>
      </div>
      {% if result.ssl.issuer %}<div class="stat-row"><span class="stat-label">Issuer</span><span class="stat-value">{{ result.ssl.issuer }}</span></div>{% endif %}
      {% if result.ssl.expires_on %}<div class="stat-row"><span class="stat-label">Expires</span><span class="stat-value">{{ result.ssl.expires_on }}</span></div>{% endif %}
      {% if result.ssl.days_until_expiry is not none %}<div class="stat-row"><span class="stat-label">Days Left</span><span class="stat-value">{{ result.ssl.days_until_expiry }}d</span></div>{% endif %}
      <div class="msg-box {{ result.ssl.status.value }}">{{ result.ssl.message }}</div>
    </div>
    {% endif %}

    {% if result.mobile_responsiveness %}
    <div class="card">
      <div class="card-header">
        <div class="card-title"><span class="card-icon">📱</span> Mobile</div>
        <span class="badge badge-{{ result.mobile_responsiveness.status.value }}">{{ result.mobile_responsiveness.status.value }}</span>
      </div>
      <div class="stat-row"><span class="stat-label">Viewport Meta</span><span class="stat-value">{{ '✅ Yes' if result.mobile_responsiveness.has_viewport_meta else '❌ No' }}</span></div>
      <div class="stat-row"><span class="stat-label">Responsive CSS</span><span class="stat-value">{{ '✅ Yes' if result.mobile_responsiveness.has_responsive_css else '❌ No' }}</span></div>
      {% if result.mobile_responsiveness.mobile_score is not none %}<div class="stat-row"><span class="stat-label">Mobile Score</span><span class="stat-value">{{ result.mobile_responsiveness.mobile_score }}/100</span></div>{% endif %}
      <div class="msg-box {{ result.mobile_responsiveness.status.value }}">{{ result.mobile_responsiveness.message }}</div>
    </div>
    {% endif %}

    {% if result.broken_links %}
    <div class="card">
      <div class="card-header">
        <div class="card-title"><span class="card-icon">🔗</span> Broken Links</div>
        <span class="badge badge-{{ result.broken_links.status.value }}">{{ result.broken_links.status.value }}</span>
      </div>
      <div class="stat-row"><span class="stat-label">Total Checked</span><span class="stat-value">{{ result.broken_links.total_links }}</span></div>
      <div class="stat-row"><span class="stat-label">Broken</span><span class="stat-value">{{ result.broken_links.broken_count }}</span></div>
      <div class="msg-box {{ result.broken_links.status.value }}">{{ result.broken_links.message }}</div>
      {% if result.broken_links.broken_links %}
      <ul class="broken-list">
        {% for bl in result.broken_links.broken_links[:5] %}
        <li class="broken-item">
          <div><span class="broken-item-code">{{ bl.status_code or bl.error }}</span></div>
          <div class="broken-item-url">{{ bl.url[:80] }}{% if bl.url|length > 80 %}…{% endif %}</div>
          <div class="broken-item-src">on: {{ bl.found_on }}</div>
        </li>
        {% endfor %}
      </ul>
      {% endif %}
    </div>
    {% endif %}

    {% if result.missing_images %}
    <div class="card">
      <div class="card-header">
        <div class="card-title"><span class="card-icon">🖼️</span> Images</div>
        <span class="badge badge-{{ result.missing_images.status.value }}">{{ result.missing_images.status.value }}</span>
      </div>
      <div class="stat-row"><span class="stat-label">Total Images</span><span class="stat-value">{{ result.missing_images.total_images }}</span></div>
      <div class="stat-row"><span class="stat-label">Missing</span><span class="stat-value">{{ result.missing_images.missing_count }}</span></div>
      <div class="msg-box {{ result.missing_images.status.value }}">{{ result.missing_images.message }}</div>
    </div>
    {% endif %}

    {% if result.js_errors %}
    <div class="card">
      <div class="card-header">
        <div class="card-title"><span class="card-icon">🐛</span> JS Errors</div>
        <span class="badge badge-{{ result.js_errors.status.value }}">{{ result.js_errors.status.value }}</span>
      </div>
      <div class="stat-row"><span class="stat-label">Error Count</span><span class="stat-value">{{ result.js_errors.error_count }}</span></div>
      <div class="msg-box {{ result.js_errors.status.value }}">{{ result.js_errors.message }}</div>
      {% for err in result.js_errors.errors[:3] %}
      <div class="js-error-item">{{ err.message[:100] }}</div>
      {% endfor %}
    </div>
    {% endif %}

    {% if result.login_success is not none %}
    <div class="card">
      <div class="card-header">
        <div class="card-title"><span class="card-icon">🔑</span> Login Test</div>
        <span class="badge {{ 'badge-pass' if result.login_success else 'badge-fail' }}">{{ 'pass' if result.login_success else 'fail' }}</span>
      </div>
      <div class="login-result {{ 'pass' if result.login_success else 'fail' }}">
        <span class="login-icon">{{ '✅' if result.login_success else '❌' }}</span>
        <span class="login-msg">{{ result.login_message }}</span>
      </div>
    </div>
    {% endif %}

  </div>

  <!-- Post-Login UI Interactions -->
  {% if result.post_login %}
  <div class="section-title" style="margin-top:48px">Post-Login UI Testing</div>
  <div class="post-login-summary">
    <div class="pl-stat-card">
      <div class="pl-stat-num" style="color:var(--pass)">{{ result.post_login.nav_links_passed }}</div>
      <div class="pl-stat-label">Nav Links Passed</div>
    </div>
    <div class="pl-stat-card">
      <div class="pl-stat-num" style="color:{{ 'var(--fail)' if result.post_login.nav_links_failed > 0 else 'var(--muted)' }}">{{ result.post_login.nav_links_failed }}</div>
      <div class="pl-stat-label">Nav Links Failed</div>
    </div>
    <div class="pl-stat-card">
      <div class="pl-stat-num" style="color:var(--pass)">{{ result.post_login.buttons_passed }}</div>
      <div class="pl-stat-label">Buttons Passed</div>
    </div>
    <div class="pl-stat-card">
      <div class="pl-stat-num" style="color:{{ 'var(--fail)' if result.post_login.buttons_failed > 0 else 'var(--muted)' }}">{{ result.post_login.buttons_failed }}</div>
      <div class="pl-stat-label">Buttons Failed</div>
    </div>
    <div class="pl-stat-card">
      <div class="pl-stat-num" style="color:var(--accent)">{{ result.post_login.forms_found }}</div>
      <div class="pl-stat-label">Forms Detected</div>
    </div>
  </div>

  <div class="pl-landing">
    <span class="pl-landing-icon">🏠</span>
    <div>
      <div class="pl-landing-title">{{ result.post_login.landing_title or 'No title' }}</div>
      <div class="pl-landing-url">{{ result.post_login.landing_url }}</div>
    </div>
    <span class="badge badge-{{ result.post_login.status.value }}">{{ result.post_login.status.value }}</span>
  </div>

  {% if result.post_login.actions %}
  <div class="actions-table-wrap">
    <table class="actions-table">
      <thead>
        <tr>
          <th>Type</th>
          <th>Element</th>
          <th>Status</th>
          <th>Speed</th>
          <th>Notes</th>
        </tr>
      </thead>
      <tbody>
      {% for action in result.post_login.actions %}
      <tr>
        <td>
          <span class="action-type-pill action-{{ action.action_type }}">
            {% if action.action_type == 'nav_link' %}🔗 Nav
            {% elif action.action_type == 'button' %}🖱️ Button
            {% elif action.action_type == 'form' %}📋 Form
            {% elif action.action_type == 'modal' %}💬 Modal
            {% else %}{{ action.action_type }}{% endif %}
          </span>
        </td>
        <td class="action-label">{{ action.label[:50] }}</td>
        <td>
          {% if action.status.value == 'pass' %}<span class="code-pill code-2xx">PASS</span>
          {% elif action.status.value == 'fail' %}<span class="code-pill code-4xx">FAIL</span>
          {% else %}<span class="code-pill code-err">SKIP</span>{% endif %}
        </td>
        <td class="speed-val">{{ action.response_time_ms ~ ' ms' if action.response_time_ms else '—' }}</td>
        <td class="action-note">
          {% if action.error %}<span style="color:var(--fail)">{{ action.error[:80] }}</span>
          {% elif action.screenshot_note %}{{ action.screenshot_note[:80] }}
          {% elif action.result_url %}→ {{ action.result_url[:60] }}
          {% else %}—{% endif %}
        </td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}

  {% if result.post_login.js_errors_post_login %}
  <div style="margin-top:16px">
    <div class="section-title" style="font-size:0.65rem">JS Errors During UI Testing</div>
    {% for err in result.post_login.js_errors_post_login[:5] %}
    <div class="js-error-item">{{ err.message[:120] }}</div>
    {% endfor %}
  </div>
  {% endif %}

  {% endif %}

  <!-- Crawled pages -->
  {% if result.pages_crawled %}
  <div class="section-title">Crawled Pages ({{ result.total_pages }})</div>
  <div class="table-wrap">
    <table class="pages-table">
      <thead>
        <tr>
          <th>URL</th>
          <th>Status</th>
          <th>Speed</th>
          <th>Title</th>
          <th>Depth</th>
        </tr>
      </thead>
      <tbody>
      {% for page in result.pages_crawled[:30] %}
      <tr>
        <td><a class="page-link" href="{{ page.url }}" target="_blank">{{ page.url[:65] }}{% if page.url|length > 65 %}…{% endif %}</a></td>
        <td>
          {% if page.status_code %}
            {% if page.status_code < 300 %}<span class="code-pill code-2xx">{{ page.status_code }}</span>
            {% elif page.status_code < 400 %}<span class="code-pill code-3xx">{{ page.status_code }}</span>
            {% elif page.status_code < 500 %}<span class="code-pill code-4xx">{{ page.status_code }}</span>
            {% else %}<span class="code-pill code-5xx">{{ page.status_code }}</span>{% endif %}
          {% else %}<span class="code-pill code-err">ERR</span>{% endif %}
        </td>
        <td><span class="speed-val">{{ page.load_time_ms or '—' }} ms</span></td>
        <td><span class="page-title">{{ (page.title or '—')[:40] }}</span></td>
        <td><span class="depth-pill">{{ page.depth }}</span></td>
      </tr>
      {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}

  <!-- Footer -->
  <footer class="footer">
    <div>Generated by <strong>TestVerse</strong> — Automated Website Quality Assurance</div>
    <div>{{ generated_at }} &nbsp;·&nbsp; {{ result.test_id }}</div>
  </footer>

</div>
</body>
</html>
"""


async def generate_html_report(result: TestResult) -> str:
    """Generate a downloadable HTML report and save it. Returns the file path."""
    os.makedirs(settings.reports_dir, exist_ok=True)
    filename = f"report_{result.test_id}.html"
    filepath = os.path.join(settings.reports_dir, filename)

    template = Template(REPORT_TEMPLATE)
    try:
        os.makedirs(settings.reports_dir, exist_ok=True)
        filename = f"report_{result.test_id}.html"
        file_path = os.path.join(settings.reports_dir, filename)

        template = Template(REPORT_TEMPLATE)
        html_content = template.render(
            result=result,
            generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        )

        with open(file_path, "w", encoding="utf-8") as fp:
            fp.write(html_content)

        return file_path
    except Exception as e:
        print(f"Error generating HTML report: {e}")
        return ""


async def generate_pdf_report(result: TestResult) -> str:
    """Generate a cleanly formatted PDF report using reportlab's platypus layout engine."""
    import os
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors

    try:
        if not os.path.exists(settings.reports_dir):
            os.makedirs(settings.reports_dir)
            
        file_name = f"report_{result.test_id[:8]}.pdf"
        file_path = os.path.join(settings.reports_dir, file_name)

        doc = SimpleDocTemplate(file_path, pagesize=letter)
        styles = getSampleStyleSheet()
        story = []

        # Custom styles
        title_style = ParagraphStyle(
            'TitleStyle',
            parent=styles['Heading1'],
            fontSize=22,
            spaceAfter=20,
            textColor=colors.darkblue
        )
        h2_style = ParagraphStyle(
            'Heading2Style',
            parent=styles['Heading2'],
            fontSize=16,
            spaceAfter=10,
            textColor=colors.indigo
        )
        normal_style = styles['Normal']

        # Header
        story.append(Paragraph(f"TestVerse Audit Report", title_style))
        story.append(Paragraph(f"URL: {result.url}", normal_style))
        story.append(Paragraph(f"Test ID: {result.test_id}", normal_style))
        story.append(Paragraph(f"Score: {result.overall_score}/100" if result.overall_score else "Score: N/A", normal_style))
        story.append(Spacer(1, 20))

        # Check results table
        data = [['Module', 'Status', 'Message']]
        
        msg_style = styles['Normal']
        
        if result.uptime:
            data.append(['Uptime', result.uptime.status.value.upper(), Paragraph(result.uptime.message, msg_style)])
        if result.speed:
            data.append(['Speed', result.speed.status.value.upper(), Paragraph(result.speed.message, msg_style)])
        if result.ssl:
            data.append(['SSL Configuration', result.ssl.status.value.upper(), Paragraph(result.ssl.message, msg_style)])
        if result.broken_links:
            data.append(['Broken Links', result.broken_links.status.value.upper(), Paragraph(f"Found {result.broken_links.broken_count} broken links.", msg_style)])
        if result.mobile_responsiveness:
            data.append(['Mobile Friendly', result.mobile_responsiveness.status.value.upper(), Paragraph(result.mobile_responsiveness.message, msg_style)])
            
        t = Table(data, colWidths=[130, 80, 290])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.darkblue),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        story.append(t)
        story.append(Spacer(1, 20))

        # AI Recommendations
        if result.ai_recommendations:
            story.append(Paragraph("AI Recommendations", h2_style))
            for i, rec in enumerate(result.ai_recommendations):
                story.append(Paragraph(f"{i+1}. {rec}", normal_style))
                story.append(Spacer(1, 5))
            story.append(Spacer(1, 15))

        # Build PDF
        doc.build(story)
        return file_path
        
    except Exception as e:
        print(f"Error generating PDF report: {e}")
        return ""


def calculate_overall_score(result: TestResult) -> int:
    """
    Calculate a 0-100 overall health score from all checks.
    Weights: uptime(30), speed(20), ssl(15), broken_links(15),
             mobile(10), images(5), js_errors(5)
    """
    weights = {
        "uptime": 30,
        "speed": 20,
        "ssl": 15,
        "broken_links": 15,
        "mobile_responsiveness": 10,
        "missing_images": 5,
        "js_errors": 5,
    }

    def check_score(check, weight):
        if check is None:
            return weight
        status = check.status
        if status == CheckStatus.PASS:
            return weight
        elif status == CheckStatus.WARNING:
            return weight // 2
        elif status == CheckStatus.SKIP:
            return weight
        else:
            return 0

    score = 0
    score += check_score(result.uptime, weights["uptime"])
    score += check_score(result.speed, weights["speed"])
    score += check_score(result.ssl, weights["ssl"])
    score += check_score(result.broken_links, weights["broken_links"])
    score += check_score(result.mobile_responsiveness, weights["mobile_responsiveness"])
    score += check_score(result.missing_images, weights["missing_images"])
    score += check_score(result.js_errors, weights["js_errors"])

    return min(100, max(0, score))
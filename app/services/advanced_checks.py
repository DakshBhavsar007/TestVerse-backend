"""
app/services/advanced_checks.py
9 advanced website checks:
  1. SEO analysis
  2. Accessibility audit
  3. Security headers
  4. Core Web Vitals (via Playwright)
  5. Cookie / GDPR compliance
  6. HTML validation
  7. Content quality
  8. PWA readiness
  9. Functionality audit (forms, CTAs, search, navigation)
"""
import asyncio
import re
import socket
import sys
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ── Shared HTTP helper ─────────────────────────────────────────────────────────

def _get(url: str, timeout: int = 15) -> requests.Response:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/121.0.0.0 Safari/537.36"
        )
    }
    return requests.get(url, timeout=timeout, headers=headers)


def _soup(url: str) -> tuple:
    """Return (response, BeautifulSoup) or raise."""
    r = _get(url)
    return r, BeautifulSoup(r.text, "html.parser")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SEO ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

def check_seo(url: str) -> dict:
    issues = []
    score = 100
    data: Dict[str, Any] = {}

    try:
        r, soup = _soup(url)

        # Title
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""
        data["title"] = title
        if not title:
            issues.append({"severity": "error", "message": "Missing <title> tag"})
            score -= 15
        elif len(title) < 10:
            issues.append({"severity": "warning", "message": f"Title too short ({len(title)} chars, recommend 50–60)"})
            score -= 5
        elif len(title) > 70:
            issues.append({"severity": "warning", "message": f"Title too long ({len(title)} chars, recommend 50–60)"})
            score -= 5

        # Meta description
        meta_desc = soup.find("meta", attrs={"name": "description"})
        desc = meta_desc.get("content", "").strip() if meta_desc else ""
        data["meta_description"] = desc
        if not desc:
            issues.append({"severity": "error", "message": "Missing meta description"})
            score -= 15
        elif len(desc) < 50:
            issues.append({"severity": "warning", "message": f"Meta description too short ({len(desc)} chars)"})
            score -= 5
        elif len(desc) > 160:
            issues.append({"severity": "warning", "message": f"Meta description too long ({len(desc)} chars)"})
            score -= 3

        # Headings
        h1s = soup.find_all("h1")
        h2s = soup.find_all("h2")
        data["h1_count"] = len(h1s)
        data["h2_count"] = len(h2s)
        data["h1_text"] = [h.get_text(strip=True)[:80] for h in h1s[:3]]
        if len(h1s) == 0:
            issues.append({"severity": "error", "message": "No <h1> tag found"})
            score -= 10
        elif len(h1s) > 1:
            issues.append({"severity": "warning", "message": f"Multiple <h1> tags found ({len(h1s)})"})
            score -= 5

        # Canonical
        canonical = soup.find("link", attrs={"rel": "canonical"})
        data["canonical"] = canonical.get("href", "") if canonical else None
        if not canonical:
            issues.append({"severity": "info", "message": "No canonical URL defined"})

        # OG tags
        og_title = soup.find("meta", attrs={"property": "og:title"})
        og_desc = soup.find("meta", attrs={"property": "og:description"})
        og_image = soup.find("meta", attrs={"property": "og:image"})
        data["og_tags"] = {
            "title": og_title.get("content", "") if og_title else None,
            "description": og_desc.get("content", "") if og_desc else None,
            "image": og_image.get("content", "") if og_image else None,
        }
        if not og_title:
            issues.append({"severity": "info", "message": "Missing og:title (Open Graph)"})
        if not og_image:
            issues.append({"severity": "info", "message": "Missing og:image (Open Graph)"})

        # Sitemap
        p = urlparse(url)
        sitemap_url = f"{p.scheme}://{p.netloc}/sitemap.xml"
        try:
            sr = requests.head(sitemap_url, timeout=8)
            data["sitemap"] = sitemap_url if sr.status_code < 400 else None
            if sr.status_code >= 400:
                issues.append({"severity": "info", "message": "No sitemap.xml found"})
        except Exception:
            data["sitemap"] = None

        # Robots.txt
        robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
        try:
            rr = requests.get(robots_url, timeout=8)
            data["robots_txt"] = rr.status_code < 400
            if rr.status_code >= 400:
                issues.append({"severity": "info", "message": "No robots.txt found"})
        except Exception:
            data["robots_txt"] = False

        # Image alt tags (SEO angle)
        imgs = soup.find_all("img")
        missing_alt = [i.get("src", "")[:60] for i in imgs if not i.get("alt")]
        data["images_missing_alt"] = len(missing_alt)
        if missing_alt:
            issues.append({"severity": "warning", "message": f"{len(missing_alt)} image(s) missing alt attributes"})
            score -= min(10, len(missing_alt) * 2)

        score = max(0, score)
        status = "pass" if score >= 80 else ("warning" if score >= 50 else "fail")
        return {"status": status, "score": score, "issues": issues, "data": data}

    except Exception as e:
        return {"status": "error", "score": 0, "issues": [], "error": str(e), "data": {}}


# ═══════════════════════════════════════════════════════════════════════════════
# 2. ACCESSIBILITY AUDIT
# ═══════════════════════════════════════════════════════════════════════════════

def check_accessibility(url: str) -> dict:
    issues = []
    score = 100
    data: Dict[str, Any] = {}

    try:
        r, soup = _soup(url)

        # Images without alt
        imgs = soup.find_all("img")
        missing_alt = [i.get("src", "")[:80] for i in imgs if not i.get("alt") and not i.get("role") == "presentation"]
        data["images_missing_alt"] = len(missing_alt)
        data["images_missing_alt_samples"] = missing_alt[:5]
        if missing_alt:
            issues.append({"severity": "error", "message": f"{len(missing_alt)} image(s) missing alt text (WCAG 1.1.1)"})
            score -= min(20, len(missing_alt) * 3)

        # Form labels
        inputs = soup.find_all("input", type=lambda t: t not in ["hidden", "submit", "button", "image"])
        unlabeled = []
        for inp in inputs:
            inp_id = inp.get("id")
            has_label = (inp_id and soup.find("label", attrs={"for": inp_id})) or inp.get("aria-label") or inp.get("aria-labelledby")
            if not has_label:
                unlabeled.append(inp.get("name", inp.get("type", "unknown")))
        data["unlabeled_inputs"] = len(unlabeled)
        if unlabeled:
            issues.append({"severity": "error", "message": f"{len(unlabeled)} form input(s) missing labels (WCAG 1.3.1)"})
            score -= min(15, len(unlabeled) * 3)

        # Buttons without accessible text
        buttons = soup.find_all("button")
        unnamed_btns = [b for b in buttons if not b.get_text(strip=True) and not b.get("aria-label") and not b.get("title")]
        data["unnamed_buttons"] = len(unnamed_btns)
        if unnamed_btns:
            issues.append({"severity": "warning", "message": f"{len(unnamed_btns)} button(s) have no accessible text (WCAG 4.1.2)"})
            score -= min(10, len(unnamed_btns) * 2)

        # Lang attribute
        html_tag = soup.find("html")
        has_lang = html_tag and html_tag.get("lang")
        data["lang_attribute"] = str(has_lang) if has_lang else None
        if not has_lang:
            issues.append({"severity": "error", "message": "Missing lang attribute on <html> (WCAG 3.1.1)"})
            score -= 10

        # Skip navigation link
        skip_links = soup.find_all("a", href=lambda h: h and h.startswith("#"))
        has_skip = any("skip" in (a.get_text().lower()) or "main" in (a.get("href", "").lower()) for a in skip_links[:5])
        data["has_skip_link"] = has_skip
        if not has_skip:
            issues.append({"severity": "info", "message": "No skip navigation link found (WCAG 2.4.1)"})

        # ARIA landmarks
        landmarks = soup.find_all(["main", "nav", "header", "footer", "aside"])
        role_landmarks = soup.find_all(attrs={"role": ["main", "navigation", "banner", "contentinfo", "complementary"]})
        total_landmarks = len(landmarks) + len(role_landmarks)
        data["landmark_count"] = total_landmarks
        if total_landmarks == 0:
            issues.append({"severity": "warning", "message": "No ARIA landmark regions found (WCAG 1.3.6)"})
            score -= 5

        # Tab index issues
        bad_tabindex = soup.find_all(attrs={"tabindex": lambda v: v and int(v) > 0})
        if bad_tabindex:
            issues.append({"severity": "warning", "message": f"{len(bad_tabindex)} element(s) with tabindex > 0 (disrupts natural focus order)"})
            score -= 5

        score = max(0, score)
        status = "pass" if score >= 80 else ("warning" if score >= 50 else "fail")
        return {"status": status, "score": score, "issues": issues, "data": data}

    except Exception as e:
        return {"status": "error", "score": 0, "issues": [], "error": str(e), "data": {}}


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SECURITY HEADERS
# ═══════════════════════════════════════════════════════════════════════════════

def check_security_headers(url: str) -> dict:
    REQUIRED = {
        "strict-transport-security": {"severity": "error", "desc": "HSTS — enforces HTTPS connections"},
        "content-security-policy": {"severity": "error", "desc": "CSP — prevents XSS attacks"},
        "x-frame-options": {"severity": "warning", "desc": "Prevents clickjacking attacks"},
        "x-content-type-options": {"severity": "warning", "desc": "Prevents MIME type sniffing"},
        "referrer-policy": {"severity": "info", "desc": "Controls referrer information"},
        "permissions-policy": {"severity": "info", "desc": "Controls browser feature access"},
    }
    score = 100
    issues = []
    present = {}
    missing = []

    try:
        r = requests.head(url, timeout=10, allow_redirects=True)
        headers_lower = {k.lower(): v for k, v in r.headers.items()}

        for header, meta in REQUIRED.items():
            if header in headers_lower:
                present[header] = headers_lower[header]
            else:
                missing.append({"header": header, **meta})
                deduction = {"error": 20, "warning": 10, "info": 3}[meta["severity"]]
                score -= deduction
                issues.append({"severity": meta["severity"], "message": f"Missing {header.title()} — {meta['desc']}"})

        # Check for insecure cookies
        set_cookie = headers_lower.get("set-cookie", "")
        if set_cookie and "secure" not in set_cookie.lower():
            issues.append({"severity": "warning", "message": "Cookie(s) missing Secure flag"})
            score -= 5
        if set_cookie and "httponly" not in set_cookie.lower():
            issues.append({"severity": "warning", "message": "Cookie(s) missing HttpOnly flag"})
            score -= 5

        # Server header info leakage
        server = headers_lower.get("server", "")
        if server and any(v in server.lower() for v in ["apache/", "nginx/", "iis/"]):
            issues.append({"severity": "info", "message": f"Server header reveals version info: {server}"})

        score = max(0, score)
        status = "pass" if score >= 80 else ("warning" if score >= 50 else "fail")
        return {
            "status": status, "score": score, "issues": issues,
            "present": present, "missing": [m["header"] for m in missing],
        }

    except Exception as e:
        return {"status": "error", "score": 0, "issues": [], "error": str(e), "present": {}, "missing": []}


# ═══════════════════════════════════════════════════════════════════════════════
# 4. CORE WEB VITALS (Playwright)
# ═══════════════════════════════════════════════════════════════════════════════

def check_core_web_vitals(url: str) -> dict:
    """Measure LCP, CLS, FID proxy (TBT) via Playwright JS injection."""
    async def _async():
        from playwright.async_api import async_playwright
        browser = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(viewport={"width": 1280, "height": 800})
                page = await context.new_page()

                # Inject web-vitals measurement
                vitals_script = """
                () => new Promise((resolve) => {
                    const result = { lcp: null, cls: 0, fid: null, tbt: 0 };
                    
                    // LCP
                    const lcpObs = new PerformanceObserver((list) => {
                        const entries = list.getEntries();
                        if (entries.length) result.lcp = entries[entries.length - 1].startTime;
                    });
                    try { lcpObs.observe({ type: 'largest-contentful-paint', buffered: true }); } catch(e) {}
                    
                    // CLS
                    let clsValue = 0;
                    const clsObs = new PerformanceObserver((list) => {
                        for (const entry of list.getEntries()) {
                            if (!entry.hadRecentInput) clsValue += entry.value;
                        }
                        result.cls = clsValue;
                    });
                    try { clsObs.observe({ type: 'layout-shift', buffered: true }); } catch(e) {}
                    
                    // TBT proxy (long tasks)
                    let tbt = 0;
                    const tbtObs = new PerformanceObserver((list) => {
                        for (const entry of list.getEntries()) {
                            tbt += Math.max(0, entry.duration - 50);
                        }
                        result.tbt = tbt;
                    });
                    try { tbtObs.observe({ type: 'longtask', buffered: true }); } catch(e) {}
                    
                    setTimeout(() => {
                        result.cls = clsValue;
                        result.tbt = tbt;
                        resolve(result);
                    }, 4000);
                })
                """

                try:
                    await page.goto(url, timeout=30000, wait_until="domcontentloaded")
                    await page.add_script_tag(content="")
                    vitals = await page.evaluate(vitals_script)
                except Exception:
                    vitals = {"lcp": None, "cls": None, "fid": None, "tbt": None}

                # Navigation timing
                timing = await page.evaluate("""
                () => {
                    const t = performance.timing;
                    const nav = performance.getEntriesByType('navigation')[0];
                    return {
                        ttfb: t.responseStart - t.requestStart,
                        dom_interactive: t.domInteractive - t.navigationStart,
                        dom_complete: t.domComplete - t.navigationStart,
                        load_event: t.loadEventEnd - t.navigationStart,
                        fcp: (() => {
                            const fcp = performance.getEntriesByName('first-contentful-paint')[0];
                            return fcp ? fcp.startTime : null;
                        })()
                    }
                }
                """)

                await context.close()
                await browser.close()
                return {**vitals, **timing}

        except Exception as e:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            return {"error": str(e)}

    def _run():
        loop = asyncio.new_event_loop()
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_async())
        finally:
            loop.close()

    try:
        raw = _run()
        if "error" in raw:
            return {"status": "error", "error": raw["error"], "metrics": {}}

        lcp = raw.get("lcp")
        cls = raw.get("cls", 0)
        fcp = raw.get("fcp")
        tbt = raw.get("tbt", 0)
        ttfb = raw.get("ttfb")

        issues = []
        score = 100

        # LCP thresholds: good <2500ms, needs improvement <4000ms, poor >=4000ms
        if lcp is not None:
            if lcp > 4000:
                issues.append({"severity": "error", "message": f"LCP {lcp:.0f}ms — Poor (>4s)"})
                score -= 25
            elif lcp > 2500:
                issues.append({"severity": "warning", "message": f"LCP {lcp:.0f}ms — Needs improvement (2.5–4s)"})
                score -= 10

        # CLS thresholds: good <0.1, needs improvement <0.25
        if cls is not None:
            if cls > 0.25:
                issues.append({"severity": "error", "message": f"CLS {cls:.3f} — Poor (>0.25)"})
                score -= 25
            elif cls > 0.1:
                issues.append({"severity": "warning", "message": f"CLS {cls:.3f} — Needs improvement (0.1–0.25)"})
                score -= 10

        # TBT thresholds
        if tbt > 600:
            issues.append({"severity": "error", "message": f"TBT {tbt:.0f}ms — Poor (>600ms)"})
            score -= 20
        elif tbt > 200:
            issues.append({"severity": "warning", "message": f"TBT {tbt:.0f}ms — Needs improvement"})
            score -= 10

        score = max(0, score)
        status = "pass" if score >= 80 else ("warning" if score >= 50 else "fail")

        return {
            "status": status, "score": score, "issues": issues,
            "metrics": {
                "lcp_ms": round(lcp, 1) if lcp else None,
                "cls": round(cls, 4) if cls is not None else None,
                "fcp_ms": round(fcp, 1) if fcp else None,
                "tbt_ms": round(tbt, 1) if tbt is not None else None,
                "ttfb_ms": round(ttfb, 1) if ttfb else None,
                "dom_interactive_ms": raw.get("dom_interactive"),
                "load_event_ms": raw.get("load_event"),
            }
        }
    except Exception as e:
        return {"status": "error", "score": 0, "issues": [], "error": str(e), "metrics": {}}


# ═══════════════════════════════════════════════════════════════════════════════
# 5. COOKIE / GDPR COMPLIANCE
# ═══════════════════════════════════════════════════════════════════════════════

def check_cookies_gdpr(url: str) -> dict:
    issues = []
    score = 100
    data: Dict[str, Any] = {}

    try:
        session = requests.Session()
        r = session.get(url, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        # Analyze cookies
        cookies = session.cookies
        cookie_list = []
        for c in cookies:
            info = {
                "name": c.name,
                "domain": c.domain,
                "secure": c.secure,
                "httponly": bool(c._rest.get("HttpOnly")) if hasattr(c, "_rest") else False,
                "samesite": c._rest.get("SameSite", "not set") if hasattr(c, "_rest") else "unknown",
            }
            cookie_list.append(info)

            if not c.secure:
                issues.append({"severity": "warning", "message": f"Cookie '{c.name}' missing Secure flag"})
                score -= 5
        data["cookies"] = cookie_list
        data["cookie_count"] = len(cookie_list)

        # Consent banner detection
        consent_keywords = [
            "cookie", "consent", "gdpr", "we use cookies", "accept cookies",
            "privacy", "cookie policy", "cookie notice", "cookie banner",
        ]
        page_text = soup.get_text().lower()
        has_consent = any(kw in page_text for kw in consent_keywords)
        consent_selectors = [
            "[id*='cookie']", "[class*='cookie']", "[id*='consent']",
            "[class*='consent']", "[id*='gdpr']", "[class*='gdpr']",
            "[id*='banner']", "[class*='banner']",
        ]
        has_consent_element = any(soup.find(attrs={"id": re.compile(kw, re.I)}) or
                                   soup.find(attrs={"class": re.compile(kw, re.I)})
                                   for kw in ["cookie", "consent", "gdpr"])
        data["consent_banner_detected"] = has_consent or has_consent_element

        if not has_consent and not has_consent_element and len(cookie_list) > 0:
            issues.append({"severity": "error", "message": "Cookies set but no consent banner detected (potential GDPR violation)"})
            score -= 20

        # Privacy policy link
        privacy_links = soup.find_all("a", href=True)
        has_privacy = any("privacy" in (a.get_text() + a.get("href", "")).lower() for a in privacy_links)
        data["has_privacy_policy"] = has_privacy
        if not has_privacy:
            issues.append({"severity": "warning", "message": "No privacy policy link found"})
            score -= 10

        # Third-party scripts
        scripts = soup.find_all("script", src=True)
        p = urlparse(url)
        third_party = [s.get("src") for s in scripts
                       if s.get("src") and urlparse(s.get("src")).netloc and urlparse(s.get("src")).netloc != p.netloc]
        data["third_party_scripts"] = len(third_party)
        data["third_party_script_samples"] = third_party[:5]
        if len(third_party) > 5:
            issues.append({"severity": "info", "message": f"{len(third_party)} third-party scripts detected (review for GDPR)"})

        score = max(0, score)
        status = "pass" if score >= 80 else ("warning" if score >= 50 else "fail")
        return {"status": status, "score": score, "issues": issues, "data": data}

    except Exception as e:
        return {"status": "error", "score": 0, "issues": [], "error": str(e), "data": {}}


# ═══════════════════════════════════════════════════════════════════════════════
# 6. HTML VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

def check_html_validation(url: str) -> dict:
    issues = []
    score = 100
    data: Dict[str, Any] = {}

    DEPRECATED_TAGS = ["font", "center", "strike", "tt", "big", "basefont", "applet", "acronym", "frame", "frameset"]
    DEPRECATED_ATTRS = ["align", "bgcolor", "border", "cellpadding", "cellspacing", "color", "face", "size", "valign", "width", "height"]

    try:
        r, soup = _soup(url)

        # DOCTYPE
        raw = r.text
        has_doctype = raw.strip().lower().startswith("<!doctype")
        data["has_doctype"] = has_doctype
        if not has_doctype:
            issues.append({"severity": "error", "message": "Missing DOCTYPE declaration"})
            score -= 10

        # Charset
        charset_meta = soup.find("meta", charset=True) or soup.find("meta", attrs={"http-equiv": "Content-Type"})
        data["has_charset"] = bool(charset_meta)
        if not charset_meta:
            issues.append({"severity": "warning", "message": "No charset declaration found"})
            score -= 5

        # Viewport meta
        viewport = soup.find("meta", attrs={"name": "viewport"})
        data["has_viewport"] = bool(viewport)
        if not viewport:
            issues.append({"severity": "error", "message": "Missing viewport meta tag (breaks mobile rendering)"})
            score -= 10

        # Deprecated tags
        found_deprecated = []
        for tag in DEPRECATED_TAGS:
            elements = soup.find_all(tag)
            if elements:
                found_deprecated.append({"tag": tag, "count": len(elements)})
                score -= min(5, len(elements))
        data["deprecated_tags"] = found_deprecated
        if found_deprecated:
            tag_names = ", ".join(f"<{d['tag']}>" for d in found_deprecated[:5])
            issues.append({"severity": "warning", "message": f"Deprecated HTML tags found: {tag_names}"})

        # Empty tags that shouldn't be empty
        empty_links = [a for a in soup.find_all("a") if not a.get_text(strip=True) and not a.find("img")]
        data["empty_links"] = len(empty_links)
        if empty_links:
            issues.append({"severity": "warning", "message": f"{len(empty_links)} empty <a> tag(s) found"})
            score -= min(10, len(empty_links) * 2)

        # Duplicate IDs
        all_ids = [el.get("id") for el in soup.find_all(id=True)]
        seen = set()
        dupes = set()
        for id_val in all_ids:
            if id_val in seen:
                dupes.add(id_val)
            seen.add(id_val)
        data["duplicate_ids"] = list(dupes)[:10]
        if dupes:
            issues.append({"severity": "error", "message": f"{len(dupes)} duplicate ID(s) found (invalid HTML)"})
            score -= min(15, len(dupes) * 3)

        # Inline styles (code quality indicator)
        inline_styles = len(soup.find_all(style=True))
        data["inline_style_count"] = inline_styles
        if inline_styles > 20:
            issues.append({"severity": "info", "message": f"{inline_styles} elements use inline styles (prefer CSS classes)"})

        score = max(0, score)
        status = "pass" if score >= 80 else ("warning" if score >= 50 else "fail")
        return {"status": status, "score": score, "issues": issues, "data": data}

    except Exception as e:
        return {"status": "error", "score": 0, "issues": [], "error": str(e), "data": {}}


# ═══════════════════════════════════════════════════════════════════════════════
# 7. CONTENT QUALITY
# ═══════════════════════════════════════════════════════════════════════════════

def check_content_quality(url: str) -> dict:
    issues = []
    score = 100
    data: Dict[str, Any] = {}

    try:
        r, soup = _soup(url)

        # Remove script/style for text analysis
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)
        words = text.split()
        word_count = len(words)
        data["word_count"] = word_count

        if word_count < 100:
            issues.append({"severity": "error", "message": f"Very thin content — only {word_count} words detected"})
            score -= 25
        elif word_count < 300:
            issues.append({"severity": "warning", "message": f"Thin content — {word_count} words (recommend 300+)"})
            score -= 10

        # Duplicate title / description
        title = soup.find("title")
        meta_desc = soup.find("meta", attrs={"name": "description"})
        title_text = title.get_text(strip=True) if title else ""
        desc_text = meta_desc.get("content", "") if meta_desc else ""

        if title_text and desc_text and title_text.lower() == desc_text.lower():
            issues.append({"severity": "warning", "message": "Title and meta description are identical"})
            score -= 10

        # Reading level proxy (avg sentence length)
        sentences = re.split(r'[.!?]+', text)
        sentences = [s.strip() for s in sentences if len(s.strip().split()) > 3]
        avg_sentence_len = sum(len(s.split()) for s in sentences) / max(len(sentences), 1)
        data["avg_sentence_length"] = round(avg_sentence_len, 1)
        data["sentence_count"] = len(sentences)

        if avg_sentence_len > 30:
            issues.append({"severity": "info", "message": f"Average sentence length is {avg_sentence_len:.0f} words — consider shorter sentences"})

        # Content-to-HTML ratio
        html_len = len(r.text)
        text_len = len(text)
        ratio = round((text_len / html_len) * 100, 1) if html_len > 0 else 0
        data["content_ratio_pct"] = ratio
        if ratio < 10:
            issues.append({"severity": "warning", "message": f"Low content-to-HTML ratio ({ratio}%) — page may be bloated"})
            score -= 10

        # Keyword stuffing detection (top word frequency)
        word_freq: Dict[str, int] = {}
        stop_words = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by", "is", "are", "was", "were", "be", "been", "have", "has", "had"}
        for w in words:
            w = w.lower().strip(".,!?;:")
            if len(w) > 4 and w not in stop_words:
                word_freq[w] = word_freq.get(w, 0) + 1

        top_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:10]
        data["top_keywords"] = [{"word": w, "count": c} for w, c in top_words]

        if top_words and word_count > 0:
            top_density = (top_words[0][1] / word_count) * 100
            if top_density > 5:
                issues.append({"severity": "warning", "message": f"Possible keyword stuffing — '{top_words[0][0]}' appears {top_words[0][1]}x ({top_density:.1f}% density)"})
                score -= 10

        score = max(0, score)
        status = "pass" if score >= 80 else ("warning" if score >= 50 else "fail")
        return {"status": status, "score": score, "issues": issues, "data": data}

    except Exception as e:
        return {"status": "error", "score": 0, "issues": [], "error": str(e), "data": {}}


# ═══════════════════════════════════════════════════════════════════════════════
# 8. PWA READINESS
# ═══════════════════════════════════════════════════════════════════════════════

def check_pwa(url: str) -> dict:
    issues = []
    score = 0  # PWA is additive scoring
    data: Dict[str, Any] = {}

    try:
        r, soup = _soup(url)
        p = urlparse(url)

        # HTTPS
        is_https = p.scheme == "https"
        data["https"] = is_https
        if is_https:
            score += 20
        else:
            issues.append({"severity": "error", "message": "PWA requires HTTPS"})

        # Manifest
        manifest_link = soup.find("link", attrs={"rel": "manifest"})
        manifest_href = manifest_link.get("href") if manifest_link else None
        data["has_manifest"] = bool(manifest_href)
        if manifest_href:
            score += 25
            manifest_url = urljoin(url, manifest_href)
            try:
                mr = requests.get(manifest_url, timeout=8)
                manifest_data = mr.json()
                data["manifest"] = {
                    "name": manifest_data.get("name"),
                    "short_name": manifest_data.get("short_name"),
                    "start_url": manifest_data.get("start_url"),
                    "display": manifest_data.get("display"),
                    "theme_color": manifest_data.get("theme_color"),
                    "background_color": manifest_data.get("background_color"),
                    "icons": len(manifest_data.get("icons", [])),
                }
                if not manifest_data.get("name"):
                    issues.append({"severity": "warning", "message": "Manifest missing 'name' field"})
                if not manifest_data.get("icons"):
                    issues.append({"severity": "warning", "message": "Manifest missing icons"})
                else:
                    score += 10
                if manifest_data.get("display") in ["standalone", "fullscreen"]:
                    score += 5
            except Exception:
                issues.append({"severity": "warning", "message": "Manifest found but could not be parsed"})
        else:
            issues.append({"severity": "error", "message": "No web app manifest found"})

        # Service Worker
        sw_url = f"{p.scheme}://{p.netloc}/service-worker.js"
        sw_url2 = f"{p.scheme}://{p.netloc}/sw.js"
        has_sw = False
        for sw in [sw_url, sw_url2]:
            try:
                sr = requests.head(sw, timeout=6)
                if sr.status_code < 400:
                    has_sw = True
                    data["service_worker_url"] = sw
                    score += 25
                    break
            except Exception:
                pass

        # Check page source for service worker registration
        if not has_sw:
            page_text = r.text
            if "serviceWorker" in page_text or "service-worker" in page_text.lower():
                has_sw = True
                score += 15
                data["service_worker_url"] = "registered in page"

        data["has_service_worker"] = has_sw
        if not has_sw:
            issues.append({"severity": "error", "message": "No service worker detected (required for offline support)"})

        # Apple touch icon
        apple_icon = soup.find("link", attrs={"rel": "apple-touch-icon"})
        data["has_apple_touch_icon"] = bool(apple_icon)
        if apple_icon:
            score += 5

        # Theme color meta
        theme_color = soup.find("meta", attrs={"name": "theme-color"})
        data["has_theme_color"] = bool(theme_color)
        if theme_color:
            score += 5

        # Viewport (required for PWA)
        viewport = soup.find("meta", attrs={"name": "viewport"})
        data["has_viewport"] = bool(viewport)
        if viewport:
            score += 5
        else:
            issues.append({"severity": "error", "message": "Missing viewport meta (required for PWA)"})

        score = min(100, score)
        status = "pass" if score >= 70 else ("warning" if score >= 40 else "fail")
        return {"status": status, "score": score, "issues": issues, "data": data}

    except Exception as e:
        return {"status": "error", "score": 0, "issues": [], "error": str(e), "data": {}}


# ═══════════════════════════════════════════════════════════════════════════════
# 9. FUNCTIONALITY AUDIT
# ═══════════════════════════════════════════════════════════════════════════════

def check_functionality(url: str) -> dict:
    """
    Static analysis of site functionality:
    - Forms detection and field analysis
    - CTA buttons detection
    - Search functionality
    - Navigation structure
    - Contact information
    - Social links
    """
    issues = []
    score = 100
    data: Dict[str, Any] = {}

    try:
        r, soup = _soup(url)

        # Forms
        forms = soup.find_all("form")
        form_data = []
        for i, form in enumerate(forms[:10]):
            inputs = form.find_all("input", type=lambda t: t not in ["hidden"])
            textarea = form.find_all("textarea")
            submit = form.find("button", type="submit") or form.find("input", type="submit") or form.find("button")
            form_data.append({
                "action": form.get("action", ""),
                "method": form.get("method", "get").upper(),
                "input_count": len(inputs) + len(textarea),
                "has_submit": bool(submit),
            })
            if not submit:
                issues.append({"severity": "warning", "message": f"Form #{i+1} has no visible submit button"})
                score -= 5
        data["forms"] = form_data
        data["form_count"] = len(forms)

        # CTA detection
        cta_keywords = ["get started", "sign up", "register", "buy now", "shop now",
                        "try free", "contact us", "learn more", "book now", "subscribe",
                        "download", "start free", "request demo", "get demo"]
        cta_buttons = []
        for a in soup.find_all(["a", "button"]):
            text = a.get_text(strip=True).lower()
            if any(kw in text for kw in cta_keywords):
                cta_buttons.append({"text": a.get_text(strip=True)[:60], "href": a.get("href", "")})
        data["cta_buttons"] = cta_buttons[:10]
        data["cta_count"] = len(cta_buttons)
        if not cta_buttons:
            issues.append({"severity": "info", "message": "No clear CTA (call-to-action) buttons detected"})

        # Search
        search_inputs = soup.find_all("input", type="search")
        search_forms = [f for f in forms if "search" in (f.get("action", "") + f.get("role", "") + f.get("class", [""])[0] if f.get("class") else "").lower()]
        search_by_name = soup.find_all("input", attrs={"name": re.compile("search|query|q", re.I)})
        has_search = bool(search_inputs or search_forms or search_by_name)
        data["has_search"] = has_search

        # Navigation structure
        navs = soup.find_all("nav")
        nav_links = []
        for nav in navs[:2]:
            for a in nav.find_all("a", href=True):
                text = a.get_text(strip=True)
                if text and len(text) < 50:
                    nav_links.append({"text": text, "href": a.get("href")})
        data["navigation"] = nav_links[:20]
        data["nav_link_count"] = len(nav_links)
        if not nav_links:
            issues.append({"severity": "warning", "message": "No navigation structure detected"})
            score -= 10

        # Contact info
        page_text = soup.get_text()
        email_pattern = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
        phone_pattern = re.compile(r'[\+]?[(]?[0-9]{3}[)]?[-\s\.]?[0-9]{3}[-\s\.]?[0-9]{4,6}')
        emails = list(set(email_pattern.findall(page_text)))[:3]
        phones = list(set(phone_pattern.findall(page_text)))[:3]
        data["contact_emails"] = emails
        data["contact_phones"] = phones
        data["has_contact_info"] = bool(emails or phones)

        # Social links
        social_domains = ["twitter.com", "x.com", "facebook.com", "instagram.com",
                          "linkedin.com", "youtube.com", "github.com", "tiktok.com"]
        social_links = []
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            for domain in social_domains:
                if domain in href:
                    social_links.append({"platform": domain.split(".")[0], "url": href})
                    break
        data["social_links"] = social_links[:10]

        # 404 page check
        try:
            p = urlparse(url)
            test_404 = f"{p.scheme}://{p.netloc}/this-page-definitely-does-not-exist-xyz123"
            r404 = requests.get(test_404, timeout=8)
            data["has_custom_404"] = r404.status_code == 404 and len(r404.text) > 500
            if not data["has_custom_404"] and r404.status_code != 404:
                issues.append({"severity": "warning", "message": f"Non-existent pages return {r404.status_code} instead of 404"})
                score -= 5
        except Exception:
            data["has_custom_404"] = None

        score = max(0, score)
        status = "pass" if score >= 80 else ("warning" if score >= 50 else "fail")
        return {"status": status, "score": score, "issues": issues, "data": data}

    except Exception as e:
        return {"status": "error", "score": 0, "issues": [], "error": str(e), "data": {}}

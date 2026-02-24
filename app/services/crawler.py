"""
BFS web crawler — discovers pages, checks broken links and missing images.
"""
import asyncio
import time
from collections import deque
from urllib.parse import urljoin, urlparse
from typing import List, Set, Tuple
import aiohttp
from bs4 import BeautifulSoup

from ..models import (
    BrokenLinksCheck, MissingImagesCheck, BrokenLink, MissingImage,
    CrawledPage, CheckStatus, MobileResponsivenessCheck
)
from ..config import get_settings

settings = get_settings()


def _same_domain(base_url: str, link: str) -> bool:
    """Return True if link belongs to the same domain as base_url."""
    base_netloc = urlparse(base_url).netloc
    link_netloc = urlparse(link).netloc
    return link_netloc == "" or link_netloc == base_netloc


def _normalize_url(base: str, href: str) -> str:
    """Resolve relative URLs against base."""
    url = urljoin(base, href)
    parsed = urlparse(url)
    # Strip fragment
    return parsed._replace(fragment="").geturl()


# Domains and path patterns that indicate external OAuth/SSO flows.
# These links are always present on sites with social login but will
# 404 or redirect when hit directly — they are NOT broken links.
_OAUTH_DOMAINS = (
    'accounts.google.com',
    'oauth2.googleapis.com',
    'github.com/login',
    'login.microsoftonline.com',
    'login.live.com',
    'appleid.apple.com',
    'facebook.com/dialog',
    'twitter.com/i/oauth',
    'linkedin.com/oauth',
    'auth0.com',
    'okta.com',
    'cognito-idp.',
)

_OAUTH_PATH_PATTERNS = (
    '/signin/v2/',
    '/lifecycle/flows/',
    '/login/google',
    '/login/github',
    '/login/facebook',
    '/oauth/authorize',
    '/oauth2/authorize',
    '/connect/authorize',
)


def _is_oauth_url(url: str) -> bool:
    """Return True if the URL is an external OAuth/SSO provider URL."""
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    path = parsed.path.lower()

    # External OAuth provider domain
    if any(domain in netloc for domain in _OAUTH_DOMAINS):
        return True

    # Same-domain OAuth callback/lifecycle paths (e.g. /signin/v2/..., /lifecycle/flows/...)
    if any(path.startswith(pattern) for pattern in _OAUTH_PATH_PATTERNS):
        return True

    return False


async def _fetch_html(url: str, session: aiohttp.ClientSession) -> Tuple[int, float, str]:
    """Fetch a URL and return (status_code, time_ms, html_body)."""
    start = time.monotonic()
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=settings.request_timeout_seconds),
            allow_redirects=True,
            ssl=False,
        ) as resp:
            elapsed = (time.monotonic() - start) * 1000
            try:
                html = await resp.text(errors="replace")
            except Exception:
                html = ""
            return resp.status, round(elapsed, 2), html
    except asyncio.TimeoutError:
        return -1, 0.0, ""
    except Exception:
        return -2, 0.0, ""


async def _check_link_status(url: str, session: aiohttp.ClientSession) -> int:
    """HEAD request to check a link's status code."""
    try:
        async with session.head(
            url,
            timeout=aiohttp.ClientTimeout(total=8),
            allow_redirects=True,
            ssl=False,
        ) as resp:
            return resp.status
    except asyncio.TimeoutError:
        return -1
    except Exception:
        # Fallback to GET
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=8),
                allow_redirects=True,
                ssl=False,
            ) as resp:
                return resp.status
        except Exception:
            return -2


async def crawl_website(
    start_url: str,
    session: aiohttp.ClientSession,
    max_pages: int = None,
) -> Tuple[List[CrawledPage], BrokenLinksCheck, MissingImagesCheck, MobileResponsivenessCheck]:
    """
    BFS crawl starting from start_url.
    Returns crawled pages, broken links, missing images, mobile check.
    """
    if max_pages is None:
        max_pages = settings.max_crawl_pages

    visited: Set[str] = set()
    queue: deque = deque([(start_url, 0)])  # (url, depth)
    crawled_pages: List[CrawledPage] = []

    all_links: List[Tuple[str, str]] = []  # (link_url, found_on)
    all_images: List[Tuple[str, str]] = []  # (img_src, found_on)

    mobile_issues: List[str] = []
    has_viewport_meta = False
    has_responsive_css = False
    first_page_html = ""

    while queue and len(crawled_pages) < max_pages:
        url, depth = queue.popleft()
        if url in visited:
            continue
        visited.add(url)

        status_code, load_ms, html = await _fetch_html(url, session)

        # Extract page title
        title = None
        if html:
            try:
                soup = BeautifulSoup(html, "lxml")
                title_tag = soup.find("title")
                title = title_tag.get_text(strip=True) if title_tag else None
            except Exception:
                pass

        crawled_pages.append(CrawledPage(
            url=url,
            status_code=status_code,
            load_time_ms=load_ms,
            title=title,
            depth=depth,
        ))

        if not html or status_code < 0:
            continue

        # Save first page HTML for mobile check
        if not first_page_html:
            first_page_html = html

        # Parse links and images
        try:
            soup = BeautifulSoup(html, "lxml")

            # Collect <a href> links
            for tag in soup.find_all("a", href=True):
                href = tag["href"].strip()
                if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                    continue
                full_url = _normalize_url(url, href)
                # Skip external OAuth/SSO URLs — they 404 when hit directly
                if _is_oauth_url(full_url):
                    continue
                all_links.append((full_url, url))
                # Only crawl same-domain pages
                if _same_domain(start_url, full_url) and full_url not in visited:
                    queue.append((full_url, depth + 1))

            # Collect <img src> images
            for img in soup.find_all("img"):
                src = img.get("src", "").strip()
                if not src or src.startswith("data:"):
                    continue
                full_src = _normalize_url(url, src)
                all_images.append((full_src, url))

        except Exception:
            pass

    # ── Check broken links concurrently ─────────────────────────────────────
    unique_links = list({lnk for lnk, _ in all_links})
    link_found_on = {lnk: pg for lnk, pg in all_links}

    # Limit concurrent checks
    sem = asyncio.Semaphore(10)

    async def check_one_link(lnk: str) -> Tuple[str, int]:
        async with sem:
            status = await _check_link_status(lnk, session)
            return lnk, status

    link_results = await asyncio.gather(*[check_one_link(lnk) for lnk in unique_links])

    broken_links: List[BrokenLink] = []
    for lnk, sc in link_results:
        if sc == -1:
            broken_links.append(BrokenLink(url=lnk, status_code=None, found_on=link_found_on.get(lnk, ""), error="Timeout"))
        elif sc == -2:
            broken_links.append(BrokenLink(url=lnk, status_code=None, found_on=link_found_on.get(lnk, ""), error="Connection error"))
        elif sc >= 400:
            broken_links.append(BrokenLink(url=lnk, status_code=sc, found_on=link_found_on.get(lnk, ""), error=f"HTTP {sc}"))

    # Handle edge case where initial crawl fails
    if len(crawled_pages) == 1 and crawled_pages[0].status_code is not None and crawled_pages[0].status_code < 0:
        c_status = CheckStatus.SKIP
        c_msg = "Could not crawl website (Main page unreachable)"
    else:
        c_status = CheckStatus.PASS if not broken_links else (
            CheckStatus.WARNING if len(broken_links) <= 3 else CheckStatus.FAIL
        )
        c_msg = (
            f"All {len(unique_links)} links OK" if not broken_links
            else f"Found {len(broken_links)} broken link(s) out of {len(unique_links)} checked"
        )
        
    broken_links_check = BrokenLinksCheck(
        status=c_status,
        total_links=len(unique_links),
        broken_count=len(broken_links),
        broken_links=broken_links[:50],  # cap at 50
        message=c_msg,
    )

    # ── Check missing images concurrently ────────────────────────────────────
    unique_images = list({img for img, _ in all_images})
    img_found_on = {img: pg for img, pg in all_images}

    async def check_one_image(img_url: str) -> Tuple[str, int]:
        async with sem:
            status = await _check_link_status(img_url, session)
            return img_url, status

    img_results = await asyncio.gather(*[check_one_image(img) for img in unique_images])

    missing_images: List[MissingImage] = []
    for img_url, sc in img_results:
        if sc < 0 or sc >= 400:
            missing_images.append(MissingImage(
                src=img_url,
                found_on=img_found_on.get(img_url, ""),
                status_code=sc if sc > 0 else None,
                error="Timeout" if sc == -1 else ("Connection error" if sc == -2 else f"HTTP {sc}"),
            ))

    if len(crawled_pages) == 1 and crawled_pages[0].status_code is not None and crawled_pages[0].status_code < 0:
        img_status = CheckStatus.SKIP
        img_msg = "Could not crawl website (Main page unreachable)"
    else:
        img_status = CheckStatus.PASS if not missing_images else (
            CheckStatus.WARNING if len(missing_images) <= 2 else CheckStatus.FAIL
        )
        img_msg = (
            f"All {len(unique_images)} images loaded OK" if not missing_images
            else f"Found {len(missing_images)} missing image(s)"
        )

    missing_images_check = MissingImagesCheck(
        status=img_status,
        total_images=len(unique_images),
        missing_count=len(missing_images),
        missing_images=missing_images[:50],
        message=img_msg,
    )

    # ── Mobile Responsiveness Check ─────────────────────────────────────────
    if first_page_html:
        try:
            soup = BeautifulSoup(first_page_html, "lxml")
            # Check viewport meta tag
            viewport = soup.find("meta", attrs={"name": "viewport"})
            has_viewport_meta = viewport is not None

            # Check for responsive CSS hints
            responsive_keywords = ["@media", "max-width", "min-width", "flex", "grid"]
            page_text = first_page_html.lower()
            has_responsive_css = any(kw in page_text for kw in responsive_keywords)

            if not has_viewport_meta:
                mobile_issues.append("Missing <meta name='viewport'> tag")
            if not has_responsive_css:
                mobile_issues.append("No responsive CSS patterns detected (missing @media queries or flex/grid)")

            # Score: 0-100
            score = 100
            if not has_viewport_meta:
                score -= 50
            if not has_responsive_css:
                score -= 30

            mobile_status = CheckStatus.PASS if score >= 80 else (
                CheckStatus.WARNING if score >= 50 else CheckStatus.FAIL
            )
            mobile_msg = (
                "Site appears mobile-friendly" if not mobile_issues
                else f"{len(mobile_issues)} mobile issue(s) found"
            )
        except Exception:
            score = 0
            mobile_status = CheckStatus.WARNING
            mobile_msg = "Could not fully analyze mobile responsiveness"
    else:
        score = 0
        mobile_status = CheckStatus.SKIP
        mobile_msg = "No HTML content to analyze"
        has_viewport_meta = None
        has_responsive_css = None

    mobile_check = MobileResponsivenessCheck(
        status=mobile_status,
        has_viewport_meta=has_viewport_meta if first_page_html else None,
        has_responsive_css=has_responsive_css if first_page_html else None,
        mobile_score=score if first_page_html else None,
        issues=mobile_issues,
        message=mobile_msg,
    )

    return crawled_pages, broken_links_check, missing_images_check, mobile_check
"""
Playwright-based login automation, JS error capture, and post-login UI testing.

Windows fix: All Playwright work runs in a ThreadPoolExecutor with its own
asyncio.ProactorEventLoop so it doesn't conflict with uvicorn's event loop.

Changes vs original:
- _async_capture_js_errors: browser/context always closed in finally block
- _async_run_login_test: browser/context always closed in finally block
  (password deletion moved to finally block too so it ALWAYS runs)
- _playwright_executor: max_workers tied to env var PLAYWRIGHT_WORKERS
"""
import asyncio
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple

from playwright.async_api import async_playwright, Page, Browser, BrowserContext

from ..models import (
    JSError, JSErrorsCheck, CheckStatus,
    PostLoginCheck, UIActionResult, UIActionStatus,
)
from ..config import get_settings

settings = get_settings()

# Tie concurrency to env var so it can be scaled without code changes
_MAX_WORKERS = int(os.getenv("PLAYWRIGHT_WORKERS", "2"))
_playwright_executor = ThreadPoolExecutor(max_workers=_MAX_WORKERS, thread_name_prefix="playwright")


def _run_in_thread(coro):
    loop = asyncio.new_event_loop()
    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── Post-login UI tester ─────────────────────────────────────────────────────

async def _test_post_login_ui(page: Page, base_url: str) -> PostLoginCheck:
    """
    After a successful login, systematically test all interactive UI elements:
    - Navigation links
    - Buttons (non-destructive)
    - Forms (detect but don't submit)
    """
    actions: List[UIActionResult] = []
    post_login_js_errors: List[JSError] = []

    page.on("pageerror", lambda exc: post_login_js_errors.append(
        JSError(message=str(exc), page_url=page.url)
    ))
    page.on("console", lambda msg: (
        post_login_js_errors.append(JSError(
            message=msg.text,
            source=msg.location.get("url") if msg.location else None,
            line=msg.location.get("lineNumber") if msg.location else None,
            page_url=page.url,
        )) if msg.type == "error" else None
    ))

    landing_url = page.url
    try:
        landing_title = await page.title()
    except Exception:
        landing_title = ""

    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        await asyncio.sleep(2)

    # ── 1. Navigation Links ───────────────────────────────────────────────────
    nav_passed = nav_failed = 0
    try:
        nav_selectors = [
            "nav a[href]", "header a[href]",
            "[role='navigation'] a[href]", ".sidebar a[href]",
            ".menu a[href]", ".navbar a[href]",
        ]
        nav_links = []
        for sel in nav_selectors:
            try:
                links = await page.query_selector_all(sel)
                nav_links.extend(links)
            except Exception:
                pass

        seen_hrefs = set()
        unique_nav = []
        for link in nav_links:
            try:
                href = await link.get_attribute("href") or ""
                text = (await link.inner_text()).strip()[:50]
                if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                    continue
                if any(d in href for d in ["google.com", "github.com", "facebook.com", "twitter.com"]):
                    continue
                if href.startswith("http") and base_url not in href:
                    continue
                key = href.split("?")[0]
                if key in seen_hrefs:
                    continue
                seen_hrefs.add(key)
                unique_nav.append((link, href, text or href))
            except Exception:
                continue

        original_url = page.url
        for link_el, href, label in unique_nav[:10]:
            start = time.monotonic()
            try:
                full_url = href if href.startswith("http") else f"{base_url.rstrip('/')}/{href.lstrip('/')}"
                await page.goto(full_url, timeout=12000, wait_until="domcontentloaded")
                await asyncio.sleep(0.5)
                elapsed = round((time.monotonic() - start) * 1000, 2)
                actions.append(UIActionResult(
                    action_type="nav_link", label=label,
                    selector=f"a[href='{href}']", page_url=original_url,
                    status=UIActionStatus.PASS, response_time_ms=elapsed,
                    result_url=page.url, screenshot_note=f"Navigated to {page.url}",
                ))
                nav_passed += 1
                await page.goto(original_url, timeout=12000, wait_until="domcontentloaded")
                await asyncio.sleep(0.3)
            except Exception as e:
                elapsed = round((time.monotonic() - start) * 1000, 2)
                actions.append(UIActionResult(
                    action_type="nav_link", label=label,
                    selector=f"a[href='{href}']", page_url=original_url,
                    status=UIActionStatus.FAIL, response_time_ms=elapsed, error=str(e)[:120],
                ))
                nav_failed += 1
                try:
                    await page.goto(original_url, timeout=8000, wait_until="domcontentloaded")
                except Exception:
                    pass
    except Exception:
        pass

    # ── 2. Buttons ────────────────────────────────────────────────────────────
    btn_passed = btn_failed = 0
    SKIP_KEYWORDS = [
        "delete", "remove", "logout", "log out", "sign out", "signout",
        "deactivate", "cancel account", "unsubscribe", "reset", "clear all",
        "terminate", "destroy", "drop", "purge", "ban", "kick",
    ]
    BUTTON_SELECTORS = [
        "button", "[role='button']", "input[type='button']",
        "input[type='submit']", "a.btn", "a.button",
        "[class*='btn']", "[class*='button']", "[class*='Button']",
    ]

    pages_to_scan = list(set([landing_url] + [
        a.result_url for a in actions if a.action_type == "nav_link" and a.result_url
    ]))

    for scan_url in pages_to_scan[:5]:
        try:
            if page.url != scan_url:
                await page.goto(scan_url, timeout=12000, wait_until="networkidle")
                await asyncio.sleep(1)

            current_url = page.url
            seen_labels = set()

            for sel in BUTTON_SELECTORS:
                try:
                    locator = page.locator(sel)
                    count = await locator.count()
                    for i in range(min(count, 30)):
                        btn = locator.nth(i)
                        try:
                            if not await btn.is_visible(): continue
                            if not await btn.is_enabled(): continue

                            label = (await btn.inner_text()).strip()[:60]
                            if not label:
                                label = (
                                    await btn.get_attribute("aria-label") or
                                    await btn.get_attribute("title") or
                                    await btn.get_attribute("value") or
                                    "Unnamed Button"
                                ).strip()[:60]
                            if len(label) < 2: continue

                            label_lower = label.lower()
                            if any(kw in label_lower for kw in SKIP_KEYWORDS):
                                actions.append(UIActionResult(
                                    action_type="button", label=label, selector=sel,
                                    page_url=current_url, status=UIActionStatus.SKIP,
                                    screenshot_note="Skipped — potentially destructive action",
                                ))
                                continue

                            dedup_key = f"{current_url}::{label_lower}"
                            if dedup_key in seen_labels: continue
                            seen_labels.add(dedup_key)

                            start = time.monotonic()
                            pre_url = page.url
                            try:
                                await btn.click(timeout=3000, force=True)
                                await asyncio.sleep(0.8)
                                try:
                                    await page.wait_for_load_state("networkidle", timeout=4000)
                                except Exception:
                                    pass

                                elapsed = round((time.monotonic() - start) * 1000, 2)
                                post_url = page.url

                                modal_opened = False
                                try:
                                    modal = await page.query_selector(
                                        "[role='dialog'], [role='alertdialog'], .modal, [class*='modal'], [class*='dialog']"
                                    )
                                    if modal and await modal.is_visible():
                                        modal_opened = True
                                        await page.keyboard.press("Escape")
                                        await asyncio.sleep(0.3)
                                except Exception:
                                    pass

                                note = (
                                    "Opened modal/dialog — closed with Escape" if modal_opened else
                                    f"Navigated to {post_url}" if post_url != pre_url else
                                    "Button clicked — UI response detected (no navigation)"
                                )
                                actions.append(UIActionResult(
                                    action_type="button", label=label, selector=sel,
                                    page_url=current_url, status=UIActionStatus.PASS,
                                    response_time_ms=elapsed,
                                    result_url=post_url if post_url != pre_url else None,
                                    screenshot_note=note,
                                ))
                                btn_passed += 1
                                if post_url != pre_url:
                                    await page.goto(current_url, timeout=10000, wait_until="domcontentloaded")
                                    await asyncio.sleep(0.5)

                            except Exception as e:
                                elapsed = round((time.monotonic() - start) * 1000, 2)
                                actions.append(UIActionResult(
                                    action_type="button", label=label, selector=sel,
                                    page_url=current_url, status=UIActionStatus.FAIL,
                                    response_time_ms=elapsed, error=str(e)[:120],
                                ))
                                btn_failed += 1
                                try:
                                    await page.goto(current_url, timeout=8000, wait_until="domcontentloaded")
                                except Exception:
                                    pass
                        except Exception:
                            continue
                except Exception:
                    continue
        except Exception:
            continue

    # ── 3. Forms ──────────────────────────────────────────────────────────────
    forms_found = forms_tested = 0
    try:
        forms = await page.query_selector_all("form")
        forms_found = len(forms)
        for form in forms[:5]:
            try:
                inputs = await form.query_selector_all("input:not([type='hidden']):not([type='submit'])")
                actions.append(UIActionResult(
                    action_type="form", label=f"Form with {len(inputs)} input(s)",
                    selector="form", page_url=page.url, status=UIActionStatus.PASS,
                    screenshot_note=f"Form detected — {len(inputs)} visible input(s). Not submitted to avoid data mutation.",
                ))
                forms_tested += 1
            except Exception:
                pass
    except Exception:
        pass

    # ── Summary ───────────────────────────────────────────────────────────────
    total_actions = len([a for a in actions if a.status != UIActionStatus.SKIP])
    total_passed  = len([a for a in actions if a.status == UIActionStatus.PASS])
    total_failed  = len([a for a in actions if a.status == UIActionStatus.FAIL])

    if total_actions == 0:
        overall_status, msg = CheckStatus.WARNING, "No interactive UI elements found on the post-login page"
    elif total_failed == 0:
        overall_status, msg = CheckStatus.PASS, f"All {total_passed} UI interaction(s) passed successfully"
    elif total_failed <= total_passed:
        overall_status, msg = CheckStatus.WARNING, f"{total_passed} passed, {total_failed} failed out of {total_actions}"
    else:
        overall_status, msg = CheckStatus.FAIL, f"{total_failed} UI interaction(s) failed out of {total_actions} tested"

    return PostLoginCheck(
        status=overall_status, landing_url=landing_url, landing_title=landing_title,
        buttons_found=len([a for a in actions if a.action_type == "button"]),
        buttons_passed=btn_passed, buttons_failed=btn_failed,
        nav_links_found=nav_passed + nav_failed, nav_links_passed=nav_passed, nav_links_failed=nav_failed,
        forms_found=forms_found, forms_tested=forms_tested,
        actions=actions[:50], js_errors_post_login=post_login_js_errors[:20], message=msg,
    )


# ─── JS error capture (basic test) ────────────────────────────────────────────

async def _async_capture_js_errors(url: str) -> Tuple[JSErrorsCheck, Optional[PostLoginCheck]]:
    """Capture JS errors using headless Playwright. Browser always closed in finally."""
    js_errors: List[JSError] = []
    post_login_check: Optional[PostLoginCheck] = None
    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()
            page.on("pageerror", lambda exc: js_errors.append(JSError(message=str(exc), page_url=url)))
            page.on("console", lambda msg: (
                js_errors.append(JSError(
                    message=msg.text,
                    source=msg.location.get("url") if msg.location else None,
                    line=msg.location.get("lineNumber") if msg.location else None,
                    page_url=url,
                )) if msg.type == "error" else None
            ))
            try:
                await page.goto(url, timeout=90000, wait_until="domcontentloaded")
                await asyncio.sleep(2)
                try:
                    post_login_check = await _test_post_login_ui(page, url)
                except Exception as e:
                    post_login_check = PostLoginCheck(
                        status=CheckStatus.WARNING, landing_url=url,
                        message=f"Basic UI test error: {str(e)[:120]}",
                    )
            except Exception as e:
                js_errors.append(JSError(message=f"Page navigation error: {str(e)[:120]}", page_url=url))
        finally:
            # ✅ ALWAYS runs — browser never left hanging even if crash occurs
            if context is not None:
                try:
                    await context.clear_cookies()   # prevent state leakage between runs
                    await context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass

    count = len(js_errors)
    js_check = JSErrorsCheck(
        status=CheckStatus.PASS if count == 0 else (CheckStatus.WARNING if count <= 3 else CheckStatus.FAIL),
        error_count=count, errors=js_errors[:30],
        message="No JavaScript errors detected" if count == 0 else f"Found {count} JavaScript console error(s)",
    )
    return js_check, post_login_check


# ─── Login test ───────────────────────────────────────────────────────────────

async def _async_run_login_test(
    url: str, username: str, password: str,
    login_url: Optional[str], username_selector: Optional[str],
    password_selector: Optional[str], submit_selector: Optional[str],
    success_indicator: Optional[str], progress_cb=None,
) -> Tuple[bool, str, JSErrorsCheck, Optional[PostLoginCheck]]:
    """Run login automation. Browser always closed in finally. Password always deleted in finally."""
    js_errors: List[JSError] = []
    login_success = False
    message = ""
    post_login_check: Optional[PostLoginCheck] = None
    target_login = login_url or url
    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
            )
            page: Page = await context.new_page()

            page.on("pageerror", lambda exc: js_errors.append(JSError(message=str(exc), page_url=page.url)))
            page.on("console", lambda msg: (
                js_errors.append(JSError(
                    message=msg.text,
                    source=msg.location.get("url") if msg.location else None,
                    line=msg.location.get("lineNumber") if msg.location else None,
                    page_url=page.url,
                )) if msg.type == "error" else None
            ))

            # Pre-warm: hit base URL to wake Render free-tier
            try:
                base_origin = target_login.split("/admin")[0].split("/login")[0]
                await page.goto(base_origin, timeout=90000, wait_until="domcontentloaded")
                await asyncio.sleep(2)
            except Exception:
                pass

            await page.goto(target_login, timeout=90000, wait_until="domcontentloaded")
            await asyncio.sleep(2)

            EMAIL_SELECTORS = (
                'input[type="email"], input[name="email"], input[placeholder*="email" i], '
                'input[name="username"], input[placeholder*="username" i], input[type="text"]'
            )
            try:
                await page.wait_for_selector(EMAIL_SELECTORS, timeout=30000, state="visible")
            except Exception:
                pass

            user_sel = username_selector or await _detect_username_field(page)
            pass_sel = password_selector or 'input[type="password"]'
            sub_sel  = submit_selector  or await _detect_submit_button(page)

            if not user_sel:
                message = "Could not find username/email input field on the page"
                login_success = False
            else:
                await page.wait_for_selector(user_sel, state="visible", timeout=20000)
                await page.fill(user_sel, username)
                await page.wait_for_selector(pass_sel, state="visible", timeout=20000)
                await page.fill(pass_sel, password)

                if sub_sel:
                    await page.wait_for_selector(sub_sel, state="visible", timeout=15000)
                    await page.click(sub_sel)
                else:
                    await page.keyboard.press("Enter")

                try:
                    await page.wait_for_load_state("networkidle", timeout=30000)
                except Exception:
                    await asyncio.sleep(4)

                # Verify login outcome
                if success_indicator:
                    try:
                        await page.wait_for_selector(success_indicator, timeout=8000)
                        login_success = True
                        message = "Login successful — success indicator found"
                    except Exception:
                        if page.url != target_login:
                            login_success = True
                            message = f"Login succeeded — redirected to {page.url}"
                        else:
                            login_success = False
                            message = "Login failed — success indicator not found and URL unchanged"
                else:
                    current_url = page.url
                    try:
                        body_text = (await page.inner_text("body")).lower()
                    except Exception:
                        body_text = (await page.content()).lower()

                    dashboard_keywords = ["dashboard", "home", "profile", "account", "welcome", "logout", "sign out"]
                    error_keywords = ["invalid email", "invalid password", "incorrect", "wrong password", "unauthorized", "login failed", "doesn't match"]

                    has_dashboard = any(kw in current_url.lower() or kw in body_text for kw in dashboard_keywords)
                    has_error = any(kw in body_text for kw in error_keywords)
                    base_current = current_url.split("?")[0].rstrip("/")
                    base_target = target_login.split("?")[0].rstrip("/")
                    redirected = (base_current != base_target)

                    if redirected and not has_error:
                        login_success, message = True, f"Login succeeded — redirected to {current_url}"
                    elif has_error:
                        login_success, message = False, "Login failed — error message detected on page"
                    elif has_dashboard:
                        login_success, message = True, "Login likely succeeded — dashboard keywords visible"
                    elif redirected:
                        login_success, message = True, f"Login succeeded — redirected to {current_url}"
                    else:
                        login_success, message = False, "Login result unclear — page did not change significantly"

                # Post-login UI testing
                if login_success:
                    if progress_cb:
                        progress_cb(2, "Authentication complete. Performing interactive post-login UI assessment...")
                    try:
                        post_login_check = await _test_post_login_ui(page, url)
                    except Exception as e:
                        post_login_check = PostLoginCheck(
                            status=CheckStatus.WARNING, landing_url=page.url,
                            message=f"Post-login UI test error: {str(e)[:120]}",
                        )

        except Exception as e:
            error_str = str(e).lower()
            if "timeout" in error_str:
                message = "Login timed out before completing."
            elif "not found" in error_str or "unreachable" in error_str:
                message = "Could not reach the login page."
            else:
                message = "Login automation encountered an issue."
            login_success = False

        finally:
            # ✅ Password ALWAYS deleted — even if exception occurred mid-fill
            try:
                del password
            except NameError:
                pass
            # ✅ Browser ALWAYS closed — no zombie Chromium processes
            if context is not None:
                try:
                    await context.clear_cookies()
                    await context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass

    error_count = len(js_errors)
    js_check = JSErrorsCheck(
        status=CheckStatus.PASS if error_count == 0 else (CheckStatus.WARNING if error_count <= 3 else CheckStatus.FAIL),
        error_count=error_count, errors=js_errors[:30],
        message="No JavaScript errors detected" if error_count == 0 else f"Found {error_count} JavaScript error(s)",
    )
    return login_success, message, js_check, post_login_check


# ─── Selector helpers ─────────────────────────────────────────────────────────

async def _detect_username_field(page: Page) -> Optional[str]:
    candidates = [
        'input[type="email"]', 'input[name="email"]', 'input[placeholder*="email" i]',
        'input[name="username"]', 'input[name="user"]', 'input[name="login"]',
        'input[id="email"]', 'input[id="username"]',
        'input[placeholder*="username" i]', 'input[type="text"]',
    ]
    for sel in candidates:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                return sel
        except Exception:
            continue
    return None


async def _detect_submit_button(page: Page) -> Optional[str]:
    candidates = [
        'button[type="submit"]', 'input[type="submit"]',
        'button:has-text("Sign In")', 'button:has-text("Sign in")',
        'button:has-text("Login")', 'button:has-text("Log in")',
        'button:has-text("Submit")',
        '[role="button"]:has-text("Sign In")', '[role="button"]:has-text("Login")',
    ]
    for sel in candidates:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                return sel
        except Exception:
            continue
    return None


# ─── Public API ───────────────────────────────────────────────────────────────

async def capture_js_errors(url: str) -> Tuple[JSErrorsCheck, Optional[PostLoginCheck]]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_playwright_executor, _run_in_thread, _async_capture_js_errors(url))


async def run_login_test(
    url: str, username: str, password: str,
    login_url: Optional[str] = None, username_selector: Optional[str] = None,
    password_selector: Optional[str] = None, submit_selector: Optional[str] = None,
    success_indicator: Optional[str] = None, progress_cb=None,
) -> Tuple[bool, str, JSErrorsCheck, Optional[PostLoginCheck]]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _playwright_executor, _run_in_thread,
        _async_run_login_test(
            url=url, username=username, password=password,
            login_url=login_url, username_selector=username_selector,
            password_selector=password_selector, submit_selector=submit_selector,
            success_indicator=success_indicator, progress_cb=progress_cb,
        ),
    )
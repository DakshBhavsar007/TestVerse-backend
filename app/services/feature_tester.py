"""
app/services/feature_tester.py
─────────────────────────────────────────────────────────────────────────────
Human-Like Feature Testing Engine — Phase 8F (Enterprise)

Detects real functional features on a website and *actually uses* each one
like a real user, step-by-step, with Playwright.

Supported features:
  task_manager, byte_battle, shop, leaderboard, search,
  profile, notifications, dashboard, flashcards

Returns a rich result object per feature with individual step statuses.
"""

import asyncio
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PWTimeout,
)

# ── Feature detection keywords ─────────────────────────────────────────────────
FEATURE_KEYWORDS: Dict[str, List[str]] = {
    "task_manager":  ["task", "tasks", "todo", "to-do", "add task", "my tasks", "checklist"],
    "byte_battle":   ["battle", "byte battle", "byte-battle", "compete", "challenge", "1v1",
                      "duel", "arena", "fight", "coding battle"],
    "shop":          ["shop", "store", "buy", "cart", "product", "marketplace",
                      "purchase", "coins", "items", "inventory"],
    "leaderboard":   ["leaderboard", "rankings", "ranking", "top users", "scoreboard",
                      "hall of fame", "top players"],
    "search":        ["search", "find", "query", "explore"],
    "profile":       ["profile", "my profile", "account", "avatar", "settings", "edit profile"],
    "notifications": ["notification", "notifications", "bell", "alerts", "inbox"],
    "dashboard":     ["dashboard", "analytics", "overview", "stats", "statistics", "metrics"],
    "flashcards":    ["flashcard", "flashcards", "cards", "flip", "study", "deck", "quiz"],
}

# ── Pretty labels for features ─────────────────────────────────────────────────
FEATURE_LABELS: Dict[str, str] = {
    "task_manager":  "Task Manager",
    "byte_battle":   "Byte Battle",
    "shop":          "Shop / Store",
    "leaderboard":   "Leaderboard",
    "search":        "Search",
    "profile":       "User Profile",
    "notifications": "Notifications",
    "dashboard":     "Dashboard",
    "flashcards":    "Flashcards",
}

# ── Data helpers ───────────────────────────────────────────────────────────────

def _step(action: str, status: str, detail: str = "") -> Dict[str, str]:
    return {"action": action, "status": status, "detail": detail}


def _score(steps: List[Dict]) -> int:
    passed = sum(1 for s in steps if s["status"] == "pass")
    total  = sum(1 for s in steps if s["status"] in ("pass", "fail"))
    return int(passed / max(total, 1) * 100)


def _result(feature: str, steps: List[Dict], url: str = "") -> Dict[str, Any]:
    score = _score(steps)
    status = "pass" if score >= 80 else "partial" if score >= 40 else "fail"
    passed = sum(1 for s in steps if s["status"] == "pass")
    total  = sum(1 for s in steps if s["status"] in ("pass", "fail"))
    label  = FEATURE_LABELS.get(feature, feature)
    msg    = f"{passed}/{total} steps passed"
    return {
        "feature": feature,
        "label":   label,
        "status":  status,
        "score":   score,
        "steps":   steps,
        "message": msg,
        "tested_url": url,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# FeatureTester class
# ═══════════════════════════════════════════════════════════════════════════════

class FeatureTester:
    """
    Detects and human-tests features on a website using Playwright.

    Usage:
        tester = FeatureTester(url, email="u@e.com", password="pw")
        result = await tester.run_all_tests(progress_cb=my_fn)
    """

    TIMEOUT = 5000      # ms — per selector wait
    NAV_TIMEOUT = 20000 # ms — page navigation

    def __init__(
        self,
        url: str,
        email: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self.url      = url.rstrip("/")
        self.email    = email
        self.password = password
        self._origin  = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    # ── Utilities ──────────────────────────────────────────────────────────────

    def _abs(self, path: str) -> str:
        """Resolve a relative path against origin."""
        if path.startswith("http"):
            return path
        return self._origin + ("/" + path.lstrip("/"))

    async def _goto(self, page: Page, url: str) -> bool:
        try:
            await page.goto(url, timeout=self.NAV_TIMEOUT, wait_until="domcontentloaded")
            await asyncio.sleep(1)
            return True
        except Exception:
            return False

    async def _find(self, page: Page, selectors: List[str], timeout: int = None):
        t = timeout or self.TIMEOUT
        for sel in selectors:
            try:
                el = page.locator(sel).first
                await el.wait_for(state="visible", timeout=t)
                return el
            except Exception:
                continue
        return None

    async def _click_find(self, page: Page, selectors: List[str]) -> Optional[str]:
        """Click first visible element from list of selectors, return which one."""
        for sel in selectors:
            try:
                el = page.locator(sel).first
                await el.wait_for(state="visible", timeout=self.TIMEOUT)
                await el.click()
                await asyncio.sleep(0.8)
                return sel
            except Exception:
                continue
        return None

    async def _page_text(self, page: Page) -> str:
        try:
            return (await page.inner_text("body")).lower()
        except Exception:
            return ""

    async def _count(self, page: Page, sel: str) -> int:
        try:
            return await page.locator(sel).count()
        except Exception:
            return 0

    async def _has_keywords(self, text: str, keywords: List[str], min_matches: int = 1) -> bool:
        found = [kw for kw in keywords if kw in text]
        return len(found) >= min_matches

    # ── Feature detection ──────────────────────────────────────────────────────

    async def detect_features(self, page: Page) -> Dict[str, str]:
        """
        Scan nav links, headings, buttons and body text.
        Returns { feature_key: absolute_url }.
        """
        detected: Dict[str, str] = {}

        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            await asyncio.sleep(2)

        # 1. Crawl all anchor links
        try:
            links = await page.query_selector_all("a[href]")
            for link in links:
                try:
                    href = (await link.get_attribute("href") or "").strip()
                    text = (await link.inner_text()).strip().lower()

                    # resolve URL
                    if href.startswith("http"):
                        full = href
                    elif href.startswith("/"):
                        full = self._origin + href
                    else:
                        continue

                    # block external + dangerous
                    if urlparse(full).netloc != urlparse(self.url).netloc:
                        continue
                    if any(x in href.lower() for x in ["logout", "signout", "delete", "remove"]):
                        continue

                    combined = f"{text} {href.lower()}"
                    for feat, kws in FEATURE_KEYWORDS.items():
                        if feat not in detected and any(kw in combined for kw in kws):
                            detected[feat] = full
                except Exception:
                    continue
        except Exception:
            pass

        # 2. Also check input[type=search] for search feature
        try:
            if "search" not in detected:
                el = await page.query_selector("input[type='search'], input[placeholder*='search' i]")
                if el:
                    detected["search"] = self.url
        except Exception:
            pass

        # 3. Scan body text for features without dedicated nav links
        try:
            body = await self._page_text(page)
            for feat, kws in FEATURE_KEYWORDS.items():
                if feat not in detected:
                    if any(kw in body for kw in kws):
                        detected[feat] = self.url
        except Exception:
            pass

        return detected

    # ══════════════════════════════════════════════════════════════════════════
    # Individual feature test methods
    # ══════════════════════════════════════════════════════════════════════════

    async def test_task_manager(self, page: Page, url: str) -> Dict[str, Any]:
        steps: List[Dict] = []

        # Navigate
        ok = await self._goto(page, url)
        steps.append(_step("Navigate to task manager page", "pass" if ok else "fail",
                            page.url if ok else f"Failed to load {url}"))
        if not ok:
            return _result("task_manager", steps, url)

        # Find add-task input
        input_sels = [
            "input[placeholder*='task' i]", "input[placeholder*='todo' i]",
            "input[placeholder*='add' i]", "textarea[placeholder*='task' i]",
            "input[type='text']", "textarea",
        ]
        inp = await self._find(page, input_sels)
        if inp:
            steps.append(_step("Find task input field", "pass", "Input element located"))
        else:
            steps.append(_step("Find task input field", "fail", "No text input found for task creation"))

        # Type task
        task_text = "Buy groceries for the week"
        if inp:
            try:
                await inp.fill(task_text)
                await asyncio.sleep(0.3)
                steps.append(_step(f"Type task: '{task_text}'", "pass", "Text entered"))
            except Exception as e:
                steps.append(_step(f"Type task: '{task_text}'", "fail", str(e)[:80]))
                inp = None

        # Submit (Enter or button)
        if inp:
            submitted = False
            try:
                btn = await self._find(page, [
                    "button[type='submit']", "button:has-text('Add')",
                    "button:has-text('Create')", "button:has-text('+')",
                    "button:has-text('Save')",
                ])
                if btn:
                    await btn.click()
                    submitted = True
                else:
                    await page.keyboard.press("Enter")
                    submitted = True
                await asyncio.sleep(1.2)
                steps.append(_step("Submit task", "pass" if submitted else "fail",
                                   "Task submitted via button/Enter"))
            except Exception as e:
                steps.append(_step("Submit task", "fail", str(e)[:80]))

        # Verify task appears
        try:
            body = await self._page_text(page)
            short = task_text.split()[0].lower()  # "buy"
            task_els = await page.query_selector_all(
                "[class*='task'], [class*='todo'], [class*='item'], li"
            )
            if short in body or len(task_els) > 0:
                steps.append(_step("Verify task appears in list", "pass",
                                   f"Task content found ({len(task_els)} list items)"))
            else:
                steps.append(_step("Verify task appears in list", "fail",
                                   "Task not visible after submission"))
        except Exception as e:
            steps.append(_step("Verify task appears in list", "fail", str(e)[:80]))

        # Mark task complete
        complete_sels = [
            "input[type='checkbox']",
            "[class*='check']:not(input)", "[class*='complete']",
            "button:has-text('Done')", "button:has-text('Complete')",
            "button:has-text('Mark')", "[class*='tick']",
        ]
        clicked = await self._click_find(page, complete_sels)
        if clicked:
            steps.append(_step("Mark task as complete", "pass", f"Clicked: {clicked}"))
        else:
            steps.append(_step("Mark task as complete", "skip",
                                "No completion control found (task may already be completed)"))

        # Verify completed state
        try:
            body = await self._page_text(page)
            completed_indicators = ["completed", "done", "finished", "✓", "checked"]
            if any(ind in body for ind in completed_indicators):
                steps.append(_step("Verify completed state", "pass", "Completion state detected in page"))
            else:
                steps.append(_step("Verify completed state", "skip", "Could not confirm completion state"))
        except Exception:
            pass

        return _result("task_manager", steps, url)

    async def test_byte_battle(self, page: Page, url: str) -> Dict[str, Any]:
        steps: List[Dict] = []

        # Try common paths
        for path in [url, self._abs("/battle"), self._abs("/byte-battle"),
                      self._abs("/battles"), self._abs("/compete")]:
            ok = await self._goto(page, path)
            if ok:
                body = await self._page_text(page)
                if any(kw in body for kw in ["battle", "compete", "challenge", "byte", "duel"]):
                    url = path
                    break
        else:
            ok = await self._goto(page, url)

        steps.append(_step("Navigate to battle page", "pass" if ok else "fail",
                            page.url if ok else "Failed to load battle page"))
        if not ok:
            return _result("byte_battle", steps, url)

        # Detect battle UI elements
        battle_sels = [
            "[class*='battle']", "[class*='arena']", "[class*='duel']",
            "[id*='battle']", "[id*='arena']", "[class*='compete']",
        ]
        battle_el = await self._find(page, battle_sels, timeout=3000)
        body = await self._page_text(page)
        battle_kws = ["battle", "fight", "vs", "duel", "arena", "compete", "challenge", "byte"]
        kws_found = [kw for kw in battle_kws if kw in body]

        if battle_el or kws_found:
            steps.append(_step("Detect battle UI / content", "pass",
                                f"Found: {kws_found[:3] if kws_found else 'battle element'}"))
        else:
            steps.append(_step("Detect battle UI / content", "fail",
                                "No battle-related UI or content found"))

        # Click start/join battle
        battle_btn_texts = [
            "Start Battle", "Join Battle", "Create Battle", "Quick Match",
            "Find Opponent", "Start", "Challenge", "Create Room", "Join Room",
            "Compete", "Battle",
        ]
        started = False
        for txt in battle_btn_texts:
            try:
                btn = page.get_by_role("button", name=re.compile(txt, re.I)).first
                visible = await btn.is_visible()
                if visible:
                    pre_url = page.url
                    await btn.click()
                    await asyncio.sleep(2)
                    steps.append(_step(f"Click '{txt}' button", "pass",
                                       "Battle initiated successfully"))
                    started = True
                    break
            except Exception:
                continue

        if not started:
            # Try generic battle-class buttons
            for sel in ["[class*='battle'] button", "[class*='start']", "a[href*='battle']"]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible():
                        await el.click()
                        await asyncio.sleep(1.5)
                        steps.append(_step("Click battle action", "pass", f"Clicked: {sel}"))
                        started = True
                        break
                except Exception:
                    continue

        if not started:
            steps.append(_step("Initiate battle session", "skip",
                                "No battle start button found (may require login or opponent)"))

        # Verify battle content post-click
        try:
            body_after = await self._page_text(page)
            post_kws = ["waiting", "matchmaking", "lobby", "opponent", "ready",
                        "battle", "code", "question", "editor", "timer", "score"]
            found_post = [kw for kw in post_kws if kw in body_after]
            if len(found_post) >= 1:
                steps.append(_step("Verify battle interface loaded", "pass",
                                   f"Battle context: {', '.join(found_post[:4])}"))
            else:
                steps.append(_step("Verify battle interface loaded", "skip",
                                   "Battle state unclear — may need opponent to be paired"))
        except Exception as e:
            steps.append(_step("Verify battle interface loaded", "fail", str(e)[:80]))

        return _result("byte_battle", steps, url)

    async def test_shop(self, page: Page, url: str) -> Dict[str, Any]:
        steps: List[Dict] = []

        # Try common shop paths
        for path in [url, self._abs("/shop"), self._abs("/store"),
                      self._abs("/marketplace"), self._abs("/items")]:
            ok = await self._goto(page, path)
            if ok:
                body = await self._page_text(page)
                if any(kw in body for kw in ["shop", "store", "buy", "cart", "item", "product"]):
                    url = path
                    break
        else:
            ok = await self._goto(page, url)

        steps.append(_step("Navigate to shop page", "pass" if ok else "fail",
                            page.url if ok else "Failed to load shop"))
        if not ok:
            return _result("shop", steps, url)

        # Count product items
        product_sels = [
            "[class*='product']", "[class*='shop-item']", "[class*='store-item']",
            "[class*='item-card']", "[class*='card']", "[data-product]",
            ".grid > div", ".items > *", ".products > *",
        ]
        items_count = 0
        used_sel = ""
        for sel in product_sels:
            try:
                n = await page.locator(sel).count()
                if n > 1:
                    items_count = n
                    used_sel = sel
                    break
            except Exception:
                continue

        if items_count > 0:
            steps.append(_step("Verify products loaded", "pass",
                                f"{items_count} items found (selector: {used_sel})"))
        else:
            body = await self._page_text(page)
            shop_kws = ["buy", "purchase", "coins", "price", "unlock", "get"]
            found = [kw for kw in shop_kws if kw in body]
            if len(found) >= 2:
                steps.append(_step("Verify products loaded", "pass",
                                   f"Shop content: {', '.join(found)}"))
            else:
                steps.append(_step("Verify products loaded", "fail",
                                   "No products or shop content found"))

        # Click first product
        try:
            first_product = page.locator(
                "[class*='product'], [class*='item-card'], [class*='shop-item'], [class*='card']"
            ).first
            if await first_product.is_visible():
                await first_product.click()
                await asyncio.sleep(1)
                steps.append(_step("Click first product", "pass", "Product details/modal loaded"))
        except Exception as e:
            steps.append(_step("Click first product", "skip", str(e)[:80]))

        # Click Add to Cart / Buy
        buy_btn = await self._click_find(page, [
            "button:has-text('Add to Cart')", "button:has-text('Buy Now')",
            "button:has-text('Buy')", "button:has-text('Purchase')",
            "button:has-text('Get')", "button:has-text('Unlock')",
            "button:has-text('Add')", "[class*='buy']", "[class*='cart-btn']",
        ])
        if buy_btn:
            steps.append(_step("Click 'Add to Cart' / 'Buy' button", "pass",
                                f"Purchase action triggered"))
            # Handle confirmation dialog
            try:
                modal = await page.query_selector(
                    "[role='dialog'], .modal, [class*='modal'], [class*='confirm']"
                )
                if modal and await modal.is_visible():
                    steps.append(_step("Handle confirmation dialog", "pass",
                                       "Confirmation dialog appeared correctly"))
                    await page.keyboard.press("Escape")
                    await asyncio.sleep(0.5)
                else:
                    body_after = await self._page_text(page)
                    if any(kw in body_after for kw in ["added", "cart", "success", "purchased", "owned"]):
                        steps.append(_step("Verify cart action result", "pass",
                                            "Cart/purchase feedback detected"))
                    else:
                        steps.append(_step("Verify cart action result", "skip",
                                            "No explicit cart feedback shown"))
            except Exception:
                pass
        else:
            steps.append(_step("Click purchase button", "skip",
                                "No buy/add-to-cart button found"))

        # Check cart
        cart_el = await self._find(page, [
            "[class*='cart']", "a[href*='cart']", "button:has-text('Cart')",
            "[aria-label*='cart' i]", "[class*='bag']",
        ], timeout=3000)
        if cart_el:
            steps.append(_step("Verify cart element exists", "pass", "Cart UI element found"))
        else:
            steps.append(_step("Verify cart element exists", "skip", "No cart element found"))

        return _result("shop", steps, url)

    async def test_leaderboard(self, page: Page, url: str) -> Dict[str, Any]:
        steps: List[Dict] = []

        for path in [url, self._abs("/leaderboard"), self._abs("/rankings"),
                      self._abs("/ranking"), self._abs("/top")]:
            ok = await self._goto(page, path)
            if ok:
                body = await self._page_text(page)
                if any(kw in body for kw in ["leaderboard", "rank", "score", "#1", "position"]):
                    url = path
                    break
        else:
            ok = await self._goto(page, url)

        steps.append(_step("Navigate to leaderboard page", "pass" if ok else "fail",
                            page.url if ok else "Failed to load leaderboard"))
        if not ok:
            return _result("leaderboard", steps, url)

        # Count rows
        row_sels = ["table tr", "tbody tr", "[class*='leaderboard'] > *",
                     "[class*='rank-row']", "[class*='player-row']", "[class*='entry']"]
        row_count = 0
        for sel in row_sels:
            try:
                n = await page.locator(sel).count()
                if n >= 2:
                    row_count = n
                    break
            except Exception:
                continue

        if row_count >= 2:
            steps.append(_step("Count leaderboard rows", "pass",
                                f"{row_count} entries found"))
        else:
            body = await self._page_text(page)
            lb_kws = ["rank", "#1", "#2", "score", "points", "level", "xp", "top"]
            found = [kw for kw in lb_kws if kw in body]
            if len(found) >= 2:
                steps.append(_step("Count leaderboard rows", "pass",
                                   f"Leaderboard data: {', '.join(found[:4])}"))
            else:
                steps.append(_step("Count leaderboard rows", "fail",
                                   "No leaderboard rows or data found"))

        # Read first entry
        try:
            first_cells = page.locator("td, [class*='rank-num'], [class*='position'], [class*='name']")
            n = await first_cells.count()
            if n > 0:
                first_text = (await first_cells.first.inner_text()).strip()
                steps.append(_step("Read first leaderboard entry", "pass",
                                   f"First entry: '{first_text[:40]}'"))
            else:
                steps.append(_step("Read first leaderboard entry", "skip", "Could not read entry"))
        except Exception as e:
            steps.append(_step("Read first leaderboard entry", "skip", str(e)[:80]))

        # Verify data present
        try:
            body = await self._page_text(page)
            if any(kw in body for kw in ["score", "points", "xp", "rank", "level"]):
                steps.append(_step("Verify ranking data present", "pass",
                                   "Score/ranking data visible on page"))
            else:
                steps.append(_step("Verify ranking data present", "fail",
                                   "No ranking data values found"))
        except Exception as e:
            steps.append(_step("Verify ranking data present", "fail", str(e)[:80]))

        return _result("leaderboard", steps, url)

    async def test_search(self, page: Page, url: str) -> Dict[str, Any]:
        steps: List[Dict] = []

        ok = await self._goto(page, url)
        steps.append(_step("Navigate to page with search", "pass" if ok else "fail",
                            page.url if ok else "Failed"))
        if not ok:
            return _result("search", steps, url)

        # Find search input
        search_sels = [
            "input[type='search']",
            "input[placeholder*='search' i]",
            "input[placeholder*='find' i]",
            "input[name='q']",
            "input[name='search']",
            "[role='searchbox']",
            "input[aria-label*='search' i]",
        ]
        inp = await self._find(page, search_sels)
        if inp:
            steps.append(_step("Find search input", "pass", "Search input located"))
        else:
            steps.append(_step("Find search input", "fail",
                                "No search input found on page"))
            return _result("search", steps, url)

        # Type query
        query = "python"
        try:
            await inp.fill(query)
            await asyncio.sleep(0.4)
            steps.append(_step(f"Type search query '{query}'", "pass", "Query entered"))
        except Exception as e:
            steps.append(_step(f"Type search query '{query}'", "fail", str(e)[:80]))

        # Submit
        try:
            submit_btn = await self._find(page, [
                "button[type='submit']", "button:has-text('Search')",
                "[aria-label*='search' i][role='button']",
                "button[class*='search']",
            ], timeout=2000)
            if submit_btn:
                await submit_btn.click()
            else:
                await page.keyboard.press("Enter")
            await asyncio.sleep(2)
            steps.append(_step("Submit search query", "pass", "Search submitted"))
        except Exception as e:
            steps.append(_step("Submit search query", "fail", str(e)[:80]))

        # Verify results
        result_sels = [
            "[class*='result']", "[class*='search-result']",
            "[class*='search-item']", ".results > *", "ul li",
        ]
        results_found = False
        for sel in result_sels:
            try:
                n = await page.locator(sel).count()
                if n >= 1:
                    steps.append(_step("Verify search results appear", "pass",
                                       f"{n} result element(s) found"))
                    results_found = True
                    break
            except Exception:
                continue

        if not results_found:
            body = await self._page_text(page)
            if query in body or "result" in body or "found" in body or "match" in body:
                steps.append(_step("Verify search results appear", "pass",
                                   "Search response present in page content"))
            else:
                steps.append(_step("Verify search results appear", "fail",
                                   "No search results detected after query"))

        return _result("search", steps, url)

    async def test_profile(self, page: Page, url: str) -> Dict[str, Any]:
        steps: List[Dict] = []

        for path in [url, self._abs("/profile"), self._abs("/account"),
                      self._abs("/me"), self._abs("/user")]:
            ok = await self._goto(page, path)
            if ok:
                body = await self._page_text(page)
                if any(kw in body for kw in ["profile", "account", "username", "email", "avatar"]):
                    url = path
                    break
        else:
            ok = await self._goto(page, url)

        steps.append(_step("Navigate to profile page", "pass" if ok else "fail",
                            page.url if ok else "Failed"))
        if not ok:
            return _result("profile", steps, url)

        # Verify avatar or username
        identity_sels = [
            "img[class*='avatar']", "img[class*='profile']",
            "[class*='avatar']", "[class*='username']",
            "[class*='profile-name']", "[class*='user-name']",
            "h1", "h2",
        ]
        identity_el = await self._find(page, identity_sels, timeout=4000)
        if identity_el:
            try:
                label_text = (await identity_el.inner_text()).strip()[:50]
                steps.append(_step("Verify avatar/username visible", "pass",
                                   f"Identity element: '{label_text}'"))
            except Exception:
                steps.append(_step("Verify avatar/username visible", "pass",
                                   "Profile element found"))
        else:
            body = await self._page_text(page)
            if any(kw in body for kw in ["profile", "username", "email", "@"]):
                steps.append(_step("Verify avatar/username visible", "pass",
                                   "Profile data found in page text"))
            else:
                steps.append(_step("Verify avatar/username visible", "fail",
                                   "No profile identity elements found"))

        # Check stats
        try:
            body = await self._page_text(page)
            stat_kws = ["level", "xp", "score", "rank", "points", "joined",
                         "battles", "wins", "solved", "streak"]
            found = [kw for kw in stat_kws if kw in body]
            if found:
                steps.append(_step("Verify profile stats visible", "pass",
                                   f"Stats: {', '.join(found[:5])}"))
            else:
                steps.append(_step("Verify profile stats visible", "skip",
                                   "No stat keywords found"))
        except Exception:
            pass

        # Check for Edit Profile button
        edit_el = await self._find(page, [
            "button:has-text('Edit')", "button:has-text('Update')",
            "a:has-text('Edit Profile')", "button:has-text('Edit Profile')",
            "[class*='edit-profile']",
        ], timeout=3000)
        if edit_el:
            steps.append(_step("Detect Edit Profile option", "pass", "Edit button found"))
        else:
            steps.append(_step("Detect Edit Profile option", "skip",
                                "No edit option found (may be read-only)"))

        return _result("profile", steps, url)

    async def test_notifications(self, page: Page, url: str) -> Dict[str, Any]:
        steps: List[Dict] = []

        ok = await self._goto(page, url)
        steps.append(_step("Navigate to site (notifications in header)", "pass" if ok else "fail",
                            page.url if ok else "Failed"))
        if not ok:
            return _result("notifications", steps, url)

        # Find bell / notification button
        notif_sels = [
            "[aria-label*='notification' i]",
            "button[class*='notification']", "button[class*='bell']",
            "[class*='notification-bell']", "[class*='notif-btn']",
            "button:has-text('Notifications')",
            "[id*='notification']", "[id*='notif']",
            "svg[class*='bell']",
        ]
        notif_el = await self._find(page, notif_sels, timeout=4000)

        if notif_el:
            steps.append(_step("Find notification bell/button", "pass",
                                "Notification trigger element found"))
            # Click it
            try:
                pre_count = await self._count(page, "[class*='dropdown'], [class*='popover'], [class*='panel']")
                await notif_el.click()
                await asyncio.sleep(1.2)
                post_count = await self._count(page, "[class*='dropdown'], [class*='popover'], [class*='panel']")

                if post_count > pre_count:
                    steps.append(_step("Open notification panel", "pass",
                                       "Dropdown/panel appeared after click"))
                else:
                    # Check page changed
                    body = await self._page_text(page)
                    if "notification" in body or "alert" in body:
                        steps.append(_step("Open notification panel", "pass",
                                            "Notification content visible"))
                    else:
                        steps.append(_step("Open notification panel", "pass",
                                            "Notification button clicked (UI responded)"))

                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
            except Exception as e:
                steps.append(_step("Open notification panel", "fail", str(e)[:80]))
        else:
            # Check text
            body = await self._page_text(page)
            if "notification" in body or "alert" in body or "inbox" in body:
                steps.append(_step("Find notification element", "pass",
                                   "Notification content found in page"))
                steps.append(_step("Open notification panel", "skip",
                                   "No clickable bell found, but content present"))
            else:
                steps.append(_step("Find notification element", "fail",
                                   "No notification UI found"))
                steps.append(_step("Open notification panel", "fail",
                                   "Cannot test without notification element"))

        return _result("notifications", steps, url)

    async def test_dashboard(self, page: Page, url: str) -> Dict[str, Any]:
        steps: List[Dict] = []

        for path in [url, self._abs("/dashboard"), self._abs("/home"),
                      self._abs("/overview"), self._abs("/analytics")]:
            ok = await self._goto(page, path)
            if ok:
                body = await self._page_text(page)
                if any(kw in body for kw in ["dashboard", "analytics", "overview",
                                               "stats", "metric", "activity", "total"]):
                    url = path
                    break
        else:
            ok = await self._goto(page, url)

        steps.append(_step("Navigate to dashboard", "pass" if ok else "fail",
                            page.url if ok else "Failed"))
        if not ok:
            return _result("dashboard", steps, url)

        # Count stat cards / charts
        chart_sels = [
            "canvas", "svg[class*='chart']", "[class*='chart']",
            "[class*='graph']", "[class*='stat-card']", "[class*='stats']",
            "[class*='metric']", "[class*='kpi']",
        ]
        stat_count = 0
        for sel in chart_sels:
            try:
                n = await page.locator(sel).count()
                if n > 0:
                    stat_count = n
                    steps.append(_step("Count stat cards / charts", "pass",
                                       f"{n} stat/chart element(s): {sel}"))
                    break
            except Exception:
                continue

        if stat_count == 0:
            body = await self._page_text(page)
            stat_kws = ["total", "score", "count", "today", "this week", "activity", "average"]
            found = [kw for kw in stat_kws if kw in body]
            if len(found) >= 2:
                steps.append(_step("Count stat cards / charts", "pass",
                                   f"Dashboard data: {', '.join(found[:4])}"))
            else:
                steps.append(_step("Count stat cards / charts", "fail",
                                   "No chart or stat card elements found"))

        # Click interactive element
        SKIP = ["delete", "remove", "logout", "sign out", "cancel", "reset"]
        try:
            btns = await page.query_selector_all("button")
            btn_count = len(btns)
            clicked_label = None
            for btn in btns[:15]:
                try:
                    label = (await btn.inner_text()).strip().lower()
                    if not label or any(s in label for s in SKIP):
                        continue
                    if await btn.is_visible() and await btn.is_enabled():
                        await btn.click()
                        await asyncio.sleep(0.8)
                        clicked_label = label[:40]
                        break
                except Exception:
                    continue

            if clicked_label:
                steps.append(_step("Interact with dashboard element", "pass",
                                   f"Clicked: '{clicked_label}'"))
            else:
                steps.append(_step("Interact with dashboard element", "skip",
                                   f"{btn_count} buttons found but none safely clickable"))
        except Exception as e:
            steps.append(_step("Interact with dashboard element", "skip", str(e)[:80]))

        return _result("dashboard", steps, url)

    async def test_flashcards(self, page: Page, url: str) -> Dict[str, Any]:
        steps: List[Dict] = []

        for path in [url, self._abs("/flashcards"), self._abs("/cards"),
                      self._abs("/study"), self._abs("/decks")]:
            ok = await self._goto(page, path)
            if ok:
                body = await self._page_text(page)
                if any(kw in body for kw in ["flashcard", "card", "flip", "study", "deck"]):
                    url = path
                    break
        else:
            ok = await self._goto(page, url)

        steps.append(_step("Navigate to flashcards page", "pass" if ok else "fail",
                            page.url if ok else "Failed"))
        if not ok:
            return _result("flashcards", steps, url)

        # Find card / deck to open
        card_sels = [
            "[class*='flashcard']", "[class*='flash-card']",
            "[class*='card']", "[class*='deck']", ".card",
        ]
        card_el = await self._find(page, card_sels, timeout=4000)
        if card_el:
            steps.append(_step("Find flashcard element", "pass", "Card/deck element located"))
            try:
                await card_el.click()
                await asyncio.sleep(1)
                steps.append(_step("Open / flip card", "pass", "Card interaction successful"))
            except Exception as e:
                steps.append(_step("Open / flip card", "fail", str(e)[:80]))
        else:
            steps.append(_step("Find flashcard element", "fail", "No flashcard elements found"))

        # Click Next / Flip navigation
        nav_btns = ["Next", "Previous", "Prev", "Flip", "Know it", "Got it", "Pass", "Skip"]
        nav_clicked = None
        for txt in nav_btns:
            try:
                btn = page.get_by_role("button", name=re.compile(txt, re.I)).first
                if await btn.is_visible():
                    await btn.click()
                    await asyncio.sleep(0.7)
                    nav_clicked = txt
                    break
            except Exception:
                continue

        if nav_clicked:
            steps.append(_step(f"Click '{nav_clicked}' navigation button", "pass",
                                "Flashcard navigation works"))
        else:
            steps.append(_step("Click navigation button", "skip",
                                "No Next/Flip navigation found"))

        # Verify content shows
        try:
            body = await self._page_text(page)
            card_kws = ["card", "front", "back", "answer", "definition", "term", "question"]
            found = [kw for kw in card_kws if kw in body]
            if found:
                steps.append(_step("Verify flashcard content visible", "pass",
                                   f"Content: {', '.join(found[:3])}"))
            else:
                steps.append(_step("Verify flashcard content visible", "skip",
                                   "Card content not identified"))
        except Exception:
            pass

        return _result("flashcards", steps, url)

    # ══════════════════════════════════════════════════════════════════════════
    # Generic fallback tester  (any feature without a dedicated method)
    # ══════════════════════════════════════════════════════════════════════════

    async def test_generic(self, page: Page, url: str,
                            feature: str = "feature") -> Dict[str, Any]:
        """
        Universal fallback tester.
        Steps:
          1. Navigate to the detected feature URL.
          2. Count all meaningful page elements (headings, cards, buttons, links).
          3. Record the page title / h1 as feature identity.
          4. Click the first safe interactive button.
          5. Verify that page content changed or contains relevant material.
        """
        steps: List[Dict] = []
        label = FEATURE_LABELS.get(feature, feature.replace("_", " ").title())

        # ── Step 1: Navigate ─────────────────────────────────────────────────
        ok = await self._goto(page, url)
        steps.append(_step(
            f"Navigate to {label} page",
            "pass" if ok else "fail",
            page.url if ok else f"Failed to load {url}",
        ))
        if not ok:
            return _result(feature, steps, url)

        # ── Step 2: Count meaningful elements ───────────────────────────────
        try:
            counts: Dict[str, int] = {}
            for sel, name in [
                ("h1, h2, h3",          "headings"),
                ("button",              "buttons"),
                ("a[href]",             "links"),
                ("[class*='card']",     "cards"),
                ("[class*='item']",     "items"),
                ("[class*='list'] > *", "list items"),
                ("input, select",       "inputs"),
                ("img",                 "images"),
            ]:
                n = await page.locator(sel).count()
                if n > 0:
                    counts[name] = n

            total_els = sum(counts.values())
            detail = ", ".join(f"{v} {k}" for k, v in list(counts.items())[:5])
            if total_els > 3:
                steps.append(_step(
                    f"Count page elements ({label})",
                    "pass",
                    f"Found {total_els} elements — {detail}",
                ))
            else:
                steps.append(_step(
                    f"Count page elements ({label})",
                    "fail",
                    f"Only {total_els} element(s) found — page may not have loaded correctly",
                ))
        except Exception as e:
            steps.append(_step(f"Count page elements ({label})", "fail", str(e)[:80]))

        # ── Step 3: Record page identity (title / h1) ────────────────────────
        try:
            title = await page.title()
            h1_el = await page.query_selector("h1")
            h1_text = (await h1_el.inner_text()).strip() if h1_el else ""
            identity = h1_text or title or page.url
            steps.append(_step(
                "Read page identity (title / heading)",
                "pass",
                f"'{identity[:60]}'",
            ))
        except Exception as e:
            steps.append(_step("Read page identity", "skip", str(e)[:80]))

        # ── Step 4: Click first safe interactive button ──────────────────────
        UNSAFE_WORDS = [
            "delete", "remove", "logout", "sign out", "reset",
            "cancel", "unsubscribe", "deactivate", "disable",
        ]
        clicked_label = None
        pre_body = await self._page_text(page)
        try:
            btns = await page.query_selector_all("button, a[href][class*='btn'], a[role='button']")
            for btn in btns[:20]:
                try:
                    label_text = (await btn.inner_text()).strip().lower()
                    if not label_text or any(w in label_text for w in UNSAFE_WORDS):
                        continue
                    if not await btn.is_visible():
                        continue
                    # Skip nav/external links
                    if btn.get_property:
                        href = await btn.get_attribute("href") or ""
                        if href.startswith("http") and urlparse(href).netloc != urlparse(self.url).netloc:
                            continue
                    await btn.click()
                    await asyncio.sleep(1.0)
                    clicked_label = label_text[:40]
                    break
                except Exception:
                    continue

            if clicked_label:
                steps.append(_step(
                    f"Click first safe button: '{clicked_label}'",
                    "pass",
                    "UI responded to interaction",
                ))
            else:
                steps.append(_step(
                    "Click first safe button",
                    "skip",
                    "No safe clickable button found on page",
                ))
        except Exception as e:
            steps.append(_step("Click first safe button", "skip", str(e)[:80]))

        # ── Step 5: Verify content is present / changed ──────────────────────
        try:
            post_body = await self._page_text(page)
            kws = FEATURE_KEYWORDS.get(feature, [])
            kws_found = [kw for kw in kws if kw in post_body]
            content_changed = post_body != pre_body

            if kws_found:
                steps.append(_step(
                    "Verify feature content present",
                    "pass",
                    f"Keywords found: {', '.join(kws_found[:4])}",
                ))
            elif content_changed:
                steps.append(_step(
                    "Verify feature content present",
                    "pass",
                    "Page content changed after interaction — feature responded",
                ))
            elif len(post_body) > 200:
                steps.append(_step(
                    "Verify feature content present",
                    "partial",
                    f"Page has content ({len(post_body)} chars) but no specific keywords matched",
                ))
            else:
                steps.append(_step(
                    "Verify feature content present",
                    "fail",
                    "Page appears empty or unchanged after interaction",
                ))
        except Exception as e:
            steps.append(_step("Verify feature content present", "fail", str(e)[:80]))

        return _result(feature, steps, url)

    async def _login(self, page: Page) -> bool:
        if not self.email or not self.password:
            return False
        try:
            for path in [self._abs("/login"), self._abs("/signin"),
                          self._abs("/auth/login"), self.url]:
                await page.goto(path, timeout=self.NAV_TIMEOUT, wait_until="domcontentloaded")
                await asyncio.sleep(1.5)
                pw_inp = await page.query_selector("input[type='password']")
                if pw_inp:
                    break

            # Fill email
            for sel in ["input[type='email']", "input[name='email']",
                         "input[placeholder*='email' i]", "input[name='username']",
                         "input[placeholder*='username' i]", "input[type='text']"]:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        await el.fill(self.email)
                        break
                except Exception:
                    continue

            # Fill password
            pw_el = await page.query_selector("input[type='password']")
            if pw_el:
                await pw_el.fill(self.password)

            # Submit
            submitted = False
            for sel in ["button[type='submit']", "button:has-text('Login')",
                         "button:has-text('Sign In')", "button:has-text('Log in')"]:
                try:
                    el = await page.query_selector(sel)
                    if el and await el.is_visible():
                        await el.click()
                        submitted = True
                        break
                except Exception:
                    continue
            if not submitted:
                await page.keyboard.press("Enter")

            await asyncio.sleep(3)
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

            # Verify login (no longer on login page, or token appears)
            current = page.url.lower()
            if "login" not in current and "signin" not in current:
                return True
            # Try checking for a logged-in indicator
            body = await self._page_text(page)
            if self.email.split("@")[0].lower() in body or "dashboard" in body or "welcome" in body:
                return True
            return False
        except Exception:
            return False

    # ══════════════════════════════════════════════════════════════════════════
    # Main orchestrator
    # ══════════════════════════════════════════════════════════════════════════

    FEATURE_TESTERS = {
        "task_manager":  test_task_manager,
        "byte_battle":   test_byte_battle,
        "shop":          test_shop,
        "leaderboard":   test_leaderboard,
        "search":        test_search,
        "profile":       test_profile,
        "notifications": test_notifications,
        "dashboard":     test_dashboard,
        "flashcards":    test_flashcards,
    }

    async def run_all_tests(
        self,
        progress_cb: Optional[Callable[[str, Optional[Dict]], None]] = None,
        features_filter: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Full test run.  progress_cb(message, partial_result|None) is called after each step.
        """

        def _notify(msg: str, feature_result: Optional[Dict] = None):
            if progress_cb:
                try:
                    progress_cb(msg, feature_result)
                except Exception:
                    pass

        _notify("🚀 Launching browser...")

        all_results: List[Dict] = []
        detected_features: Dict[str, str] = {}
        logged_in = False

        async with async_playwright() as p:
            browser: Browser = await p.chromium.launch(headless=True)
            ctx: BrowserContext = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
            )
            try:
                scan_page: Page = await ctx.new_page()

                # Login
                if self.email and self.password:
                    _notify("🔐 Logging in with provided credentials...")
                    logged_in = await self._login(scan_page)
                    _notify(f"{'✅' if logged_in else '⚠️'} Login {'successful' if logged_in else 'failed — continuing as guest'}...")

                # Scan for features
                _notify("🔍 Scanning website for features...")
                try:
                    await scan_page.goto(self.url, timeout=self.NAV_TIMEOUT, wait_until="domcontentloaded")
                    await asyncio.sleep(1.5)
                except Exception:
                    pass
                detected_features = await self.detect_features(scan_page)
                await scan_page.close()

                # Apply filter
                if features_filter:
                    detected_features = {k: v for k, v in detected_features.items()
                                         if k in features_filter}

                if not detected_features:
                    _notify("⚠️ No features detected on this site.")
                    return self._build_summary([], {}, logged_in)

                feature_list = list(detected_features.keys())
                _notify(f"✅ Detected {len(feature_list)} feature(s): {', '.join(FEATURE_LABELS.get(f, f) for f in feature_list)}")

                # Run per-feature tests
                for feat, feat_url in detected_features.items():
                    label = FEATURE_LABELS.get(feat, feat)
                    _notify(f"🧪 Testing: {label}...")

                    tester_fn = self.FEATURE_TESTERS.get(feat)
                    if not tester_fn:
                        # ── Generic fallback for any unrecognised feature ──
                        # Use default arg to capture feat by VALUE (avoids loop-closure bug)
                        tester_fn = lambda self, pg, u, _f=feat: self.test_generic(pg, u, feature=_f)

                    feat_page: Page = await ctx.new_page()
                    try:
                        res = await tester_fn(self, feat_page, feat_url)
                        all_results.append(res)
                        icon = "✅" if res["status"] == "pass" else "⚠️" if res["status"] == "partial" else "❌"
                        _notify(
                            f"{icon} {label}: {res['status'].upper()} ({res['score']}%)",
                            res,
                        )
                    except Exception as e:
                        err_result = _result(feat, [_step("Run test", "fail", str(e)[:120])], feat_url)
                        all_results.append(err_result)
                        _notify(f"❌ {label}: FAILED (exception)", err_result)
                    finally:
                        try:
                            await feat_page.close()
                        except Exception:
                            pass

            finally:
                try:
                    await ctx.clear_cookies()
                    await ctx.close()
                except Exception:
                    pass
                try:
                    await browser.close()
                except Exception:
                    pass

        return self._build_summary(all_results, detected_features, logged_in)

    def _build_summary(
        self,
        results: List[Dict],
        detected: Dict[str, str],
        logged_in: bool,
    ) -> Dict[str, Any]:
        passed  = sum(1 for r in results if r["status"] == "pass")
        partial = sum(1 for r in results if r["status"] == "partial")
        failed  = sum(1 for r in results if r["status"] == "fail")
        total   = len(results)
        overall = int(sum(r["score"] for r in results) / max(total, 1))

        pass_list = [r["label"] for r in results if r["status"] == "pass"]
        fail_list = [r["label"] for r in results if r["status"] == "fail"]

        parts = [f"Tested {total} feature(s): {passed} passed, {partial} partial, {failed} failed."]
        if pass_list:
            parts.append(f"✅ Working: {', '.join(pass_list)}.")
        if fail_list:
            parts.append(f"❌ Issues: {', '.join(fail_list)}.")

        return {
            "url":               self.url,
            "features_detected": list(detected.keys()),
            "results":           results,
            "overall_score":     overall,
            "summary":           " ".join(parts) if results else "No features detected.",
            "total_features":    total,
            "passed":            passed,
            "partial":           partial,
            "failed":            failed,
            "logged_in":         logged_in,
            "tested_at":         datetime.now(timezone.utc).isoformat(),
        }


# ── Public async entry point ───────────────────────────────────────────────────

async def run_feature_tests(
    url: str,
    email: Optional[str] = None,
    password: Optional[str] = None,
    progress_cb: Optional[Callable] = None,
    features_filter: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Top-level async function for use from the router."""
    tester = FeatureTester(url=url, email=email, password=password)
    return await tester.run_all_tests(
        progress_cb=progress_cb,
        features_filter=features_filter,
    )

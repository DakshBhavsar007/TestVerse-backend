from pydantic import BaseModel, HttpUrl, Field, SecretStr
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class TestStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class CheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    SKIP = "skip"


# ─── Request Models ────────────────────────────────────────────────────────────

class BasicTestRequest(BaseModel):
    url: str = Field(..., description="The URL to test")
    user_id: Optional[str] = Field(None, description="Optional user identifier")

    model_config = {
        "json_schema_extra": {
            "example": {
                "url": "https://example.com",
                "user_id": "user_123"
            }
        }
    }


class LoginTestRequest(BaseModel):
    url: str = Field(..., description="The URL to test")
    login_url: Optional[str] = Field(None, description="Login page URL if different")
    username: str = Field(..., description="Login username/email")
    password: SecretStr = Field(..., description="Login password (never stored)")
    username_selector: Optional[str] = Field(None, description="CSS selector for username field")
    password_selector: Optional[str] = Field(None, description="CSS selector for password field")
    submit_selector: Optional[str] = Field(None, description="CSS selector for submit button")
    success_indicator: Optional[str] = Field(None, description="CSS selector or text to verify login success")
    user_id: Optional[str] = Field(None, description="Optional user identifier")

    model_config = {
        "json_schema_extra": {
            "example": {
                "url": "https://example.com",
                "username": "user@example.com",
                "password": "••••••••",
                "user_id": "user_123"
            }
        }
    }


# ─── Check Result Models ───────────────────────────────────────────────────────

class UptimeCheck(BaseModel):
    status: CheckStatus
    http_status_code: Optional[int] = None
    response_time_ms: Optional[float] = None
    message: str = ""


class SpeedCheck(BaseModel):
    status: CheckStatus
    load_time_ms: Optional[float] = None
    ttfb_ms: Optional[float] = None
    page_size_kb: Optional[float] = None
    message: str = ""


class SSLCheck(BaseModel):
    status: CheckStatus
    valid: Optional[bool] = None
    expires_on: Optional[str] = None
    days_until_expiry: Optional[int] = None
    issuer: Optional[str] = None
    message: str = ""


class BrokenLink(BaseModel):
    url: str
    status_code: Optional[int] = None
    found_on: str
    error: Optional[str] = None


class BrokenLinksCheck(BaseModel):
    status: CheckStatus
    total_links: int = 0
    broken_count: int = 0
    broken_links: List[BrokenLink] = []
    message: str = ""


class MissingImage(BaseModel):
    src: str
    found_on: str
    status_code: Optional[int] = None
    error: Optional[str] = None


class MissingImagesCheck(BaseModel):
    status: CheckStatus
    total_images: int = 0
    missing_count: int = 0
    missing_images: List[MissingImage] = []
    message: str = ""


class JSError(BaseModel):
    message: str
    source: Optional[str] = None
    line: Optional[int] = None
    page_url: str


class JSErrorsCheck(BaseModel):
    status: CheckStatus
    error_count: int = 0
    errors: List[JSError] = []
    message: str = ""


class MobileResponsivenessCheck(BaseModel):
    status: CheckStatus
    has_viewport_meta: Optional[bool] = None
    has_responsive_css: Optional[bool] = None
    mobile_score: Optional[int] = None
    issues: List[str] = []
    message: str = ""


class CrawledPage(BaseModel):
    url: str
    status_code: Optional[int] = None
    load_time_ms: Optional[float] = None
    title: Optional[str] = None
    depth: int = 0


# ─── Post-Login UI Test Models ─────────────────────────────────────────────────

class UIActionStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


class UIActionResult(BaseModel):
    """Result of a single interactive UI action (button click, nav, form, etc.)"""
    action_type: str          # "button", "nav_link", "form", "modal", "dropdown"
    label: str                # Human-readable label (button text, link text, etc.)
    selector: str             # CSS selector that was used
    page_url: str             # Page where the action was found
    status: UIActionStatus
    response_time_ms: Optional[float] = None
    result_url: Optional[str] = None      # URL after action (if navigation happened)
    error: Optional[str] = None
    screenshot_note: Optional[str] = None  # Human-readable note about what changed


class PostLoginCheck(BaseModel):
    """Aggregated result of all post-login UI interaction tests."""
    status: CheckStatus
    landing_url: str = ""           # Where we landed after login
    landing_title: str = ""         # Page title after login
    buttons_found: int = 0
    buttons_passed: int = 0
    buttons_failed: int = 0
    nav_links_found: int = 0
    nav_links_passed: int = 0
    nav_links_failed: int = 0
    forms_found: int = 0
    forms_tested: int = 0
    actions: List[UIActionResult] = []
    js_errors_post_login: List[JSError] = []
    message: str = ""


# ─── Test Result Models ────────────────────────────────────────────────────────

class TestResult(BaseModel):
    test_id: str
    url: str
    status: TestStatus
    user_id: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    test_type: str = "basic"  # basic | login
    current_step: Optional[str] = "Initializing backend containers..."
    current_step_idx: int = 1

    # Checks
    uptime: Optional[UptimeCheck] = None
    speed: Optional[SpeedCheck] = None
    ssl: Optional[SSLCheck] = None
    broken_links: Optional[BrokenLinksCheck] = None
    missing_images: Optional[MissingImagesCheck] = None
    js_errors: Optional[JSErrorsCheck] = None
    mobile_responsiveness: Optional[MobileResponsivenessCheck] = None

    # Crawl info
    pages_crawled: List[CrawledPage] = []
    total_pages: int = 0

    # Login info (sanitized)
    login_success: Optional[bool] = None
    login_message: Optional[str] = None

    # Post-login UI interaction tests (new)
    post_login: Optional[PostLoginCheck] = None

    # Summary
    overall_score: Optional[int] = None
    summary: Optional[str] = None
    ai_recommendations: Optional[List[str]] = None
    report_path: Optional[str] = None
    error: Optional[str] = None


class TestResultResponse(BaseModel):
    success: bool
    test_id: str
    message: str
    data: Optional[TestResult] = None


class TestListResponse(BaseModel):
    success: bool
    total: int
    tests: List[Dict[str, Any]] = []
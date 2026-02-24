"""
TestVerse Phase 8C — CI/CD Integrations Router
Features:
  1. GitHub webhook (run tests on push/PR, post results as PR comment)
  2. GitLab webhook (run tests on push/MR)
  3. Jira integration (auto-create ticket on test failure)
  4. Postman collection importer (import tests from Postman JSON)
"""
import hashlib
import hmac
import httpx
import json
import uuid
import asyncio
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Request, Header, BackgroundTasks
from pydantic import BaseModel

from app.utils.auth import get_current_user
from app.database import get_db
from app.config import get_settings

settings = get_settings()
router = APIRouter(prefix="/cicd", tags=["Phase 8C — CI/CD"])


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def score_emoji(s):
    if s is None: return "⚪"
    if s >= 80: return "🟢"
    if s >= 60: return "🟡"
    if s >= 40: return "🟠"
    return "🔴"


async def _trigger_test(url: str, user_id: str) -> str:
    """Trigger a TestVerse test and return test_id."""
    import uuid as _uuid
    from app.utils.db_results import save_result
    from app.routers.test_router import _run_all
    tid = str(_uuid.uuid4())
    asyncio.create_task(_run_all(tid, url, None, None, user_id))
    return tid


async def _wait_for_result(test_id: str, timeout: int = 120) -> Optional[dict]:
    """Poll for test result until complete or timeout."""
    from app.utils.db_results import get_result
    for _ in range(timeout // 3):
        await asyncio.sleep(3)
        result = await get_result(test_id)
        if result and result.get("status") in ("completed", "failed"):
            return result
    return None


# ═══════════════════════════════════════════════════════════════
# 1. GITHUB WEBHOOK
# ═══════════════════════════════════════════════════════════════

class GitHubConfig(BaseModel):
    webhook_secret: str
    target_url: str                        # URL to test when webhook fires
    test_on_push: bool = True
    test_on_pr: bool = True
    post_pr_comment: bool = True           # Post result as GitHub PR comment
    github_token: Optional[str] = None     # Required for PR comments
    notify_slack: bool = True
    notify_email: bool = True
    score_threshold: int = 70              # Fail CI if score below this


@router.post("/github/config")
async def save_github_config(
    body: GitHubConfig,
    current_user: dict = Depends(get_current_user),
):
    """Save GitHub webhook configuration."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    doc = {
        "user_id": current_user["sub"],
        "provider": "github",
        **body.model_dump(),
        "updated_at": now_iso(),
    }
    await db.cicd_configs.update_one(
        {"user_id": current_user["sub"], "provider": "github"},
        {"$set": doc, "$setOnInsert": {"created_at": now_iso()}},
        upsert=True,
    )
    doc.pop("_id", None)
    # Mask sensitive fields
    if doc.get("github_token"):
        doc["github_token"] = "••••" + doc["github_token"][-4:]
    if doc.get("webhook_secret"):
        doc["webhook_secret"] = "••••" + doc["webhook_secret"][-4:]
    return {"success": True, "config": doc}


@router.get("/github/config")
async def get_github_config(current_user: dict = Depends(get_current_user)):
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    config = await db.cicd_configs.find_one(
        {"user_id": current_user["sub"], "provider": "github"}, {"_id": 0}
    )
    return {"configured": bool(config), "config": config}


@router.delete("/github/config")
async def delete_github_config(current_user: dict = Depends(get_current_user)):
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    await db.cicd_configs.delete_one(
        {"user_id": current_user["sub"], "provider": "github"}
    )
    return {"success": True}


@router.post("/github/webhook/{user_id}")
async def github_webhook(
    user_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: Optional[str] = Header(None),
    x_github_event: Optional[str] = Header(None),
):
    """
    GitHub webhook endpoint.
    Register this URL in GitHub: Settings → Webhooks → Payload URL
    Format: https://yourapi.com/cicd/github/webhook/{your_user_id}
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    config = await db.cicd_configs.find_one({"user_id": user_id, "provider": "github"})
    if not config:
        raise HTTPException(status_code=404, detail="No GitHub config for this user")

    # Verify webhook signature
    body = await request.body()
    secret = config.get("webhook_secret", "")
    if secret and x_hub_signature_256:
        expected = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, x_hub_signature_256):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = json.loads(body)
    event = x_github_event or "push"

    # Determine if we should run a test
    should_run = False
    pr_data = None

    if event == "push" and config.get("test_on_push"):
        should_run = True
    elif event == "pull_request" and config.get("test_on_pr"):
        action = payload.get("action", "")
        if action in ("opened", "synchronize", "reopened"):
            should_run = True
            pr_data = {
                "number": payload.get("number"),
                "repo": payload.get("repository", {}).get("full_name"),
                "token": config.get("github_token"),
                "sha": payload.get("pull_request", {}).get("head", {}).get("sha"),
            }

    if not should_run:
        return {"message": "Event ignored", "event": event}

    # Log the trigger
    trigger_id = str(uuid.uuid4())
    await db.cicd_triggers.insert_one({
        "trigger_id": trigger_id,
        "user_id": user_id,
        "provider": "github",
        "event": event,
        "target_url": config["target_url"],
        "status": "running",
        "triggered_at": now_iso(),
        "pr_data": pr_data,
    })

    background_tasks.add_task(
        _run_github_test, trigger_id, user_id, config, pr_data
    )

    return {"message": "Test triggered", "trigger_id": trigger_id}


async def _run_github_test(trigger_id: str, user_id: str, config: dict, pr_data: Optional[dict]):
    """Background task: run test, post PR comment, notify."""
    db = get_db()
    try:
        test_id = await _trigger_test(config["target_url"], user_id)
        result = await _wait_for_result(test_id)

        score = result.get("overall_score") if result else None
        status = result.get("status", "unknown") if result else "timeout"
        passed = score is not None and score >= config.get("score_threshold", 70)
        report_url = f"{settings.app_url}/result/{test_id}"

        # Post GitHub PR comment
        if pr_data and config.get("post_pr_comment") and config.get("github_token"):
            await _post_github_pr_comment(
                repo=pr_data["repo"],
                pr_number=pr_data["number"],
                token=pr_data["token"] or config.get("github_token"),
                score=score,
                url=config["target_url"],
                report_url=report_url,
                passed=passed,
                threshold=config.get("score_threshold", 70),
            )

        # Slack notification
        if config.get("notify_slack"):
            from app.routers.slack_router import notify_slack_on_complete
            await notify_slack_on_complete(user_id=user_id, test_result=result or {})

        # Email notification
        if config.get("notify_email"):
            from app.services.notification_service import notify_on_complete
            await notify_on_complete(
                user_id=user_id, url=config["target_url"],
                test_id=test_id, status=status,
                score=score, summary=result.get("summary") if result else None,
            )

        await db.cicd_triggers.update_one(
            {"trigger_id": trigger_id},
            {"$set": {
                "status": "completed", "test_id": test_id,
                "score": score, "passed": passed, "completed_at": now_iso(),
            }}
        )
    except Exception as e:
        await db.cicd_triggers.update_one(
            {"trigger_id": trigger_id},
            {"$set": {"status": "error", "error": str(e), "completed_at": now_iso()}}
        )


async def _post_github_pr_comment(repo, pr_number, token, score, url, report_url, passed, threshold):
    """Post a test result comment on a GitHub PR."""
    emoji = score_emoji(score)
    status_line = f"{'✅ PASSED' if passed else '❌ FAILED'} (threshold: {threshold})"
    body = (
        f"## {emoji} TestVerse Results\n\n"
        f"| | |\n|---|---|\n"
        f"| **URL** | `{url}` |\n"
        f"| **Score** | **{score if score is not None else '—'}/100** |\n"
        f"| **Status** | {status_line} |\n\n"
        f"[📄 View Full Report]({report_url})\n\n"
        f"<sub>Powered by TestVerse</sub>"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
                headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
                json={"body": body},
            )
    except Exception as e:
        print(f"GitHub PR comment failed: {e}")


# ═══════════════════════════════════════════════════════════════
# 2. GITLAB WEBHOOK
# ═══════════════════════════════════════════════════════════════

class GitLabConfig(BaseModel):
    webhook_secret: str
    target_url: str
    test_on_push: bool = True
    test_on_mr: bool = True
    gitlab_token: Optional[str] = None    # Personal access token for MR comments
    gitlab_project_id: Optional[str] = None
    notify_slack: bool = True
    notify_email: bool = True
    score_threshold: int = 70


@router.post("/gitlab/config")
async def save_gitlab_config(
    body: GitLabConfig,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = {"user_id": current_user["sub"], "provider": "gitlab", **body.model_dump(), "updated_at": now_iso()}
    await db.cicd_configs.update_one(
        {"user_id": current_user["sub"], "provider": "gitlab"},
        {"$set": doc, "$setOnInsert": {"created_at": now_iso()}},
        upsert=True,
    )
    doc.pop("_id", None)
    return {"success": True, "config": doc}


@router.get("/gitlab/config")
async def get_gitlab_config(current_user: dict = Depends(get_current_user)):
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    config = await db.cicd_configs.find_one(
        {"user_id": current_user["sub"], "provider": "gitlab"}, {"_id": 0}
    )
    return {"configured": bool(config), "config": config}


@router.post("/gitlab/webhook/{user_id}")
async def gitlab_webhook(
    user_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    x_gitlab_token: Optional[str] = Header(None),
    x_gitlab_event: Optional[str] = Header(None),
):
    """
    GitLab webhook endpoint.
    Register in GitLab: Settings → Webhooks → URL
    Format: https://yourapi.com/cicd/gitlab/webhook/{your_user_id}
    """
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")

    config = await db.cicd_configs.find_one({"user_id": user_id, "provider": "gitlab"})
    if not config:
        raise HTTPException(status_code=404, detail="No GitLab config for this user")

    # Verify secret token
    if config.get("webhook_secret") and x_gitlab_token != config["webhook_secret"]:
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    body = await request.body()
    payload = json.loads(body)
    event = x_gitlab_event or ""

    should_run = False
    mr_data = None

    if "Push Hook" in event and config.get("test_on_push"):
        should_run = True
    elif "Merge Request Hook" in event and config.get("test_on_mr"):
        action = payload.get("object_attributes", {}).get("action", "")
        if action in ("open", "update", "reopen"):
            should_run = True
            mr_data = {
                "iid": payload.get("object_attributes", {}).get("iid"),
                "project_id": config.get("gitlab_project_id"),
                "token": config.get("gitlab_token"),
            }

    if not should_run:
        return {"message": "Event ignored", "event": event}

    trigger_id = str(uuid.uuid4())
    await db.cicd_triggers.insert_one({
        "trigger_id": trigger_id, "user_id": user_id, "provider": "gitlab",
        "event": event, "target_url": config["target_url"],
        "status": "running", "triggered_at": now_iso(), "mr_data": mr_data,
    })

    background_tasks.add_task(_run_gitlab_test, trigger_id, user_id, config, mr_data)
    return {"message": "Test triggered", "trigger_id": trigger_id}


async def _run_gitlab_test(trigger_id: str, user_id: str, config: dict, mr_data: Optional[dict]):
    db = get_db()
    try:
        test_id = await _trigger_test(config["target_url"], user_id)
        result = await _wait_for_result(test_id)
        score = result.get("overall_score") if result else None
        status = result.get("status", "unknown") if result else "timeout"
        passed = score is not None and score >= config.get("score_threshold", 70)
        report_url = f"{settings.app_url}/result/{test_id}"

        # Post GitLab MR comment
        if mr_data and config.get("gitlab_token") and mr_data.get("project_id"):
            await _post_gitlab_mr_comment(
                project_id=mr_data["project_id"],
                mr_iid=mr_data["iid"],
                token=mr_data["token"],
                score=score, url=config["target_url"],
                report_url=report_url, passed=passed,
                threshold=config.get("score_threshold", 70),
            )

        if config.get("notify_slack"):
            from app.routers.slack_router import notify_slack_on_complete
            await notify_slack_on_complete(user_id=user_id, test_result=result or {})

        if config.get("notify_email"):
            from app.services.notification_service import notify_on_complete
            await notify_on_complete(
                user_id=user_id, url=config["target_url"],
                test_id=test_id, status=status, score=score,
                summary=result.get("summary") if result else None,
            )

        await db.cicd_triggers.update_one(
            {"trigger_id": trigger_id},
            {"$set": {"status": "completed", "test_id": test_id, "score": score, "passed": passed, "completed_at": now_iso()}}
        )
    except Exception as e:
        await db.cicd_triggers.update_one(
            {"trigger_id": trigger_id},
            {"$set": {"status": "error", "error": str(e), "completed_at": now_iso()}}
        )


async def _post_gitlab_mr_comment(project_id, mr_iid, token, score, url, report_url, passed, threshold):
    emoji = score_emoji(score)
    body = (
        f"## {emoji} TestVerse Results\n\n"
        f"**URL:** `{url}`  \n"
        f"**Score:** **{score if score is not None else '—'}/100**  \n"
        f"**Status:** {'✅ PASSED' if passed else '❌ FAILED'} (threshold: {threshold})\n\n"
        f"[📄 View Full Report]({report_url})"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://gitlab.com/api/v4/projects/{project_id}/merge_requests/{mr_iid}/notes",
                headers={"PRIVATE-TOKEN": token},
                json={"body": body},
            )
    except Exception as e:
        print(f"GitLab MR comment failed: {e}")


# ═══════════════════════════════════════════════════════════════
# 3. JIRA INTEGRATION
# ═══════════════════════════════════════════════════════════════

class JiraConfig(BaseModel):
    jira_url: str           # e.g. https://yourcompany.atlassian.net
    email: str
    api_token: str
    project_key: str        # e.g. "QA" or "DEV"
    issue_type: str = "Bug"
    score_threshold: int = 60   # Create ticket if score drops below this
    auto_create: bool = True


@router.post("/jira/config")
async def save_jira_config(
    body: JiraConfig,
    current_user: dict = Depends(get_current_user),
):
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = {"user_id": current_user["sub"], **body.model_dump(), "updated_at": now_iso()}
    await db.jira_configs.update_one(
        {"user_id": current_user["sub"]},
        {"$set": doc, "$setOnInsert": {"created_at": now_iso()}},
        upsert=True,
    )
    doc.pop("_id", None)
    doc["api_token"] = "••••" + doc["api_token"][-4:]
    return {"success": True, "config": doc}


@router.get("/jira/config")
async def get_jira_config(current_user: dict = Depends(get_current_user)):
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    config = await db.jira_configs.find_one(
        {"user_id": current_user["sub"]}, {"_id": 0}
    )
    if config and config.get("api_token"):
        config["api_token"] = "••••" + config["api_token"][-4:]
    return {"configured": bool(config), "config": config}


@router.delete("/jira/config")
async def delete_jira_config(current_user: dict = Depends(get_current_user)):
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    await db.jira_configs.delete_one({"user_id": current_user["sub"]})
    return {"success": True}


@router.post("/jira/test-connection")
async def test_jira_connection(current_user: dict = Depends(get_current_user)):
    """Test Jira API connection."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    config = await db.jira_configs.find_one({"user_id": current_user["sub"]})
    if not config:
        raise HTTPException(status_code=404, detail="No Jira config found")
    try:
        import base64
        creds = base64.b64encode(f"{config['email']}:{config['api_token']}".encode()).decode()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{config['jira_url'].rstrip('/')}/rest/api/3/myself",
                headers={"Authorization": f"Basic {creds}", "Accept": "application/json"},
            )
        if resp.status_code == 200:
            data = resp.json()
            return {"success": True, "message": f"Connected as {data.get('displayName', config['email'])}"}
        return {"success": False, "message": f"Jira returned {resp.status_code}"}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/jira/create-ticket")
async def create_jira_ticket_manual(
    test_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Manually create a Jira ticket for a test result."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    config = await db.jira_configs.find_one({"user_id": current_user["sub"]})
    if not config:
        raise HTTPException(status_code=404, detail="No Jira config found")

    from app.utils.db_results import get_result
    result = await get_result(test_id)
    if not result:
        raise HTTPException(status_code=404, detail="Test result not found")

    ticket_key = await _create_jira_ticket(config, result)
    return {"success": True, "ticket": ticket_key, "jira_url": f"{config['jira_url'].rstrip('/')}/browse/{ticket_key}"}


async def _create_jira_ticket(config: dict, result: dict) -> str:
    """Create a Jira issue for a failed test. Returns issue key."""
    import base64
    score = result.get("overall_score")
    url = result.get("url", "Unknown")
    test_id = result.get("test_id", "")
    report_url = f"{settings.app_url}/result/{test_id}"

    # Find failing checks
    failing = []
    for check in ["ssl", "speed", "seo", "accessibility", "security_headers", "broken_links"]:
        data = result.get(check, {})
        if isinstance(data, dict) and data.get("status") in ("fail", "error"):
            failing.append(check.replace("_", " ").title())

    description = {
        "type": "doc", "version": 1,
        "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": f"TestVerse detected issues on: {url}", "marks": [{"type": "strong"}]}
            ]},
            {"type": "paragraph", "content": [
                {"type": "text", "text": f"Overall Score: {score}/100"}
            ]},
            {"type": "paragraph", "content": [
                {"type": "text", "text": f"Failing checks: {', '.join(failing) or 'See full report'}"}
            ]},
            {"type": "paragraph", "content": [
                {"type": "text", "text": f"Full Report: {report_url}"}
            ]},
        ]
    }

    creds = base64.b64encode(f"{config['email']}:{config['api_token']}".encode()).decode()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{config['jira_url'].rstrip('/')}/rest/api/3/issue",
            headers={"Authorization": f"Basic {creds}", "Content-Type": "application/json"},
            json={
                "fields": {
                    "project": {"key": config["project_key"]},
                    "summary": f"TestVerse: Score dropped to {score}/100 on {url}",
                    "issuetype": {"name": config.get("issue_type", "Bug")},
                    "description": description,
                    "priority": {"name": "High" if score and score < 40 else "Medium"},
                }
            }
        )
    data = resp.json()
    return data.get("key", "UNKNOWN")


# Auto-create Jira ticket utility (called from test_router on completion)
async def auto_jira_on_failure(user_id: str, test_result: dict):
    """Called after test completes — auto-create Jira ticket if score is low."""
    db = get_db()
    config = await db.jira_configs.find_one({"user_id": user_id})
    if not config or not config.get("auto_create"):
        return
    score = test_result.get("overall_score")
    threshold = config.get("score_threshold", 60)
    if score is not None and score < threshold:
        try:
            await _create_jira_ticket(config, test_result)
        except Exception as e:
            print(f"Auto Jira ticket failed: {e}")


# ═══════════════════════════════════════════════════════════════
# 4. POSTMAN COLLECTION IMPORTER
# ═══════════════════════════════════════════════════════════════

class PostmanImportRequest(BaseModel):
    collection: dict    # Raw Postman collection JSON


class PostmanImportUrlRequest(BaseModel):
    collection_url: str  # Public Postman share URL


@router.post("/postman/import")
async def import_postman_collection(
    body: PostmanImportRequest,
    current_user: dict = Depends(get_current_user),
):
    """Import a Postman collection JSON and convert to TestVerse test configs."""
    try:
        tests = _parse_postman_collection(body.collection)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid Postman collection: {e}")

    if not tests:
        raise HTTPException(status_code=400, detail="No requests found in collection")

    # Save imported tests to DB
    db = get_db()
    import_id = str(uuid.uuid4())
    doc = {
        "import_id": import_id,
        "user_id": current_user["sub"],
        "source": "postman",
        "collection_name": body.collection.get("info", {}).get("name", "Imported Collection"),
        "tests": tests,
        "total": len(tests),
        "imported_at": now_iso(),
    }
    if db is not None:
        await db.imported_tests.insert_one(doc)
    doc.pop("_id", None)
    return {"success": True, "import_id": import_id, "total": len(tests), "tests": tests}


@router.post("/postman/import-url")
async def import_postman_from_url(
    body: PostmanImportUrlRequest,
    current_user: dict = Depends(get_current_user),
):
    """Fetch and import a Postman collection from a public URL."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(body.collection_url)
            collection = resp.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch collection: {e}")

    return await import_postman_collection(
        PostmanImportRequest(collection=collection), current_user
    )


@router.get("/postman/imports")
async def list_postman_imports(current_user: dict = Depends(get_current_user)):
    """List all imported Postman collections for this user."""
    db = get_db()
    if db is None:
        return {"imports": []}
    cursor = db.imported_tests.find(
        {"user_id": current_user["sub"]},
        {"_id": 0, "tests": 0}
    ).sort("imported_at", -1)
    imports = await cursor.to_list(length=50)
    return {"imports": imports, "total": len(imports)}


@router.get("/postman/imports/{import_id}")
async def get_postman_import(
    import_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get full details of an imported collection."""
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    doc = await db.imported_tests.find_one(
        {"import_id": import_id, "user_id": current_user["sub"]}, {"_id": 0}
    )
    if not doc:
        raise HTTPException(status_code=404, detail="Import not found")
    return doc


def _parse_postman_collection(collection: dict) -> List[dict]:
    """Parse a Postman v2.1 collection into TestVerse test configs."""
    tests = []

    def _extract_items(items, folder=""):
        for item in items:
            # Folder — recurse
            if "item" in item:
                _extract_items(item["item"], folder=item.get("name", ""))
                continue

            request = item.get("request", {})
            if not request:
                continue

            method = request.get("method", "GET").upper()
            url_data = request.get("url", {})

            # URL can be string or object
            if isinstance(url_data, str):
                raw_url = url_data
            else:
                raw_url = url_data.get("raw", "")

            if not raw_url:
                continue

            # Headers
            headers = {}
            for h in request.get("header", []):
                if not h.get("disabled"):
                    headers[h.get("key", "")] = h.get("value", "")

            # Body
            body = None
            body_data = request.get("body", {})
            if body_data:
                mode = body_data.get("mode", "")
                if mode == "raw":
                    try:
                        body = json.loads(body_data.get("raw", "null"))
                    except Exception:
                        body = body_data.get("raw")
                elif mode == "urlencoded":
                    body = {p["key"]: p["value"] for p in body_data.get("urlencoded", []) if not p.get("disabled")}

            # Auth
            auth = request.get("auth", {})
            auth_type = auth.get("type", "")
            if auth_type == "bearer":
                bearer = next((a["value"] for a in auth.get("bearer", []) if a["key"] == "token"), "")
                if bearer:
                    headers["Authorization"] = f"Bearer {bearer}"

            tests.append({
                "test_id": str(uuid.uuid4()),
                "name": item.get("name", f"{method} {raw_url}"),
                "folder": folder,
                "url": raw_url,
                "method": method,
                "headers": headers,
                "body": body,
                "expected_status": 200,
                "tags": ["postman", folder] if folder else ["postman"],
                "source": "postman",
            })

    items = collection.get("item", [])
    _extract_items(items)
    return tests


# ═══════════════════════════════════════════════════════════════
# 5. TRIGGER HISTORY
# ═══════════════════════════════════════════════════════════════

@router.get("/triggers")
async def list_triggers(
    provider: Optional[str] = None,
    limit: int = 50,
    current_user: dict = Depends(get_current_user),
):
    """List all CI/CD webhook triggers for this user."""
    db = get_db()
    if db is None:
        return {"triggers": []}
    query = {"user_id": current_user["sub"]}
    if provider:
        query["provider"] = provider
    cursor = db.cicd_triggers.find(query, {"_id": 0}).sort("triggered_at", -1).limit(limit)
    triggers = await cursor.to_list(length=limit)
    return {"triggers": triggers, "total": len(triggers)}


@router.get("/triggers/{trigger_id}")
async def get_trigger(trigger_id: str, current_user: dict = Depends(get_current_user)):
    db = get_db()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    trigger = await db.cicd_triggers.find_one(
        {"trigger_id": trigger_id, "user_id": current_user["sub"]}, {"_id": 0}
    )
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return trigger

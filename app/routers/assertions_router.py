"""
app/routers/assertions_router.py
Phase 7B: Advanced Assertions
- JSONPath validation against API responses
- Regex matching on page content
- Custom assertion rule sets
- Assertion history & pass/fail tracking
"""
import uuid, re, json
from datetime import datetime, timezone
from typing import Optional, Any, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from app.database import get_db
from app.utils.auth import get_current_user

router = APIRouter(prefix="/assertions", tags=["Assertions"])


# ─── Models ────────────────────────────────────────────────────────────────────

class Assertion(BaseModel):
    assertion_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    type: str           # jsonpath | regex | status_code | response_time | contains | not_contains | header
    target: str         # JSONPath expression, regex, or header name
    operator: str       # equals | contains | matches | gt | lt | gte | lte | exists | not_exists
    expected: Any       # expected value
    description: Optional[str] = None


class AssertionRuleSet(BaseModel):
    name: str
    description: Optional[str] = None
    url_pattern: Optional[str] = None
    assertions: List[Assertion]
    fail_fast: bool = False     # stop on first failure


class RunAssertionsRequest(BaseModel):
    url: str
    ruleset_id: Optional[str] = None
    assertions: Optional[List[Assertion]] = None  # or inline assertions
    test_result_id: Optional[str] = None           # assert against existing result


class ValidateAssertionRequest(BaseModel):
    """Test a single assertion against provided data."""
    assertion: Assertion
    data: Any  # the data to assert against


# ─── JSONPath implementation (lightweight) ─────────────────────────────────────

def jsonpath_extract(data: Any, path: str) -> Any:
    """Simple JSONPath extractor supporting $, ., [] notation."""
    if not path.startswith("$"):
        return None
    parts = path.lstrip("$").lstrip(".").split(".")
    current = data
    for part in parts:
        if not part:
            continue
        if "[" in part:
            key = part[:part.index("[")]
            idx = int(part[part.index("[")+1:part.index("]")])
            if key:
                current = current.get(key, {}) if isinstance(current, dict) else None
            current = current[idx] if isinstance(current, list) and len(current) > idx else None
        else:
            current = current.get(part) if isinstance(current, dict) else None
        if current is None:
            break
    return current


def evaluate_assertion(assertion: Assertion, data: Any) -> dict:
    """Evaluate a single assertion against data. Returns result dict."""
    actual = None
    error = None

    try:
        if assertion.type == "jsonpath":
            if isinstance(data, str):
                data = json.loads(data)
            actual = jsonpath_extract(data, assertion.target)

        elif assertion.type == "regex":
            text = str(data) if not isinstance(data, str) else data
            match = re.search(assertion.target, text, re.IGNORECASE | re.DOTALL)
            actual = match.group(0) if match else None

        elif assertion.type == "contains":
            text = str(data)
            actual = assertion.target in text

        elif assertion.type == "not_contains":
            text = str(data)
            actual = assertion.target not in text

        elif assertion.type == "status_code":
            actual = data if isinstance(data, int) else int(str(data))

        elif assertion.type == "response_time":
            actual = float(data)

        elif assertion.type in ("header", "jsonpath"):
            actual = data

    except Exception as e:
        error = str(e)

    # Evaluate operator
    passed = False
    if error is None:
        try:
            if assertion.operator == "equals":
                passed = str(actual) == str(assertion.expected)
            elif assertion.operator == "contains":
                passed = str(assertion.expected).lower() in str(actual).lower()
            elif assertion.operator == "matches":
                passed = bool(re.search(str(assertion.expected), str(actual), re.IGNORECASE))
            elif assertion.operator == "gt":
                passed = float(actual) > float(assertion.expected)
            elif assertion.operator == "lt":
                passed = float(actual) < float(assertion.expected)
            elif assertion.operator == "gte":
                passed = float(actual) >= float(assertion.expected)
            elif assertion.operator == "lte":
                passed = float(actual) <= float(assertion.expected)
            elif assertion.operator == "exists":
                passed = actual is not None
            elif assertion.operator == "not_exists":
                passed = actual is None
            elif assertion.operator == "is_true":
                passed = bool(actual) is True
            elif assertion.operator == "is_false":
                passed = bool(actual) is False
        except Exception as e:
            error = str(e)

    return {
        "assertion_id": assertion.assertion_id,
        "name": assertion.name,
        "type": assertion.type,
        "passed": passed,
        "actual": actual,
        "expected": assertion.expected,
        "operator": assertion.operator,
        "error": error,
    }


# ─── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/validate")
async def validate_assertion(
    req: ValidateAssertionRequest,
    current_user: dict = Depends(get_current_user)
):
    """Test a single assertion against provided data immediately."""
    result = evaluate_assertion(req.assertion, req.data)
    return {"success": True, "result": result}


@router.post("/rulesets")
async def create_ruleset(
    req: AssertionRuleSet,
    current_user: dict = Depends(get_current_user)
):
    """Save an assertion rule set."""
    db = get_db()
    if db is None:
        raise HTTPException(500, "Database not available")

    user_id = current_user.get("id") or current_user.get("sub")
    ruleset_id = str(uuid.uuid4())

    doc = {
        "ruleset_id": ruleset_id,
        "user_id": user_id,
        "name": req.name,
        "description": req.description,
        "url_pattern": req.url_pattern,
        "assertions": [a.dict() for a in req.assertions],
        "fail_fast": req.fail_fast,
        "created_at": datetime.now(timezone.utc),
        "run_count": 0,
        "last_run": None,
        "last_pass_rate": None,
    }
    await db.assertion_rulesets.insert_one(doc)

    return {"success": True, "ruleset_id": ruleset_id, "message": f"Rule set '{req.name}' saved with {len(req.assertions)} assertions"}


@router.get("/rulesets")
async def list_rulesets(current_user: dict = Depends(get_current_user)):
    """List all assertion rule sets."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")
    if db is None:
        return {"success": True, "rulesets": []}

    docs = await db.assertion_rulesets.find({"user_id": user_id}).sort("created_at", -1).to_list(100)
    return {
        "success": True,
        "rulesets": [
            {
                "ruleset_id": d["ruleset_id"],
                "name": d["name"],
                "description": d.get("description"),
                "assertion_count": len(d.get("assertions", [])),
                "url_pattern": d.get("url_pattern"),
                "run_count": d.get("run_count", 0),
                "last_pass_rate": d.get("last_pass_rate"),
                "last_run": d["last_run"].isoformat() if d.get("last_run") else None,
            }
            for d in docs
        ]
    }


@router.get("/rulesets/{ruleset_id}")
async def get_ruleset(ruleset_id: str, current_user: dict = Depends(get_current_user)):
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")
    if db is None:
        raise HTTPException(500, "Database not available")

    doc = await db.assertion_rulesets.find_one({"ruleset_id": ruleset_id, "user_id": user_id})
    if not doc:
        raise HTTPException(404, "Rule set not found")
    doc.pop("_id", None)
    return {"success": True, "ruleset": doc}


@router.delete("/rulesets/{ruleset_id}")
async def delete_ruleset(ruleset_id: str, current_user: dict = Depends(get_current_user)):
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")
    if db:
        await db.assertion_rulesets.delete_one({"ruleset_id": ruleset_id, "user_id": user_id})
    return {"success": True, "message": "Rule set deleted"}


@router.post("/run/{ruleset_id}")
async def run_ruleset(
    ruleset_id: str,
    test_result_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """Run an assertion rule set against a test result or live fetch."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")

    if db is None:
        raise HTTPException(500, "Database not available")

    ruleset_doc = await db.assertion_rulesets.find_one({"ruleset_id": ruleset_id, "user_id": user_id})
    if not ruleset_doc:
        raise HTTPException(404, "Rule set not found")

    # Get data to assert against
    data = {}
    if test_result_id:
        result = await db.test_results.find_one({"test_id": test_result_id, "user_id": user_id})
        if not result:
            raise HTTPException(404, "Test result not found")
        result.pop("_id", None)
        data = result

    assertions = [Assertion(**a) for a in ruleset_doc.get("assertions", [])]
    results = []
    passed = 0

    for assertion in assertions:
        # Determine what data to pass based on assertion type
        if assertion.type == "jsonpath":
            target_data = data
        elif assertion.type == "status_code":
            target_data = data.get("status_code", data.get("status"))
        elif assertion.type == "response_time":
            target_data = data.get("ttfb_ms") or data.get("load_time_ms")
        elif assertion.type == "regex":
            target_data = json.dumps(data)
        else:
            target_data = data

        r = evaluate_assertion(assertion, target_data)
        results.append(r)
        if r["passed"]:
            passed += 1

        if ruleset_doc.get("fail_fast") and not r["passed"]:
            break

    pass_rate = round(passed / len(results) * 100, 1) if results else 0

    # Save run history
    run_id = str(uuid.uuid4())
    await db.assertion_runs.insert_one({
        "run_id": run_id,
        "ruleset_id": ruleset_id,
        "user_id": user_id,
        "test_result_id": test_result_id,
        "results": results,
        "passed": passed,
        "total": len(results),
        "pass_rate": pass_rate,
        "ran_at": datetime.now(timezone.utc),
    })

    # Update ruleset stats
    await db.assertion_rulesets.update_one(
        {"ruleset_id": ruleset_id},
        {"$inc": {"run_count": 1}, "$set": {"last_run": datetime.now(timezone.utc), "last_pass_rate": pass_rate}}
    )

    return {
        "success": True,
        "run_id": run_id,
        "ruleset_name": ruleset_doc["name"],
        "passed": passed,
        "failed": len(results) - passed,
        "total": len(results),
        "pass_rate": pass_rate,
        "all_passed": passed == len(results),
        "results": results,
    }


@router.get("/runs")
async def list_runs(
    ruleset_id: Optional[str] = None,
    limit: int = 20,
    current_user: dict = Depends(get_current_user)
):
    """List recent assertion run history."""
    db = get_db()
    user_id = current_user.get("id") or current_user.get("sub")
    if db is None:
        return {"success": True, "runs": []}

    query = {"user_id": user_id}
    if ruleset_id:
        query["ruleset_id"] = ruleset_id

    runs = await db.assertion_runs.find(query).sort("ran_at", -1).limit(limit).to_list(limit)
    return {
        "success": True,
        "runs": [
            {
                "run_id": r["run_id"],
                "ruleset_id": r["ruleset_id"],
                "passed": r["passed"],
                "total": r["total"],
                "pass_rate": r["pass_rate"],
                "all_passed": r["passed"] == r["total"],
                "ran_at": r["ran_at"].isoformat(),
            }
            for r in runs
        ]
    }


@router.get("/assertion-types")
async def get_assertion_types():
    """Reference: all supported assertion types and operators."""
    return {
        "success": True,
        "types": [
            {"value": "jsonpath",      "label": "JSONPath",       "description": "Extract and assert on JSON response data using JSONPath expressions"},
            {"value": "regex",         "label": "Regex Match",    "description": "Match regular expressions against page content or response body"},
            {"value": "status_code",   "label": "Status Code",    "description": "Assert on HTTP response status code"},
            {"value": "response_time", "label": "Response Time",  "description": "Assert on TTFB or total load time in milliseconds"},
            {"value": "contains",      "label": "Contains",       "description": "Assert page content contains a string"},
            {"value": "not_contains",  "label": "Not Contains",   "description": "Assert page content does not contain a string"},
            {"value": "header",        "label": "Header",         "description": "Assert on HTTP response header value"},
        ],
        "operators": [
            {"value": "equals",     "label": "Equals",          "types": ["all"]},
            {"value": "contains",   "label": "Contains",        "types": ["jsonpath", "regex", "header"]},
            {"value": "matches",    "label": "Regex matches",   "types": ["jsonpath", "header", "status_code"]},
            {"value": "gt",         "label": "> Greater than",  "types": ["status_code", "response_time", "jsonpath"]},
            {"value": "lt",         "label": "< Less than",     "types": ["status_code", "response_time", "jsonpath"]},
            {"value": "gte",        "label": ">= Greater or eq","types": ["status_code", "response_time", "jsonpath"]},
            {"value": "lte",        "label": "<= Less or eq",   "types": ["status_code", "response_time", "jsonpath"]},
            {"value": "exists",     "label": "Exists",          "types": ["jsonpath", "header"]},
            {"value": "not_exists", "label": "Does not exist",  "types": ["jsonpath", "header"]},
        ]
    }

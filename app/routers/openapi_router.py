"""
Phase 8E: OpenAPI/Swagger Auto-Import Router
Parses OpenAPI 2.0 / 3.x specs and generates TestVerse test cases
"""
from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import httpx, json, yaml, uuid
from datetime import datetime

router = APIRouter(prefix="/api/openapi", tags=["Phase 8E - OpenAPI Import"])


# ─── Models ───────────────────────────────────────────────────────────────────

class ImportFromURLRequest(BaseModel):
    url: str
    base_url: Optional[str] = None
    tag_filter: Optional[List[str]] = None   # only import endpoints with these tags

class GeneratedTest(BaseModel):
    id: str
    name: str
    method: str
    url: str
    headers: Dict[str, str]
    body: Optional[Dict[str, Any]]
    expected_status: int
    description: str
    tags: List[str]

class ImportResult(BaseModel):
    import_id: str
    spec_title: str
    spec_version: str
    total_endpoints: int
    generated_tests: List[GeneratedTest]
    warnings: List[str]
    imported_at: str


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _resolve_ref(spec: dict, ref: str) -> dict:
    """Resolve a $ref like '#/components/schemas/User'"""
    parts = ref.lstrip("#/").split("/")
    node = spec
    for p in parts:
        node = node.get(p, {})
    return node


def _schema_to_example(spec: dict, schema: dict, depth: int = 0) -> Any:
    """Recursively build an example payload from a JSON Schema node."""
    if depth > 4:
        return None
    if "$ref" in schema:
        schema = _resolve_ref(spec, schema["$ref"])
    if "example" in schema:
        return schema["example"]
    t = schema.get("type", "object")
    if t == "object":
        result = {}
        for prop, pschema in schema.get("properties", {}).items():
            result[prop] = _schema_to_example(spec, pschema, depth + 1)
        return result
    if t == "array":
        items = schema.get("items", {})
        return [_schema_to_example(spec, items, depth + 1)]
    if t == "string":
        fmt = schema.get("format", "")
        if fmt == "email":   return "user@example.com"
        if fmt == "date":    return "2024-01-01"
        if fmt == "date-time": return "2024-01-01T00:00:00Z"
        if fmt == "uri":     return "https://example.com"
        if fmt == "uuid":    return str(uuid.uuid4())
        if "enum" in schema: return schema["enum"][0]
        return schema.get("default", "string")
    if t == "integer": return schema.get("default", 0)
    if t == "number":  return schema.get("default", 0.0)
    if t == "boolean": return schema.get("default", True)
    return None


def _extract_request_body(spec: dict, operation: dict) -> Optional[dict]:
    """Pull out a sample request body from an OpenAPI 3 operation."""
    rb = operation.get("requestBody", {})
    if "$ref" in rb:
        rb = _resolve_ref(spec, rb["$ref"])
    content = rb.get("content", {})
    for mime in ("application/json", "application/x-www-form-urlencoded"):
        if mime in content:
            schema = content[mime].get("schema", {})
            if "$ref" in schema:
                schema = _resolve_ref(spec, schema["$ref"])
            return _schema_to_example(spec, schema)
    return None


def _parse_openapi3(spec: dict, base_url: str, tag_filter: Optional[List[str]]) -> tuple:
    tests: List[GeneratedTest] = []
    warnings: List[str] = []
    info = spec.get("info", {})

    servers = spec.get("servers", [])
    if not base_url:
        base_url = servers[0].get("url", "https://api.example.com") if servers else "https://api.example.com"

    for path, path_item in spec.get("paths", {}).items():
        for method in ("get", "post", "put", "patch", "delete", "head", "options"):
            operation = path_item.get(method)
            if not operation:
                continue
            tags = operation.get("tags", ["untagged"])
            if tag_filter and not any(t in tags for t in tag_filter):
                continue

            op_id = operation.get("operationId", f"{method}_{path.replace('/', '_')}")
            summary = operation.get("summary", op_id)
            responses = operation.get("responses", {})
            expected = 200
            for code in ("200", "201", "204", "default"):
                if code in responses and code != "default":
                    expected = int(code)
                    break

            # Build sample URL (replace path params)
            sample_url = base_url.rstrip("/") + path
            for param in operation.get("parameters", []) + path_item.get("parameters", []):
                if "$ref" in param:
                    param = _resolve_ref(spec, param["$ref"])
                if param.get("in") == "path":
                    name = param["name"]
                    schema = param.get("schema", {})
                    example = schema.get("example") or schema.get("default") or f"example_{name}"
                    sample_url = sample_url.replace("{" + name + "}", str(example))

            body = None
            if method in ("post", "put", "patch"):
                body = _extract_request_body(spec, operation)

            tests.append(GeneratedTest(
                id=str(uuid.uuid4()),
                name=summary,
                method=method.upper(),
                url=sample_url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                body=body,
                expected_status=expected,
                description=operation.get("description", summary),
                tags=tags,
            ))

    return tests, warnings, info


def _parse_swagger2(spec: dict, base_url: str, tag_filter: Optional[List[str]]) -> tuple:
    tests: List[GeneratedTest] = []
    warnings: List[str] = []
    info = spec.get("info", {})

    if not base_url:
        host = spec.get("host", "localhost")
        base_path = spec.get("basePath", "/")
        schemes = spec.get("schemes", ["https"])
        base_url = f"{schemes[0]}://{host}{base_path}"

    for path, path_item in spec.get("paths", {}).items():
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if not operation:
                continue
            tags = operation.get("tags", ["untagged"])
            if tag_filter and not any(t in tags for t in tag_filter):
                continue

            summary = operation.get("summary", operation.get("operationId", f"{method} {path}"))
            responses = operation.get("responses", {})
            expected = 200
            for code in ("200", "201", "204"):
                if code in responses:
                    expected = int(code)
                    break

            sample_url = base_url.rstrip("/") + path
            body = None
            for param in operation.get("parameters", []):
                if param.get("in") == "path":
                    name = param["name"]
                    sample_url = sample_url.replace("{" + name + "}", f"1")
                if param.get("in") == "body":
                    schema = param.get("schema", {})
                    if "$ref" in schema:
                        schema = _resolve_ref(spec, schema["$ref"])
                    body = _schema_to_example(spec, schema)

            tests.append(GeneratedTest(
                id=str(uuid.uuid4()),
                name=summary,
                method=method.upper(),
                url=sample_url,
                headers={"Content-Type": "application/json"},
                body=body,
                expected_status=expected,
                description=operation.get("description", summary),
                tags=tags,
            ))

    return tests, warnings, info


def _parse_spec(spec: dict, base_url: Optional[str], tag_filter: Optional[List[str]]) -> ImportResult:
    if "openapi" in spec:
        tests, warnings, info = _parse_openapi3(spec, base_url or "", tag_filter)
    elif "swagger" in spec:
        tests, warnings, info = _parse_swagger2(spec, base_url or "", tag_filter)
    else:
        raise HTTPException(status_code=400, detail="Not a valid OpenAPI/Swagger spec")

    return ImportResult(
        import_id=str(uuid.uuid4()),
        spec_title=info.get("title", "Untitled API"),
        spec_version=info.get("version", "unknown"),
        total_endpoints=len(tests),
        generated_tests=tests,
        warnings=warnings,
        imported_at=datetime.utcnow().isoformat(),
    )


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/import/url", response_model=ImportResult, summary="Import spec from URL")
async def import_from_url(req: ImportFromURLRequest):
    """Fetch an OpenAPI/Swagger spec from a public URL and generate tests."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(req.url, follow_redirects=True)
            r.raise_for_status()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch spec: {e}")

    ct = r.headers.get("content-type", "")
    try:
        if "yaml" in ct or req.url.endswith((".yaml", ".yml")):
            spec = yaml.safe_load(r.text)
        else:
            spec = r.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Could not parse spec as JSON or YAML")

    return _parse_spec(spec, req.base_url, req.tag_filter)


@router.post("/import/file", response_model=ImportResult, summary="Import spec from uploaded file")
async def import_from_file(
    file: UploadFile = File(...),
    base_url: Optional[str] = None,
):
    """Upload a .json or .yaml OpenAPI/Swagger file and generate tests."""
    content = await file.read()
    try:
        if file.filename.endswith((".yaml", ".yml")):
            spec = yaml.safe_load(content)
        else:
            spec = json.loads(content)
    except Exception:
        raise HTTPException(status_code=400, detail="Could not parse uploaded file")

    return _parse_spec(spec, base_url, None)


@router.post("/import/text", response_model=ImportResult, summary="Import spec from raw JSON/YAML text")
async def import_from_text(payload: Dict[str, Any]):
    """Paste raw spec JSON directly and generate tests."""
    return _parse_spec(payload, None, None)


@router.get("/popular-specs", summary="Return a list of popular public API specs to try")
async def popular_specs():
    return {
        "specs": [
            {"name": "Petstore (OpenAPI 3)", "url": "https://petstore3.swagger.io/api/v3/openapi.json"},
            {"name": "Petstore (Swagger 2)", "url": "https://petstore.swagger.io/v2/swagger.json"},
            {"name": "GitHub REST API", "url": "https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json"},
        ]
    }

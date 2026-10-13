"""Tools the agent can use: API operations discovered from OpenAPI, plus file tools.

Reliability rules live here, in code, not in the prompt:
  - reads (GET) are retried automatically on 5xx / timeouts
  - writes carry an Idempotency-Key derived from (run, tool, args), so retrying them
    can never create a duplicate; they are retried on the same transient errors
  - if a write still fails, the result says the outcome is UNKNOWN so the agent
    checks state before trying again
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

RETRIES = 2
TEXT_SUFFIXES = {".txt", ".csv", ".json", ".md"}
DOC_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


@dataclass
class ToolResult:
    ok: bool
    output: Any = None
    error: str | None = None
    retries: int = 0
    status: int | None = None
    notes: list[str] = field(default_factory=list)

    def text(self, limit: int = 2500) -> str:
        body = json.dumps(self.output, ensure_ascii=False) if self.ok and not isinstance(self.output, str) \
            else (self.output if self.ok else f"ERROR: {self.error}")
        if len(body) > limit:
            body = body[:limit] + f"... [truncated, {len(body) - limit} more chars]"
        extra = "".join(f" ({n})" for n in self.notes)
        return f"{body}{extra}"


@dataclass
class Tool:
    name: str
    effect: str  # "read" or "write"
    description: str
    signature: str
    run: Callable[[dict], ToolResult]

    def doc(self) -> str:
        return f"- {self.name} [{self.effect}] {self.description}\n    args: {self.signature}"


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self.tools = {t.name: t for t in tools}

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def read_only(self) -> "ToolRegistry":
        return ToolRegistry([t for t in self.tools.values() if t.effect == "read"])

    def docs(self) -> str:
        return "\n".join(t.doc() for t in self.tools.values())


# --------------------------------------------------------------------------- API tools from OpenAPI

def api_tools(base_url: str, openapi_path: str, run_id: str, http: httpx.Client | None = None) -> list[Tool]:
    http = http or httpx.Client(base_url=base_url, timeout=15)
    spec = _get_with_retry(http, openapi_path).json()
    components = spec.get("components", {}).get("schemas", {})
    tools = []
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            params = op.get("parameters", [])
            body_schema = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
            sig = [f"{p['name']}{'*' if p.get('required') else ''}: {_schema(p.get('schema', {}), components)}"
                   for p in params if p["in"] in ("path", "query")]
            if body_schema:
                sig.append(_schema(body_schema, components).strip("{}"))
            description = (op.get("description") or op.get("summary") or "").strip().splitlines()[0]
            tools.append(Tool(
                name=op["operationId"],
                effect="read" if method == "get" else "write",
                description=f"{method.upper()} {path} - {description}",
                signature="{" + ", ".join(s for s in sig if s) + "}",
                run=_api_caller(http, method.upper(), path, params, bool(body_schema), op["operationId"], run_id),
            ))
    return tools


def _api_caller(http: httpx.Client, method: str, path: str, params: list, has_body: bool, name: str, run_id: str):
    path_names = [p["name"] for p in params if p["in"] == "path"]
    query_names = [p["name"] for p in params if p["in"] == "query"]
    is_write = method != "GET"

    def run(args: dict) -> ToolResult:
        args = dict(args)
        url = path
        for p in path_names:
            if p not in args:
                return ToolResult(False, error=f"missing required argument '{p}'")
            url = url.replace("{" + p + "}", str(args.pop(p)))
        query = {k: args.pop(k) for k in query_names if k in args and args[k] is not None}
        if args and not has_body:
            return ToolResult(False, error=f"unknown argument(s) {sorted(args)} for {name}")
        headers = {}
        if is_write:
            canonical = json.dumps({"tool": name, "url": url, "body": args}, sort_keys=True)
            headers["Idempotency-Key"] = hashlib.sha256(f"{run_id}:{canonical}".encode()).hexdigest()[:32]

        notes, last_error, status = [], "", None
        for attempt in range(RETRIES + 1):
            if attempt:
                time.sleep(0.6 * 2 ** (attempt - 1))
            try:
                r = http.request(method, url, params=query or None, json=args if has_body else None, headers=headers)
            except httpx.HTTPError as e:
                last_error, status = f"network error: {type(e).__name__}", None
                notes.append(f"attempt {attempt + 1}: {last_error}")
                continue
            status = r.status_code
            if r.status_code >= 500 or r.status_code == 429:
                last_error = f"HTTP {r.status_code}: {_detail(r)}"
                notes.append(f"attempt {attempt + 1}: {last_error}")
                continue
            if attempt:
                notes = [f"succeeded after {attempt} automatic retr{'y' if attempt == 1 else 'ies'}: {'; '.join(notes)}"]
            if r.headers.get("Idempotent-Replay"):
                notes.append("server recognised the Idempotency-Key: this write had ALREADY been applied by an "
                             "earlier attempt; no duplicate was created")
            if r.status_code < 400:
                return ToolResult(True, output=_json_or_text(r), retries=attempt, status=r.status_code, notes=notes)
            return ToolResult(False, error=f"HTTP {r.status_code}: {_detail(r)}", retries=attempt, status=r.status_code)

        error = f"{last_error} (gave up after {RETRIES + 1} attempts)"
        if is_write:
            error += (". OUTCOME UNKNOWN: the write may or may not have been applied. Check the current state "
                      "with a read before deciding whether to retry.")
        return ToolResult(False, error=error, retries=RETRIES, status=status)

    return run


def _get_with_retry(http: httpx.Client, url: str) -> httpx.Response:
    for attempt in range(5):
        try:
            r = http.get(url)
            if r.status_code < 500:
                r.raise_for_status()
                return r
        except httpx.TransportError:
            if attempt == 4:
                raise
        time.sleep(1)
    raise RuntimeError(f"could not load {url}")


def _detail(r: httpx.Response) -> str:
    try:
        detail = r.json().get("detail", r.text)
    except (ValueError, AttributeError):
        detail = r.text
    if isinstance(detail, list):  # pydantic validation errors
        detail = "; ".join(f"{'.'.join(str(x) for x in d.get('loc', [])[1:])}: {d.get('msg')}" for d in detail)
    return str(detail)[:500]


def _json_or_text(r: httpx.Response):
    try:
        return r.json()
    except ValueError:
        return r.text


def _schema(s: dict, components: dict, depth: int = 0) -> str:
    """Render a JSON schema as a compact, model-friendly type signature."""
    if "$ref" in s:
        s = components[s["$ref"].split("/")[-1]]
    if "anyOf" in s:
        options = [o for o in s["anyOf"] if o.get("type") != "null"]
        return _schema(options[0], components, depth) if options else "null"
    if "enum" in s:
        return "one of " + "|".join(map(str, s["enum"]))
    kind = s.get("type")
    if kind == "object" and depth < 3:
        required = set(s.get("required", []))
        fields = []
        for name, sub in s.get("properties", {}).items():
            desc = f' "{sub["description"]}"' if sub.get("description") else ""
            fields.append(f"{name}{'*' if name in required else ''}: {_schema(sub, components, depth + 1)}{desc}")
        return "{" + ", ".join(fields) + "}"
    if kind == "array":
        return f"[{_schema(s.get('items', {}), components, depth + 1)}]"
    return kind or "any"


# --------------------------------------------------------------------------- file tools

def file_tools(root: Path, read_document: Callable[[bytes, str], str]) -> list[Tool]:
    cache: dict[str, str] = {}

    def resolve(rel: str) -> Path | None:
        path = (root / rel).resolve()
        return path if path.is_relative_to(root) else None

    def list_files(args: dict) -> ToolResult:
        folder = resolve(args.get("folder", ""))
        if not folder or not folder.is_dir():
            return ToolResult(False, error=f"no such folder: {args.get('folder')!r}")
        files = [{"path": p.relative_to(root).as_posix(), "bytes": p.stat().st_size,
                  "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(p.stat().st_mtime))}
                 for p in sorted(folder.rglob("*")) if p.is_file()]
        return ToolResult(True, output=files)

    def read_file(args: dict) -> ToolResult:
        path = resolve(str(args.get("path", "")))
        if not path or not path.is_file():
            return ToolResult(False, error=f"no such file: {args.get('path')!r} (use list_files)")
        suffix = path.suffix.lower()
        if suffix in TEXT_SUFFIXES:
            return ToolResult(True, output=path.read_text(encoding="utf-8", errors="replace")[:8000])
        if suffix not in DOC_SUFFIXES:
            return ToolResult(False, error=f"unsupported file type {suffix}")
        data = path.read_bytes()
        key = hashlib.sha256(data).hexdigest()
        if key not in cache:
            mime = "application/pdf" if suffix == ".pdf" else mimetypes.guess_type(path.name)[0] or "image/png"
            cache[key] = read_document(data, mime)
        return ToolResult(True, output=cache[key], notes=["transcribed from the scanned document by the vision model"])

    return [
        Tool("list_files", "read", "List the owner's documents (scanned bills, receipts, etc.).",
             '{folder: string "sub-folder, default all"}', list_files),
        Tool("read_file", "read", "Read a document. PDFs and photos are transcribed to text.",
             '{path*: string "path from list_files"}', read_file),
    ]

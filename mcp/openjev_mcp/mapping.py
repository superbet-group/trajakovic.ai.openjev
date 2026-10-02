"""OpenJev failures -> ToolError (spec 2.4). Pure: no I/O, never raises."""
from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from .errors import RETRYABLE, RETRY_ONCE, ToolError

DETAIL_CAP = 2048
TAGS = ("noul", "choice", "score")
PINNED = "Pinned Jev versions (jev-1.13.0) are not valid on OpenJev; use openjev-latest or openjev-0.1"
IMAGE_RULE = re.compile(r"at most \d+ images per request|image type .* is not supported|"
                        r"image data is not valid base64|image data is larger than|image is \d+ bytes")


def parse_retry_after(headers: Mapping[str, str]) -> float | None:
    raw = {str(k).lower(): v for k, v in headers.items()}.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(str(raw).strip())
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def parse_server_timing(value: str | None) -> dict[str, float] | None:
    if not value:
        return None
    out: dict[str, float] = {}
    for part in value.split(","):
        name, *params = (p.strip() for p in part.split(";"))
        for p in params:
            key, _, num = p.partition("=")
            if key.strip() == "dur":
                try:
                    dur = float(num.strip())
                except ValueError:
                    continue
                if name and math.isfinite(dur):
                    out[f"{name}_ms"] = dur
    return out or None


def _trim(detail: Any) -> Any:
    try:
        text = json.dumps(detail, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(detail)[:DETAIL_CAP]
    if len(text.encode("utf-8")) <= DETAIL_CAP:
        return detail
    return text.encode("utf-8")[:DETAIL_CAP].decode("utf-8", "ignore") + "..."


def _loc_path(loc: Sequence[Any]) -> tuple[str, str | None]:
    """('body','questions','sev','score','criteria') -> ('questions.sev.criteria', 'score')."""
    parts = [str(p) for p in loc]
    if parts and parts[0] == "body":
        parts = parts[1:]
    tag = None
    if len(parts) >= 3 and parts[0] == "questions" and parts[2] in TAGS:
        tag = parts.pop(2)
    return ".".join(parts), tag


def _validation(items: list) -> tuple[str, str, str | None]:
    first = next((e for e in items if isinstance(e, dict)), {})
    loc = first.get("loc") if isinstance(first.get("loc"), list) else []
    path, tag = _loc_path(loc)
    msg = str(first.get("msg") or first.get("type") or "invalid request")
    field = path.rsplit(".", 1)[-1] if path else ""
    hint = None
    if path == "questions":
        hint = "questions must be a non-empty object"
    elif path in ("samples", "steps", "think"):
        hint = {"samples": "samples must be 1-32", "steps": "steps must be 1-8", "think": "think must be 0-4096"}[path]
    elif field == "criteria" and tag == "score":
        hint = "score criteria must be a list of level strings, got an object"
    elif field == "criteria" and tag == "choice":
        hint = "choice needs criteria {option: description}; 'options' is not a field"
    elif field == "criteria" and tag == "noul":
        hint = "noul criteria is {true: ..., false: ...}"
    message = f"{path}: {msg}" if path else msg
    return message, hint, path or None


def _bad_request(text: str) -> tuple[str, str | None, str]:
    """A 400 plain-string detail against the 2.4 templates, in table order -> (code, message, hint)."""
    r = "OJ_REJECTED"
    if m := re.match(r"Choice question must have at least one choice: (.+)$", text):
        return r, f"question {m[1]} has no options", "If a filter left no candidates, skip the call and treat the answer as 'none'."
    if text.startswith("Too many choices for "):
        n = re.search(r"fit in (\d+)", text)
        return r, text, f"pre-filter the options to fit {n[1] if n else 'N'} label tokens on {text[21:].split(':')[0]}"
    if text.startswith("Too many choices"):
        return r, text, "pre-filter to <= 100 candidates (embeddings/keywords) or split into a tree (recipe taxonomy_classify)"
    if text.startswith("Too many score levels"):
        return r, text, "use <= 10 levels; for a 1-10 rating use 10 levels and add 1"
    if re.match(r"at most \d+ questions per request", text):
        return r, text, "split across requests (batch / filter do this)"
    if IMAGE_RULE.search(text):
        return r, text, "send <= 8 JPEG/PNG/WebP/GIF images <= 5 MB; ask_image re-encodes files for you"
    if re.search(r"(think|sequential) needs a text state", text):
        return r, text, "drop think/sequential for image reads, or convert the UI to text (accessibility tree) and think on that"
    if m := re.match(r"the request is (\d+) tokens; the limit is (\d+)", text):
        return ("OJ_TOO_LONG", f"state is {m[1]} tokens; the limit is {m[2]} (parsed from the server message)",
                "Chunk the state or pre-filter; one question per chunk")
    if m := re.match(r"(\S+) does not support (\w+)$", text):
        return r, text, f"{m[1]} does not support {m[2]}; drop it or use openjev-latest"
    if re.search(r"need \d+ label tokens; a read allows \d+", text):
        return r, text, "shorten option keys or split the questions across requests"
    if re.match(r"answer template is \d+ tokens", text):
        return r, text, "shorten question ids and option keys"
    if m := re.match(r"the model rejected this request: (.*)$", text, re.S):
        return r, text, f"rejected upstream: {m[1]}; report with request_id"
    return r, text, None


def _unknown_model(text: str, model: str | None, known: Sequence[str] | None) -> tuple[str, str]:
    m = re.match(r"Unknown model:\s*(.+)$", text)
    name = m[1].strip() if m else (model or "?")
    avail = f"Available: {', '.join(known)}. " if known else ""
    return f"unknown model {name}", f"{avail}{PINNED}"


def _classify(status: int, headers: Mapping[str, str], body: Any, *, base_url: str, model: str | None,
              has_images: bool, known_models: Sequence[str] | None) -> tuple[str, str, str | None, str | None, Any]:
    """-> (code, message, hint, path, detail)."""
    detail: Any = body
    etype = text = chat_code = None
    if isinstance(body, dict):
        if isinstance(body.get("error"), dict):
            err = body["error"]
            detail, chat_code, etype, text = err, err.get("code"), err.get("type"), err.get("message")
        elif "detail" in body:
            detail = body["detail"]
            if isinstance(detail, dict):
                etype, text = detail.get("error_type"), detail.get("message")
            elif isinstance(detail, str):
                text = detail
    elif isinstance(body, str):
        text = body
    text = text if isinstance(text, str) else None
    ctype = headers.get("content-type", "")

    if status == 401 and (etype == "authentication_error" or etype is None):
        return "OJ_AUTH", "API key rejected", f"Set OPENJEV_API_KEY for {base_url}", None, detail
    if status == 403 and etype == "authentication_error":
        return "OJ_AUTH", f"{base_url} requires an API key", "Set OPENJEV_API_KEY", None, detail
    if status == 403 and etype == "permission_error":
        return ("OJ_FORBIDDEN", f"{base_url} only accepts requests through its front proxy (origin secret)",
                "Point OPENJEV_BASE_URL at the proxy", None, detail)
    if status == 403:
        return "OJ_FORBIDDEN", text or f"{base_url} refused the request", "check OPENJEV_API_KEY and OPENJEV_ORIGIN_SECRET", None, detail
    if status == 413:
        m = re.search(r"larger than (\d+) bytes", text or "")
        n = m[1] if m else "the"
        return ("OJ_TOO_LARGE", text or "request body too large",
                f"body over the server's {n}-byte limit; send fewer/smaller images", None, detail)
    if status == 429:
        after = parse_retry_after(headers)
        return ("OJ_RATE_LIMITED", "rate limited",
                f"rate limited; retry after {after if after is not None else 'a moment'} s", None, detail)
    if status == 529 or etype == "overloaded_error":
        return "OJ_OVERLOADED", text or "OpenJev at capacity", "OpenJev at capacity; retry shortly", None, detail
    if status == 503:
        return "OJ_UNAVAILABLE", text or "inference backend unavailable", "backend down; retry shortly", None, detail
    if status == 404 and chat_code == "model_not_found":
        return "OJ_UNKNOWN_MODEL", text or "unknown chat model", "chat model must be diffusiongemma-26b", None, detail
    if status == 404:
        return ("OJ_NOT_FOUND", f"{base_url} answered 404", f"OPENJEV_BASE_URL points at something that is not OpenJev ({base_url})",
                None, detail)
    if status == 405:
        return "OJ_NOT_FOUND", f"{base_url} answered 405", "wrong verb or URL; check OPENJEV_BASE_URL", None, detail
    if status == 422 and isinstance(detail, list):
        message, hint, path = _validation(detail)
        return "OJ_VALIDATION", message, hint, path, detail
    if status == 400 and etype == "api_usage_error" and text:
        if text.startswith("Unknown model"):
            message, hint = _unknown_model(text, model, known_models)
            return "OJ_UNKNOWN_MODEL", message, hint, None, detail
        if text.startswith("Invalid request"):
            return "OJ_BAD_TYPE", text, "question type must be noul, choice or score", None, detail
    if status == 400 and text:
        code, message, hint = _bad_request(text)
        return code, message, hint, None, detail
    if status == 500 and (not isinstance(body, dict) or "text/plain" in ctype):
        if has_images:
            return ("OJ_BAD_IMAGE", "the server failed on a request with images",
                    "an image is not decodable (server bug 12.1). Re-encode it; ask_image does", None, detail)
        return "OJ_SERVER", "server error without request id", "retry once, then report", None, detail
    if status >= 500:
        gateway = status in (502, 504)
        return ("OJ_UNAVAILABLE" if gateway else "OJ_SERVER", text or f"OpenJev answered {status}",
                "gateway or backend down; retry shortly" if gateway else "retry once, then report", None, detail)
    if status >= 400:
        return "OJ_REJECTED", text or f"OpenJev answered {status}", None, None, detail
    return "OJ_PROTOCOL", f"unexpected status {status} from {base_url}", None, None, detail


def map_http_error(status: int, headers: Mapping[str, str], content: bytes, *, base_url: str,
                   model: str | None, has_images: bool, timeout_ms: int,
                   known_models: Sequence[str] | None = None) -> ToolError:
    try:
        hdrs = {str(k).lower(): v for k, v in headers.items()}
        raw = content.decode("utf-8", "replace") if content else ""
        try:
            body: Any = json.loads(raw)
        except ValueError:
            body = raw
        code, message, hint, path, detail = _classify(
            status, hdrs, body, base_url=base_url, model=model, has_images=has_images, known_models=known_models)
        after = parse_retry_after(hdrs)
        if after is None and code == "OJ_UNAVAILABLE":
            after = 2.0
        elif after is None and code == "OJ_OVERLOADED":
            after = 2.0 if isinstance(body, dict) and "error" in body else 1.0
        return ToolError(code, message, http_status=status, hint=hint, path=path,
                         retryable=code in RETRYABLE or code in RETRY_ONCE, retry_after_s=after,
                         request_id=hdrs.get("x-request-id"), server_detail=_trim(detail))
    except Exception as exc:
        return ToolError("OJ_INTERNAL", f"internal error in openjev-mcp: {type(exc).__name__}", http_status=status)


def map_transport_error(exc: BaseException, *, base_url: str, timeout_ms: int) -> ToolError:
    if isinstance(exc, httpx.ConnectTimeout) or not isinstance(exc, (httpx.TimeoutException, TimeoutError)) \
            and isinstance(exc, (httpx.HTTPError, OSError)):
        return ToolError("OJ_UNREACHABLE", f"no OpenJev at {base_url}", retryable=True,
                         hint="Start it (not from an agent): mise run start, or fix OPENJEV_BASE_URL")
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return ToolError("OJ_TIMEOUT", f"no answer from {base_url} in {timeout_ms} ms", retryable=True,
                         hint="the server is shared and serial on MLX. Raise timeout_ms or use samples:1")
    return ToolError("OJ_INTERNAL", f"internal error in openjev-mcp: {type(exc).__name__}")

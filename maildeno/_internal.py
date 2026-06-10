"""Shared internals used by both the sync and async clients."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any, Dict, Optional

import httpx

from ._error import MaildenoError
from ._types import DynamicData, TemplateJson

DEFAULT_BASE_URL  = "https://api.maildeno.com"
DEFAULT_TIMEOUT   = 30.0
TEMPLATE_PATH     = "/v1/sdk/template"

try:
    _VERSION = version("maildeno")
except PackageNotFoundError:
    _VERSION = "unknown"

USER_AGENT = f"sdk-python/{_VERSION}"


def normalise_base_url(base_url: Optional[str]) -> str:
    return (base_url or DEFAULT_BASE_URL).rstrip("/")


def build_headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }


def parse_template_response(payload: Any) -> TemplateJson:
    """Validate and return the raw template JSON from the API."""
    if not isinstance(payload, dict):
        raise MaildenoError(
            "UNKNOWN",
            f"Unexpected response shape: {type(payload).__name__}",
        )
    # Minimal required fields — engine validates the rest at render time
    if "template_id" not in payload or "rows" not in payload:
        raise MaildenoError(
            "UNKNOWN",
            "Template response missing required fields (template_id, rows).",
        )
    return payload  # type: ignore[return-value]


def raise_for_response(response: httpx.Response) -> None:
    """Raise :class:`MaildenoError` on non-2xx responses."""
    if response.is_success:
        return

    detail: Any = None
    try:
        body = response.json()
        if isinstance(body, dict):
            detail = body.get("detail")
    except (ValueError, httpx.DecodingError):
        pass

    raise MaildenoError.from_status(response.status_code, detail)


def map_transport_error(exc: Exception) -> MaildenoError:
    """Translate an httpx transport / timeout exception into a MaildenoError."""
    if isinstance(exc, httpx.TimeoutException):
        return MaildenoError("TIMEOUT", f"Request timed out: {exc}")
    if isinstance(exc, httpx.HTTPError):
        return MaildenoError("NETWORK_ERROR", str(exc) or "Network request failed")
    return MaildenoError("NETWORK_ERROR", str(exc) or "Network request failed")


def normalise_dynamic_data(data: DynamicData) -> Dict[str, Any]:
    """Return a dict containing only the sub-groups the caller populated."""
    merge_tags_in = data.get("merge_tags") or {}
    merge_tags: Dict[str, Dict[str, str]] = {}

    for key in ("text", "url", "attr"):
        val = merge_tags_in.get(key)  # type: ignore[literal-required]
        if val:
            merge_tags[key] = val

    result: Dict[str, Any] = {}
    if merge_tags:
        result["merge_tags"] = merge_tags

    context = data.get("context")
    if context:
        result["context"] = context

    return result

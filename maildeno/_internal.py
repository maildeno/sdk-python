"""Shared internals used by both the sync and async clients."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any, Dict, Optional

import httpx

from ._error import MaildenoError
from ._types import DynamicData, RenderResult, RenderTarget

DEFAULT_BASE_URL = "https://api.maildeno.com"
DEFAULT_TIMEOUT = 30.0
RENDER_PATH = "/v1/sdk/render"

try:
    _VERSION = version("maildeno")
except PackageNotFoundError:
    _VERSION = "unknown"

USER_AGENT = f"sdk-python/{_VERSION}"


def normalise_base_url(base_url: Optional[str]) -> str:
    """Strip a trailing slash from ``base_url`` and apply the default."""
    return (base_url or DEFAULT_BASE_URL).rstrip("/")


def build_headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }


def build_render_body(
    template_id: str,
    target: RenderTarget,
    dynamic_data: Optional[DynamicData],
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "template_id": template_id,
        "target": target,
    }

    if dynamic_data is not None:
        normalised = _normalise_dynamic_data(dynamic_data)
        if normalised:
            body["dynamic_data"] = normalised

    return body


def parse_render_response(payload: Any) -> RenderResult:
    if not isinstance(payload, dict):
        raise MaildenoError(
            "UNKNOWN",
            f"Unexpected response shape: {type(payload).__name__}",
        )
    try:
        return RenderResult(
            template_id=payload["template_id"],
            target=payload["target"],
            output=payload["output"],
        )
    except KeyError as exc:
        raise MaildenoError(
            "UNKNOWN",
            f"Missing field in response: {exc.args[0]}",
        ) from exc


def raise_for_response(response: httpx.Response) -> None:
    """Inspect a response and raise :class:`MaildenoError` on non-2xx."""
    if response.is_success:
        return

    detail: Any = None
    try:
        body = response.json()
        if isinstance(body, dict):
            detail = body.get("detail")
    except (ValueError, httpx.DecodingError):
        # Body wasn't JSON — fall back to status-based message.
        pass

    raise MaildenoError.from_status(response.status_code, detail)


def map_transport_error(exc: Exception) -> MaildenoError:
    """Translate an httpx transport / timeout exception into a MaildenoError."""
    if isinstance(exc, httpx.TimeoutException):
        return MaildenoError("TIMEOUT", f"Request timed out: {exc}")
    if isinstance(exc, httpx.HTTPError):
        return MaildenoError("NETWORK_ERROR", str(exc) or "Network request failed")
    return MaildenoError("NETWORK_ERROR", str(exc) or "Network request failed")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _normalise_dynamic_data(data: DynamicData) -> Dict[str, Any]:
    """Return a dict containing only the sub-groups the caller actually populated."""
    merge_tags_in = data.get("merge_tags") or {}
    merge_tags: Dict[str, Dict[str, str]] = {}

    text = merge_tags_in.get("text")
    if text:
        merge_tags["text"] = text
    url = merge_tags_in.get("url")
    if url:
        merge_tags["url"] = url
    attr = merge_tags_in.get("attr")
    if attr:
        merge_tags["attr"] = attr

    result: Dict[str, Any] = {}
    if merge_tags:
        result["merge_tags"] = merge_tags

    context = data.get("context")
    if context:
        result["context"] = context

    return result
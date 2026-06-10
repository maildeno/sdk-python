"""Public types for the Maildeno SDK."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Union

if sys.version_info >= (3, 11):
    from typing import Literal, NotRequired, TypedDict
else:
    from typing_extensions import Literal, NotRequired, TypedDict


# ── Render target ─────────────────────────────────────────────────────────────

RenderTarget = Literal["html", "react-email", "mjml"]


# ── Dynamic data — all fields optional ────────────────────────────────────────

class MergeTagGroup(TypedDict, total=False):
    """Merge tag values, split by escaping rule."""

    #: Resolved into paragraph / heading / list / button text. HTML-escaped.
    text: Dict[str, str]
    #: Resolved into href / src attributes. URL-encoded.
    url: Dict[str, str]
    #: Resolved into HTML attribute values (alt, aria-label, ...). Attribute-safe.
    attr: Dict[str, str]


#: Runtime context value. JSON-serialisable scalar used by visibility rules.
ContextValue = Union[str, int, float, bool]


class DynamicData(TypedDict, total=False):
    """Optional dynamic data passed to render."""

    #: Merge tag values, split by type. All sub-groups are optional.
    merge_tags: MergeTagGroup
    #: Runtime context used for visibility rules (show / hide rows).
    context: Dict[str, ContextValue]


# ── Template JSON returned by GET /v1/sdk/template/{id} ──────────────────────

class TemplateJson(TypedDict, total=False):
    """Shape of the raw template payload returned by the Maildeno API."""

    template_id: str
    template_name: str
    canvas: Dict[str, Any]
    rows: List[Any]
    schema_version: str


# ── Cache configuration ───────────────────────────────────────────────────────

class CacheConfig(TypedDict, total=False):
    """Cache strategy configuration.

    Example — memory with custom TTL::

        cache={"ttl": 60_000}

    Example — persistent disk cache::

        cache={"type": "disk", "path": "/var/cache/maildeno", "ttl": 300_000}
    """

    #: Storage strategy. ``"memory"`` (default) or ``"disk"``.
    type: Literal["memory", "disk"]

    #: Directory used when ``type="disk"``.
    #: Absolute or relative to ``os.getcwd()``. Created automatically on first write.
    #: Default: ``".maildeno-cache"``.
    path: str

    #: How long a cached template is considered fresh (milliseconds).
    #: After this period a re-fetch is attempted; stale copy used if server unreachable.
    #: Default: ``300_000`` (5 minutes).
    ttl: int

    #: Maximum number of template entries to hold.
    #: Oldest entry evicted when the limit is reached.
    #: Default: ``50``.
    max_entries: int


# ── Render result ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RenderResult:
    """Result returned by :meth:`MaildenoClient.render`."""

    template_id: str
    target: RenderTarget
    #: The rendered output string (HTML, TSX, or MJML).
    output: str
    #: True when rendered from a stale cache entry because the server was
    #: unreachable. The output is still valid — use this flag to log/alert.
    from_stale_cache: bool = field(default=False)


# ── Validation issues (422 from FastAPI / pydantic) ──────────────────────────

class ValidationIssue(TypedDict, total=False):
    """A single pydantic validation error."""

    type: str
    loc: List[Any]
    msg: str
    input: Any


# ── Error codes ───────────────────────────────────────────────────────────────

SdkErrorCode = Literal[
    "INVALID_API_KEY",
    "FORBIDDEN",
    "TEMPLATE_NOT_FOUND",
    "RENDER_ERROR",
    "NETWORK_ERROR",
    "TIMEOUT",
    "UNKNOWN",
]


# ── Re-exports ────────────────────────────────────────────────────────────────

__all__ = [
    "CacheConfig",
    "ContextValue",
    "DynamicData",
    "MergeTagGroup",
    "RenderResult",
    "RenderTarget",
    "SdkErrorCode",
    "TemplateJson",
    "ValidationIssue",
]

_ = NotRequired  # help static checkers that don't see the conditional import

"""Public types for the Maildeno SDK."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Union

if sys.version_info >= (3, 11):
    from typing import Literal, NotRequired, TypedDict
else:
    from typing_extensions import Literal, NotRequired, TypedDict


# ── Render target ─────────────────────────────────────────────────────────────

RenderTarget = Literal["html", "react-email", "mjml"]


# ── Dynamic data — all fields optional ────────────────────────────────────────
#
# Pass only what you need. Everything defaults to {}.
#
# Examples:
#   {}                                            no merge tags, no context
#   {"merge_tags": {"text": {"name": "Noruwa"}}}
#   {"context": {"plan": "pro"}}
#   {"merge_tags": {"text": {...}, "url": {...}}, "context": {"country": "usa"}}


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


# ── Render result ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RenderResult:
    """Result returned by :meth:`MaildenoClient.render`."""

    template_id: str
    target: RenderTarget
    #: The rendered output string (HTML, TSX, or MJML).
    output: str


# ── Validation issues (422 from FastAPI / pydantic) ──────────────────────────


class ValidationIssue(TypedDict, total=False):
    """A single pydantic validation error.

    Returned by FastAPI inside the ``detail`` array of a 422 response.

    Example::

        {
            "type": "uuid_parsing",
            "loc":  ["body", "template_id"],
            "msg":  "Input should be a valid UUID, ...",
            "input": "not-a-uuid",
        }
    """

    type: str
    loc: List[Any]
    msg: str
    input: Any


# ── Error codes ───────────────────────────────────────────────────────────────

SdkErrorCode = Literal[
    "INVALID_API_KEY",     # 401  bad or missing key
    "FORBIDDEN",           # 403  key lacks scope for the requested target
    "TEMPLATE_NOT_FOUND",  # 404  template_id not in DB
    "RENDER_ERROR",        # 422  builder / validation failed
    "NETWORK_ERROR",       # transport / DNS / connection failure
    "TIMEOUT",             # request exceeded timeout
    "UNKNOWN",
]


# ── Re-exports kept tidy ──────────────────────────────────────────────────────

__all__ = [
    "ContextValue",
    "DynamicData",
    "MergeTagGroup",
    "RenderResult",
    "RenderTarget",
    "SdkErrorCode",
    "ValidationIssue",
]

# Help static checkers that don't see the conditional import above.
_ = NotRequired

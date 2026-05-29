"""Error class for the Maildeno SDK."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ._types import SdkErrorCode, ValidationIssue


class MaildenoError(Exception):
    """All errors raised by the Maildeno SDK are instances of this class.

    Example::

        from maildeno import MaildenoClient, MaildenoError

        try:
            client.render(template_id="...")
        except MaildenoError as err:
            print(err.code, err.message, err.status)
            # For validation errors (422 from malformed input), inspect err.issues
            if err.issues:
                for issue in err.issues:
                    print(issue["loc"], issue["msg"])
    """

    #: Machine-readable error code.
    code: SdkErrorCode
    #: Human-readable detail from the API (or a fallback like ``"HTTP 500"``).
    message: str
    #: HTTP status code. ``0`` for ``NETWORK_ERROR`` / ``TIMEOUT``.
    status: int
    #: Structured validation issues, when the API returned a list of pydantic
    #: errors (422 from malformed request data, e.g. a non-UUID ``template_id``).
    #: ``None`` for all other error shapes.
    issues: Optional[List[ValidationIssue]]

    def __init__(
        self,
        code: SdkErrorCode,
        message: str,
        status: int = 0,
        issues: Optional[List[ValidationIssue]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.issues = issues

    def __repr__(self) -> str:
        return (
            f"MaildenoError(code={self.code!r}, status={self.status}, "
            f"message={self.message!r})"
        )

    @classmethod
    def from_status(cls, status: int, detail: Any) -> MaildenoError:
        """Build a :class:`MaildenoError` from an HTTP status and the raw
        ``detail`` field of the API response.

        ``detail`` may be:

        - a string  (FastAPI ``HTTPException`` — your custom 401/403/404 messages)
        - a list    (FastAPI ``RequestValidationError`` — pydantic issues)
        - missing / None — falls back to ``"HTTP <status>"``
        """
        code = _STATUS_TO_CODE.get(status, "UNKNOWN")
        message, issues = _format_detail(detail, status)
        return cls(code, message, status, issues)


# ── Internals ─────────────────────────────────────────────────────────────────

_STATUS_TO_CODE: Dict[int, SdkErrorCode] = {
    401: "INVALID_API_KEY",
    403: "FORBIDDEN",
    404: "TEMPLATE_NOT_FOUND",
    422: "RENDER_ERROR",
}


def _format_detail(
    detail: Any,
    status: int,
) -> Tuple[str, Optional[List[ValidationIssue]]]:
    """Normalise ``detail`` into a readable message + optional issues list."""

    # String — FastAPI HTTPException(...) path. Use as-is.
    if isinstance(detail, str) and detail:
        return detail, None

    # List — FastAPI RequestValidationError path. Each entry has msg + loc.
    if isinstance(detail, list) and detail and _is_validation_issue_list(detail):
        issues: List[ValidationIssue] = detail
        parts: List[str] = []
        for issue in issues:
            loc = issue.get("loc", [])
            # Drop the leading "body" — it's noise to API consumers.
            if loc and loc[0] == "body":
                loc = loc[1:]
            path = ".".join(str(p) for p in loc)
            msg = issue.get("msg", "")
            parts.append(f"{path}: {msg}" if path else msg)
        return "; ".join(parts), issues

    # Object / None / weird — fall back to a status-based message.
    return f"HTTP {status}", None


def _is_validation_issue_list(items: List[Any]) -> bool:
    return all(isinstance(i, dict) and isinstance(i.get("msg"), str) for i in items)


__all__ = ["MaildenoError"]

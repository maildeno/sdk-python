"""Official Python SDK for the Maildeno render API.

Quick start::

    from maildeno import MaildenoClient

    client = MaildenoClient(api_key="sk_live_...")
    html = client.render_html("550e8400-e29b-41d4-a716-446655440000")
"""

from importlib.metadata import PackageNotFoundError, version

from ._async_client import AsyncMaildenoClient
from ._client import MaildenoClient
from ._error import MaildenoError
from ._types import (
    ContextValue,
    DynamicData,
    MergeTagGroup,
    RenderResult,
    RenderTarget,
    SdkErrorCode,
    ValidationIssue,
)

try:
    __version__ = version("maildeno")
except PackageNotFoundError:
    __version__ = "unknown"

__all__ = [
    "AsyncMaildenoClient",
    "ContextValue",
    "DynamicData",
    "MaildenoClient",
    "MaildenoError",
    "MergeTagGroup",
    "RenderResult",
    "RenderTarget",
    "SdkErrorCode",
    "ValidationIssue",
    "__version__",
]
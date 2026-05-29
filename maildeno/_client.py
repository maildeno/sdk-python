"""Synchronous Maildeno client."""

from __future__ import annotations

from types import TracebackType
from typing import Optional, Type

import httpx

from ._error import MaildenoError
from ._internal import (
    DEFAULT_TIMEOUT,
    RENDER_PATH,
    build_headers,
    build_render_body,
    map_transport_error,
    normalise_base_url,
    parse_render_response,
    raise_for_response,
)
from ._types import DynamicData, RenderResult, RenderTarget


class MaildenoClient:
    """The main entry point for the Maildeno SDK.

    Example::

        from maildeno import MaildenoClient

        client = MaildenoClient(
            api_key="sk_live_4a7f2c8d...",
        )

        result = client.render(
            template_id="550e8400-e29b-41d4-a716-446655440000",
            target="html",
            dynamic_data={
                "merge_tags": {"text": {"name": "Noruwa"}},
                "context": {"plan": "pro"},
            },
        )
        print(result.output)  # full HTML string

    The client is reusable across many requests. For long-lived applications,
    instantiate one client at startup and reuse it (an underlying connection
    pool is kept warm). It can also be used as a context manager::

        with MaildenoClient(api_key="...") as client:
            html = client.render_html("template-id")
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        if not api_key:
            raise MaildenoError("INVALID_API_KEY", "api_key is required.")

        self._api_key = api_key
        self._base_url = normalise_base_url(base_url)
        self._timeout = timeout

        if http_client is not None:
            self._http = http_client
            self._owns_http = False
        else:
            self._http = httpx.Client(timeout=timeout)
            self._owns_http = True

    # ── Public API ────────────────────────────────────────────────────────────

    def render(
        self,
        *,
        template_id: str,
        target: RenderTarget = "html",
        dynamic_data: Optional[DynamicData] = None,
    ) -> RenderResult:
        """Render a template to HTML, React Email TSX, or MJML.

        :param template_id:  UUID of the template (required).
        :param target:       ``"html"`` | ``"react-email"`` | ``"mjml"``. Defaults to ``"html"``.
        :param dynamic_data: Merge tags + visibility context (fully optional).
        :raises MaildenoError: On any non-2xx response or transport failure.
        """
        body = build_render_body(template_id, target, dynamic_data)
        payload = self._post(RENDER_PATH, body)
        return parse_render_response(payload)

    def render_html(
        self,
        template_id: str,
        dynamic_data: Optional[DynamicData] = None,
    ) -> str:
        """Convenience: render to HTML, returning the output string directly.

        Example::

            html = client.render_html(
                "550e8400-...",
                {"merge_tags": {"text": {"name": "Noruwa"}}},
            )
        """
        return self.render(
            template_id=template_id, target="html", dynamic_data=dynamic_data
        ).output

    def render_react(
        self,
        template_id: str,
        dynamic_data: Optional[DynamicData] = None,
    ) -> str:
        """Convenience: render to React Email TSX."""
        return self.render(
            template_id=template_id, target="react-email", dynamic_data=dynamic_data
        ).output

    def render_mjml(
        self,
        template_id: str,
        dynamic_data: Optional[DynamicData] = None,
    ) -> str:
        """Convenience: render to MJML."""
        return self.render(
            template_id=template_id, target="mjml", dynamic_data=dynamic_data
        ).output

    def close(self) -> None:
        """Close the underlying HTTP client.

        Only closes the client if this :class:`MaildenoClient` created it.
        If you injected your own ``http_client``, it's your responsibility to close it.
        """
        if self._owns_http:
            self._http.close()

    # ── Context-manager protocol ──────────────────────────────────────────────

    def __enter__(self) -> MaildenoClient:
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _post(self, path: str, body: object) -> object:
        url = f"{self._base_url}{path}"
        try:
            response = self._http.post(
                url,
                json=body,
                headers=build_headers(self._api_key),
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise map_transport_error(exc) from exc
        except Exception as exc:  # pragma: no cover  — defensive
            raise map_transport_error(exc) from exc

        raise_for_response(response)
        return response.json()


__all__ = ["MaildenoClient"]

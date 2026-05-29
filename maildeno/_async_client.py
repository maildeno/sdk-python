"""Asynchronous Maildeno client."""

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


class AsyncMaildenoClient:
    """Asynchronous Maildeno client. Mirrors :class:`MaildenoClient`.

    Example::

        import asyncio
        from maildeno import AsyncMaildenoClient

        async def main():
            async with AsyncMaildenoClient(api_key="sk_live_...") as client:
                html = await client.render_html("550e8400-...")
                print(html)

        asyncio.run(main())

    For FastAPI / Starlette / aiohttp servers, instantiate one
    :class:`AsyncMaildenoClient` per process at startup and reuse it. Closing
    is only required when shutting down.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: Optional[httpx.AsyncClient] = None,
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
            self._http = httpx.AsyncClient(timeout=timeout)
            self._owns_http = True

    # ── Public API ────────────────────────────────────────────────────────────

    async def render(
        self,
        *,
        template_id: str,
        target: RenderTarget = "html",
        dynamic_data: Optional[DynamicData] = None,
    ) -> RenderResult:
        """Render a template to HTML, React Email TSX, or MJML."""
        body = build_render_body(template_id, target, dynamic_data)
        payload = await self._post(RENDER_PATH, body)
        return parse_render_response(payload)

    async def render_html(
        self,
        template_id: str,
        dynamic_data: Optional[DynamicData] = None,
    ) -> str:
        """Convenience: render to HTML, returning the output string directly."""
        result = await self.render(
            template_id=template_id, target="html", dynamic_data=dynamic_data
        )
        return result.output

    async def render_react(
        self,
        template_id: str,
        dynamic_data: Optional[DynamicData] = None,
    ) -> str:
        """Convenience: render to React Email TSX."""
        result = await self.render(
            template_id=template_id, target="react-email", dynamic_data=dynamic_data
        )
        return result.output

    async def render_mjml(
        self,
        template_id: str,
        dynamic_data: Optional[DynamicData] = None,
    ) -> str:
        """Convenience: render to MJML."""
        result = await self.render(
            template_id=template_id, target="mjml", dynamic_data=dynamic_data
        )
        return result.output

    async def aclose(self) -> None:
        """Close the underlying HTTP client.

        Only closes the client if this :class:`AsyncMaildenoClient` created it.
        """
        if self._owns_http:
            await self._http.aclose()

    # ── Async context-manager protocol ────────────────────────────────────────

    async def __aenter__(self) -> AsyncMaildenoClient:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.aclose()

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _post(self, path: str, body: object) -> object:
        url = f"{self._base_url}{path}"
        try:
            response = await self._http.post(
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


__all__ = ["AsyncMaildenoClient"]

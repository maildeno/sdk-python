"""Asynchronous Maildeno client."""

from __future__ import annotations

import asyncio
import functools
import warnings
from types import TracebackType
from typing import List, Optional, Type

import httpx

from ._cache import build_cache
from ._minify import minify_output
from ._error import MaildenoError
from ._internal import (
    DEFAULT_TIMEOUT,
    TEMPLATE_PATH,
    build_headers,
    map_transport_error,
    normalise_base_url,
    normalise_dynamic_data,
    parse_template_response,
    raise_for_response,
)
from ._renderer import render_template
from ._types import CacheConfig, DynamicData, RenderResult, RenderTarget, TemplateJson


class AsyncMaildenoClient:
    """Asynchronous Maildeno client. Mirrors :class:`MaildenoClient`.

    Template JSON is fetched asynchronously, cached locally, and rendered
    via the embedded Wasm engine in a thread-pool executor so the event
    loop is never blocked.

    Example::

        import asyncio
        from maildeno import AsyncMaildenoClient

        async def main():
            async with AsyncMaildenoClient(api_key="sk_live_...") as client:
                html = await client.render_html("550e8400-...")
                print(html)

        asyncio.run(main())

    For FastAPI / Starlette / aiohttp, instantiate one client per process
    at startup and reuse it across all requests.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        cache: Optional[CacheConfig] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        if not api_key:
            raise MaildenoError("INVALID_API_KEY", "api_key is required.")

        self._api_key  = api_key
        self._base_url = normalise_base_url(base_url)
        self._timeout  = timeout
        self._cache    = build_cache(cache)  # type: ignore[arg-type]

        if http_client is not None:
            self._http = http_client
            self._owns_http = False
        else:
            self._http = httpx.AsyncClient(timeout=timeout)
            self._owns_http = True

    # ── Render ────────────────────────────────────────────────────────────────

    async def render(
        self,
        *,
        template_id: str,
        target: RenderTarget = "html",
        dynamic_data: Optional[DynamicData] = None,
    ) -> RenderResult:
        """Render a template to HTML, React Email TSX, or MJML.

        The Wasm engine is synchronous; it runs in a thread-pool executor so
        the event loop is never blocked.

        :param template_id:  UUID of the template (required).
        :param target:       ``"html"`` | ``"react-email"`` | ``"mjml"``.
        :param dynamic_data: Merge tags + visibility context (fully optional).
        :raises MaildenoError: On any API or render failure.
        """
        template, from_stale = await self._get_template(template_id, target)

        norm_data: Optional[DynamicData] = None
        if dynamic_data is not None:
            raw = normalise_dynamic_data(dynamic_data)
            if raw:
                norm_data = raw  # type: ignore[assignment]

        # Run the synchronous Wasm render in a thread to avoid blocking the
        # event loop. functools.partial binds the arguments cleanly.
        loop = asyncio.get_event_loop()
        raw_output: str = await loop.run_in_executor(
            None,
            functools.partial(render_template, template, target, norm_data),
        )
        output = minify_output(target, raw_output)

        return RenderResult(
            template_id=template_id,
            target=target,
            output=output,
            from_stale_cache=from_stale,
        )

    async def render_html(
        self,
        template_id: str,
        dynamic_data: Optional[DynamicData] = None,
    ) -> str:
        """Convenience: render to HTML, returning the output string directly."""
        return (
            await self.render(
                template_id=template_id, target="html", dynamic_data=dynamic_data
            )
        ).output

    async def render_react(
        self,
        template_id: str,
        dynamic_data: Optional[DynamicData] = None,
    ) -> str:
        """Convenience: render to React Email TSX."""
        return (
            await self.render(
                template_id=template_id, target="react-email", dynamic_data=dynamic_data
            )
        ).output

    async def render_mjml(
        self,
        template_id: str,
        dynamic_data: Optional[DynamicData] = None,
    ) -> str:
        """Convenience: render to MJML."""
        return (
            await self.render(
                template_id=template_id, target="mjml", dynamic_data=dynamic_data
            )
        ).output

    # ── Cache management ──────────────────────────────────────────────────────

    def list_cached(self) -> List[str]:
        """Return the IDs of all templates currently in the cache."""
        return self._cache.list()

    def delete_cached(self, template_id: str) -> None:
        """Remove a single template from the cache."""
        self._cache.invalidate(template_id)

    def clear_cache(self) -> None:
        """Remove all templates from the cache."""
        self._cache.clear()

    def invalidate(self, template_id: str) -> None:
        """Deprecated — use :meth:`delete_cached` instead."""
        warnings.warn(
            "invalidate() is deprecated; use delete_cached() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.delete_cached(template_id)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def aclose(self) -> None:
        """Close the underlying HTTP client.

        Only closes the client if this :class:`AsyncMaildenoClient` created it.
        """
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> AsyncMaildenoClient:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        await self.aclose()

    # ── Template fetching ─────────────────────────────────────────────────────

    async def _get_template(
        self,
        template_id: str,
        target: RenderTarget,
    ) -> tuple[TemplateJson, bool]:
        """Return (template, from_stale_cache)."""
        fresh = self._cache.get_fresh(template_id)
        if fresh is not None:
            return fresh, False

        try:
            template = await self._fetch_template(template_id, target)
            self._cache.set(template_id, template)
            return template, False
        except MaildenoError:
            stale = self._cache.get_fallback(template_id)
            if stale is not None:
                return stale, True
            raise

    async def _fetch_template(
        self,
        template_id: str,
        target: RenderTarget,
    ) -> TemplateJson:
        url = f"{self._base_url}{TEMPLATE_PATH}/{template_id}?target={target}"
        try:
            response = await self._http.get(
                url,
                headers=build_headers(self._api_key),
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise map_transport_error(exc) from exc
        except Exception as exc:  # pragma: no cover
            raise map_transport_error(exc) from exc

        raise_for_response(response)
        return parse_template_response(response.json())


__all__ = ["AsyncMaildenoClient"]

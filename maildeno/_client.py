"""Synchronous Maildeno client."""

from __future__ import annotations

import warnings
from types import TracebackType
from typing import Any, Dict, List, Optional, Type

import httpx

from ._cache import TemplateCache, build_cache
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


class MaildenoClient:
    """Synchronous Maildeno client.

    Template JSON is fetched from the Maildeno API once, cached locally
    (memory or disk), and rendered in-process using the embedded Wasm engine.
    Merge tags and visibility context never leave your server.

    Example::

        from maildeno import MaildenoClient

        client = MaildenoClient(api_key="sk_live_...")

        result = client.render(
            template_id="550e8400-e29b-41d4-a716-446655440000",
            target="html",
            dynamic_data={
                "merge_tags": {"text": {"name": "Noruwa"}},
                "context": {"plan": "pro"},
            },
        )
        print(result.output)

    Cache strategies::

        # Memory (default) — lost on restart, zero config
        client = MaildenoClient(api_key="...", cache={"ttl": 60_000})

        # Disk — survives restarts, shared across workers on same filesystem
        client = MaildenoClient(
            api_key="...",
            cache={"type": "disk", "path": "/var/cache/maildeno", "ttl": 300_000},
        )

    The client can be used as a context manager::

        with MaildenoClient(api_key="...") as client:
            html = client.render_html("template-id")
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        cache: Optional[CacheConfig] = None,
        http_client: Optional[httpx.Client] = None,
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
            self._http = httpx.Client(timeout=timeout)
            self._owns_http = True

    # ── Render ────────────────────────────────────────────────────────────────

    def render(
        self,
        *,
        template_id: str,
        target: RenderTarget = "html",
        dynamic_data: Optional[DynamicData] = None,
    ) -> RenderResult:
        """Render a template to HTML, React Email TSX, or MJML.

        Template JSON is fetched once and cached. Subsequent calls with the
        same ``template_id`` render locally with zero network overhead until
        the TTL expires.

        :param template_id:  UUID of the template (required).
        :param target:       ``"html"`` | ``"react-email"`` | ``"mjml"``.
        :param dynamic_data: Merge tags + visibility context (fully optional).
        :raises MaildenoError: On any API or render failure.
        """
        template, from_stale = self._get_template(template_id, target)

        norm_data: Optional[DynamicData] = None
        if dynamic_data is not None:
            raw = normalise_dynamic_data(dynamic_data)
            if raw:
                norm_data = raw  # type: ignore[assignment]

        raw_output = render_template(template, target, norm_data)
        output = minify_output(target, raw_output)

        return RenderResult(
            template_id=template_id,
            target=target,
            output=output,
            from_stale_cache=from_stale,
        )

    def render_html(
        self,
        template_id: str,
        dynamic_data: Optional[DynamicData] = None,
    ) -> str:
        """Convenience: render to HTML, returning the output string directly."""
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

    # ── Cache management ──────────────────────────────────────────────────────

    def list_cached(self) -> List[str]:
        """Return the IDs of all templates currently in the cache.

        In memory mode reads the in-process dict.
        In disk mode reads the cache directory — no file contents are loaded.

        Example::

            ids = client.list_cached()
            # ["a7f4b181-...", "9ec0c043-..."]
        """
        return self._cache.list()

    def delete_cached(self, template_id: str) -> None:
        """Remove a single template from the cache.

        The next render for this template will fetch a fresh copy from the
        server regardless of TTL. Use this when you know a template has
        changed and want the update visible immediately.

        Example::

            client.delete_cached("a7f4b181-a366-4944-a371-e7b941a3c5ab")
        """
        self._cache.invalidate(template_id)

    def clear_cache(self) -> None:
        """Remove all templates from the cache.

        In disk mode deletes all ``.json`` files in the cache directory but
        leaves the directory itself intact.
        """
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

    def close(self) -> None:
        """Close the underlying HTTP client.

        Only closes the client if this :class:`MaildenoClient` created it.
        If you injected your own ``http_client``, closing is your responsibility.
        """
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> MaildenoClient:
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()

    # ── Template fetching ─────────────────────────────────────────────────────

    def _get_template(
        self,
        template_id: str,
        target: RenderTarget,
    ) -> tuple[TemplateJson, bool]:
        """Return (template, from_stale_cache).

        1. Fresh hit  → return immediately, no network.
        2. Miss/stale → fetch from API, store in cache, return fresh.
        3. Fetch fail → use stale copy if available (from_stale_cache=True).
        4. No copy at all → re-raise the original error.
        """
        fresh = self._cache.get_fresh(template_id)
        if fresh is not None:
            return fresh, False

        try:
            template = self._fetch_template(template_id, target)
            self._cache.set(template_id, template)
            return template, False
        except MaildenoError:
            stale = self._cache.get_fallback(template_id)
            if stale is not None:
                return stale, True
            raise

    def _fetch_template(
        self,
        template_id: str,
        target: RenderTarget,
    ) -> TemplateJson:
        url = f"{self._base_url}{TEMPLATE_PATH}/{template_id}?target={target}"
        try:
            response = self._http.get(
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


__all__ = ["MaildenoClient"]

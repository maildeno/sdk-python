"""Tests for the asynchronous AsyncMaildenoClient."""

from __future__ import annotations

import time
import tempfile
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from maildeno import AsyncMaildenoClient, MaildenoError
from maildeno._types import TemplateJson

API_KEY  = "sk_test_" + "a" * 64
BASE_URL = "https://api.maildeno.com"
TMPL_URL = f"{BASE_URL}/v1/sdk/template"

TEMPLATE: TemplateJson = {
    "template_id":    "t1",
    "template_name":  "Test",
    "canvas":         {"width": 600},
    "rows":           [],
    "schema_version": "1.0",
}


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


def _err(status: int, detail: object) -> httpx.Response:
    return httpx.Response(status, json={"detail": detail})


def _mock_render(output: str = "<html/>") -> MagicMock:
    return MagicMock(return_value=output)


class TestAsyncClient:
    async def test_raises_if_api_key_missing(self) -> None:
        with pytest.raises(MaildenoError):
            AsyncMaildenoClient(api_key="")

    # ── Render ────────────────────────────────────────────────────────────────

    @respx.mock
    async def test_render_html_returns_output(self) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        with patch("maildeno._async_client.render_template", _mock_render("HTML")):
            async with AsyncMaildenoClient(api_key=API_KEY) as client:
                html = await client.render_html("t1")
        assert html == "HTML"

    @respx.mock
    async def test_render_react_sets_target(self) -> None:
        route = respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        with patch("maildeno._async_client.render_template", _mock_render()):
            async with AsyncMaildenoClient(api_key=API_KEY) as client:
                await client.render_react("t1")
        assert "target=react-email" in str(route.calls.last.request.url)

    @respx.mock
    async def test_render_mjml_sets_target(self) -> None:
        route = respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        with patch("maildeno._async_client.render_template", _mock_render()):
            async with AsyncMaildenoClient(api_key=API_KEY) as client:
                await client.render_mjml("t1")
        assert "target=mjml" in str(route.calls.last.request.url)

    @respx.mock
    async def test_from_stale_cache_false_on_fresh(self) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        with patch("maildeno._async_client.render_template", _mock_render()):
            async with AsyncMaildenoClient(api_key=API_KEY) as client:
                result = await client.render(template_id="t1")
        assert result.from_stale_cache is False

    @respx.mock
    async def test_dynamic_data_passed_to_render_template(self) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        captured: list = []

        def fake_render(tmpl, tgt, ddata):  # type: ignore[no-untyped-def]
            captured.append(ddata)
            return "<html/>"

        with patch("maildeno._async_client.render_template", fake_render):
            async with AsyncMaildenoClient(api_key=API_KEY) as client:
                await client.render(
                    template_id="t1",
                    dynamic_data={"merge_tags": {"text": {"name": "Noruwa"}}},
                )
        assert captured[0]["merge_tags"]["text"] == {"name": "Noruwa"}

    # ── Caching ───────────────────────────────────────────────────────────────

    @respx.mock
    async def test_serves_second_render_from_cache(self) -> None:
        route = respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        client = AsyncMaildenoClient(api_key=API_KEY)
        with patch("maildeno._async_client.render_template", _mock_render()):
            await client.render_html("t1")
            await client.render_html("t1")
        assert route.call_count == 1
        await client.aclose()

    @respx.mock
    async def test_delete_cached_forces_refetch(self) -> None:
        route = respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        client = AsyncMaildenoClient(api_key=API_KEY)
        with patch("maildeno._async_client.render_template", _mock_render()):
            await client.render_html("t1")
            client.delete_cached("t1")
            await client.render_html("t1")
        assert route.call_count == 2
        await client.aclose()

    @respx.mock
    async def test_clear_cache(self) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        client = AsyncMaildenoClient(api_key=API_KEY)
        with patch("maildeno._async_client.render_template", _mock_render()):
            await client.render_html("t1")
        client.clear_cache()
        assert client.list_cached() == []
        await client.aclose()

    @respx.mock
    async def test_list_cached(self) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        client = AsyncMaildenoClient(api_key=API_KEY)
        assert client.list_cached() == []
        with patch("maildeno._async_client.render_template", _mock_render()):
            await client.render_html("t1")
        assert "t1" in client.list_cached()
        await client.aclose()

    async def test_invalidate_is_deprecated(self) -> None:
        client = AsyncMaildenoClient(api_key=API_KEY)
        with pytest.warns(DeprecationWarning, match="delete_cached"):
            client.invalidate("t1")
        await client.aclose()

    # ── Stale-on-error fallback ───────────────────────────────────────────────

    @respx.mock
    async def test_stale_cache_when_server_down_after_ttl(self) -> None:
        route = respx.get(f"{TMPL_URL}/t1")
        route.mock(return_value=_ok(TEMPLATE))
        client = AsyncMaildenoClient(api_key=API_KEY, cache={"ttl": 0})
        with patch("maildeno._async_client.render_template", _mock_render()):
            await client.render_html("t1")
            time.sleep(0.01)
            route.mock(side_effect=httpx.ConnectError("down"))
            result = await client.render(template_id="t1")
        assert result.from_stale_cache is True
        await client.aclose()

    @respx.mock
    async def test_raises_when_no_cache_and_server_down(self) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(side_effect=httpx.ConnectError("down"))
        with pytest.raises(MaildenoError) as ei:
            async with AsyncMaildenoClient(api_key=API_KEY) as client:
                await client.render_html("t1")
        assert ei.value.code == "NETWORK_ERROR"

    # ── Error handling ────────────────────────────────────────────────────────

    @respx.mock
    async def test_invalid_api_key_on_401(self) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(return_value=_err(401, "bad key"))
        async with AsyncMaildenoClient(api_key=API_KEY) as client:
            with pytest.raises(MaildenoError) as ei:
                await client.render_html("t1")
        assert ei.value.code == "INVALID_API_KEY"

    @respx.mock
    async def test_template_not_found_on_404(self) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(return_value=_err(404, "Not found."))
        async with AsyncMaildenoClient(api_key=API_KEY) as client:
            with pytest.raises(MaildenoError) as ei:
                await client.render_html("t1")
        assert ei.value.code == "TEMPLATE_NOT_FOUND"

    @respx.mock
    async def test_pydantic_validation_array_formatted(self) -> None:
        respx.get(f"{TMPL_URL}/bad").mock(
            return_value=_err(
                422,
                [{"type": "uuid_parsing", "loc": ["body", "template_id"],
                  "msg": "Input should be a valid UUID", "input": "bad"}],
            )
        )
        async with AsyncMaildenoClient(api_key=API_KEY) as client:
            with pytest.raises(MaildenoError) as ei:
                await client.render_html("bad")
        assert ei.value.code == "RENDER_ERROR"
        assert "template_id" in ei.value.message
        assert ei.value.issues is not None

    @respx.mock
    async def test_timeout_error(self) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(
            side_effect=httpx.ConnectTimeout("timeout")
        )
        async with AsyncMaildenoClient(api_key=API_KEY) as client:
            with pytest.raises(MaildenoError) as ei:
                await client.render_html("t1")
        assert ei.value.code == "TIMEOUT"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @respx.mock
    async def test_injected_http_client_not_closed(self) -> None:
        external = httpx.AsyncClient()
        respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        client = AsyncMaildenoClient(api_key=API_KEY, http_client=external)
        with patch("maildeno._async_client.render_template", _mock_render()):
            async with client:
                await client.render_html("t1")
        assert not external.is_closed
        await external.aclose()

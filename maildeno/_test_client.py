"""Tests for the synchronous MaildenoClient."""

from __future__ import annotations

import json
import time
import tempfile
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from maildeno import MaildenoClient, MaildenoError
from maildeno._types import TemplateJson

API_KEY    = "sk_test_" + "a" * 64
BASE_URL   = "https://api.maildeno.com"
TMPL_URL   = f"{BASE_URL}/v1/sdk/template"

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
    """Patch render_template to return a fixed string."""
    m = MagicMock(return_value=output)
    return m


# ── Constructor ───────────────────────────────────────────────────────────────

class TestConstructor:
    def test_raises_if_api_key_missing(self) -> None:
        with pytest.raises(MaildenoError) as ei:
            MaildenoClient(api_key="")
        assert ei.value.code == "INVALID_API_KEY"

    @respx.mock
    def test_strips_trailing_slash_from_base_url(self) -> None:
        route = respx.get(f"https://custom.example.com/v1/sdk/template/t1").mock(
            return_value=_ok(TEMPLATE)
        )
        with patch("maildeno._client.render_template", _mock_render()):
            client = MaildenoClient(
                api_key=API_KEY, base_url="https://custom.example.com/"
            )
            client.render_html("t1")
        assert route.called

    @respx.mock
    def test_defaults_to_production_base_url(self) -> None:
        route = respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        with patch("maildeno._client.render_template", _mock_render()):
            MaildenoClient(api_key=API_KEY).render_html("t1")
        assert route.called

    def test_accepts_memory_cache_config(self) -> None:
        client = MaildenoClient(
            api_key=API_KEY,
            cache={"type": "memory", "ttl": 60_000, "max_entries": 20},
        )
        assert client is not None

    def test_accepts_disk_cache_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = MaildenoClient(
                api_key=API_KEY,
                cache={"type": "disk", "path": tmp, "ttl": 60_000},
            )
            assert client is not None


# ── Template fetching & caching ───────────────────────────────────────────────

class TestTemplateCache:
    @respx.mock
    def test_fetches_template_on_first_call(self) -> None:
        route = respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        with patch("maildeno._client.render_template", _mock_render()):
            MaildenoClient(api_key=API_KEY).render_html("t1")
        assert route.call_count == 1

    @respx.mock
    def test_serves_subsequent_renders_from_cache(self) -> None:
        route = respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        client = MaildenoClient(api_key=API_KEY)
        with patch("maildeno._client.render_template", _mock_render()):
            client.render_html("t1")
            client.render_html("t1")
            client.render_html("t1")
        assert route.call_count == 1

    @respx.mock
    def test_refetches_after_ttl_expires(self) -> None:
        route = respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        client = MaildenoClient(api_key=API_KEY, cache={"ttl": 0})
        with patch("maildeno._client.render_template", _mock_render()):
            client.render_html("t1")
            time.sleep(0.01)  # let TTL=0 expire
            client.render_html("t1")
        assert route.call_count == 2

    @respx.mock
    def test_delete_cached_forces_refetch(self) -> None:
        route = respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        client = MaildenoClient(api_key=API_KEY)
        with patch("maildeno._client.render_template", _mock_render()):
            client.render_html("t1")
            client.delete_cached("t1")
            client.render_html("t1")
        assert route.call_count == 2

    @respx.mock
    def test_clear_cache_forces_refetch(self) -> None:
        route = respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        client = MaildenoClient(api_key=API_KEY)
        with patch("maildeno._client.render_template", _mock_render()):
            client.render_html("t1")
            client.clear_cache()
            client.render_html("t1")
        assert route.call_count == 2

    @respx.mock
    def test_list_cached_returns_ids(self) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        client = MaildenoClient(api_key=API_KEY)
        assert client.list_cached() == []
        with patch("maildeno._client.render_template", _mock_render()):
            client.render_html("t1")
        assert "t1" in client.list_cached()

    @respx.mock
    def test_list_cached_empty_after_clear(self) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        client = MaildenoClient(api_key=API_KEY)
        with patch("maildeno._client.render_template", _mock_render()):
            client.render_html("t1")
        client.clear_cache()
        assert client.list_cached() == []

    def test_invalidate_is_deprecated_alias(self) -> None:
        client = MaildenoClient(api_key=API_KEY)
        with pytest.warns(DeprecationWarning, match="delete_cached"):
            client.invalidate("t1")


# ── Stale-on-error fallback ───────────────────────────────────────────────────

class TestStaleOnError:
    @respx.mock
    def test_uses_stale_cache_when_server_unreachable_after_ttl(self) -> None:
        route = respx.get(f"{TMPL_URL}/t1")
        route.mock(return_value=_ok(TEMPLATE))
        client = MaildenoClient(api_key=API_KEY, cache={"ttl": 0})
        with patch("maildeno._client.render_template", _mock_render()):
            client.render_html("t1")
            time.sleep(0.01)

            route.mock(side_effect=httpx.ConnectError("down"))
            result = client.render(template_id="t1")

        assert result.from_stale_cache is True
        assert result.output == "<html/>"

    @respx.mock
    def test_raises_when_no_cache_and_server_down(self) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(
            side_effect=httpx.ConnectError("down")
        )
        with pytest.raises(MaildenoError) as ei:
            MaildenoClient(api_key=API_KEY).render_html("t1")
        assert ei.value.code == "NETWORK_ERROR"

    @respx.mock
    def test_from_stale_cache_absent_on_fresh_hit(self) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        with patch("maildeno._client.render_template", _mock_render()):
            result = MaildenoClient(api_key=API_KEY).render(template_id="t1")
        assert result.from_stale_cache is False


# ── Disk cache ────────────────────────────────────────────────────────────────

class TestDiskCache:
    @respx.mock
    def test_disk_cache_persists_across_client_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            route = respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
            with patch("maildeno._client.render_template", _mock_render()):
                # First client fetches and caches to disk
                client1 = MaildenoClient(
                    api_key=API_KEY,
                    cache={"type": "disk", "path": tmp},
                )
                client1.render_html("t1")
                assert route.call_count == 1

                # Second client reads from disk — no network call
                client2 = MaildenoClient(
                    api_key=API_KEY,
                    cache={"type": "disk", "path": tmp},
                )
                client2.render_html("t1")
                assert route.call_count == 1  # still 1

    @respx.mock
    def test_disk_list_cached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
            client = MaildenoClient(
                api_key=API_KEY,
                cache={"type": "disk", "path": tmp},
            )
            with patch("maildeno._client.render_template", _mock_render()):
                client.render_html("t1")
            assert "t1" in client.list_cached()

    @respx.mock
    def test_disk_delete_cached(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            route = respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
            client = MaildenoClient(
                api_key=API_KEY,
                cache={"type": "disk", "path": tmp},
            )
            with patch("maildeno._client.render_template", _mock_render()):
                client.render_html("t1")
                client.delete_cached("t1")
                client.render_html("t1")
            assert route.call_count == 2

    @respx.mock
    def test_disk_clear_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
            client = MaildenoClient(
                api_key=API_KEY,
                cache={"type": "disk", "path": tmp},
            )
            with patch("maildeno._client.render_template", _mock_render()):
                client.render_html("t1")
            client.clear_cache()
            assert client.list_cached() == []


# ── Render ────────────────────────────────────────────────────────────────────

class TestRender:
    @respx.mock
    def test_sends_bearer_auth_header(self) -> None:
        route = respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        with patch("maildeno._client.render_template", _mock_render()):
            MaildenoClient(api_key=API_KEY).render_html("t1")
        assert route.calls.last.request.headers["Authorization"] == f"Bearer {API_KEY}"

    @respx.mock
    def test_passes_target_as_query_param(self) -> None:
        route = respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        with patch("maildeno._client.render_template", _mock_render()):
            MaildenoClient(api_key=API_KEY).render_mjml("t1")
        assert "target=mjml" in str(route.calls.last.request.url)

    @respx.mock
    def test_render_result_fields(self) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        with patch("maildeno._client.render_template", _mock_render("RENDERED")):
            result = MaildenoClient(api_key=API_KEY).render(
                template_id="t1", target="mjml"
            )
        assert result.template_id == "t1"
        assert result.target == "mjml"
        assert result.output == "RENDERED"
        assert result.from_stale_cache is False

    @respx.mock
    def test_dynamic_data_passed_to_render_template(self) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        captured: list = []

        def fake_render(tmpl, tgt, ddata):  # type: ignore[no-untyped-def]
            captured.append(ddata)
            return "<html/>"

        with patch("maildeno._client.render_template", fake_render):
            MaildenoClient(api_key=API_KEY).render(
                template_id="t1",
                dynamic_data={"merge_tags": {"text": {"name": "Noruwa"}}},
            )
        assert captured[0]["merge_tags"]["text"] == {"name": "Noruwa"}

    @respx.mock
    def test_empty_dynamic_data_not_passed(self) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        captured: list = []

        def fake_render(tmpl, tgt, ddata):  # type: ignore[no-untyped-def]
            captured.append(ddata)
            return "<html/>"

        with patch("maildeno._client.render_template", fake_render):
            MaildenoClient(api_key=API_KEY).render(
                template_id="t1",
                dynamic_data={"merge_tags": {}, "context": {}},
            )
        assert captured[0] is None


# ── Convenience methods ───────────────────────────────────────────────────────

class TestConvenienceMethods:
    @respx.mock
    def test_render_html_returns_string(self) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        with patch("maildeno._client.render_template", _mock_render("HTML")):
            assert MaildenoClient(api_key=API_KEY).render_html("t1") == "HTML"

    @respx.mock
    def test_render_react_sets_target(self) -> None:
        route = respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        with patch("maildeno._client.render_template", _mock_render()):
            MaildenoClient(api_key=API_KEY).render_react("t1")
        assert "target=react-email" in str(route.calls.last.request.url)

    @respx.mock
    def test_render_mjml_sets_target(self) -> None:
        route = respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        with patch("maildeno._client.render_template", _mock_render()):
            MaildenoClient(api_key=API_KEY).render_mjml("t1")
        assert "target=mjml" in str(route.calls.last.request.url)


# ── Error handling ────────────────────────────────────────────────────────────

class TestErrorHandling:
    @pytest.fixture
    def client(self) -> MaildenoClient:
        return MaildenoClient(api_key=API_KEY)

    @respx.mock
    def test_invalid_api_key_on_401(self, client: MaildenoClient) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(
            return_value=_err(401, "Invalid or missing API key.")
        )
        with pytest.raises(MaildenoError) as ei:
            client.render_html("t1")
        assert ei.value.code == "INVALID_API_KEY"
        assert ei.value.status == 401

    @respx.mock
    def test_forbidden_on_403(self, client: MaildenoClient) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(
            return_value=_err(403, "Key scope violation.")
        )
        with pytest.raises(MaildenoError) as ei:
            client.render_html("t1")
        assert ei.value.code == "FORBIDDEN"

    @respx.mock
    def test_template_not_found_on_404(self, client: MaildenoClient) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(return_value=_err(404, "Not found."))
        with pytest.raises(MaildenoError) as ei:
            client.render_html("t1")
        assert ei.value.code == "TEMPLATE_NOT_FOUND"

    @respx.mock
    def test_pydantic_validation_array_formatted(self, client: MaildenoClient) -> None:
        respx.get(f"{TMPL_URL}/bad-id").mock(
            return_value=_err(
                422,
                [{"type": "uuid_parsing", "loc": ["body", "template_id"],
                  "msg": "Input should be a valid UUID", "input": "bad-id"}],
            )
        )
        with pytest.raises(MaildenoError) as ei:
            client.render_html("bad-id")
        assert ei.value.code == "RENDER_ERROR"
        assert "template_id" in ei.value.message
        assert ei.value.issues is not None

    @respx.mock
    def test_falls_back_on_non_json_500(self, client: MaildenoClient) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(
            return_value=httpx.Response(500, content=b"not json")
        )
        with pytest.raises(MaildenoError) as ei:
            client.render_html("t1")
        assert ei.value.code == "UNKNOWN"
        assert ei.value.message == "HTTP 500"

    @respx.mock
    def test_network_error(self, client: MaildenoClient) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(
            side_effect=httpx.ConnectError("Failed to connect")
        )
        with pytest.raises(MaildenoError) as ei:
            client.render_html("t1")
        assert ei.value.code == "NETWORK_ERROR"
        assert ei.value.status == 0

    @respx.mock
    def test_timeout_error(self, client: MaildenoClient) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(
            side_effect=httpx.ConnectTimeout("timed out")
        )
        with pytest.raises(MaildenoError) as ei:
            client.render_html("t1")
        assert ei.value.code == "TIMEOUT"


# ── Context manager ───────────────────────────────────────────────────────────

class TestContextManager:
    @respx.mock
    def test_context_manager_closes_client(self) -> None:
        respx.get(f"{TMPL_URL}/t1").mock(return_value=_ok(TEMPLATE))
        with patch("maildeno._client.render_template", _mock_render()):
            with MaildenoClient(api_key=API_KEY) as client:
                assert client.render_html("t1") == "<html/>"

    def test_injected_http_client_not_closed(self) -> None:
        external = httpx.Client()
        client = MaildenoClient(api_key=API_KEY, http_client=external)
        client.close()
        assert not external.is_closed
        external.close()


# ── Minify unit tests ─────────────────────────────────────────────────────────

class TestMinifyOutput:
    def test_collapses_inter_tag_whitespace_in_html(self) -> None:
        from maildeno._minify import minify_output
        result = minify_output("html", "<p>Hi</p>  \n  <p>World</p>")
        assert not re.search(r">\s{2,}<", result)

    def test_does_not_corrupt_css_inside_style_blocks(self) -> None:
        from maildeno._minify import minify_output
        source = "<style> @media (max-width: 600px) { .col { width: 100%; } } </style><p>Hi</p>"
        result = minify_output("html", source)
        assert "@media" in result
        assert "max-width: 600px" in result

    def test_does_not_corrupt_css_inside_mj_style_blocks(self) -> None:
        from maildeno._minify import minify_output
        source = "<mjml><mj-head><mj-style> .btn { color: red; } </mj-style></mj-head></mjml>"
        result = minify_output("mjml", source)
        assert ".btn" in result
        assert "color: red" in result

    def test_strips_blank_lines_from_react_email(self) -> None:
        from maildeno._minify import minify_output
        result = minify_output("react-email", "line1\n\n\n\nline2")
        assert not re.search(r"\n{3,}", result)

    def test_returns_source_unchanged_for_unknown_target(self) -> None:
        from maildeno._minify import minify_output
        source = "  some content  "
        assert minify_output("unknown-target", source) == source

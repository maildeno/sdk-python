"""Tests for the synchronous MaildenoClient."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from maildeno import MaildenoClient, MaildenoError

API_KEY = "sk_test_" + "a" * 64
BASE_URL = "https://api.maildeno.com"
RENDER_URL = f"{BASE_URL}/v1/sdk/render"


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


def _err(status: int, detail) -> httpx.Response:  # type: ignore[no-untyped-def]
    return httpx.Response(status, json={"detail": detail})


# ── Constructor ──────────────────────────────────────────────────────────────


class TestConstructor:
    def test_raises_if_api_key_missing(self) -> None:
        with pytest.raises(MaildenoError) as ei:
            MaildenoClient(api_key="")
        assert ei.value.code == "INVALID_API_KEY"

    @respx.mock
    def test_strips_trailing_slash_from_base_url(self) -> None:
        route = respx.post("https://api.example.com/v1/sdk/render").mock(
            return_value=_ok({"template_id": "abc", "target": "html", "output": "<html/>"})
        )
        client = MaildenoClient(api_key=API_KEY, base_url="https://api.example.com/")
        client.render_html("abc")
        assert route.called

    @respx.mock
    def test_defaults_to_localhost_when_base_url_missing(self) -> None:
        route = respx.post(RENDER_URL).mock(
            return_value=_ok({"template_id": "abc", "target": "html", "output": "<html/>"})
        )
        client = MaildenoClient(api_key=API_KEY)
        client.render_html("abc")
        assert route.called


# ── render() ─────────────────────────────────────────────────────────────────


class TestRender:
    @pytest.fixture
    def client(self) -> MaildenoClient:
        return MaildenoClient(api_key=API_KEY, base_url=BASE_URL)

    @respx.mock
    def test_sends_authorization_bearer_header(self, client: MaildenoClient) -> None:
        route = respx.post(RENDER_URL).mock(
            return_value=_ok({"template_id": "t1", "target": "html", "output": "<p>Hi</p>"})
        )
        client.render(template_id="t1")
        assert route.calls.last.request.headers["Authorization"] == f"Bearer {API_KEY}"

    @respx.mock
    def test_defaults_target_to_html(self, client: MaildenoClient) -> None:
        route = respx.post(RENDER_URL).mock(
            return_value=_ok({"template_id": "t1", "target": "html", "output": "<p/>"})
        )
        client.render(template_id="t1")
        body = json.loads(route.calls.last.request.content)
        assert body["target"] == "html"

    @respx.mock
    def test_omits_dynamic_data_when_not_provided(self, client: MaildenoClient) -> None:
        route = respx.post(RENDER_URL).mock(
            return_value=_ok({"template_id": "t1", "target": "html", "output": "<p/>"})
        )
        client.render(template_id="t1")
        body = json.loads(route.calls.last.request.content)
        assert "dynamic_data" not in body

    @respx.mock
    def test_includes_only_provided_merge_tag_subgroups(self, client: MaildenoClient) -> None:
        route = respx.post(RENDER_URL).mock(
            return_value=_ok({"template_id": "t1", "target": "html", "output": "<p>Noruwa</p>"})
        )
        client.render(
            template_id="t1",
            dynamic_data={"merge_tags": {"text": {"name": "Noruwa"}}},
        )
        body = json.loads(route.calls.last.request.content)
        assert body["dynamic_data"]["merge_tags"]["text"] == {"name": "Noruwa"}
        assert "url" not in body["dynamic_data"]["merge_tags"]
        assert "attr" not in body["dynamic_data"]["merge_tags"]

    @respx.mock
    def test_includes_context_when_provided(self, client: MaildenoClient) -> None:
        route = respx.post(RENDER_URL).mock(
            return_value=_ok({"template_id": "t1", "target": "html", "output": "<p/>"})
        )
        client.render(
            template_id="t1",
            dynamic_data={"context": {"plan": "pro", "country": "usa"}},
        )
        body = json.loads(route.calls.last.request.content)
        assert body["dynamic_data"]["context"] == {"plan": "pro", "country": "usa"}

    @respx.mock
    def test_omits_dynamic_data_when_all_subgroups_empty(self, client: MaildenoClient) -> None:
        route = respx.post(RENDER_URL).mock(
            return_value=_ok({"template_id": "t1", "target": "html", "output": "<p/>"})
        )
        client.render(
            template_id="t1",
            dynamic_data={"merge_tags": {}, "context": {}},
        )
        body = json.loads(route.calls.last.request.content)
        assert "dynamic_data" not in body

    @respx.mock
    def test_maps_response_correctly(self, client: MaildenoClient) -> None:
        respx.post(RENDER_URL).mock(
            return_value=_ok({
                "template_id": "t1",
                "target": "react-email",
                "output": "export default function...",
            })
        )
        result = client.render(template_id="t1", target="react-email")
        assert result.template_id == "t1"
        assert result.target == "react-email"
        assert result.output == "export default function..."


# ── Convenience methods ──────────────────────────────────────────────────────


class TestConvenienceMethods:
    @pytest.fixture
    def client(self) -> MaildenoClient:
        return MaildenoClient(api_key=API_KEY)

    @respx.mock
    def test_render_html_returns_output_string(self, client: MaildenoClient) -> None:
        respx.post(RENDER_URL).mock(
            return_value=_ok({"template_id": "t1", "target": "html", "output": "<html>...</html>"})
        )
        assert client.render_html("t1") == "<html>...</html>"

    @respx.mock
    def test_render_react_sets_target(self, client: MaildenoClient) -> None:
        route = respx.post(RENDER_URL).mock(
            return_value=_ok({"template_id": "t1", "target": "react-email", "output": "tsx..."})
        )
        client.render_react("t1")
        body = json.loads(route.calls.last.request.content)
        assert body["target"] == "react-email"

    @respx.mock
    def test_render_mjml_sets_target(self, client: MaildenoClient) -> None:
        route = respx.post(RENDER_URL).mock(
            return_value=_ok({"template_id": "t1", "target": "mjml", "output": "<mjml/>"})
        )
        client.render_mjml("t1")
        body = json.loads(route.calls.last.request.content)
        assert body["target"] == "mjml"


# ── Error handling ───────────────────────────────────────────────────────────


class TestErrorHandling:
    @pytest.fixture
    def client(self) -> MaildenoClient:
        return MaildenoClient(api_key=API_KEY)

    @respx.mock
    def test_invalid_api_key_on_401(self, client: MaildenoClient) -> None:
        respx.post(RENDER_URL).mock(return_value=_err(401, "Invalid or missing API key."))
        with pytest.raises(MaildenoError) as ei:
            client.render(template_id="t1")
        assert ei.value.code == "INVALID_API_KEY"
        assert ei.value.status == 401

    @respx.mock
    def test_forbidden_on_403(self, client: MaildenoClient) -> None:
        respx.post(RENDER_URL).mock(
            return_value=_err(403, "This API key does not have access to the 'mjml' target.")
        )
        with pytest.raises(MaildenoError) as ei:
            client.render_mjml("t1")
        assert ei.value.code == "FORBIDDEN"
        assert ei.value.status == 403
        assert "mjml" in ei.value.message

    @respx.mock
    def test_template_not_found_on_404(self, client: MaildenoClient) -> None:
        respx.post(RENDER_URL).mock(return_value=_err(404, "Template not found."))
        with pytest.raises(MaildenoError) as ei:
            client.render_html("bad-id")
        assert ei.value.code == "TEMPLATE_NOT_FOUND"
        assert ei.value.status == 404

    @respx.mock
    def test_render_error_on_422_with_string_detail(self, client: MaildenoClient) -> None:
        respx.post(RENDER_URL).mock(
            return_value=_err(422, "Render failed for target 'html'.")
        )
        with pytest.raises(MaildenoError) as ei:
            client.render_html("t1")
        assert ei.value.code == "RENDER_ERROR"

    @respx.mock
    def test_pydantic_validation_array_formatted_into_message(
        self, client: MaildenoClient
    ) -> None:
        # FastAPI's shape when template_id fails UUID validation
        pydantic_detail = [
            {
                "type": "uuid_parsing",
                "loc": ["body", "template_id"],
                "msg": "Input should be a valid UUID, found `z` at 1",
                "input": "zzz-not-a-uuid",
            }
        ]
        respx.post(RENDER_URL).mock(return_value=_err(422, pydantic_detail))

        with pytest.raises(MaildenoError) as ei:
            client.render_html("zzz-not-a-uuid")

        err = ei.value
        assert err.code == "RENDER_ERROR"
        assert err.status == 422
        # No more "[object Object]" — should be a real sentence.
        assert "[object" not in err.message
        assert "template_id" in err.message
        assert "valid UUID" in err.message
        # Structured issues should be available for programmatic inspection.
        assert err.issues is not None
        assert len(err.issues) == 1
        assert err.issues[0]["loc"] == ["body", "template_id"]

    @respx.mock
    def test_multiple_pydantic_issues_joined_with_semicolons(
        self, client: MaildenoClient
    ) -> None:
        pydantic_detail = [
            {"type": "missing", "loc": ["body", "template_id"], "msg": "Field required"},
            {"type": "string_type", "loc": ["body", "target"], "msg": "Input should be a string"},
        ]
        respx.post(RENDER_URL).mock(return_value=_err(422, pydantic_detail))
        with pytest.raises(MaildenoError) as ei:
            client.render_html("t1")
        assert ei.value.message == (
            "template_id: Field required; target: Input should be a string"
        )
        assert ei.value.issues is not None
        assert len(ei.value.issues) == 2

    @respx.mock
    def test_falls_back_when_detail_unparseable(self, client: MaildenoClient) -> None:
        respx.post(RENDER_URL).mock(return_value=httpx.Response(500, content=b"not json"))
        with pytest.raises(MaildenoError) as ei:
            client.render_html("t1")
        assert ei.value.code == "UNKNOWN"
        assert ei.value.message == "HTTP 500"
        assert ei.value.issues is None

    @respx.mock
    def test_falls_back_when_detail_is_none(self, client: MaildenoClient) -> None:
        respx.post(RENDER_URL).mock(return_value=_err(500, None))
        with pytest.raises(MaildenoError) as ei:
            client.render_html("t1")
        assert ei.value.message == "HTTP 500"

    @respx.mock
    def test_network_error_when_transport_fails(self, client: MaildenoClient) -> None:
        respx.post(RENDER_URL).mock(side_effect=httpx.ConnectError("Failed to connect"))
        with pytest.raises(MaildenoError) as ei:
            client.render_html("t1")
        assert ei.value.code == "NETWORK_ERROR"
        assert ei.value.status == 0

    @respx.mock
    def test_timeout_error(self, client: MaildenoClient) -> None:
        respx.post(RENDER_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
        with pytest.raises(MaildenoError) as ei:
            client.render_html("t1")
        assert ei.value.code == "TIMEOUT"


# ── Context manager ──────────────────────────────────────────────────────────


class TestContextManager:
    @respx.mock
    def test_can_be_used_as_context_manager(self) -> None:
        respx.post(RENDER_URL).mock(
            return_value=_ok({"template_id": "t1", "target": "html", "output": "<p/>"})
        )
        with MaildenoClient(api_key=API_KEY) as client:
            assert client.render_html("t1") == "<p/>"

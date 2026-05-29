"""Tests for the asynchronous AsyncMaildenoClient.

Most internals are shared with the sync client, so this file focuses on
async-specific surface (await, async-context-manager, transport plumbing).
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from maildeno import AsyncMaildenoClient, MaildenoError

API_KEY = "sk_test_" + "a" * 64
RENDER_URL = "https://api.maildeno.com/v1/sdk/render"


def _ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json=body)


def _err(status: int, detail) -> httpx.Response:  # type: ignore[no-untyped-def]
    return httpx.Response(status, json={"detail": detail})


class TestAsyncClient:
    async def test_raises_if_api_key_missing(self) -> None:
        with pytest.raises(MaildenoError):
            AsyncMaildenoClient(api_key="")

    @respx.mock
    async def test_render_html_returns_output(self) -> None:
        respx.post(RENDER_URL).mock(
            return_value=_ok({"template_id": "t1", "target": "html", "output": "<html/>"})
        )
        async with AsyncMaildenoClient(api_key=API_KEY) as client:
            html = await client.render_html("t1")
        assert html == "<html/>"

    @respx.mock
    async def test_render_passes_dynamic_data(self) -> None:
        route = respx.post(RENDER_URL).mock(
            return_value=_ok({"template_id": "t1", "target": "html", "output": "<p/>"})
        )
        async with AsyncMaildenoClient(api_key=API_KEY) as client:
            await client.render(
                template_id="t1",
                dynamic_data={
                    "merge_tags": {"text": {"name": "Noruwa"}},
                    "context": {"plan": "pro"},
                },
            )
        body = json.loads(route.calls.last.request.content)
        assert body["dynamic_data"]["merge_tags"]["text"] == {"name": "Noruwa"}
        assert body["dynamic_data"]["context"] == {"plan": "pro"}

    @respx.mock
    async def test_render_react_sets_target(self) -> None:
        route = respx.post(RENDER_URL).mock(
            return_value=_ok({"template_id": "t1", "target": "react-email", "output": "tsx"})
        )
        async with AsyncMaildenoClient(api_key=API_KEY) as client:
            await client.render_react("t1")
        body = json.loads(route.calls.last.request.content)
        assert body["target"] == "react-email"

    @respx.mock
    async def test_pydantic_validation_array_formatted(self) -> None:
        respx.post(RENDER_URL).mock(
            return_value=_err(
                422,
                [
                    {
                        "type": "uuid_parsing",
                        "loc": ["body", "template_id"],
                        "msg": "Input should be a valid UUID",
                        "input": "bad",
                    }
                ],
            )
        )
        async with AsyncMaildenoClient(api_key=API_KEY) as client:
            with pytest.raises(MaildenoError) as ei:
                await client.render_html("bad")

        assert ei.value.code == "RENDER_ERROR"
        assert "template_id" in ei.value.message
        assert "[object" not in ei.value.message
        assert ei.value.issues is not None
        assert len(ei.value.issues) == 1

    @respx.mock
    async def test_invalid_api_key_on_401(self) -> None:
        respx.post(RENDER_URL).mock(return_value=_err(401, "bad key"))
        async with AsyncMaildenoClient(api_key=API_KEY) as client:
            with pytest.raises(MaildenoError) as ei:
                await client.render_html("t1")
        assert ei.value.code == "INVALID_API_KEY"

    @respx.mock
    async def test_timeout_error(self) -> None:
        respx.post(RENDER_URL).mock(side_effect=httpx.ConnectTimeout("timeout"))
        async with AsyncMaildenoClient(api_key=API_KEY) as client:
            with pytest.raises(MaildenoError) as ei:
                await client.render_html("t1")
        assert ei.value.code == "TIMEOUT"

    @respx.mock
    async def test_injected_http_client_is_not_closed(self) -> None:
        # If the user injects their own AsyncClient, we must not close it.
        external = httpx.AsyncClient()
        respx.post(RENDER_URL).mock(
            return_value=_ok({"template_id": "t1", "target": "html", "output": "<p/>"})
        )
        client = AsyncMaildenoClient(api_key=API_KEY, http_client=external)
        async with client:
            await client.render_html("t1")
        # External client should still be usable.
        assert not external.is_closed
        await external.aclose()

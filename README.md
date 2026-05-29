# maildeno

Official Python SDK for the **Maildeno** render API.

Sync and async clients, full type hints, single dependency (`httpx`).

## Installation

```bash
pip install maildeno
# or
poetry add maildeno
# or
uv add maildeno
```

---

## Quick start

```python
from maildeno import MaildenoClient

client = MaildenoClient(
    api_key="sk_live_4a7f2c8d...",
)

html = client.render_html("550e8400-e29b-41d4-a716-446655440000")
print(html)  # <!DOCTYPE html>...
```

Async:

```python
import asyncio
from maildeno import AsyncMaildenoClient

async def main():
    async with AsyncMaildenoClient(api_key="sk_live_...") as client:
        html = await client.render_html("550e8400-e29b-41d4-a716-446655440000")
        print(html)

asyncio.run(main())
```

---

## Configuration

```python
client = MaildenoClient(
    # Required — obtain from Dashboard → API Keys → Create Key
    api_key="sk_live_...",

    # Optional — request timeout in seconds, defaults to 30.0
    timeout=10.0,
)
```

For long-lived applications, instantiate the client **once** at startup and reuse it — the underlying `httpx` connection pool is kept warm. Both clients also support context-manager use for short-lived scripts:

```python
with MaildenoClient(api_key="...") as client:
    html = client.render_html("template-id")
# client is automatically closed here
```

---

## Rendering

### `render(...)` — full control

```python
result = client.render(
    template_id="550e8400-e29b-41d4-a716-446655440000",
    target="html",        # "html" | "react-email" | "mjml"  (default: "html")
    dynamic_data={...},   # optional — see Dynamic data section
)

print(result.output)       # rendered string
print(result.target)       # "html"
print(result.template_id)  # "550e8400-..."
```

`render()` returns a frozen `RenderResult` dataclass.

### Convenience methods

```python
# All return the rendered output string directly
html = client.render_html("template-id",  dynamic_data=None)
tsx  = client.render_react("template-id", dynamic_data=None)
mjml = client.render_mjml("template-id",  dynamic_data=None)
```

The async client exposes the same surface — just `await` each call:

```python
html = await async_client.render_html("template-id")
```

---

## Dynamic data

All fields are **optional**. Include only what your template actually uses.

```python
# Nothing — template has no merge tags or visibility rules
client.render_html("template-id")

# Text merge tags only
client.render_html("template-id", {
    "merge_tags": {
        "text": {"name": "Noruwa", "company": "Maildeno"},
    },
})

# URL merge tags only
client.render_html("template-id", {
    "merge_tags": {
        "url": {
            "reset_url":    "https://app.example.com/reset",
            "banner_image": "https://cdn.example.com/banner.jpg",
        },
    },
})

# HTML attribute merge tags (alt text, aria-labels, etc.)
client.render_html("template-id", {
    "merge_tags": {
        "attr": {"alt_text": "Product banner"},
    },
})

# Context — controls visibility rules (show/hide rows)
client.render_html("template-id", {
    "context": {
        "plan":    "pro",
        "country": "usa",
        "age":     25,
    },
})

# Everything together
client.render(
    template_id="template-id",
    target="mjml",
    dynamic_data={
        "merge_tags": {
            "text": {"name": "Noruwa", "company": "Maildeno", "reset_name": "Password"},
            "url":  {"reset_url": "https://app.example.com/reset/abc123"},
            "attr": {"alt_text": "Cave image"},
        },
        "context": {
            "country":      "usa",
            "country_rank": "2",
            "expiry":       "2028",
        },
    },
)
```

### Merge tag types

| Type   | Used for                                  | Escaping applied     |
|--------|-------------------------------------------|----------------------|
| `text` | Paragraph / heading / list / button text  | HTML-escaped         |
| `url`  | `href`, `src`, image URLs                 | URL percent-encoded  |
| `attr` | HTML attributes (alt, aria-label, etc.)   | HTML attribute-safe  |

### Context vs merge_tags

- **`merge_tags`** — replaces `{{ placeholders }}` inside the template content
- **`context`** — evaluated against visibility rules to show or hide rows/sections. Not injected into content.

---

## Error handling

All errors raised by the SDK are instances of `MaildenoError`.

```python
from maildeno import MaildenoClient, MaildenoError

try:
    html = client.render_html("template-id")
except MaildenoError as err:
    if err.code == "INVALID_API_KEY":
        # 401 — key is missing, malformed, revoked, or expired
        print("Check your API key")
    elif err.code == "FORBIDDEN":
        # 403 — key does not have scope for the requested target
        # e.g. key was created with targets=["html"] but you requested "mjml"
        print("Key scope:", err.message)
    elif err.code == "TEMPLATE_NOT_FOUND":
        # 404
        print("Template not found")
    elif err.code == "RENDER_ERROR":
        # 422 — template data is invalid or render failed
        print("Render failed:", err.message)
    elif err.code == "NETWORK_ERROR":
        # httpx transport error — DNS failure, connection refused, etc.
        print("Network error:", err.message)
    elif err.code == "TIMEOUT":
        # Request exceeded the configured timeout
        print("Request timed out")
```

### Error properties

```python
err.code     # SdkErrorCode — machine-readable
err.message  # Human-readable detail from the API
err.status   # HTTP status code (0 for NETWORK_ERROR / TIMEOUT)
err.issues   # list[ValidationIssue] | None — populated on 422 validation errors
```

#### Inspecting validation errors

When the API rejects a request because the input itself is malformed (for example,
a `template_id` that isn't a valid UUID), the SDK surfaces every pydantic issue
on `err.issues`:

```python
try:
    client.render_html("not-a-uuid")
except MaildenoError as err:
    if err.issues:
        for issue in err.issues:
            print(".".join(map(str, issue["loc"])), issue["msg"])
            # → body.template_id  Input should be a valid UUID, ...
```

---


### Frontend / browser usage

Not applicable — this is a server-side SDK. **Never** ship your API key to a browser. If you need to render templates from a frontend, expose a thin endpoint on your backend (see the FastAPI example below) and have the frontend call that.

---

## FastAPI example

```python
# main.py
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from maildeno import AsyncMaildenoClient, MaildenoError


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One client per process — reused across all requests.
    app.state.maildeno = AsyncMaildenoClient(
        api_key=os.environ["MAILDENO_API_KEY"]
    )
    yield
    await app.state.maildeno.aclose()


app = FastAPI(lifespan=lifespan)


class RenderRequest(BaseModel):
    template_id: str
    name: str
    plan: str


@app.post("/api/render-email")
async def render_email(req: RenderRequest):
    try:
        html = await app.state.maildeno.render_html(
            req.template_id,
            {
                "merge_tags": {"text": {"name": req.name}},
                "context": {"plan": req.plan},
            },
        )
        return {"html": html}
    except MaildenoError as err:
        raise HTTPException(
            status_code=err.status or 500,
            detail={
                "error": err.code,
                "message": err.message,
                "issues": err.issues,
            },
        )
```

---

## Flask example

```python
# app.py
import os
from flask import Flask, jsonify, request
from maildeno import MaildenoClient, MaildenoError

app = Flask(__name__)
maildeno = MaildenoClient(
    api_key=os.environ["MAILDENO_API_KEY"]
)


@app.post("/api/render-email")
def render_email():
    data = request.get_json()
    try:
        html = maildeno.render_html(
            data["template_id"],
            {
                "merge_tags": {"text": {"name": data["name"]}},
                "context": {"plan": data["plan"]},
            },
        )
        return jsonify(html=html)
    except MaildenoError as err:
        return jsonify(error=err.code, message=err.message), err.status or 500
```

---

## Django example

```python
# views.py
import json
import os
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from maildeno import MaildenoClient, MaildenoError

# Module-level — reused across requests within the worker process.
maildeno = MaildenoClient(
    api_key=os.environ["MAILDENO_API_KEY"]
)


@require_POST
def render_email(request):
    data = json.loads(request.body)
    try:
        html = maildeno.render_html(
            data["template_id"],
            {"merge_tags": {"text": {"name": data["name"]}}},
        )
        return JsonResponse({"html": html})
    except MaildenoError as err:
        return JsonResponse(
            {"error": err.code, "message": err.message},
            status=err.status or 500,
        )
```

---

## Usage in different environments

### Sync vs async — which to pick

| Use case                                   | Pick                  |
|--------------------------------------------|-----------------------|
| Flask, Django (WSGI), scripts, Celery jobs | `MaildenoClient`      |
| FastAPI, Starlette, aiohttp, async tasks   | `AsyncMaildenoClient` |

Both have the same API and behaviour — only the call style differs.

### Bring your own `httpx` client

For advanced cases (custom transports, mTLS, proxies, retries via `httpx-retries`, observability hooks, etc.) you can pass in a pre-configured `httpx.Client` / `httpx.AsyncClient`:

```python
import httpx
from maildeno import MaildenoClient

http = httpx.Client(
    timeout=15.0,
    transport=httpx.HTTPTransport(retries=3),
    headers={"X-Tenant-Id": "acme-corp"},
)

client = MaildenoClient(api_key="...", http_client=http)
# ...your code...
http.close()  # you own the lifecycle when you inject one
```

When you inject an HTTP client, **you** own its lifecycle — the SDK won't close it on your behalf.

### Environment variables

This SDK reads no environment variables itself. Pass values through the constructor — load them with `os.environ`, `pydantic-settings`, `python-decouple`, or whatever your project uses.

---

## API key scopes

API keys can be scoped to specific targets at creation time:

| Targets value     | Allowed render calls                        |
|-------------------|---------------------------------------------|
| `["all"]`         | `html`, `react-email`, `mjml`               |
| `["html"]`        | `html` only — `react-email` / `mjml` → 403  |
| `["html","mjml"]` | `html` and `mjml` — `react-email` → 403     |

Create scoped keys via the API:

```bash
curl -X POST https://api.maildeno.com/api/v1/keys \
  -H "Content-Type: application/json" \
  -d '{"name": "HTML only", "targets": ["html"]}'
```

---

## Type hints

The package is fully typed (PEP 561) — `mypy`, `pyright`, and your IDE will all see the annotations.

All public types are exported from the package root:

```python
from maildeno import (
    MaildenoClient,
    AsyncMaildenoClient,
    MaildenoError,
)
from maildeno import (
    RenderTarget,     # Literal["html", "react-email", "mjml"]
    RenderResult,     # frozen dataclass
    DynamicData,      # TypedDict({merge_tags?, context?})
    MergeTagGroup,    # TypedDict({text?, url?, attr?})
    ContextValue,     # str | int | float | bool
    SdkErrorCode,     # Literal[...] of error code strings
    ValidationIssue,  # TypedDict for entries on err.issues
)
```

`DynamicData` and `MergeTagGroup` are `TypedDict`s — you can pass plain `dict` literals and the type checker will validate keys/values for you.

---

## Requirements

- Python 3.9+
- `httpx >= 0.28.1, < 1.0` (the only runtime dependency)

---

## License

MIT

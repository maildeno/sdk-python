# maildeno

Official Python SDK for the **Maildeno** render API.

Fetches template JSON from the Maildeno server, caches it locally (memory or disk), and renders HTML / React Email TSX / MJML **in-process** using an embedded Wasm engine — so your merge tags and dynamic data never leave your server.

Sync and async clients, full type hints, single runtime dependency (`httpx`).

---

## Table of contents

- [Installation](#installation)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [How caching works](#how-caching-works)
  - [Cache sequence (Maildeno server → your project)](#cache-sequence-maildeno-server--your-project)
  - [Memory cache](#memory-cache)
  - [Disk cache](#disk-cache)
  - [Stale-on-error fallback](#stale-on-error-fallback)
  - [Cache inspection & management](#cache-inspection--management)
- [Rendering](#rendering)
  - [`render()` — full control](#render--full-control)
  - [Convenience methods](#convenience-methods)
  - [Detecting cache vs server render](#detecting-cache-vs-server-render)
- [Async client](#async-client)
- [Dynamic data](#dynamic-data)
  - [Merge tags](#merge-tags)
  - [Visibility context](#visibility-context)
- [Error handling](#error-handling)
  - [Error codes](#error-codes)
  - [Per-error-type handling](#per-error-type-handling)
  - [Global error handler pattern](#global-error-handler-pattern)
- [Framework examples](#framework-examples)
  - [FastAPI](#fastapi)
  - [Django](#django)
  - [Flask](#flask)
  - [Celery task](#celery-task)
- [TypedDict reference](#typeddict-reference)
- [Changelog](#changelog)

---

## Installation

```bash
pip install maildeno
# or
poetry add maildeno
# or
uv add maildeno
```

**Requirements:** Python 3.9+. Runtime dependencies: `httpx >= 0.25, < 1.0` and `wasmtime`.

---

## Quick start

**Sync:**

```python
from maildeno import MaildenoClient

client = MaildenoClient(api_key="sk_live_4a7f2c8d...")

# Render to HTML — template is fetched from Maildeno, cached locally,
# and rendered in-process with the embedded Wasm engine.
html = client.render_html("550e8400-e29b-41d4-a716-446655440000")
print(html)  # <!DOCTYPE html>...
```

**Async:**

```python
import asyncio
from maildeno import AsyncMaildenoClient

async def main():
    async with AsyncMaildenoClient(api_key="sk_live_4a7f2c8d...") as client:
        html = await client.render_html("550e8400-e29b-41d4-a716-446655440000")
        print(html)

asyncio.run(main())
```

---

## Configuration

```python
from maildeno import MaildenoClient

client = MaildenoClient(
    # ── Required ────────────────────────────────────────────────────────────
    api_key="sk_live_...",
    # Obtain from: Maildeno Dashboard → API Keys → Create Key

    # ── Optional ────────────────────────────────────────────────────────────

    # Base URL of the Maildeno API. Defaults to "https://api.maildeno.com".
    # Override only when proxying through your own infrastructure or running
    # a self-hosted Maildeno instance.
    base_url="https://maildeno-proxy.yourcompany.com",  # OPTIONAL

    # Request timeout in seconds. Defaults to 30.0.
    timeout=10.0,  # OPTIONAL

    # Cache configuration. Omit to use memory caching with default settings.
    # See "How caching works" for the full explanation.
    cache={
        # "memory" (default) or "disk"
        "type": "memory",             # OPTIONAL — defaults to "memory"

        # Directory for disk cache. Required when type is "disk".
        # Created automatically if it doesn't exist.
        "path": "/var/cache/maildeno",  # OPTIONAL — only used when type="disk"

        # How long a cached template is considered fresh (milliseconds).
        # After TTL expires a re-fetch is attempted.
        # Stale copy is used as fallback if the server is unreachable.
        "ttl": 300_000,               # OPTIONAL — defaults to 300_000 (5 min)

        # Max template entries before the oldest is evicted.
        "max_entries": 50,            # OPTIONAL — defaults to 50
    },
)
```

> **Tip:** Instantiate the client **once** at application startup and reuse it
> across requests. The cache and the Wasm engine instance are both attached to
> the client. For `AsyncMaildenoClient`, use as a context manager or call
> `await client.aclose()` during shutdown.

---

## How caching works

### Cache sequence (Maildeno server → your project)

When you call any render method, the SDK follows this sequence:

```
Your code
   │
   ▼
┌──────────────────────────────────────┐
│  client.render_html(template_id)     │
│                                      │
│  1. Check in-process memory cache ◄──┼── CACHE HIT → render immediately (0 ms)
│                        │             │
│      cache miss        ▼             │
│                                      │
│  2. Check disk cache (if configured) │
│     read <path>/<id>.json        ◄───┼── CACHE HIT → load into memory, render
│                        │             │
│      cache miss        ▼             │
│                                      │
│  3. Fetch template JSON from         │
│     GET https://api.maildeno.com     │
│         /v1/sdk/template/<id>    ◄───┼── NETWORK CALL (only on first render
│                        │             │   or after TTL expiry)
│      store in cache    ▼             │
│                                      │
│  4. Render template + dynamic data   │
│     in-process with Wasm engine      │
│     (merge tags & context never      │
│     leave your server)               │
└──────────────────────────────────────┘
   │
   ▼
rendered HTML / TSX / MJML string
```

**Key points:**
- The network call to Maildeno happens **at most once per template per TTL window**.
- After the first fetch, every subsequent render is pure CPU — no I/O, no latency.
- The Wasm engine processes your merge tags locally. Dynamic data is never sent to Maildeno's servers.
- `AsyncMaildenoClient` dispatches the Wasm render to `asyncio`'s thread-pool executor so the event loop is never blocked.

---

### Memory cache

The default. Zero configuration required. Lives in the process heap.
Thread-safe: a `threading.Lock` ensures exactly-once Wasm engine initialisation.

```python
# Defaults — no cache config needed
client = MaildenoClient(api_key="sk_live_...")

# Explicit memory config with custom TTL
client = MaildenoClient(
    api_key="sk_live_...",
    cache={
        "type": "memory",      # OPTIONAL — "memory" is the default
        "ttl": 60_000,         # OPTIONAL — 60 s instead of 5 min
        "max_entries": 100,    # OPTIONAL — hold up to 100 templates
    },
)
```

**When to use:** Always — unless your process restarts frequently (e.g. AWS Lambda,
Cloud Run) and you want the cache to survive cold starts.

**Trade-off:** Lost on process exit. Each worker process (Gunicorn, uWSGI) maintains
its own independent cache.

---

### Disk cache

Persists template JSON to the local filesystem. Survives process restarts and
is shared by all workers pointing at the same directory.

```python
client = MaildenoClient(
    api_key="sk_live_...",
    cache={
        "type": "disk",
        "path": "/var/cache/maildeno",  # OPTIONAL — defaults to ".maildeno-cache"
        "ttl": 600_000,                 # OPTIONAL — 10 min
        "max_entries": 200,             # OPTIONAL
    },
)
```

Each template is stored as a JSON file: `<path>/<template-id>.json`.

Writes are atomic (temp file + `os.replace`) so concurrent Gunicorn workers
safely write the same files without corruption.

**When to use:**
- AWS Lambda / Cloud Run / serverless where cold-start latency from a network
  fetch adds unacceptable overhead.
- Multi-worker Gunicorn / uWSGI deployments sharing a filesystem.
- Long-lived background workers where you want cache persistence across restarts.

---

### Stale-on-error fallback

When a cached template's TTL expires, the SDK attempts a re-fetch.
If the Maildeno server is unreachable (network error, timeout, 5xx), the SDK:

1. Renders from the **last known-good cached copy** (stale entry).
2. Sets `result.from_stale_cache = True` on the returned `RenderResult`.
3. Does **not** raise — your send pipeline continues uninterrupted.

The SDK only raises when the server is unreachable **and** no prior cached copy
exists for that template (first-ever render with no cache entry).

```python
result = client.render(
    template_id="550e8400-e29b-41d4-a716-446655440000",
    target="html",
)

if result.from_stale_cache:
    # OPTIONAL — log or alert that Maildeno was unreachable.
    # The output is still valid HTML rendered from the last cached template.
    logger.warning(
        "Rendered from stale cache — Maildeno may be down",
        extra={"template_id": result.template_id},
    )
```

---

### Cache inspection & management

```python
# List template IDs currently held in the cache
ids = client.list_cached()
# ["550e8400-...", "9ec0c043-..."]

# Remove a single template immediately (e.g. after updating it on the dashboard)
client.delete_cached("550e8400-e29b-41d4-a716-446655440000")

# Wipe the entire cache
client.clear_cache()
```

---

## Rendering

### `render()` — full control

```python
from maildeno import MaildenoClient
from maildeno import RenderResult

client = MaildenoClient(api_key="sk_live_...")

result: RenderResult = client.render(
    template_id="550e8400-e29b-41d4-a716-446655440000",  # required
    target="html",        # "html" | "react-email" | "mjml" — OPTIONAL, defaults to "html"
    dynamic_data={        # OPTIONAL — omit if your template has no dynamic content
        "merge_tags": {
            "text": {"first_name": "Noruwa", "company": "Maildeno"},
            "url":  {"unsubscribe_url": "https://example.com/unsub"},
            "attr": {"logo_alt": "Maildeno logo"},
        },
        "context": {
            "plan_name": "Pro",   # str  — show plan-specific block
        },
    },
)

print(result.output)           # rendered string
print(result.target)           # "html"
print(result.template_id)      # "550e8400-..."
print(result.from_stale_cache) # False (or True if server was unreachable)
```

---

### Convenience methods

These return the rendered **string** directly — no need to access `result.output`.

```python
# Render to HTML
html = client.render_html(
    "550e8400-e29b-41d4-a716-446655440000",
    dynamic_data={                      # OPTIONAL — same shape as render()
        "merge_tags": {"text": {"name": "Noruwa"}},
    },
)

# Render to React Email TSX
tsx = client.render_react(
    "550e8400-e29b-41d4-a716-446655440000",
    dynamic_data={                      # OPTIONAL
        "merge_tags": {"text": {"name": "Noruwa"}},
    },
)

# Render to MJML
mjml = client.render_mjml(
    "550e8400-e29b-41d4-a716-446655440000",
    dynamic_data={                      # OPTIONAL
        "merge_tags": {"text": {"name": "Noruwa"}},
    },
)
```

> **Convenience vs `render()`:** Use the convenience methods when you only need
> the output string. Use `render()` when you need `result.from_stale_cache`,
> `result.target`, or `result.template_id`.

---

### Detecting cache vs server render

Use `render()` (not the convenience methods) to inspect `from_stale_cache`.

```python
import logging
from maildeno import MaildenoClient

logger = logging.getLogger(__name__)

client = MaildenoClient(
    api_key="sk_live_...",
    cache={"ttl": 300_000},
)

def send_welcome_email(user: dict):
    result = client.render(
        template_id="550e8400-e29b-41d4-a716-446655440000",
        target="html",
        dynamic_data={                          # OPTIONAL
            "merge_tags": {
                "text": {"first_name": user["name"]},
            },
            "context": {
                "is_new_user": True,            # OPTIONAL — visibility context
            },
        },
    )

    # ── Cache diagnostic (OPTIONAL) ──────────────────────────────────────
    if result.from_stale_cache:
        # Rendered from cached copy — Maildeno was unreachable.
        # Output is still valid; log for monitoring only.
        logger.warning(
            "[maildeno] stale cache used",
            extra={"template_id": result.template_id, "user_id": user["id"]},
        )
    else:
        logger.debug("[maildeno] rendered fresh", extra={"template_id": result.template_id})
    # ── End cache diagnostic ──────────────────────────────────────────────

    email_provider.send(
        to=user["email"],
        subject="Welcome!",
        html=result.output,
    )
```

---

## Async client

`AsyncMaildenoClient` mirrors `MaildenoClient` exactly, with `async`/`await`.
All methods are awaitable. The Wasm render runs in `asyncio`'s thread executor
so the event loop is never blocked.

```python
import asyncio
from maildeno import AsyncMaildenoClient, MaildenoError

async def main():
    # Use as a context manager for automatic cleanup
    async with AsyncMaildenoClient(
        api_key="sk_live_...",
        cache={"ttl": 300_000},       # OPTIONAL
    ) as client:

        # render() — full result
        result = await client.render(
            template_id="550e8400-e29b-41d4-a716-446655440000",
            target="html",
            dynamic_data={            # OPTIONAL
                "merge_tags": {"text": {"name": "Noruwa"}},
            },
        )

        if result.from_stale_cache:   # OPTIONAL
            print("stale cache used")

        # Convenience methods
        html  = await client.render_html("template-id")
        tsx   = await client.render_react("template-id")
        mjml  = await client.render_mjml("template-id")

        # Cache management — also async
        ids = await client.list_cached()
        await client.delete_cached("template-id")
        await client.clear_cache()

asyncio.run(main())
```

**Without context manager** — call `aclose()` during shutdown:

```python
client = AsyncMaildenoClient(api_key="sk_live_...")

# Use client across your app ...

# On shutdown:
await client.aclose()
```

---

## Dynamic data

All fields are **optional**. Include only what your template actually uses.

### Merge tags

Merge tags replace placeholders in the template's content.

```python
dynamic_data = {
    "merge_tags": {
        # Text values — inserted as plain text
        "text": {
            "first_name":    "Noruwa",
            "last_name":     "Lovelace",
            "company_name":  "Anthropic",
            "plan_name":     "Pro",
        },

        # URL values — used in link hrefs / button targets
        "url": {
            "cta_url":          "https://app.example.com/dashboard",
            "unsubscribe_url":  "https://example.com/unsub?token=abc",
            "logo_url":         "https://cdn.example.com/logo.png",
        },

        # Attribute values — used in HTML attributes (alt, title, etc.)
        "attr": {
            "logo_alt":      "Example Inc.",
            "banner_title":  "Summer Sale",
        },
    },
}
```

### Visibility context

Context values drive **show/hide** rules configured in the Maildeno template
editor. Rows and blocks are conditionally shown based on these values —
without touching any template code.

```python
dynamic_data = {
    "context": {
        "is_premium":   True,    # bool  — show premium upsell row
        "plan_name":    "Pro",   # str   — show plan-specific content block
        "days_left":    3,       # int   — show trial-expiry warning when < 7
        "has_balance":  False,   # bool  — hide payment-due row when False
    },
}
```

---

## Error handling

All SDK errors are instances of `MaildenoError`, which extends `Exception`.

```python
from maildeno import MaildenoClient, MaildenoError

client = MaildenoClient(api_key="sk_live_...")

try:
    html = client.render_html("550e8400-e29b-41d4-a716-446655440000")
    # use html ...
except MaildenoError as err:
    print(err.code)     # "TEMPLATE_NOT_FOUND" | "INVALID_API_KEY" | ...
    print(err.status)   # HTTP status: 401 | 403 | 404 | 422 | 0 (network errors)
    print(err.message)  # Human-reNoruwable description

    # OPTIONAL — structured validation issues (only on RENDER_ERROR / 422)
    if err.issues:
        for issue in err.issues:
            field = ".".join(str(p) for p in issue.get("loc", []))
            print(f"  Field: {field} — {issue.get('msg')}")
```

### Error codes

| Code | HTTP | When it happens |
|------|------|-----------------|
| `INVALID_API_KEY` | 401 | API key is missing, malformed, or revoked |
| `FORBIDDEN` | 403 | Key lacks scope for this target, or plan limit reached |
| `TEMPLATE_NOT_FOUND` | 404 | `template_id` does not exist in your account |
| `RENDER_ERROR` | 422 | Template render failed — check `err.issues` for field details |
| `NETWORK_ERROR` | 0 | `httpx` raised a connection-level exception |
| `TIMEOUT` | 0 | Request exceeded the configured `timeout` |
| `UNKNOWN` | varies | Unexpected response from the server |

### Per-error-type handling

```python
import time
import logging
from maildeno import MaildenoClient, MaildenoError

logger = logging.getLogger(__name__)
client = MaildenoClient(api_key="sk_live_...")

def render_with_fallback(template_id: str, fallback_html: str) -> str:
    try:
        return client.render_html(template_id)

    except MaildenoError as err:
        if err.code == "TEMPLATE_NOT_FOUND":
            # OPTIONAL — fall back to a hardcoded HTML string
            logger.warning(f"Template {template_id} not found, using fallback")
            return fallback_html

        if err.code == "INVALID_API_KEY":
            # Configuration error — alert immediately, do not retry
            raise RuntimeError("Maildeno API key is invalid. Check MAILDENO_API_KEY.") from err

        if err.code == "FORBIDDEN":
            # Key doesn't have access to this render target
            raise RuntimeError(f"Access denied: {err.message}") from err

        if err.code == "RENDER_ERROR":
            # Template has invalid merge tag references or bad config
            # OPTIONAL — log structured issues for debugging
            logger.error("Render failed: %s", err.issues)
            raise

        if err.code in ("TIMEOUT", "NETWORK_ERROR"):
            # Transient — OPTIONAL: retry once after a short delay
            logger.warning("Maildeno unreachable, retrying in 2s...")
            time.sleep(2)
            return client.render_html(template_id)  # retry once

        raise  # unknown error — re-raise
```

### Global error handler pattern

```python
# lib/maildeno.py — centralise client & error handling for your whole app
import logging
import os
from maildeno import MaildenoClient, MaildenoError

logger = logging.getLogger(__name__)

# Instantiate once — the cache lives on this object
maildeno = MaildenoClient(
    api_key=os.environ["MAILDENO_API_KEY"],
    cache={
        "type": "disk",                   # OPTIONAL — persist across restarts
        "path": "/tmp/maildeno-cache",
        "ttl": 300_000,
    },
)

def safe_render(
    template_id: str,
    *,
    target: str = "html",
    dynamic_data: dict | None = None,     # OPTIONAL
) -> str | None:
    """
    Render a template with consistent error handling.
    Returns None on non-fatal errors (network, timeout) so your email
    pipeline can skip gracefully instead of crashing.
    """
    try:
        result = maildeno.render(
            template_id=template_id,
            target=target,
            dynamic_data=dynamic_data,
        )

        if result.from_stale_cache:         # OPTIONAL diagnostic
            logger.warning("[maildeno] stale cache used for %s", template_id)

        return result.output

    except MaildenoError as err:
        if err.code in ("TIMEOUT", "NETWORK_ERROR"):
            logger.error("[maildeno] unreachable — skipping render: %s", err.message)
            return None  # OPTIONAL — return None instead of crashing
        raise  # fatal errors bubble up
```

---

## Framework examples

### FastAPI

```python
# main.py
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from maildeno import AsyncMaildenoClient, MaildenoError

logger = logging.getLogger(__name__)

# Hold a reference at module level — initialised in lifespan
maildeno: AsyncMaildenoClient

@asynccontextmanager
async def lifespan(app: FastAPI):
    global maildeno
    maildeno = AsyncMaildenoClient(
        api_key=os.environ["MAILDENO_API_KEY"],
        cache={
            "type": "disk",               # OPTIONAL — persist across cold starts
            "path": "/tmp/maildeno-cache",
            "ttl": 300_000,
        },
    )
    yield
    await maildeno.aclose()

app = FastAPI(lifespan=lifespan)


class SendRequest(BaseModel):
    template_id: str
    email: str
    name: str


@app.post("/emails/send")
async def send_email(body: SendRequest):
    try:
        result = await maildeno.render(
            template_id=body.template_id,
            target="html",
            dynamic_data={                # OPTIONAL
                "merge_tags": {
                    "text": {"first_name": body.name},
                },
                "context": {"is_new_user": True},   # OPTIONAL
            },
        )
    except MaildenoError as err:
        # Map SDK error codes to HTTP responses
        status_map = {
            "INVALID_API_KEY":    500,
            "FORBIDDEN":          500,
            "TEMPLATE_NOT_FOUND": 404,
            "RENDER_ERROR":       500,
            "NETWORK_ERROR":      503,
            "TIMEOUT":            503,
        }
        raise HTTPException(
            status_code=status_map.get(err.code, 500),
            detail={"error": err.code, "message": err.message},
        )

    if result.from_stale_cache:           # OPTIONAL — log stale cache usage
        logger.warning("stale cache: %s", result.template_id)

    await email_provider.send(to=body.email, subject="Hello!", html=result.output)
    return {"sent": True, "from_cache": not result.from_stale_cache}
```

---

### Django

```python
# myapp/email_utils.py
import os
from maildeno import MaildenoClient, MaildenoError

# Module-level singleton — shared across all Django request threads.
# Thread-safe: the SDK's memory cache uses a threading.Lock internally.
maildeno = MaildenoClient(
    api_key=os.environ["MAILDENO_API_KEY"],
    cache={
        "type": "disk",               # OPTIONAL — shared across Gunicorn workers
        "path": "/tmp/maildeno-cache",
        "ttl": 300_000,
    },
)


def send_welcome_email(user):
    try:
        result = maildeno.render(
            template_id=os.environ["WELCOME_TEMPLATE_ID"],
            target="html",
            dynamic_data={            # OPTIONAL
                "merge_tags": {
                    "text": {
                        "first_name": user.first_name,
                        "username":   user.username,
                    },
                    "url": {
                        "dashboard_url": f"https://example.com/dashboard/{user.pk}",
                    },
                },
                "context": {
                    "is_new_user": True,          # OPTIONAL — visibility context
                    "email_verified": user.is_active,
                },
            },
        )
    except MaildenoError as err:
        # Log and re-raise or handle gracefully
        import logging
        logging.getLogger(__name__).error(
            "Maildeno render failed: %s %s", err.code, err.message
        )
        raise

    if result.from_stale_cache:        # OPTIONAL diagnostic
        import logging
        logging.getLogger(__name__).warning(
            "Stale cache used for template %s", result.template_id
        )

    from django.core.mail import send_mail
    send_mail(
        subject="Welcome!",
        message="",
        from_email="noreply@example.com",
        recipient_list=[user.email],
        html_message=result.output,
    )
```

---

### Flask

```python
# app.py
import os
import logging
from flask import Flask, request, jsonify
from maildeno import MaildenoClient, MaildenoError

app = Flask(__name__)
logger = logging.getLogger(__name__)

# Module-level singleton
maildeno = MaildenoClient(
    api_key=os.environ["MAILDENO_API_KEY"],
    cache={"ttl": 300_000},              # OPTIONAL
)


@app.post("/emails/send")
def send_email():
    body = request.get_json()

    try:
        result = maildeno.render(
            template_id=body["template_id"],
            target="html",
            dynamic_data={              # OPTIONAL
                "merge_tags": {
                    "text": {"name": body.get("name", "")},
                },
            },
        )
    except MaildenoError as err:
        code_to_status = {
            "TEMPLATE_NOT_FOUND": 404,
            "NETWORK_ERROR":      503,
            "TIMEOUT":            503,
        }
        return jsonify(error=err.code, message=err.message), code_to_status.get(err.code, 500)

    if result.from_stale_cache:         # OPTIONAL
        logger.warning("stale cache: %s", result.template_id)

    email_provider.send(to=body["email"], html=result.output)
    return jsonify(sent=True)
```

---

### Celery task

```python
# tasks.py
import os
import logging
from celery import Celery
from maildeno import MaildenoClient, MaildenoError

logger = logging.getLogger(__name__)
celery_app = Celery("tasks", broker=os.environ["REDIS_URL"])

# Worker-level singleton — one client per Celery worker process
maildeno = MaildenoClient(
    api_key=os.environ["MAILDENO_API_KEY"],
    cache={
        "type": "disk",               # OPTIONAL — shared across worker processes
        "path": "/tmp/maildeno-cache",
        "ttl": 300_000,
    },
)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=5)
def send_welcome_email(self, user_id: str, email: str, name: str):
    try:
        result = maildeno.render(
            template_id=os.environ["WELCOME_TEMPLATE_ID"],
            target="html",
            dynamic_data={            # OPTIONAL
                "merge_tags": {
                    "text": {"first_name": name},
                    "url":  {"activate_url": f"https://example.com/activate/{user_id}"},
                },
                "context": {"is_new_user": True},   # OPTIONAL
            },
        )
    except MaildenoError as err:
        if err.code in ("TIMEOUT", "NETWORK_ERROR"):
            # Transient — retry via Celery retry mechanism
            raise self.retry(exc=err)
        # Fatal errors — do not retry
        logger.error("Maildeno fatal error: %s %s", err.code, err.message)
        raise

    if result.from_stale_cache:        # OPTIONAL diagnostic
        logger.warning("stale cache used for user %s", user_id)

    email_provider.send(to=email, html=result.output)
```

---

## TypedDict reference

All public types are exported from the package root:

```python
from maildeno import (
    MaildenoClient,
    AsyncMaildenoClient,
    MaildenoError,
)
from maildeno import (
    RenderTarget,      # Literal["html", "react-email", "mjml"]
    RenderResult,      # frozen dataclass: template_id, target, output, from_stale_cache
    DynamicData,       # TypedDict: { merge_tags?, context? }
    MergeTagGroup,     # TypedDict: { text?, url?, attr? }
    ContextValue,      # Union[str, int, float, bool]
    CacheConfig,       # TypedDict: { type?, path?, ttl?, max_entries? }
    TemplateJson,      # TypedDict: raw shape of GET /v1/sdk/template/{id}
    SdkErrorCode,      # Literal[...] of error code strings
    ValidationIssue,   # TypedDict: { loc, msg, type } — entries on err.issues
)
```

**`RenderResult` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `output` | `str` | The rendered string (HTML, TSX, or MJML) |
| `target` | `RenderTarget` | Which target was rendered |
| `template_id` | `str` | UUID of the template |
| `from_stale_cache` | `bool` | `True` if rendered from stale cache due to server being unreachable |

`DynamicData` and `MergeTagGroup` are `TypedDict`s — you can pass plain `dict`
literals and the type checker will validate keys/values for you.

---

## Changelog

See [CHANGELOG.md](./CHANGELOG.md) for the full version history.

---

## License

MIT
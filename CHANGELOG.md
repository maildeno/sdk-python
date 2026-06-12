# Changelog

All notable changes to this project will be documented in this file.
This project adheres to [Semantic Versioning](https://semver.org/).

---

## [2.0.1] - 2026-06-12

### Fixed

- **Visibility context now handles non-string values.** Context passed as a
  number or boolean (e.g. `context: { premium: true }`, `context: { tier: 2 }`)
  was read as empty, so visibility rules comparing against it never matched and
  the affected rows/columns were always hidden. Numbers and booleans are now
  coerced for comparison (`2` matches `"2"`, `true` matches `"true"`). String
  context values were unaffected and continue to work.

- **Visibility context keys are now matched case-insensitively.** The rule tag
  was lowercased before lookup while the context key was matched exactly, so
  camelCase or capitalized keys (e.g. `orderCount`, `Premium`) never resolved.
  Keys now match regardless of case. Note that the rule's `tag` must still
  match the context key *name* — `isPremium` and `premium` remain distinct.

## [2.0.0] - 2026-06-07

### Changed — breaking

- **Rendering is now local.** The SDK no longer sends `dynamic_data` to the
  Maildeno server. Template JSON is fetched once via
  `GET /v1/sdk/template/{id}` and rendered in-process using the embedded
  Wasm engine. Merge tags and visibility context never leave your server.
- `POST /v1/sdk/render` is no longer called. Existing direct HTTP integrations
  continue to work until the endpoint is removed (see sunset date in the
  `Deprecation` response header).
- `RenderResult` now has an optional `from_stale_cache: bool = False` field.
  Code that accesses `.output`, `.target`, `.template_id` is unaffected.
- `invalidate(template_id)` is deprecated. Use `delete_cached(template_id)`.

### Added

- **`wasmtime`** added as a runtime dependency — the Wasm engine that runs
  the embedded renderer. Supports Linux, macOS, and Windows on Python 3.9+.
- **`engine.wasm`** shipped inside the `maildeno` package. Located via
  `importlib.resources` — works correctly in wheels, editable installs,
  virtualenvs, Lambda layers, and Docker containers.
- **In-process template cache.** Template JSON is cached after the first
  fetch. Subsequent calls to the same `template_id` render with zero network
  overhead.
- **Stale-on-error fallback.** If the cache TTL expires and the server cannot
  be reached, the SDK renders from the last known-good cached copy and sets
  `result.from_stale_cache = True`. Your send pipeline continues uninterrupted
  during Maildeno downtime. Only throws when the server is unreachable *and*
  no prior cached copy exists for that template.
- **`cache=` constructor parameter.** Controls the caching strategy — memory
  (default, zero config) or disk (survives process restarts).

  ```python
  # Memory — default
  client = MaildenoClient(api_key="...", cache={"ttl": 60_000})

  # Disk — persists across restarts
  client = MaildenoClient(
      api_key="...",
      cache={"type": "disk", "path": "/var/cache/maildeno", "ttl": 300_000},
  )
  ```

- **`list_cached()`** — return the IDs of all templates currently in the cache.
- **`delete_cached(template_id)`** — remove a single template from the cache
  immediately, bypassing TTL. Replaces `invalidate()`.
- **`clear_cache()`** — wipe the entire cache.
- **`CacheConfig` TypedDict** exported from the package root.
- **`TemplateJson` TypedDict** exported from the package root — the shape of
  the raw template payload returned by `GET /v1/sdk/template/{id}`.
- **Thread-safe Wasm singleton.** The Wasm engine instance is loaded lazily on
  the first render call and reused for the process lifetime. A lock ensures
  exactly-once initialisation even under concurrent startup.
- **Async Wasm via thread executor.** `AsyncMaildenoClient` dispatches Wasm
  renders to `asyncio`'s default thread-pool executor so the event loop is
  never blocked.

### Migration from v1

```python
# v1 — server rendered
result = client.render(
    template_id="...",
    target="html",
    dynamic_data={"merge_tags": {"text": {"name": "Noruwa"}}},
)

# v2 — local render, identical call site
result = client.render(
    template_id="...",
    target="html",
    dynamic_data={"merge_tags": {"text": {"name": "Noruwa"}}},
)

# Optional: check if rendered from stale cache
if result.from_stale_cache:
    logger.warning("Stale cache used", extra={"template_id": result.template_id})
```

No changes required to `render_html()`, `render_react()`, or `render_mjml()`.

---

## [1.0.0] - 2026-05-29

### Added

- Initial public release
- `MaildenoClient` — synchronous client with `render()`, `render_html()`,
  `render_react()`, `render_mjml()`
- `AsyncMaildenoClient` — async mirror of the sync client, fully `await`-able
- Both clients support context-manager usage (`with` / `async with`)
- Both clients accept an injected `httpx.Client` / `httpx.AsyncClient`
- `MaildenoError` with `code`, `message`, `status`, and `issues`
- Structured `ValidationIssue` list on `err.issues` for 422 pydantic errors
- Full type hints, PEP 561 `py.typed` marker
- `DynamicData` and `MergeTagGroup` as `TypedDict`s
- Single runtime dependency: `httpx>=0.28.1,<1.0`
- Python 3.9 – 3.13 support

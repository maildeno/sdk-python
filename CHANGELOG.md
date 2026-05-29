# Changelog

All notable changes to this project will be documented in this file.
This project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-05-29

### Added
- Initial public release
- `MaildenoClient` — synchronous client with `render()`, `render_html()`, `render_react()`, `render_mjml()`
- `AsyncMaildenoClient` — async mirror of the sync client, fully `await`-able
- Both clients support context-manager usage (`with` / `async with`)
- Both clients accept an injected `httpx.Client` / `httpx.AsyncClient` for custom transports
- `MaildenoError` with `code`, `message`, `status`, and `issues` properties
- Structured `ValidationIssue` list on `err.issues` for 422 pydantic errors
- Full type hints throughout — PEP 561 `py.typed` marker included
- `DynamicData` and `MergeTagGroup` as `TypedDict`s for IDE-validated dict literals
- Single runtime dependency: `httpx>=0.25,<1.0`
- Python 3.9, 3.10, 3.11, 3.12, 3.13 support
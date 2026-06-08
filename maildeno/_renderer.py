"""Wasm bridge — loads engine.wasm and exposes ``render_template()``.

Memory contract with the Rust engine
─────────────────────────────────────
  alloc(len: i32) -> i32          allocate ``len`` bytes, return pointer
  dealloc(ptr: i32, len: i32)     free a previously alloc'd region
  dealloc_str(ptr: i32)           free a null-terminated result string
  render(ptr: i32, len: i32) -> i32
      Read ``len`` bytes of UTF-8 JSON from linear memory at ``ptr``,
      process, write a null-terminated UTF-8 JSON result elsewhere,
      return its pointer.
  heap_peak() -> i32
      Return peak heap bytes used since the last render() call started.
      Safe to call after every render(). Used for high-watermark monitoring.

Input JSON shape (→ Rust)::

    {
        "template":     <TemplateJson>,
        "target":       "html" | "react-email" | "mjml",
        "dynamic_data": { "merge_tags": {...}, "context": {...} }
    }

Output JSON shape (← Rust)::

    {"output": "...rendered string..."}   on success
    {"error":  "...message..."}           on failure

The singleton Wasm instance is loaded lazily on the first call to
``render_template()`` and reused for the lifetime of the process.
Thread safety: ``_INSTANCE_LOCK`` ensures the instance is initialised
exactly once even when multiple threads call ``render_template()``
concurrently on startup.
"""

from __future__ import annotations

import importlib.resources
import json
import logging
import threading
from typing import Any, Dict, Optional

from ._error import MaildenoError
from ._types import DynamicData, RenderTarget, TemplateJson

logger = logging.getLogger(__name__)

# Warn when heap usage exceeds this fraction of the 12 MB ceiling.
# 9 MB = 75 % of 12 MB. 
# Heap size for template rendering are normally less than 2 MB. Headroom over worst case - 5.5×
_HEAP_WARN_BYTES = 9 * 1024 * 1024

# ── Wasm singleton ────────────────────────────────────────────────────────────

_instance: Optional[Any] = None          # wasmtime.Instance
_store:    Optional[Any] = None          # wasmtime.Store
_INSTANCE_LOCK = threading.Lock()


def _get_instance() -> tuple[Any, Any]:
    """Return (store, instance), loading engine.wasm on first call."""
    global _instance, _store

    if _instance is not None:
        # Fast path — already loaded. No lock needed once set.
        return _store, _instance

    with _INSTANCE_LOCK:
        # Re-check inside the lock (another thread may have initialised while
        # we were waiting).
        if _instance is not None:
            return _store, _instance

        try:
            from wasmtime import Engine, Linker, Module, Store  # type: ignore[import-untyped]
        except ImportError as exc:
            raise MaildenoError(
                "RENDER_ERROR",
                "The 'wasmtime' package is required for local rendering. "
                "Install it with: pip install wasmtime",
            ) from exc

        # Locate engine.wasm using importlib.resources so it works regardless
        # of how the package is installed (wheel, editable, Lambda layer, etc.)
        try:
            pkg_files = importlib.resources.files("maildeno")
            wasm_path = pkg_files.joinpath("engine.wasm")
            wasm_bytes = wasm_path.read_bytes()
        except (FileNotFoundError, TypeError) as exc:
            raise MaildenoError(
                "RENDER_ERROR",
                "engine.wasm not found inside the maildeno package. "
                "Reinstall the package or ensure engine.wasm is present in maildeno/.",
            ) from exc

        engine = Engine()
        new_store: Any = Store(engine)
        module = Module(engine, wasm_bytes)
        linker = Linker(engine)
        new_instance = linker.instantiate(new_store, module)

        _store = new_store
        _instance = new_instance

    return _store, _instance


# ── Public render function ────────────────────────────────────────────────────

def render_template(
    template: TemplateJson,
    target: RenderTarget,
    dynamic_data: Optional[DynamicData],
) -> str:
    """Render a template using the embedded Wasm engine.

    Returns the rendered output string.

    :raises MaildenoError: with ``code="RENDER_ERROR"`` if the engine reports
        a failure or if ``engine.wasm`` cannot be loaded.
    """
    store, instance = _get_instance()

    exports = instance.exports(store)

    # ── Helpers ───────────────────────────────────────────────────────────────

    memory = exports["memory"]

    def alloc_fn(n: int) -> int:
        return exports["alloc"](store, n)  # type: ignore[no-any-return]

    def dealloc_fn(ptr: int, n: int) -> None:
        exports["dealloc"](store, ptr, n)

    def dealloc_str_fn(ptr: int) -> None:
        exports["dealloc_str"](store, ptr)

    def render_fn(ptr: int, n: int) -> int:
        return exports["render"](store, ptr, n)  # type: ignore[no-any-return]

    def heap_peak_fn() -> Optional[int]:
        """Call heap_peak() export if present; return None if not exported."""
        try:
            return exports["heap_peak"](store)  # type: ignore[no-any-return]
        except (KeyError, Exception):
            return None

    def read_mem(ptr: int, length: int) -> bytes:
        return bytes(memory.read(store, ptr, ptr + length))

    def write_mem(ptr: int, data: bytes) -> None:
        memory.write(store, data, ptr)

    def read_cstr(ptr: int) -> str:
        """Read a null-terminated UTF-8 string from Wasm linear memory."""
        buf = bytearray()
        offset = ptr
        while True:
            byte = memory.read(store, offset, offset + 1)[0]
            if byte == 0:
                break
            buf.append(byte)
            offset += 1
        return buf.decode("utf-8")

    # ── Call the engine ───────────────────────────────────────────────────────

    payload: Dict[str, Any] = {
        "template": template,
        "target": target,
        "dynamic_data": dynamic_data or {},
    }
    input_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    input_len = len(input_bytes)

    input_ptr = alloc_fn(input_len)
    write_mem(input_ptr, input_bytes)

    result_ptr = render_fn(input_ptr, input_len)
    dealloc_fn(input_ptr, input_len)

    # ── Heap peak monitoring ──────────────────────────────────────────────────
    peak = heap_peak_fn()
    if peak is not None and peak > _HEAP_WARN_BYTES:
        logger.warning(
            "[maildeno-engine] heap usage high: %.2f MB  target=%s",
            peak / 1024 / 1024,
            target,
        )
    # ─────────────────────────────────────────────────────────────────────────

    result_json = read_cstr(result_ptr)
    dealloc_str_fn(result_ptr)

    # ── Parse result ──────────────────────────────────────────────────────────

    try:
        parsed: Dict[str, Any] = json.loads(result_json)
    except json.JSONDecodeError as exc:
        raise MaildenoError(
            "RENDER_ERROR",
            f"Engine returned non-JSON: {result_json[:120]}",
        ) from exc

    if "error" in parsed:
        raise MaildenoError("RENDER_ERROR", parsed["error"])

    if "output" not in parsed or not isinstance(parsed["output"], str):
        raise MaildenoError("RENDER_ERROR", "Engine response missing 'output' field.")

    return parsed["output"]  # type: ignore[no-any-return]
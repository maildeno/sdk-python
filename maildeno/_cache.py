"""Template cache — memory and disk strategies.

Storage layout (disk mode):

    {cache_dir}/
      a7f4b181-a366-4944-a371-e7b941a3c5ab.json
      9ec0c043-e8a1-4a68-bbb3-92fbef1ea222.json

Each file contains::

    {
      "template_id": "...",
      "fetched_at": 1717776000000,
      "ttl": 300000,
      "template": { ...TemplateJson... }
    }

The file name is the UUID as-is. UUIDs contain only [0-9a-f-] which is safe
on every modern filesystem. Any other character is replaced with ``_`` as a
defensive guard — this never fires for real Maildeno template IDs.
"""

from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from ._types import TemplateJson


# ── Shared helpers ────────────────────────────────────────────────────────────

def _now_ms() -> int:
    return int(time.time() * 1000)


def _is_stale(fetched_at: int, ttl: int) -> bool:
    return _now_ms() - fetched_at > ttl


# ── Strategy interface ────────────────────────────────────────────────────────

class _CacheStore(ABC):
    """All cache implementations must satisfy this interface."""

    @abstractmethod
    def get_fresh(self, template_id: str) -> Optional[TemplateJson]:
        """Return the template only if it is within TTL, else None."""

    @abstractmethod
    def get_fallback(self, template_id: str) -> Optional[TemplateJson]:
        """Return the template regardless of staleness, or None if absent."""

    @abstractmethod
    def set(self, template_id: str, template: TemplateJson) -> None:
        """Store or overwrite a template entry."""

    @abstractmethod
    def invalidate(self, template_id: str) -> None:
        """Remove a single entry. No-op if absent."""

    @abstractmethod
    def clear(self) -> None:
        """Remove all entries."""

    @abstractmethod
    def list(self) -> List[str]:
        """Return the IDs of all currently cached templates."""


# ── Memory store ──────────────────────────────────────────────────────────────

class _MemoryStore(_CacheStore):
    """In-process dict cache with TTL + stale-on-error fallback.

    Lost on process restart. Zero I/O. Every read is O(1).
    Oldest entry evicted when ``max_entries`` is reached.
    """

    def __init__(self, ttl: int, max_entries: int) -> None:
        self._ttl = ttl
        self._max = max_entries
        # {template_id: {"template": ..., "fetched_at": int, "ttl": int}}
        self._store: Dict[str, Dict[str, Any]] = {}

    def get_fresh(self, template_id: str) -> Optional[TemplateJson]:
        entry = self._store.get(template_id)
        if entry is None:
            return None
        if _is_stale(entry["fetched_at"], entry["ttl"]):
            return None
        return entry["template"]  # type: ignore[return-value]

    def get_fallback(self, template_id: str) -> Optional[TemplateJson]:
        entry = self._store.get(template_id)
        return entry["template"] if entry else None  # type: ignore[return-value]

    def set(self, template_id: str, template: TemplateJson) -> None:
        if len(self._store) >= self._max and template_id not in self._store:
            # Evict oldest entry
            oldest = min(self._store, key=lambda k: self._store[k]["fetched_at"])
            del self._store[oldest]
        self._store[template_id] = {
            "template": template,
            "fetched_at": _now_ms(),
            "ttl": self._ttl,
        }

    def invalidate(self, template_id: str) -> None:
        self._store.pop(template_id, None)

    def clear(self) -> None:
        self._store.clear()

    def list(self) -> List[str]:
        return list(self._store.keys())


# ── Disk store ────────────────────────────────────────────────────────────────

class _DiskStore(_CacheStore):
    """Persistent JSON-file cache.

    Each template is stored as a single minified JSON file named after its
    UUID. Survives process restarts. All reads/writes are synchronous — both
    the sync and async clients call this from a thread (the async client uses
    ``run_in_executor`` for the whole render pipeline).
    """

    def __init__(self, cache_dir: str, ttl: int, max_entries: int) -> None:
        self._dir = cache_dir
        self._ttl = ttl
        self._max = max_entries

    # ── Reads ──────────────────────────────────────────────────────────────

    def get_fresh(self, template_id: str) -> Optional[TemplateJson]:
        entry = self._read(template_id)
        if entry is None:
            return None
        if _is_stale(entry["fetched_at"], entry["ttl"]):
            return None
        return entry["template"]  # type: ignore[return-value]

    def get_fallback(self, template_id: str) -> Optional[TemplateJson]:
        entry = self._read(template_id)
        return entry["template"] if entry else None  # type: ignore[return-value]

    # ── Writes ─────────────────────────────────────────────────────────────

    def set(self, template_id: str, template: TemplateJson) -> None:
        os.makedirs(self._dir, exist_ok=True)
        self._enforce_limit()

        entry = {
            "template_id": template_id,
            "fetched_at": _now_ms(),
            "ttl": self._ttl,
            "template": template,
        }
        final = self._path(template_id)
        tmp = final + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            # Minified — no indent. 58 KB → 22 KB.
            json.dump(entry, fh, separators=(",", ":"))
        os.replace(tmp, final)  # atomic on POSIX; best-effort on Windows

    def invalidate(self, template_id: str) -> None:
        try:
            os.unlink(self._path(template_id))
        except FileNotFoundError:
            pass

    def clear(self) -> None:
        try:
            files = os.listdir(self._dir)
        except FileNotFoundError:
            return
        for f in files:
            if f.endswith(".json") and not f.endswith(".tmp"):
                try:
                    os.unlink(os.path.join(self._dir, f))
                except OSError:
                    pass

    def list(self) -> List[str]:
        try:
            files = os.listdir(self._dir)
        except FileNotFoundError:
            return []
        return [
            f[:-5]  # strip ".json"
            for f in files
            if f.endswith(".json") and not f.endswith(".tmp")
        ]

    # ── Internals ──────────────────────────────────────────────────────────

    def _path(self, template_id: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in template_id)
        return os.path.join(self._dir, f"{safe}.json")

    def _read(self, template_id: str) -> Optional[Dict[str, Any]]:
        try:
            with open(self._path(template_id), encoding="utf-8") as fh:
                return json.load(fh)  # type: ignore[no-any-return]
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return None

    def _enforce_limit(self) -> None:
        try:
            files = [
                f for f in os.listdir(self._dir)
                if f.endswith(".json") and not f.endswith(".tmp")
            ]
        except FileNotFoundError:
            return

        if len(files) < self._max:
            return

        # Read fetchedAt from each file to find the oldest
        entries = []
        for f in files:
            try:
                with open(os.path.join(self._dir, f), encoding="utf-8") as fh:
                    data = json.load(fh)
                entries.append((f, data.get("fetched_at", 0)))
            except (OSError, json.JSONDecodeError):
                entries.append((f, 0))

        entries.sort(key=lambda x: x[1])
        to_evict = entries[: len(entries) - self._max + 1]
        for fname, _ in to_evict:
            try:
                os.unlink(os.path.join(self._dir, fname))
            except OSError:
                pass


# ── Public facade ─────────────────────────────────────────────────────────────

_DEFAULT_TTL = 300_000          # 5 minutes
_DEFAULT_MAX_ENTRIES = 50
_DEFAULT_CACHE_PATH = ".maildeno-cache"


class TemplateCache:
    """Thin facade over the active ``_CacheStore``.

    Both clients delegate every cache operation here so they never reference
    ``_MemoryStore`` or ``_DiskStore`` directly.
    """

    def __init__(self, store: _CacheStore) -> None:
        self._store = store

    def get_fresh(self, template_id: str) -> Optional[TemplateJson]:
        return self._store.get_fresh(template_id)

    def get_fallback(self, template_id: str) -> Optional[TemplateJson]:
        return self._store.get_fallback(template_id)

    def set(self, template_id: str, template: TemplateJson) -> None:
        self._store.set(template_id, template)

    def invalidate(self, template_id: str) -> None:
        self._store.invalidate(template_id)

    def clear(self) -> None:
        self._store.clear()

    def list(self) -> List[str]:
        return self._store.list()


def build_cache(config: Optional[Dict[str, Any]]) -> TemplateCache:
    """Build a :class:`TemplateCache` from a raw config dict or ``None``."""
    cfg = config or {}
    ttl = int(cfg.get("ttl", _DEFAULT_TTL))
    max_entries = int(cfg.get("max_entries", _DEFAULT_MAX_ENTRIES))

    if cfg.get("type") == "disk":
        raw_path = cfg.get("path", _DEFAULT_CACHE_PATH)
        resolved = os.path.abspath(raw_path)
        return TemplateCache(_DiskStore(resolved, ttl, max_entries))

    return TemplateCache(_MemoryStore(ttl, max_entries))

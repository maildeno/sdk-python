"""Output minification for rendered templates.

Mirrors the JavaScript SDK's ``minifyOutput()`` function exactly:

- **HTML** — collapses inter-tag whitespace without touching CSS inside
  ``<style>`` blocks.
- **MJML** — same whitespace collapse, preserving ``<mj-style>`` blocks.
- **React Email** — strips runs of 3+ blank lines down to a single blank line.
- **Unknown target** — returns the source unchanged.

CSS safety guarantee: ``<style>`` / ``<mj-style>`` block contents are
extracted before whitespace collapsing and spliced back in afterwards so
media queries, selectors, and property values are never corrupted.
"""

from __future__ import annotations

import re
from typing import List, Tuple


def minify_output(target: str, source: str) -> str:
    """Minify rendered output for the given target.

    :param target: ``"html"``, ``"mjml"``, or ``"react-email"``.
    :param source: Raw output string from the Wasm engine.
    :returns:      Compacted output string.
    """
    if target == "html":
        return _minify_html(source, style_tag="style")
    if target == "mjml":
        return _minify_html(source, style_tag="mj-style")
    if target == "react-email":
        return _minify_react(source)
    # Unknown target — return unchanged (matches JS behaviour)
    return source


# ── HTML / MJML ───────────────────────────────────────────────────────────────

def _minify_html(source: str, style_tag: str) -> str:
    """Collapse inter-tag whitespace while preserving CSS block contents."""
    # Step 1: extract all <style> / <mj-style> blocks and replace with
    # stable placeholders so the whitespace pass never touches them.
    placeholders: List[Tuple[str, str]] = []
    pattern = re.compile(
        rf"(<{style_tag}(?:\s[^>]*)?>)(.*?)(</{style_tag}>)",
        re.DOTALL | re.IGNORECASE,
    )

    def extract(m: re.Match) -> str:  # type: ignore[type-arg]
        key = f"\x00STYLE{len(placeholders)}\x00"
        placeholders.append((key, m.group(0)))
        return key

    working = pattern.sub(extract, source)

    # Step 2: collapse whitespace between tags — runs of whitespace that
    # include at least one newline between a closing and opening tag become
    # a single newline.
    working = re.sub(r">\s*\n\s*<", ">\n<", working)
    # Collapse remaining multi-space runs between tags (no newline involved)
    working = re.sub(r">\s{2,}<", "><", working)

    # Step 3: restore CSS blocks
    for key, original in placeholders:
        working = working.replace(key, original)

    return working


# ── React Email ───────────────────────────────────────────────────────────────

def _minify_react(source: str) -> str:
    """Strip runs of 3+ consecutive blank lines down to a single blank line."""
    return re.sub(r"\n{3,}", "\n\n", source)

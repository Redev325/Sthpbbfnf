#!/usr/bin/env python3
"""Disable Psych Engine's built-in BF preview in the HTML5 graphics menu.

This Bloodbath build intentionally prunes the stock ``bf.json`` character. The
upstream graphics menu nevertheless asks BaseOptionsMenu to construct BF solely
as a preview for the Anti-Aliasing option, which crashes while opening settings.
The preview is decorative and not needed for this game, so disable it instead of
adding an unwanted BF character and its assets back into the build.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path


def patch_graphics_settings(engine: Path) -> bool:
    source_file = engine / "source" / "options" / "GraphicsSettingsSubState.hx"
    if not source_file.is_file():
        raise FileNotFoundError(f"Graphics settings source not found: {source_file}")

    source = source_file.read_text(encoding="utf-8")
    updated, count = re.subn(
        r"^[\t ]*option\.showBoyfriend\s*=\s*true\s*;[^\n]*(?:\n|$)",
        "",
        source,
        count=1,
        flags=re.MULTILINE,
    )
    if "option.showBoyfriend = true;" in updated:
        raise RuntimeError("Could not disable the graphics-menu BF preview safely.")

    if count:
        source_file.write_text(updated, encoding="utf-8")
    return bool(count)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, type=Path)
    args = parser.parse_args()

    changed = patch_graphics_settings(args.engine.resolve())
    print(
        "Disabled the stock BF graphics-menu preview."
        if changed
        else "Stock BF graphics-menu preview is already disabled."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Patch the Psych Engine 0.6.3 FlxGame call for HaxeFlixel 5.2.2.

FlxGame 5.2.2's constructor no longer accepts the old zoom argument. Psych
Engine 0.6.3 calculates the viewport dimensions using its local zoom value,
so that value should not be passed as updateFramerate.
"""
from __future__ import annotations

import argparse
from pathlib import Path


OLD = "addChild(new FlxGame(gameWidth, gameHeight, initialState, zoom, framerate, framerate, skipSplash, startFullscreen));"
NEW = "addChild(new FlxGame(gameWidth, gameHeight, initialState, framerate, framerate, skipSplash, startFullscreen));"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, type=Path)
    args = parser.parse_args()

    main_file = args.engine.resolve() / "source" / "Main.hx"
    if not main_file.is_file():
        raise FileNotFoundError(f"Psych Engine Main.hx not found: {main_file}")

    source = main_file.read_text(encoding="utf-8")
    if NEW in source:
        print("Main.hx already has the HaxeFlixel 5.2.2-compatible FlxGame call.")
        return 0

    if OLD not in source:
        raise RuntimeError(
            "Could not find the expected old FlxGame constructor call in Main.hx. "
            "The upstream engine source may have changed."
        )

    main_file.write_text(source.replace(OLD, NEW, 1), encoding="utf-8")
    print("Patched Main.hx: removed the obsolete zoom constructor argument.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Apply the remaining Bloodbath HTML5 compatibility fixes.

This script makes the last-minute corrections that are still easier to do at the
engine source level than by reworking the full porting script:
- hide the duplicated Kyle layer that sits behind the active Kyle sprite
- normalize Stan/Kyle health icon data so the HUD uses the correct icon names
- keep UI/world character JSON data valid for the browser build
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def ensure_char_json(engine: Path, name: str, fallback_name: str | None = None) -> None:
    chars_dir = engine / "assets" / "preload" / "characters"
    target = chars_dir / f"{name}.json"
    if not target.is_file():
        if fallback_name is None:
            return
        target = chars_dir / f"{fallback_name}.json"
        if not target.is_file():
            return
    data = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data.setdefault("image", f"characters/{name}")
        if name.lower() == "kyle c":
            data["healthicon"] = "kyle"
        elif name.lower() == "stan":
            data["healthicon"] = "stan"
        elif name.lower() == "kyle":
            data["healthicon"] = "kyle"
        elif name.lower() == "clyde":
            data["healthicon"] = "clyde"
        elif name.lower() == "craig":
            data["healthicon"] = "craig"
        if isinstance(data.get("image"), str) and not data["image"].startswith("characters/"):
            data["image"] = f"characters/{name}"
        target.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def normalize_character_assets(engine: Path) -> None:
    chars_dir = engine / "assets" / "preload" / "characters"
    if not chars_dir.is_dir():
        return
    for file in list(chars_dir.glob("*.json")):
        name = file.stem
        if name.lower() in {"stan", "kyle", "kyle c", "clyde", "craig", "kenny", "cartman"}:
            ensure_char_json(engine, name)


def fix_duplicate_kyle(engine: Path) -> None:
    play_file = engine / "source" / "PlayState.hx"
    if not play_file.is_file():
        return
    text = play_file.read_text(encoding="utf-8")
    checks = [
        (
            "southKyleCCharacter.alpha = 1;\n\t\t\t\tsouthKyleCCharacter.singDuration = 6;",
            "southKyleCCharacter.alpha = 0;\n\t\t\t\tsouthKyleCCharacter.visible = false;\n\t\t\t\tsouthKyleCCharacter.singDuration = 6;",
        ),
        (
            "southKyleCharacter.alpha = 1;\n\t\t\t\tsouthKyleCharacter.addOffset('singLEFT', 70, -48);",
            "southKyleCharacter.alpha = 1;\n\t\t\t\tsouthKyleCharacter.visible = true;\n\t\t\t\tsouthKyleCharacter.addOffset('singLEFT', 70, -48);",
        ),
    ]
    for old, new in checks:
        if old in text and new not in text:
            text = text.replace(old, new, 1)

    # If the duplicate Kyle still gets animated, it must be hidden so the visible sprite
    # stays in front and the backend keeps only one live stage character.
    text = text.replace(
        "if (southKyleCCharacter != null && southKyleCCharacter.alpha > 0.01)\n            {\n                southKyleCCharacter.playAnim(kyleAnim, true);\n                southKyleCCharacter.holdTimer = 0;\n            }\n",
        "",
        1,
    )
    text = text.replace(
        "if (southKyleCCharacter != null && southKyleCCharacter.alpha > 0.01\n                && southKyleCCharacter.animation.curAnim != null\n                && !southKyleCCharacter.animation.curAnim.name.startsWith('sing')\n                && curBeat % southKyleCCharacter.danceEveryNumBeats == 0)\n                southKyleCCharacter.dance();\n",
        "",
        1,
    )
    if "southKyleCCharacter.visible = false;" not in text:
        text = text.replace("southKyleCCharacter.alpha = 0;", "southKyleCCharacter.alpha = 0;\n\t\t\t\tsouthKyleCCharacter.visible = false;", 1)
    play_file.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, type=Path)
    args = parser.parse_args()
    engine = args.engine.resolve()
    normalize_character_assets(engine)
    fix_duplicate_kyle(engine)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

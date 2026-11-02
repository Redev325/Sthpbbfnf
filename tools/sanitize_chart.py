#!/usr/bin/env python3
"""Remove chart property events that target Lua-created desktop-only objects.

Psych Engine 0.6.3's HTML5 target does not run this mod's Lua stage script. Keep
plain engine events, but keep Set Property only for a known PlayState field.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SAFE_PROPERTY_PATHS = {"defaultCamZoom"}


def filter_events(value: Any) -> tuple[Any, int]:
    if not isinstance(value, list):
        return value, 0

    kept_groups = []
    removed = 0

    for group in value:
        if not isinstance(group, list) or len(group) < 2 or not isinstance(group[1], list):
            kept_groups.append(group)
            continue

        safe_entries = []
        for event in group[1]:
            if isinstance(event, list) and len(event) >= 2 and event[0] == "Set Property":
                if str(event[1]) not in SAFE_PROPERTY_PATHS:
                    removed += 1
                    continue
            safe_entries.append(event)

        if safe_entries:
            kept_groups.append([group[0], safe_entries, *group[2:]])
        else:
            removed += 1

    return kept_groups, removed


def sanitize_file(path: Path) -> int:
    if not path.is_file():
        return 0

    with path.open("r", encoding="utf-8") as stream:
        data = json.load(stream)

    song = data.get("song", data) if isinstance(data, dict) else data
    if not isinstance(song, dict):
        return 0

    filtered, removed = filter_events(song.get("events", []))
    if removed:
        song["events"] = filtered
        with path.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
    return removed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, type=Path)
    args = parser.parse_args()

    data_dir = args.engine.resolve() / "assets" / "preload" / "data" / "bloodbath"
    chart = data_dir / "bloodbath-hard.json"
    extra_events = data_dir / "events.json"

    if not chart.is_file():
        raise FileNotFoundError(f"Bloodbath chart not found: {chart}")

    removed = sanitize_file(chart)
    removed += sanitize_file(extra_events)
    print(f"Removed {removed} Set Property events that target desktop Lua sprites.")
    print("Retained standard Psych Engine events and Set Property: defaultCamZoom.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

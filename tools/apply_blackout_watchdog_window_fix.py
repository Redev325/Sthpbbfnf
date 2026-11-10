#!/usr/bin/env python3
"""Extend the existing South-stage blackout watchdog through the song ending.

The current generated PlayState recovery stops checking after 362 seconds (6:02),
although the reported blackout begins around 5:56 and remains for the rest of the
song. This one-time source patch changes the guard to start at 5:56 and continue
until the song/state ends. The normal watchdog still waits for a cover to remain
stuck for its existing grace period before clearing it.

Run once from the repository root, then commit the modified tools/prepare_html5.py.
The GitHub Actions workflow already runs prepare_html5.py on every build.
"""
from __future__ import annotations

from pathlib import Path

OLD = "if (songMs < 340000 || songMs > 362000)"
NEW = "if (songMs < 356000)"


def patch(source_file: Path) -> bool:
    source = source_file.read_text(encoding="utf-8")
    occurrences = source.count(OLD)
    if occurrences == 0:
        if NEW in source:
            print("Blackout watchdog already runs from 5:56 through song end.")
            return False
        raise RuntimeError("Expected blackout watchdog time guard was not found; no changes made.")
    if occurrences != 1:
        raise RuntimeError(f"Expected one watchdog time guard, found {occurrences}; no changes made.")

    updated = source.replace(
        OLD,
        NEW,
        1,
    )
    updated = updated.replace(
        "if (songMs < 356000)\n\t\t{\n\t\t\tlateBlackoutWatchSince = -1;",
        "// The reported blackout starts around 5:56. Keep watching through the\n"
        "\t\t// remainder of the chart instead of stopping at 6:02.\n"
        "\t\tif (songMs < 356000)\n\t\t{\n\t\t\tlateBlackoutWatchSince = -1;",
        1,
    )
    source_file.write_text(updated, encoding="utf-8")
    print(f"Updated {source_file}: recovery now remains active after 5:56 through song end.")
    return True


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    source_file = root / "tools" / "prepare_html5.py"
    if not source_file.is_file():
        raise FileNotFoundError(f"Build preparation script not found: {source_file}")
    patch(source_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

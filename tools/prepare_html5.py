#!/usr/bin/env python3
"""Prepare the South Park Bloodbath release for a Psych Engine 0.6.3 HTML5 build.

The supplied release is a Windows runtime plus mod resources. This script embeds
the mod's data into the matching open-source engine source tree so the web build
does not depend on desktop filesystem mod loading.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def copy_tree(source: Path, destination: Path) -> None:
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, dirs_exist_ok=True)


def copy_json_tree(source: Path, destination: Path) -> int:
    copied = 0
    if not source.is_dir():
        return copied
    for file in source.rglob("*.json"):
        relative = file.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, target)
        copied += 1
    return copied


def find_game_root(package_dir: Path) -> Path:
    if (package_dir / "mods" / "weeks" / "week-SPT.json").is_file():
        return package_dir
    candidates = list(package_dir.rglob("week-SPT.json"))
    for candidate in candidates:
        if candidate.parent.name == "weeks" and candidate.parent.parent.name == "mods":
            return candidate.parents[2]
    raise FileNotFoundError("Could not find mods/weeks/week-SPT.json in the release ZIP.")


def patch_project(engine: Path) -> None:
    project_file = engine / "Project.xml"
    project = project_file.read_text(encoding="utf-8")
    # hxCodec is a native/video dependency; this release does not include a
    # browser-compatible video cutscene, so keep the HTML5 build independent of it.
    project, removed = re.subn(
        r"^\s*<define\s+name=[\"']VIDEOS_ALLOWED[\"'][^>]*/>\s*\n",
        "",
        project,
        count=1,
        flags=re.MULTILINE,
    )
    if removed == 0 and 'name="VIDEOS_ALLOWED"' in project:
        raise RuntimeError("Could not safely disable VIDEOS_ALLOWED in Project.xml.")
    project_file.write_text(project, encoding="utf-8")


def patch_note_types(engine: Path) -> None:
    note_file = engine / "source" / "Note.hx"
    source = note_file.read_text(encoding="utf-8")
    marker = "\t\t\t\tcase 'GF Sing':\n\t\t\t\t\t gfNote = true;"
    # The source uses a tab directly before gfNote; handle its exact upstream spelling.
    marker = "\t\t\t\tcase 'GF Sing':\n\t\t\t\t\tgfNote = true;"
    addition = marker + """
\n\t\t\t\t// South Park Bloodbath custom note types: port the desktop Lua
\t\t\t\t// no-animation / hittable-note behavior into the HTML5 engine.
\t\t\t\tcase 'kyle' | 'stan' | 'cledyNOTE' | 'craignote' | 'Clyde Note' | 'Craig Note' | 'Kyle Note' | 'Stan Note':
\t\t\t\t\tnoAnimation = true;
\t\t\t\t\tif (mustPress) ignoreNote = false;
\n\t\t\t\tcase 'Warning Notes':
\t\t\t\t\tif (mustPress) ignoreNote = false;
"""
    if "South Park Bloodbath custom note types" not in source:
        if marker not in source:
            raise RuntimeError("Could not find the GF Sing note-type switch in source/Note.hx.")
        source = source.replace(marker, addition, 1)
        note_file.write_text(source, encoding="utf-8")


def patch_south_stage(engine: Path) -> None:
    play_file = engine / "source" / "PlayState.hx"
    source = play_file.read_text(encoding="utf-8")
    marker = "\t\t\tcase 'stage': //Week 1"
    addition = """\t\t\tcase 'south':
\t\t\t\t// Static browser-port of the first South Park Bloodbath stage layer.
\t\t\t\t// The desktop stage.lua is LuaJIT-based and is not run by Psych 0.6.3 HTML5.
\t\t\t\tvar southBackground:BGSprite = new BGSprite('bg', -1670, -1540, 1, 1, ['bg a'], true);
\t\t\t\tsouthBackground.scale.set(2.7, 2.7);
\t\t\t\tsouthBackground.updateHitbox();
\t\t\t\tadd(southBackground);

\t\t\tcase 'stage': //Week 1"""
    if "Static browser-port of the first South Park Bloodbath stage layer." not in source:
        if marker not in source:
            raise RuntimeError("Could not find the stage switch insertion point in source/PlayState.hx.")
        source = source.replace(marker, addition, 1)
        play_file.write_text(source, encoding="utf-8")


def convert_ogg_files(engine: Path) -> int:
    files = list((engine / "assets").rglob("*.ogg"))
    converted = 0
    for source in files:
        target = source.with_suffix(".mp3")
        if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
            continue
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-i", str(source), "-vn", "-codec:a", "libmp3lame", "-q:a", "2",
                str(target),
            ],
            check=True,
        )
        converted += 1
    return converted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, type=Path)
    parser.add_argument("--package", required=True, type=Path)
    args = parser.parse_args()

    engine = args.engine.resolve()
    if not (engine / "Project.xml").is_file():
        raise FileNotFoundError(f"Psych Engine source not found at {engine}")
    game = find_game_root(args.package.resolve())
    runtime_assets = game / "assets"
    mod = game / "mods"

    # Restore the matching 0.6.3 runtime asset folders into their source-tree
    # locations, then overlay the South Park mod's resources.
    for folder in ("images", "characters", "stages", "sounds", "music"):
        copy_tree(runtime_assets / folder, engine / "assets" / "preload" / folder)
    copy_tree(runtime_assets / "shared", engine / "assets" / "shared")

    copy_tree(mod / "images", engine / "assets" / "preload" / "images")
    copy_tree(mod / "characters", engine / "assets" / "preload" / "characters")
    copy_json_tree(mod / "stages", engine / "assets" / "preload" / "stages")
    copy_json_tree(mod / "weeks", engine / "assets" / "preload" / "weeks")
    copy_json_tree(mod / "data" / "bloodbath", engine / "assets" / "preload" / "data" / "bloodbath")
    copy_tree(mod / "fonts", engine / "assets" / "fonts")
    copy_tree(mod / "shaders", engine / "assets" / "preload" / "shaders")
    copy_tree(mod / "sounds", engine / "assets" / "preload" / "sounds")
    copy_tree(mod / "songs" / "bloodbath", engine / "assets" / "songs" / "bloodbath")

    # Make the custom week discoverable by the non-filesystem HTML5 week loader.
    week_list = engine / "assets" / "preload" / "weeks" / "weekList.txt"
    week_list.parent.mkdir(parents=True, exist_ok=True)
    existing = week_list.read_text(encoding="utf-8").splitlines() if week_list.exists() else []
    if "week-SPT" not in existing:
        with week_list.open("a", encoding="utf-8") as stream:
            if existing and existing[-1] != "":
                stream.write("\n")
            stream.write("week-SPT\n")

    # The release ZIP contains the instrumental but no Voices.ogg/Voices.mp3.
    chart_file = engine / "assets" / "preload" / "data" / "bloodbath" / "bloodbath-hard.json"
    if not chart_file.is_file():
        raise FileNotFoundError("Bloodbath Hard chart was not copied into the engine.")
    chart = json.loads(chart_file.read_text(encoding="utf-8"))
    song = chart.get("song", chart)
    song["needsVoices"] = False
    song["stage"] = "south"
    chart_file.write_text(json.dumps(chart, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # The week references menu_SPT.png, which is absent. Use the stock stage
    # menu backdrop included with Psych Engine 0.6.3; keep the three character
    # slots blank as the release intended.
    week_file = engine / "assets" / "preload" / "weeks" / "week-SPT.json"
    week = json.loads(week_file.read_text(encoding="utf-8"))
    week["weekBackground"] = "stage"
    week_file.write_text(json.dumps(week, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    for character in ("stan", "kenny", "cartman"):
        character_file = engine / "assets" / "preload" / "characters" / f"{character}.json"
        if not character_file.is_file():
            raise FileNotFoundError(f"Missing character definition: {character_file}")

    patch_project(engine)
    patch_note_types(engine)
    patch_south_stage(engine)
    converted = convert_ogg_files(engine)

    asset_bytes = sum(p.stat().st_size for p in (engine / "assets").rglob("*") if p.is_file())
    print(f"Prepared game root: {game}")
    print(f"Converted OGG files to MP3: {converted}")
    print(f"Embedded engine assets: {asset_bytes:,} bytes")
    print("HTML5 limitations: desktop Lua scripts/custom events are not executed by this first browser build.")
    print("Audio limitation: this package has no vocal track; the chart is set to instrumental-only playback.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"HTML5 preparation failed: {error}", file=sys.stderr)
        raise

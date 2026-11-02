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

    # Keep references to the original stage's animated light overlays so the
    # stage.lua step changes can be reproduced natively in the HTML5 build.
    class_marker = "class PlayState extends MusicBeatState\\n{"
    fields = """class PlayState extends MusicBeatState
{
	var southLightOverlay:BGSprite;
	var southLightOverlay2:BGSprite;
	var southLightOverlay3:BGSprite;
	var southLightOverlay4:BGSprite;
	var southLightOverlay42:BGSprite;
"""
    if "var southLightOverlay:BGSprite;" not in source:
        if class_marker not in source:
            raise RuntimeError("Could not locate the PlayState class declaration.")
        source = source.replace(class_marker, fields, 1)

    marker = "\\t\\t\\tcase 'stage': //Week 1"
    stage_code = """			case 'south':
				// Native HTML5 recreation of the South Park Bloodbath stage.lua
				// scene layers. Every texture/atlas is copied from the supplied mod.
				var southSun:BGSprite = new BGSprite('sun', -1870, -1240);
				southSun.scale.set(1.4, 1.2);
				southSun.alpha = 0;
				add(southSun);

				var southFogBack:BGSprite = new BGSprite('fog bg2', -1670, -1240);
				southFogBack.scale.set(3, 3);
				southFogBack.alpha = 0;
				add(southFogBack);

				var southVignette:BGSprite = new BGSprite('Vignette', -20, 0);
				southVignette.cameras = [camOther];
				southVignette.alpha = 0;
				add(southVignette);

				var southWhite:BGSprite = new BGSprite('white', -1700, -500);
				southWhite.cameras = [camHUD];
				southWhite.alpha = 0;
				add(southWhite);

				var southFog:BGSprite = new BGSprite('fog', -2000, -1000);
				southFog.scale.set(1.4, 1.4);
				southFog.alpha = 0;
				add(southFog);

				var southBlack:BGSprite = new BGSprite('black', 0, 0);
				southBlack.cameras = [camHUD];
				southBlack.alpha = 0;
				add(southBlack);

				var southAnim:BGSprite = new BGSprite('anim', 0, 0, 1, 1, ['anim a'], false);
				southAnim.cameras = [camHUD];
				southAnim.alpha = 0;
				add(southAnim);

				var southBackground:BGSprite = new BGSprite('bg', -1670, -1540, 1, 1, ['bg a'], true);
				southBackground.scale.set(2.7, 2.7);
				southBackground.updateHitbox();
				add(southBackground);

				southLightOverlay = new BGSprite('luzbg', -1670, -1540);
				southLightOverlay.scale.set(2.7, 2.7);
				southLightOverlay.alpha = 0;
				add(southLightOverlay);

				var southBackground3:BGSprite = new BGSprite('bg3', -1770, -940, 1, 1, ['idle'], true);
				southBackground3.scale.set(2.3, 2.3);
				southBackground3.alpha = 0;
				add(southBackground3);

				southLightOverlay3 = new BGSprite('bg3luz', -1770, -940);
				southLightOverlay3.scale.set(2.3, 2.3);
				southLightOverlay3.alpha = 0;
				add(southLightOverlay3);

				var southBackground4:BGSprite = new BGSprite('bg4', -540, -740, 1, 1, ['bg4 a'], true);
				southBackground4.scale.set(1.7, 1.7);
				southBackground4.alpha = 0;
				add(southBackground4);

				southLightOverlay4 = new BGSprite('bg4luz', -540, -740);
				southLightOverlay4.scale.set(1.7, 1.7);
				southLightOverlay4.alpha = 0;
				add(southLightOverlay4);

				southLightOverlay42 = new BGSprite('bg4luz2', -540, -740);
				southLightOverlay42.scale.set(1.7, 1.7);
				southLightOverlay42.alpha = 0;
				add(southLightOverlay42);

				var southBackground2:BGSprite = new BGSprite('bg2 s', -1970, -1040);
				southBackground2.scale.set(2.7, 2.7);
				southBackground2.alpha = 0;
				add(southBackground2);

				var southBackground2Animated:BGSprite = new BGSprite('bg2', -1970, -1040, 1, 1, ['idle'], true);
				southBackground2Animated.scale.set(2.7, 2.7);
				southBackground2Animated.alpha = 0;
				add(southBackground2Animated);

				southLightOverlay2 = new BGSprite('bg2luz', -1970, -1040);
				southLightOverlay2.scale.set(2.7, 2.7);
				southLightOverlay2.flipX = true;
				southLightOverlay2.alpha = 0;
				add(southLightOverlay2);

				var southSnow:BGSprite = new BGSprite('nieve', -1970, -1040);
				southSnow.scale.set(2.7, 2.7);
				southSnow.alpha = 0;
				add(southSnow);

				var southKyle:BGSprite = new BGSprite('kyle s', 608, 164, 1, 1, ['idle'], true);
				southKyle.alpha = 0;
				add(southKyle);

				var southOh:BGSprite = new BGSprite('oh', 608, 164, 1, 1, ['oh'], true);
				southOh.scale.set(1.9, 1.9);
				southOh.alpha = 0;
				add(southOh);

				var southCraig:BGSprite = new BGSprite('characters/craig', 1700, 490, 1, 1, ['oh'], true);
				southCraig.scale.set(0.9, 0.9);
				southCraig.alpha = 0;
				add(southCraig);

				var southBlackbar:BGSprite = new BGSprite('blackbar', 0, 0);
				southBlackbar.cameras = [camHUD];
				southBlackbar.alpha = 0;
				add(southBlackbar);

				var southAnim2:BGSprite = new BGSprite('anim2', 0, 0, 1, 1, ['anim2 a'], false);
				southAnim2.cameras = [camHUD];
				southAnim2.alpha = 0;
				add(southAnim2);

				var southAnim2C:BGSprite = new BGSprite('anim2 c', 0, 0, 1, 1, ['anim2 c a'], false);
				southAnim2C.cameras = [camHUD];
				southAnim2C.alpha = 0;
				add(southAnim2C);

				var southAnim3:BGSprite = new BGSprite('anim3', 0, 0, 1, 1, ['anim3 idle'], false);
				southAnim3.cameras = [camHUD];
				southAnim3.alpha = 0;
				add(southAnim3);

			case 'stage': //Week 1"""
    if "Native HTML5 recreation of the South Park Bloodbath stage.lua" not in source:
        if marker not in source:
            raise RuntimeError("Could not find the stage switch insertion point in source/PlayState.hx.")
        source = source.replace(marker, stage_code, 1)

    step_marker = "\\t\\tsetOnLuas('curStep', curStep);"
    step_addition = """		setOnLuas('curStep', curStep);
		if (curStage == 'south')
		{
			switch (curStep)
			{
				case 128:
					southLightOverlay.alpha = 0.5;
				case 1202:
					southLightOverlay.alpha = 0;
				case 1285:
					southLightOverlay2.alpha = 1;
				case 2325:
					southLightOverlay2.alpha = 0;
				case 2433:
					// The third background becomes visible beneath its lighting layer.
					southLightOverlay3.alpha = 0.75;
				case 3713:
					southLightOverlay3.alpha = 0;
					southLightOverlay42.alpha = 1;
					southLightOverlay4.alpha = 0.75;
			}
		}
"""
    if "southLightOverlay.alpha = 0.5;" not in source:
        if step_marker not in source:
            raise RuntimeError("Could not find the PlayState stepHit insertion point.")
        source = source.replace(step_marker, step_addition, 1)

    play_file.write_text(source, encoding="utf-8")


def keep_only_bloodbath(engine: Path) -> dict[str, int]:
    """Remove non-Bloodbath song audio, charts and week definitions."""
    songs_dir = engine / "assets" / "songs"
    bloodbath_dir = songs_dir / "bloodbath"
    if not bloodbath_dir.is_dir():
        raise FileNotFoundError(f"Bloodbath audio folder is missing: {bloodbath_dir}")

    removed_song_folders = 0
    for item in songs_dir.iterdir():
        if item.name.lower() == "bloodbath":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
        removed_song_folders += 1

    data_dir = engine / "assets" / "preload" / "data"
    removed_chart_folders = 0
    if data_dir.is_dir():
        for item in data_dir.iterdir():
            if item.is_dir() and item.name.lower() != "bloodbath":
                shutil.rmtree(item)
                removed_chart_folders += 1

    weeks_dir = engine / "assets" / "preload" / "weeks"
    removed_week_files = 0
    for item in weeks_dir.iterdir():
        if item.is_file() and item.suffix.lower() == ".json" and item.name.lower() != "week-spt.json":
            item.unlink()
            removed_week_files += 1
        elif item.is_dir():
            shutil.rmtree(item)
            removed_week_files += 1

    week_list = weeks_dir / "weekList.txt"
    week_list.write_text("week-SPT\\n", encoding="utf-8")

    audio_files = [p for p in bloodbath_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp3", ".ogg"}]
    if not any(p.stem.lower() == "inst" for p in audio_files):
        raise FileNotFoundError("Bloodbath instrumental audio is missing after copying/conversion.")

    return {
        "removedSongFolders": removed_song_folders,
        "removedChartFolders": removed_chart_folders,
        "removedWeekFiles": removed_week_files,
        "bloodbathAudioFiles": len(audio_files),
    }


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

    # Restore the matching runtime assets, including data and shaders. The
    # original copy list omitted these folders, which can leave valid package
    # references unresolved in the browser build.
    for folder in ("images", "characters", "stages", "sounds", "music", "data", "shaders"):
        copy_tree(runtime_assets / folder, engine / "assets" / "preload" / folder)
    copy_tree(runtime_assets / "shared", engine / "assets" / "shared")
    copy_tree(runtime_assets / "fonts", engine / "assets" / "fonts")

    # Embed every non-script resource folder from the mod, not just JSON files.
    # This preserves atlases, XML/animation metadata, text data and other sidecars.
    copy_tree(mod / "images", engine / "assets" / "preload" / "images")
    copy_tree(mod / "characters", engine / "assets" / "preload" / "characters")
    copy_tree(mod / "stages", engine / "assets" / "preload" / "stages")
    copy_tree(mod / "weeks", engine / "assets" / "preload" / "weeks")
    copy_tree(mod / "data" / "bloodbath", engine / "assets" / "preload" / "data" / "bloodbath")
    copy_tree(mod / "fonts", engine / "assets" / "fonts")
    copy_tree(mod / "shaders", engine / "assets" / "preload" / "shaders")
    copy_tree(mod / "sounds", engine / "assets" / "preload" / "sounds")
    copy_tree(mod / "songs" / "bloodbath", engine / "assets" / "songs" / "bloodbath")

    # This port contains exactly one playable week. Do not append Bloodbath to
    # Psych Engine's stock week list; that made all base-game songs appear too.
    week_list = engine / "assets" / "preload" / "weeks" / "weekList.txt"
    week_list.parent.mkdir(parents=True, exist_ok=True)
    week_list.write_text("week-SPT\\n", encoding="utf-8")

    # The release ZIP contains the instrumental but no Voices.ogg/Voices.mp3.
    chart_file = engine / "assets" / "preload" / "data" / "bloodbath" / "bloodbath-hard.json"
    if not chart_file.is_file():
        raise FileNotFoundError("Bloodbath Hard chart was not copied into the engine.")
    chart = json.loads(chart_file.read_text(encoding="utf-8"))
    song = chart.get("song", chart)
    song["needsVoices"] = False
    song["stage"] = "south"
    chart_file.write_text(json.dumps(chart, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # Use an existing week-menu image instead of the missing storymenu/stage.png.
    # The three character definitions and icons are in this package; expose them
    # in the week select row instead of leaving empty portrait slots.
    week_file = engine / "assets" / "preload" / "weeks" / "week-SPT.json"
    week = json.loads(week_file.read_text(encoding="utf-8"))
    week["weekBackground"] = "week1"
    # StoryMenuState uses these as opponent, player, girlfriend portraits.
    week["weekCharacters"] = ["stan", "kenny", "cartman"]
    story_bg = engine / "assets" / "preload" / "images" / "storymenu" / "week1.png"
    if not story_bg.is_file():
        raise FileNotFoundError(f"Fallback week menu background is missing: {story_bg}")
    week_file.write_text(json.dumps(week, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    for character in ("stan", "kenny", "cartman"):
        character_file = engine / "assets" / "preload" / "characters" / f"{character}.json"
        if not character_file.is_file():
            raise FileNotFoundError(f"Missing character definition: {character_file}")
        character_data = json.loads(character_file.read_text(encoding="utf-8"))
        image_name = character_data.get("image", f"characters/{character}")
        image_file = engine / "assets" / "preload" / "images" / f"{image_name}.png"
        atlas_file = engine / "assets" / "preload" / "images" / f"{image_name}.xml"
        if not image_file.is_file() or not atlas_file.is_file():
            raise FileNotFoundError(
                f"Incomplete {character} sprite atlas: expected {image_file} and {atlas_file}"
            )
        # The package includes matching unprefixed portraits for all three
        # chart characters. Prefer those over placeholder or stale icon names.
        icon_dir = engine / "assets" / "preload" / "images" / "icons"
        preferred_icon = icon_dir / f"{character}.png"
        icon_name = character_data.get("healthicon", "blank")
        icon_file = icon_dir / f"{icon_name}.png"
        legacy_icon_file = icon_dir / f"icon-{icon_name}.png"
        if preferred_icon.is_file():
            character_data["healthicon"] = character
            character_file.write_text(
                json.dumps(character_data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        elif not icon_file.is_file() and not legacy_icon_file.is_file():
            raise FileNotFoundError(f"Missing health icon for {character}: {icon_name}")

    patch_project(engine)
    patch_note_types(engine)
    patch_south_stage(engine)
    converted = convert_ogg_files(engine)
    song_cleanup = keep_only_bloodbath(engine)

    asset_bytes = sum(p.stat().st_size for p in (engine / "assets").rglob("*") if p.is_file())
    print(f"Prepared game root: {game}")
    print(f"Converted OGG files to MP3: {converted}")
    print("Bloodbath-only cleanup: " + json.dumps(song_cleanup, sort_keys=True))
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

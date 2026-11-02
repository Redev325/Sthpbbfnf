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
import os
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image


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
    # This release contains one custom Bloodbath week. Drop video support and
    # unrelated stock-week asset libraries from the browser build.
    project, removed = re.subn(
        r"^\s*<define\s+name=[\"']VIDEOS_ALLOWED[\"'][^>]*/>\s*\n",
        "",
        project,
        count=1,
        flags=re.MULTILINE,
    )
    if removed == 0 and 'name="VIDEOS_ALLOWED"' in project:
        raise RuntimeError("Could not safely disable VIDEOS_ALLOWED in Project.xml.")

    project = re.sub(
        r"^\s*<library\s+name=[\"']week[2-7][\"'][^>]*/>\s*\n",
        "",
        project,
        flags=re.MULTILINE,
    )
    project = re.sub(
        r"^\s*<assets\s+path=[\"']assets/week[2-7][\"'][^>]*/>\s*\n",
        "",
        project,
        flags=re.MULTILINE,
    )
    project_file.write_text(project, encoding="utf-8")

    for week_number in range(2, 8):
        unused_week = engine / "assets" / f"week{week_number}"
        if unused_week.is_dir():
            shutil.rmtree(unused_week)


def optimize_browser_images(
    image_dir: Path,
    max_dimension: int = 2048,
    character_max_dimension: int = 2048,
) -> dict[str, object]:
    """Downscale oversized PNGs and keep Sparrow XML coordinates in sync.

    Character atlases get a smaller 2048px cap to reduce cumulative GPU memory
    during the song's many character swaps. Stage/menu art can remain up to 4096px.
    """
    optimized = 0
    xml_updated = 0
    bytes_before = 0
    bytes_after = 0
    scale_factors: dict[str, float] = {}

    if not image_dir.is_dir():
        raise FileNotFoundError(f"Image directory is missing: {image_dir}")

    for image_path in image_dir.rglob("*.png"):
        try:
            relative = image_path.relative_to(image_dir)
            limit = character_max_dimension if relative.parts and relative.parts[0] == "characters" else max_dimension
            with Image.open(image_path) as source:
                width, height = source.size
                if max(width, height) <= limit:
                    continue
                factor = limit / float(max(width, height))
                new_size = (
                    max(1, int(round(width * factor))),
                    max(1, int(round(height * factor))),
                )
                bytes_before += image_path.stat().st_size

                atlas_path = image_path.with_suffix(".xml")
                if atlas_path.is_file():
                    try:
                        tree = ET.parse(atlas_path)
                        root = tree.getroot()
                        for subtexture in root.findall(".//SubTexture"):
                            for key in (
                                "x", "y", "width", "height",
                                "frameX", "frameY", "frameWidth", "frameHeight",
                            ):
                                value = subtexture.get(key)
                                if value is None:
                                    continue
                                try:
                                    scaled_value = float(value) * factor
                                except ValueError:
                                    continue
                                subtexture.set(key, str(int(round(scaled_value))))
                        tree.write(atlas_path, encoding="utf-8", xml_declaration=True)
                        xml_updated += 1
                    except ET.ParseError as error:
                        raise RuntimeError(f"Invalid Sparrow atlas XML: {atlas_path}") from error

                resampling = getattr(Image, "Resampling", Image)
                resized = source.convert("RGBA").resize(new_size, resample=resampling.LANCZOS)
                temporary = image_path.with_name(image_path.stem + ".webopt.png")
                resized.save(temporary, format="PNG", optimize=True)
                os.replace(temporary, image_path)
                optimized += 1
                bytes_after += image_path.stat().st_size
                scale_factors[relative.as_posix()] = factor
        except Exception as error:
            raise RuntimeError(f"Could not optimize browser image {image_path}: {error}") from error

    return {
        "optimizedPngs": optimized,
        "updatedAtlases": xml_updated,
        "bytesBefore": bytes_before,
        "bytesAfter": bytes_after,
        "maxDimension": max_dimension,
        "characterMaxDimension": character_max_dimension,
        "scaleFactors": scale_factors,
    }


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


def read_icon_changes(icon_manager_file: Path) -> list[tuple[int, str, str, str]]:
    """Read IconManager.lua's icon/color timeline for the native HTML5 build."""
    if not icon_manager_file.is_file():
        raise FileNotFoundError(f"Bloodbath icon manager is missing: {icon_manager_file}")
    source = icon_manager_file.read_text(encoding="utf-8")
    pattern = re.compile(
        r"curStep\s*==\s*(\d+)\s+then\s*"
        r"runHaxeCode\(\[\[game\.iconP1\.changeIcon\('([^']+)'\)\]\]\)\s*"
        r"setHealthBarColors\('([0-9a-fA-F]+)'\s*,\s*'([0-9a-fA-F]+)'\)",
        re.MULTILINE,
    )
    changes = [
        (int(step), icon, left.lower(), right.lower())
        for step, icon, left, right in pattern.findall(source)
    ]
    if not changes:
        raise RuntimeError("Could not read icon changes from IconManager.lua.")
    return changes


def patch_south_stage(
    engine: Path,
    image_report: dict[str, object],
    icon_changes: list[tuple[int, str, str, str]],
) -> None:
    play_file = engine / "source" / "PlayState.hx"
    source = play_file.read_text(encoding="utf-8")

    # Keep references to the original stage's animated light overlays so the
    # stage.lua step changes can be reproduced natively in the HTML5 build.
    class_marker = "class PlayState extends MusicBeatState\n{"
    fields = """class PlayState extends MusicBeatState
{
	var southLightOverlay:BGSprite;
	var southLightOverlay2:BGSprite;
	var southLightOverlay3:BGSprite;
	var southLightOverlay4:BGSprite;
	var southLightOverlay42:BGSprite;
	var southAnimatedObjects:Map<String, BGSprite> = new Map();
	var southKyleCharacter:Character;
	var southKyleCCharacter:Character;

	function applySouthHealthIcon(iconName:String, leftHex:String, rightHex:String):Void
	{
		if (iconP1 != null) iconP1.changeIcon(iconName);
		if (healthBar != null)
		{
			var left:FlxColor = Std.parseInt('0xff' + leftHex);
			var right:FlxColor = Std.parseInt('0xff' + rightHex);
			healthBar.createFilledBar(left, right);
			healthBar.updateBar();
		}
	}
"""
    if "var southLightOverlay:BGSprite;" not in source:
        if class_marker not in source:
            raise RuntimeError("Could not locate the PlayState class declaration.")
        source = source.replace(class_marker, fields, 1)

    marker = "\t\t\tcase 'stage': //Week 1"
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
				// Keep the original looping idle and one-shot anim sequences.
				southKyle.animation.addByPrefix('anim', 'idle', 24, true);
				southKyle.animation.addByPrefix('anim1', 'anim', 22, false);
				// The original stage reveals this sprite only through chart events.
				southKyle.alpha = 0;
				add(southKyle);
				southAnimatedObjects.set('kyle s', southKyle);

				var southOh:BGSprite = new BGSprite('oh', 608, 164, 1, 1, ['oh'], true);
				southOh.animation.addByPrefix('anim', 'oh', 16, true);
				southOh.scale.set(1.9, 1.9);
				southOh.alpha = 0;
				add(southOh);
				southAnimatedObjects.set('oh', southOh);

				var southCraig:BGSprite = new BGSprite('characters/craig', 1700, 490, 1, 1, ['oh'], true);
				southCraig.animation.addByPrefix('anim', 'oh', 16, true);
				southCraig.scale.set(0.9, 0.9);
				southCraig.alpha = 0;
				add(southCraig);
				southAnimatedObjects.set('craig s', southCraig);

				// Native replacements for the extra Kyle characters created by
				// kyle.lua and kyle c.lua in the desktop mod.
				southKyleCCharacter = new Character(600, 100, 'kyle c');
				southKyleCCharacter.singDuration = 6;
				southKyleCCharacter.alpha = 1;
				// kyle c.lua uses 15 FPS for its animations.
				southKyleCCharacter.animation.getByName('idle').frameRate = 15;
				southKyleCCharacter.animation.getByName('singLEFT').frameRate = 15;
				southKyleCCharacter.animation.getByName('singDOWN').frameRate = 15;
				southKyleCCharacter.animation.getByName('singUP').frameRate = 15;
				southKyleCCharacter.animation.getByName('singRIGHT').frameRate = 15;
				add(southKyleCCharacter);

				southKyleCharacter = new Character(600, 100, 'kyle');
				southKyleCharacter.singDuration = 6;
				southKyleCharacter.alpha = 1;
				southKyleCharacter.addOffset('singLEFT', 70, -48);
				// kyle.lua uses 20 FPS for all five animations.
				southKyleCharacter.animation.getByName('idle').frameRate = 20;
				southKyleCharacter.animation.getByName('singLEFT').frameRate = 20;
				southKyleCharacter.animation.getByName('singDOWN').frameRate = 20;
				southKyleCharacter.animation.getByName('singUP').frameRate = 20;
				southKyleCharacter.animation.getByName('singRIGHT').frameRate = 20;
				add(southKyleCharacter);

				var southBlackbar:BGSprite = new BGSprite('blackbar', 0, 0);
				southBlackbar.cameras = [camHUD];
				southBlackbar.alpha = 0;
				add(southBlackbar);

			case 'stage': //Week 1"""
    # Increase each sprite's Haxe scale by the inverse of its PNG resize ratio.
    # This keeps stage layout/character size while using much smaller GPU textures.
    stage_sprite_assets = {
        "southSun": "sun",
        "southFogBack": "fog bg2",
        "southVignette": "Vignette",
        "southWhite": "white",
        "southFog": "fog",
        "southBlack": "black",
        "southBackground": "bg",
        "southLightOverlay": "luzbg",
        "southBackground3": "bg3",
        "southLightOverlay3": "bg3luz",
        "southBackground4": "bg4",
        "southLightOverlay4": "bg4luz",
        "southLightOverlay42": "bg4luz2",
        "southBackground2": "bg2 s",
        "southBackground2Animated": "bg2",
        "southLightOverlay2": "bg2luz",
        "southSnow": "nieve",
        "southKyle": "kyle s",
        "southOh": "oh",
        "southCraig": "characters/craig",
        "southBlackbar": "blackbar",
    }
    resize_factors = image_report.get("scaleFactors", {})
    for sprite_name, asset_name in stage_sprite_assets.items():
        factor = resize_factors.get(f"{asset_name}.png", 1.0)
        if not isinstance(factor, (int, float)) or factor >= 1.0:
            continue
        inverse = 1.0 / float(factor)
        scale_pattern = re.compile(
            rf"{re.escape(sprite_name)}\.scale\.set\(([^,]+),\s*([^)]+)\);"
        )
        scale_match = scale_pattern.search(stage_code)
        if scale_match:
            old_x = float(scale_match.group(1).strip())
            old_y = float(scale_match.group(2).strip())
            new_x = old_x * inverse
            new_y = old_y * inverse
            stage_code = scale_pattern.sub(
                f"{sprite_name}.scale.set({new_x:.4f}, {new_y:.4f});",
                stage_code,
                count=1,
            )
        else:
            declaration = re.compile(
                rf"(^[\t ]+var {re.escape(sprite_name)}:BGSprite = .*?;\n)",
                re.MULTILINE,
            )
            if not declaration.search(stage_code):
                raise RuntimeError(f"Could not find stage sprite declaration: {sprite_name}")
            stage_code = declaration.sub(
                rf"\1\t\t\t\t{sprite_name}.scale.set({inverse:.4f}, {inverse:.4f});\n",
                stage_code,
                count=1,
            )

    if "Native HTML5 recreation of the South Park Bloodbath stage.lua" not in source:
        if marker not in source:
            raise RuntimeError("Could not find the stage switch insertion point in source/PlayState.hx.")
        source = source.replace(marker, stage_code, 1)

    step_marker = "\t\tsetOnLuas('curStep', curStep);"
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

		if (curStage == 'south')
		{
			switch (curStep)
			{
				case 11:
					if (southKyleCCharacter != null) southKyleCCharacter.alpha = 0;
				case 472:
					if (southKyleCharacter != null) southKyleCharacter.alpha = 1;
				__ICON_CASES__
			}
		}
"""
    if "southLightOverlay.alpha = 0.5;" not in source:
        if step_marker not in source:
            raise RuntimeError("Could not find the PlayState stepHit insertion point.")
        source = source.replace(step_marker, step_addition, 1)

    icon_case_lines = []
    for step, icon_name, left_hex, right_hex in icon_changes:
        icon_case_lines.extend([
            f"\t\t\t\tcase {step}:",
            f"\t\t\t\t\tapplySouthHealthIcon({json.dumps(icon_name)}, {json.dumps(left_hex)}, {json.dumps(right_hex)});",
        ])
    if "__ICON_CASES__" in source:
        source = source.replace("__ICON_CASES__", "\n".join(icon_case_lines), 1)

    # Native beat handling for the two Lua-created extra Kyle characters.
    beat_marker = "lastBeatHit = curBeat;"
    beat_code = """        if (curStage == 'south')
        {
            if (southKyleCharacter != null && southKyleCharacter.alpha > 0.01
                && southKyleCharacter.animation.curAnim != null
                && !southKyleCharacter.animation.curAnim.name.startsWith('sing')
                && curBeat % southKyleCharacter.danceEveryNumBeats == 0)
                southKyleCharacter.dance();

            if (southKyleCCharacter != null && southKyleCCharacter.alpha > 0.01
                && southKyleCCharacter.animation.curAnim != null
                && !southKyleCCharacter.animation.curAnim.name.startsWith('sing')
                && curBeat % southKyleCCharacter.danceEveryNumBeats == 0)
                southKyleCCharacter.dance();
        }

        lastBeatHit = curBeat;"""
    if "southKyleCharacter.dance();" not in source:
        if beat_marker not in source:
            raise RuntimeError("Could not find the PlayState beatHit insertion point.")
        source = source.replace(beat_marker, beat_code, 1)

    # Route the custom kyle note type to the visible extra Kyle character.
    note_marker = "if(note.noteType == 'Hey!' && dad.animOffsets.exists('hey')) {"
    note_code = """        if (curStage == 'south' && note.noteType == 'kyle')
        {
            var kyleAnim:String = singAnimations[Std.int(Math.abs(note.noteData))];
            // Both Lua character scripts receive the same custom note type.
            if (southKyleCharacter != null && southKyleCharacter.alpha > 0.01)
            {
                southKyleCharacter.playAnim(kyleAnim, true);
                southKyleCharacter.holdTimer = 0;
            }
            if (southKyleCCharacter != null && southKyleCCharacter.alpha > 0.01)
            {
                southKyleCCharacter.playAnim(kyleAnim, true);
                southKyleCCharacter.holdTimer = 0;
            }
        }

        if(note.noteType == 'Hey!' && dad.animOffsets.exists('hey')) {"""
    if "var kyleTarget:Character" not in source:
        if note_marker not in source:
            raise RuntimeError("Could not find the PlayState opponentNoteHit insertion point.")
        source = source.replace(note_marker, note_code, 1)

    # Kyle's chart notes are player-side in several sections. Anchor this
    # insertion inside goodNoteHit itself: a plain search for
    # "if(!note.noAnimation)" also matches the opponent method's
    # "else if(!note.noAnimation)", which silently puts the player hook in
    # opponentNoteHit and leaves Kyle idle when the player hits notes.
    good_note_function = "function goodNoteHit(note:Note):Void"
    good_note_start = source.find(good_note_function)
    if good_note_start < 0:
        raise RuntimeError("Could not locate the PlayState goodNoteHit function.")
    next_function = source.find("\n\tfunction ", good_note_start + len(good_note_function))
    good_note_end = next_function if next_function >= 0 else len(source)
    good_note_body = source[good_note_start:good_note_end]

    good_note_marker = "if(!note.noAnimation) {"
    good_note_code = """        if (curStage == 'south' && (note.noteType == 'kyle' || note.noteType == 'Kyle Note'))
        {
            var kylePlayerAnim:String = singAnimations[Std.int(Math.abs(note.noteData))];
            if (southKyleCharacter != null && southKyleCharacter.alpha > 0.01)
            {
                southKyleCharacter.playAnim(kylePlayerAnim, true);
                southKyleCharacter.holdTimer = 0;
            }
            if (southKyleCCharacter != null && southKyleCCharacter.alpha > 0.01)
            {
                southKyleCCharacter.playAnim(kylePlayerAnim, true);
                southKyleCCharacter.holdTimer = 0;
            }
        }

        if(!note.noAnimation) {"""
    if "var kylePlayerAnim:String" not in good_note_body:
        if good_note_marker not in good_note_body:
            raise RuntimeError("Could not find the animation block inside goodNoteHit.")
        good_note_offset = good_note_start + good_note_body.index(good_note_marker)
        source = source[:good_note_offset] + good_note_code + source[good_note_offset + len(good_note_marker):]

    event_marker = "case 'Camera Follow Pos':"
    event_code = """\t\t\tcase 'aObject Play Animation':
\t\t\t\tif (curStage == 'south')
\t\t\t\t{
\t\t\t\t\tvar stageSprite:BGSprite = southAnimatedObjects.get(value1);
\t\t\t\t\tif (stageSprite != null)
\t\t\t\t\t{
\t\t\t\t\t\tstageSprite.visible = true;
\t\t\t\t\t\tstageSprite.alpha = 1;
\t\t\t\t\t\tstageSprite.animation.play(value2, true);
\t\t\t\t\t}
\t\t\t\t}

\t\t\tcase 'Camera Follow Pos':"""
    if "case 'aObject Play Animation':" not in source:
        if event_marker not in source:
            raise RuntimeError("Could not find the PlayState custom-event insertion point.")
        source = source.replace(event_marker, event_code, 1)

    play_file.write_text(source, encoding="utf-8")


def patch_south_stage_fades(engine: Path) -> None:
    """Port the South stage fade and temporary black-flash events to HTML5."""
    play_file = engine / "source" / "PlayState.hx"
    source = play_file.read_text(encoding="utf-8")

    # The desktop stage.lua starts these layers hidden and reveals them with
    # afadein/afadeout chart events. Register the native replacements under
    # the exact object names used by the chart.
    registration_marker = """				var southBlackbar:BGSprite = new BGSprite('blackbar', 0, 0);
				southBlackbar.cameras = [camHUD];
				southBlackbar.alpha = 0;
				add(southBlackbar);
"""
    registration_code = registration_marker + """
				// Stage sprite lookup for the mod's afadein/afadeout events.
				southAnimatedObjects.set('sun', southSun);
				southAnimatedObjects.set('wow1', southFogBack);
				southAnimatedObjects.set('Vignette', southVignette);
				southAnimatedObjects.set('white', southWhite);
				southAnimatedObjects.set('fog', southFog);
				southAnimatedObjects.set('black', southBlack);
				southAnimatedObjects.set('bg', southBackground);
				southAnimatedObjects.set('bgluz', southLightOverlay);
				southAnimatedObjects.set('bg3', southBackground3);
				southAnimatedObjects.set('bgluz3', southLightOverlay3);
				southAnimatedObjects.set('bg4', southBackground4);
				southAnimatedObjects.set('bgluz4', southLightOverlay4);
				southAnimatedObjects.set('bgluz42', southLightOverlay42);
				southAnimatedObjects.set('bg2', southBackground2);
				southAnimatedObjects.set('bg2 n', southBackground2Animated);
				southAnimatedObjects.set('bgluz2', southLightOverlay2);
				southAnimatedObjects.set('nieve', southSnow);
				southAnimatedObjects.set('blackbar', southBlackbar);
				southAnimatedObjects.set('craig', southCraig);
"""
    if "Stage sprite lookup for the mod's afadein/afadeout events." not in source:
        if registration_marker not in source:
            raise RuntimeError("Could not find the end of the South stage sprite setup.")
        source = source.replace(registration_marker, registration_code, 1)

    event_marker = "case 'Camera Follow Pos':"
    event_code = """			case 'afadein' | 'afadeout':
				if (curStage == 'south')
				{
					var fadeSprite:BGSprite = southAnimatedObjects.get(value1);
					if (fadeSprite != null)
					{
						var fadeDuration:Float = Std.parseFloat(value2);
						if (Math.isNaN(fadeDuration) || fadeDuration < 0) fadeDuration = 0;
						var fadeTarget:Float = eventName == 'afadein' ? 1 : 0;
						FlxTween.cancelTweensOf(fadeSprite);
						if (fadeDuration <= 0) fadeSprite.alpha = fadeTarget;
						else FlxTween.tween(fadeSprite, {alpha: fadeTarget}, fadeDuration, {ease: FlxEase.linear});
					}
				}

			case 'Flash Black':
				if (curStage == 'south')
				{
					var flashBlackSprite:BGSprite = southAnimatedObjects.get('black');
					if (flashBlackSprite != null)
					{
						// This is a flash, not a permanent black overlay.
						FlxTween.cancelTweensOf(flashBlackSprite);
						flashBlackSprite.alpha = 1;
						FlxTween.tween(flashBlackSprite, {alpha: 0}, 0.25, {ease: FlxEase.quadOut});
					}
				}

			case 'Camera Follow Pos':"""
    if "case 'Stage sprite lookup for the mod's afadein/afadeout events':" not in source and "case 'afadein':" not in source:
        if event_marker not in source:
            raise RuntimeError("Could not find the PlayState custom-event insertion point.")
        source = source.replace(event_marker, event_code, 1)

    if "southAnimatedObjects.set('black', southBlack);" not in source or "case 'Flash Black':" not in source:
        raise RuntimeError("South stage fade registration or Flash Black handling was not inserted.")
    play_file.write_text(source, encoding="utf-8")


def patch_botplay_pause_menu(engine: Path) -> None:
    """Expose the built-in Botplay toggle in every gameplay pause menu."""
    pause_file = engine / "source" / "PauseSubState.hx"
    source = pause_file.read_text(encoding="utf-8")

    # Psych Engine already implements CPU-controlled note hitting and the
    # pause-menu toggle, but upstream only inserts that menu item in
    # Charting Mode. Move the insertion to the shared pause-menu setup.
    if "Bloodbath: expose Botplay during normal gameplay" not in source:
        charting_only_line = "\t\t\tmenuItemsOG.insert(5 + num, 'Toggle Botplay');\n"
        if charting_only_line not in source:
            raise RuntimeError("Could not find the Charting Mode-only Botplay menu entry.")
        source = source.replace(charting_only_line, "", 1)

        menu_marker = "\t\tmenuItems = menuItemsOG;"
        menu_addition = """\t\t// Bloodbath: expose Botplay during normal gameplay as well as Charting Mode.
\t\tif (!menuItemsOG.contains('Toggle Botplay'))
\t\t{
\t\t\tvar botplayIndex:Int = menuItemsOG.indexOf('Exit to menu');
\t\t\tif (botplayIndex < 0) botplayIndex = menuItemsOG.length;
\t\t\tmenuItemsOG.insert(botplayIndex, 'Toggle Botplay');
\t\t}
\t\tmenuItems = menuItemsOG;"""
        if menu_marker not in source:
            raise RuntimeError("Could not find the shared PauseSubState menu setup.")
        source = source.replace(menu_marker, menu_addition, 1)

    if "case 'Toggle Botplay':" not in source or "PlayState.instance.cpuControlled = !PlayState.instance.cpuControlled;" not in source:
        raise RuntimeError("Psych Engine's built-in Botplay toggle handler is missing.")
    pause_file.write_text(source, encoding="utf-8")


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
    week_list.write_text("week-SPT\n", encoding="utf-8")

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
    week_list.write_text("week-SPT\n", encoding="utf-8")

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

    # Remove unrelated stock-week libraries and resize oversized browser textures.
    patch_project(engine)
    image_report = optimize_browser_images(
        engine / "assets" / "preload" / "images",
        max_dimension=2048,
        character_max_dimension=2048,
    )

    character_names = {"stan", "kenny", "cartman"}
    for role in ("player1", "player2", "gfVersion"):
        role_name = str(song.get(role, "")).strip()
        if role_name:
            character_names.add(role_name)

    # Lua-created characters are not listed in Change Character events.
    for lua_file in (mod / "data" / "bloodbath").glob("*.lua"):
        lua_source = lua_file.read_text(encoding="utf-8", errors="replace")
        for match in re.findall(r'''characterPath\s*=\s*['"]characters/([^'"]+)''', lua_source):
            lua_character = match.strip()
            # Some Lua-controlled sprites (for example Clyde) are not Psych
            # Character JSON definitions. Only include names with a real JSON.
            if (mod / "characters" / f"{lua_character}.json").is_file():
                character_names.add(lua_character)

    for event_group in song.get("events", []):
        if not isinstance(event_group, list) or len(event_group) < 2:
            continue
        entries = event_group[1] if isinstance(event_group[1], list) else []
        for event in entries:
            if isinstance(event, list) and len(event) >= 3 and event[0] == "Change Character":
                character_names.add(str(event[2]).strip())

    icon_dir = engine / "assets" / "preload" / "images" / "icons"
    for character in sorted(character_names):
        character_file = engine / "assets" / "preload" / "characters" / f"{character}.json"
        if not character_file.is_file():
            raise FileNotFoundError(f"Missing chart character definition: {character_file}")
        character_data = json.loads(character_file.read_text(encoding="utf-8"))
        image_name = character_data.get("image", f"characters/{character}")
        relative_image = f"{image_name}.png"
        image_factor = image_report.get("scaleFactors", {}).get(relative_image, 1.0)
        scale_adjusted = False
        if image_factor < 1.0:
            original_scale = float(character_data.get("scale", 1.0))
            character_data["scale"] = round(original_scale / image_factor, 4)
            scale_adjusted = True
        image_file = engine / "assets" / "preload" / "images" / f"{image_name}.png"
        atlas_file = engine / "assets" / "preload" / "images" / f"{image_name}.xml"
        if not image_file.is_file() or not atlas_file.is_file():
            raise FileNotFoundError(
                f"Incomplete {character} sprite atlas: expected {image_file} and {atlas_file}"
            )

        # Some alternate forms ship with a stale "faker-tails" icon value.
        # Prefer their own icon, then the base form's icon, then the stock blank icon.
        current_icon = str(character_data.get("healthicon", "")).strip()
        placeholder_icons = {"", "blank", "icon-blank", "face", "icon-face", "faker-tails", "icon-faker-tails"}
        candidates = []
        if current_icon.lower() not in placeholder_icons:
            candidates.append(current_icon)
        candidates.append(character)
        parts = character.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].lower() in {"c", "s", "w", "f", "a"}:
            candidates.append(parts[0])
        selected_icon = None
        for candidate in candidates:
            if not candidate:
                continue
            # Psych Engine loads health icons using the icon-<name>.png
            # convention. Some mod packages store the same image without the
            # prefix, so normalize it here rather than setting a name the engine
            # cannot resolve.
            prefixed_icon = icon_dir / f"icon-{candidate}.png"
            plain_icon = icon_dir / f"{candidate}.png"
            if prefixed_icon.is_file():
                selected_icon = candidate
                break
            if plain_icon.is_file():
                icon_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(plain_icon, prefixed_icon)
                selected_icon = candidate
                break
        if selected_icon is None:
            if not (icon_dir / "icon-blank.png").is_file():
                raise FileNotFoundError(f"Missing fallback health icon for {character}.")
            selected_icon = "blank"

        if character_data.get("healthicon") != selected_icon:
            character_data["healthicon"] = selected_icon
            scale_adjusted = True
        if scale_adjusted:
            character_file.write_text(
                json.dumps(character_data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )

    icon_changes = read_icon_changes(mod / "data" / "bloodbath" / "IconManager.lua")
    patch_note_types(engine)
    patch_south_stage(engine, image_report, icon_changes)
    patch_south_stage_fades(engine)
    patch_botplay_pause_menu(engine)
    converted = convert_ogg_files(engine)
    song_cleanup = keep_only_bloodbath(engine)

    asset_bytes = sum(p.stat().st_size for p in (engine / "assets").rglob("*") if p.is_file())
    print(f"Prepared game root: {game}")
    print(f"Converted OGG files to MP3: {converted}")
    print("Bloodbath-only cleanup: " + json.dumps(song_cleanup, sort_keys=True))
    print("Browser image optimization: " + json.dumps(image_report, sort_keys=True))
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

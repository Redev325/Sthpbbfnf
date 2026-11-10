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

    Character atlases get a 1280px cap to reduce WebGL texture memory on lower-memory devices
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
	var badAppleWhiteBg:FlxSprite;
	var badAppleSilhouetteActive:Bool = false;
	var badAppleLastCameraSection:Int = -1;
	var southCameraDadX:Float = -320;
	var southCameraDadY:Float = 400;
	var southCameraBoyfriendX:Float = 1200;
	var southCameraBoyfriendY:Float = 540;

	function tweenBadAppleSpriteColor(sprite:FlxSprite, targetColor:FlxColor, duration:Float):Void
	{
		if (sprite == null) return;
		FlxTween.cancelTweensOf(sprite);
		if (duration <= 0) sprite.color = targetColor;
		else FlxTween.color(sprite, duration, sprite.color, targetColor, {ease: FlxEase.linear});
	}

	function setBadAppleSilhouette(enabled:Bool, duration:Float):Void
	{
		var targetColor:FlxColor = enabled ? FlxColor.BLACK : FlxColor.WHITE;
		tweenBadAppleSpriteColor(boyfriend, targetColor, duration);
		tweenBadAppleSpriteColor(dad, targetColor, duration);
		tweenBadAppleSpriteColor(gf, targetColor, duration);
	}

	function updateBadAppleCameraFollow():Void
	{
		if (!badAppleSilhouetteActive || curStage != 'south' || !generatedMusic) return;
		if (badAppleLastCameraSection == curSection) return;
		badAppleLastCameraSection = curSection;
		if (SONG == null || SONG.notes == null || SONG.notes[curSection] == null) return;

		// A Camera Follow Pos event can leave the camera locked to the opponent.
		// During Bad Apple, release that lock at every section boundary and use
		// the chart's real GF/player/opponent section target.
		isCameraOnForcedPos = false;
		moveCameraSection();
	}

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
				southAnimatedObjects.set('white', southWhite);

				var southFog:BGSprite = new BGSprite('fog', -2000, -1000);
				southFog.scale.set(1.4, 1.4);
				southFog.alpha = 0;
				add(southFog);

				var southBlack:BGSprite = new BGSprite('black', 0, 0);
				southBlack.cameras = [camHUD];
				southBlack.alpha = 0;
				add(southBlack);
				southAnimatedObjects.set('black', southBlack);

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
				southAnimatedObjects.set('blackbar', southBlackbar);

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
    """Port South stage fades and black flashes without leaving cover sprites behind."""
    play_file = engine / "source" / "PlayState.hx"
    source = play_file.read_text(encoding="utf-8")

    field_marker = "\tvar badAppleWhiteBg:FlxSprite;"
    if "var southBlackFlashOverlay:FlxSprite;" not in source:
        if field_marker not in source:
            raise RuntimeError("Could not locate the South stage fields for the black flash overlay.")
        source = source.replace(
            field_marker,
            field_marker + "\n\tvar southBlackFlashOverlay:FlxSprite;\n\tvar southCameraZoomTween:FlxTween;",
            1,
        )

    function_marker = "\tfunction applySouthHealthIcon(iconName:String, leftHex:String, rightHex:String):Void"
    flash_function = """\tfunction flashSouthScreenBlack(duration:Float):Void
\t{
\t\tif (curStage != 'south' || southBlackFlashOverlay == null) return;
\t\tif (Math.isNaN(duration) || duration <= 0) duration = 1;
\t\t// The original Black effect Lua uses value1 as the fade-out duration.
\t\t// Clamp malformed chart values so a bad event cannot hold a black screen.
\t\tif (duration > 2.5) duration = 2.5;
\t\tFlxTween.cancelTweensOf(southBlackFlashOverlay);
\t\tsouthBlackFlashOverlay.alpha = 1;
\t\tFlxTween.tween(southBlackFlashOverlay, {alpha: 0}, duration, {ease: FlxEase.linear});
\t}

"""
    if "function flashSouthScreenBlack(duration:Float):Void" not in source:
        if function_marker not in source:
            raise RuntimeError("Could not find the South stage helper insertion point.")
        source = source.replace(function_marker, flash_function + function_marker, 1)

    create_marker = "\t\tcallOnLuas('onCreatePost', []);"
    create_code = """\t\t// The mod's Black effect Lua creates a black sprite on the GAME camera,
\t\t// then fades it back out over value1 seconds. Keep it separate from the
\t\t// stage's persistent black layer and keep the HUD camera unobscured.
\t\tsouthBlackFlashOverlay = new FlxSprite(0, 0);
\t\t// Match the original Lua's screen-sized empty sprite scaled by 10.
\t\t// A game-camera overlay must still cover the viewport at the zoom values
\t\t// used during the late section.
\t\tsouthBlackFlashOverlay.makeGraphic(FlxG.width, FlxG.height, FlxColor.BLACK);
\t\tsouthBlackFlashOverlay.scale.set(10, 10);
\t\tsouthBlackFlashOverlay.scrollFactor.set(0, 0);
\t\tsouthBlackFlashOverlay.cameras = [camGame];
\t\tsouthBlackFlashOverlay.alpha = 0;
\t\tadd(southBlackFlashOverlay);

\t\tcallOnLuas('onCreatePost', []);"""
    if "southBlackFlashOverlay = new FlxSprite(0, 0);" not in source:
        if create_marker not in source:
            raise RuntimeError("Could not find PlayState.onCreatePost() for black flash overlay creation.")
        source = source.replace(create_marker, create_code, 1)

    event_marker = "case 'Camera Follow Pos':"
    event_code = """\t\t\tcase 'afadein' | 'afadeout':
\t\t\t\tif (curStage == 'south')
\t\t\t\t{
\t\t\t\t\tvar fadeSprite:BGSprite = southAnimatedObjects.get(value1);
\t\t\t\t\tif (fadeSprite != null)
\t\t\t\t\t{
\t\t\t\t\t\tvar fadeDuration:Float = Std.parseFloat(value2);
\t\t\t\t\t\tif (Math.isNaN(fadeDuration) || fadeDuration < 0) fadeDuration = 0;
\t\t\t\t\t\tvar fadeTarget:Float = eventName == 'afadein' ? 1 : 0;
\t\t\t\t\t\tFlxTween.cancelTweensOf(fadeSprite);
\t\t\t\t\t\tif (fadeDuration <= 0) fadeSprite.alpha = fadeTarget;
\t\t\t\t\t\telse FlxTween.tween(fadeSprite, {alpha: fadeTarget}, fadeDuration, {ease: FlxEase.linear});
\t\t\t\t\t}
\t\t\t\t}

 \t\t\tcase 'Flash Black' | 'aCameraFlashBlack' | 'CameraFlashBlack':
\t\t\t\tif (curStage == 'south')
\t\t\t\t{
\t\t\t\t\tvar blackFlashDuration:Float = Std.parseFloat(value1);
\t\t\t\t\tif (Math.isNaN(blackFlashDuration) || blackFlashDuration <= 0) blackFlashDuration = 1;
\t\t\t\t\t// This desktop-only custom event is not included in the HTML5 export.\n\t\t\t\t\t// Do not emulate it with a fully opaque overlay: that creates the late-song\n\t\t\t\t\t// black blink immediately after the Bad Apple transition.\n\t\t\t\t\tif (southBlackFlashOverlay != null)\n\t\t\t\t\t{\n\t\t\t\t\t\tFlxTween.cancelTweensOf(southBlackFlashOverlay);\n\t\t\t\t\t\tsouthBlackFlashOverlay.alpha = 0;\n\t\t\t\t\t}
\t\t\t\t}

\t\t\tcase 'Camera Flash':
\t\t\t\tif (curStage == 'south')
\t\t\t\t{
\t\t\t\t\t// Ordinary camera flash: white, not a persistent black overlay.
\t\t\t\t\tvar cameraFlashDuration:Float = Std.parseFloat(value1);
\t\t\t\t\tif (Math.isNaN(cameraFlashDuration) || cameraFlashDuration <= 0) cameraFlashDuration = 0.5;
\t\t\t\t\tif (cameraFlashDuration > 2.5) cameraFlashDuration = 2.5;
\t\t\t\t\tcamGame.flash(FlxColor.WHITE, cameraFlashDuration, null, true);
\t\t\t\t}

case 'badapplelol':
\t\t\t\tif (curStage == 'south')
\t\t\t\t{
\t\t\t\t\tvar badAppleDuration:Float = Std.parseFloat(value2);
\t\t\t\t\tif (Math.isNaN(badAppleDuration) || badAppleDuration < 0) badAppleDuration = 0;
\t\t\t\t\tif (value1.toLowerCase() == 'a')
\t\t\t\t\t{
\t\t\t\t\t\tbadAppleSilhouetteActive = true;
\t\t\t\t\t\tbadAppleLastCameraSection = -1;
\t\t\t\t\t\tif (badAppleWhiteBg == null)
\t\t\t\t\t\t{
\t\t\t\t\t\t\tbadAppleWhiteBg = new FlxSprite();
\t\t\t\t\t\t\tbadAppleWhiteBg.makeGraphic(FlxG.width * 2, FlxG.height * 2, FlxColor.WHITE);
\t\t\t\t\t\t\tbadAppleWhiteBg.setPosition(
\t\t\t\t\t\t\t\t(FlxG.width - badAppleWhiteBg.width) * 0.5,
\t\t\t\t\t\t\t\t(FlxG.height - badAppleWhiteBg.height) * 0.5
\t\t\t\t\t\t\t);
\t\t\t\t\t\t\tbadAppleWhiteBg.scrollFactor.set(0, 0);
\t\t\t\t\t\t\tbadAppleWhiteBg.cameras = [camGame];
\t\t\t\t\t\t\tbadAppleWhiteBg.alpha = 0;
\t\t\t\t\t\t\tvar backgroundIndex:Int = members.indexOf(gfGroup);
\t\t\t\t\t\t\tif (backgroundIndex >= 0) insert(backgroundIndex, badAppleWhiteBg);
\t\t\t\t\t\t\telse add(badAppleWhiteBg);
\t\t\t\t\t\t}
\t\t\t\t\t\tFlxTween.cancelTweensOf(badAppleWhiteBg);
\t\t\t\t\t\tif (badAppleDuration <= 0) badAppleWhiteBg.alpha = 1;
\t\t\t\t\t\telse FlxTween.tween(badAppleWhiteBg, {alpha: 1}, badAppleDuration, {ease: FlxEase.linear});
\t\t\t\t\t\tsetBadAppleSilhouette(true, badAppleDuration);
\t\t\t\t\t}
\t\t\t\t\telse if (value1.toLowerCase() == 'b')
\t\t\t\t\t{
\t\t\t\t\t\tbadAppleSilhouetteActive = false;
\t\t\t\t\t\tbadAppleLastCameraSection = -1;
\t\t\t\t\t\tif (badAppleWhiteBg != null)
\t\t\t\t\t\t{
\t\t\t\t\t\t\tFlxTween.cancelTweensOf(badAppleWhiteBg);
\t\t\t\t\t\t\tif (badAppleDuration <= 0) badAppleWhiteBg.alpha = 0;
\t\t\t\t\t\t\telse FlxTween.tween(badAppleWhiteBg, {alpha: 0}, badAppleDuration, {ease: FlxEase.linear});
\t\t\t\t\t\t}
\t\t\t\t\t\tsetBadAppleSilhouette(false, badAppleDuration);
\t\t\t\t\t\t// Drop only stale previous-event covers; the next charted flash
\t\t\t\t\t\t// is handled independently a few milliseconds later.
\t\t\t\t\t\tvar stageBlack:BGSprite = southAnimatedObjects.get('black');
\t\t\t\t\t\tif (stageBlack != null)
\t\t\t\t\t\t{
\t\t\t\t\t\t\tFlxTween.cancelTweensOf(stageBlack);
\t\t\t\t\t\t\tstageBlack.alpha = 0;
\t\t\t\t\t\t}
\t\t\t\t\t}
\t\t\t\t}

			case 'aofisetiBF' | 'ofisetiBF' | 'aofisetiBF-fest':
				if (curStage == 'south')
				{
					var newBoyfriendCameraX:Float = Std.parseFloat(value1);
					var newBoyfriendCameraY:Float = Std.parseFloat(value2);
					if (!Math.isNaN(newBoyfriendCameraX)) southCameraBoyfriendX = newBoyfriendCameraX;
					if (!Math.isNaN(newBoyfriendCameraY)) southCameraBoyfriendY = newBoyfriendCameraY;
					moveCameraSection();
				}

			case 'aofisetiDad' | 'ofisetiDad' | 'aofisetiDad-fest':
				if (curStage == 'south')
				{
					var newDadCameraX:Float = Std.parseFloat(value1);
					var newDadCameraY:Float = Std.parseFloat(value2);
					if (!Math.isNaN(newDadCameraX)) southCameraDadX = newDadCameraX;
					if (!Math.isNaN(newDadCameraY)) southCameraDadY = newDadCameraY;
					moveCameraSection();
				}

			case 'aCamaraZoom' | 'aCamaraZoom1':
				if (curStage == 'south' && camGame != null)
				{
					var targetCameraZoom:Float = Std.parseFloat(value1);
					var cameraZoomDuration:Float = Std.parseFloat(value2);
					if (!Math.isNaN(targetCameraZoom) && targetCameraZoom > 0 && targetCameraZoom <= 4)
					{
						if (southCameraZoomTween != null)
						{
							southCameraZoomTween.cancel();
							southCameraZoomTween = null;
						}
						// Psych Engine eases camGame.zoom toward defaultCamZoom every
						// update. Tween the target rather than fighting that live loop.
						var currentCameraZoom:Float = defaultCamZoom;
						if (Math.isNaN(currentCameraZoom) || currentCameraZoom <= 0 || currentCameraZoom > 4)
						{
							currentCameraZoom = camGame.zoom;
							if (Math.isNaN(currentCameraZoom) || currentCameraZoom <= 0 || currentCameraZoom > 4)
								currentCameraZoom = 0.6;
							defaultCamZoom = currentCameraZoom;
							if (!camZooming) camGame.zoom = currentCameraZoom;
							trace('[Bloodbath camera zoom] Recovered invalid camera zoom target: ' + Std.string(currentCameraZoom));
						}
						if (Math.isNaN(cameraZoomDuration) || cameraZoomDuration <= 0 || cameraZoomDuration > 10)
						{
							defaultCamZoom = targetCameraZoom;
							if (!camZooming) camGame.zoom = targetCameraZoom;
						}
						else
						{
							var cameraZoomEase:Float->Float = eventName == 'aCamaraZoom1' ? FlxEase.expoOut : FlxEase.sineInOut;
							southCameraZoomTween = FlxTween.num(currentCameraZoom, targetCameraZoom, cameraZoomDuration, {
								ease: cameraZoomEase,
								onComplete: function(twn:FlxTween) {
									defaultCamZoom = targetCameraZoom;
									if (!camZooming && camGame != null)
										camGame.zoom = targetCameraZoom;
								}
							}, function(zoomValue:Float) {
								if (!Math.isNaN(zoomValue) && zoomValue > 0 && zoomValue <= 4)
								{
									defaultCamZoom = zoomValue;
									if (!camZooming && camGame != null)
										camGame.zoom = zoomValue;
								}
							});
						}
					}
				}

case 'Camera Follow Pos':"""
    if "case 'Flash Black' | 'aCameraFlashBlack' | 'Camera Flash':" not in source:
        if len(source.split(event_marker)) - 1 != 1:
            raise RuntimeError("Expected one Camera Follow Pos event marker for South overlay event handling.")
        source = source.replace(event_marker, event_code, 1)

    required = (
        "function flashSouthScreenBlack(duration:Float):Void",
        "southBlackFlashOverlay = new FlxSprite(0, 0);",
        "case 'Flash Black' | 'aCameraFlashBlack' | 'CameraFlashBlack':",
        "case 'Camera Flash':",
        "case 'badapplelol':",
        "case 'aCamaraZoom' | 'aCamaraZoom1':",
    )
    missing = [item for item in required if item not in source]
    if missing:
        raise RuntimeError("South overlay/flash event patch is incomplete: " + repr(missing))
    move_camera_marker = "\tfunction moveCameraSection():Void {"
    move_camera_code = move_camera_marker + """
\t\tif (curStage == 'south' && SONG != null && SONG.notes[curSection] != null)
\t\t{
\t\t\t// The original camera script uses fixed stage-space positions,
\t\t\t// updated by aofisetiBF/aofisetiDad chart events.
\t\t\tif (SONG.notes[curSection].mustHitSection)
\t\t\t\tcamFollow.set(southCameraBoyfriendX, southCameraBoyfriendY);
\t\t\telse
\t\t\t\tcamFollow.set(southCameraDadX, southCameraDadY);
\t\t\treturn;
\t\t}
"""
    if "southCameraBoyfriendX, southCameraBoyfriendY" not in source:
        if move_camera_marker not in source:
            raise RuntimeError("Could not find Psych Engine moveCameraSection() for South custom camera targeting.")
        source = source.replace(move_camera_marker, move_camera_code, 1)

    play_file.write_text(source, encoding="utf-8")



def patch_black_screen_diagnostics(engine: Path) -> None:
    """Recover stuck South-stage covers without drawing diagnostics over gameplay."""
    play_file = engine / "source" / "PlayState.hx"
    source = play_file.read_text(encoding="utf-8")

    field_marker = "\tvar badAppleWhiteBg:FlxSprite;"
    fields = field_marker + """
\tvar lateBlackoutWatchSince:Float = -1;
\tvar lateBlackoutRecoveries:Int = 0;
\tvar blackDiagnosticLastEvent:String = "none";
\tvar blackDiagnosticLastEventTime:Float = 0;
\tvar blackDiagnosticEventHistory:Array<String> = [];
\tvar southBlackDiagnosticPanel:FlxSprite;
\tvar southBlackDiagnosticText:FlxText;

\tfunction blackDiagnosticCameraValue(camera:FlxCamera, fieldName:String):Float
\t{
\t\tif (camera == null) return 0;
\t\tvar rawValue:Dynamic = Reflect.field(camera, fieldName);
\t\tif (rawValue == null) return 0;
\t\tvar parsed:Float = Std.parseFloat(Std.string(rawValue));
\t\treturn Math.isNaN(parsed) ? 0 : parsed;
\t}

\tfunction blackDiagnosticHasBlackFx(camera:FlxCamera):Bool
\t{
\t\tif (camera == null) return false;
\t\tvar fadeAlpha:Float = blackDiagnosticCameraValue(camera, "_fxFadeAlpha");
\t\tvar flashAlpha:Float = blackDiagnosticCameraValue(camera, "_fxFlashAlpha");
\t\tvar fadeColor:Dynamic = Reflect.field(camera, "_fxFadeColor");
\t\tvar flashColor:Dynamic = Reflect.field(camera, "_fxFlashColor");
\t\treturn (fadeAlpha > 0.02 && fadeColor == FlxColor.BLACK)
\t\t\t|| (flashAlpha > 0.02 && flashColor == FlxColor.BLACK);
\t}

\tfunction recoverLateBlackout():Void
\t{
\t\tvar songMs:Float = Conductor.songPosition;
\t\t// The reported blackout starts around 5:56. Keep watching until the
\t\t// song ends instead of stopping at 6:02.
\t\tif (songMs < 356000)
\t\t{
\t\t\tlateBlackoutWatchSince = -1;
\t\t\treturn;
\t\t}

\t\tvar blackLayer:BGSprite = southAnimatedObjects.get('black');
\t\tvar blackbarLayer:BGSprite = southAnimatedObjects.get('blackbar');
\t\tvar whiteLayer:BGSprite = southAnimatedObjects.get('white');
\t\t// At these timestamps every full-screen cover should have finished its
\t\t// charted transition. Catch partially opaque leftovers as well as 100% covers.
\t\t// The Bad Apple white backdrop is intentional until the chart's B event.
\t\tvar badAppleBackdropStuck:Bool = !badAppleSilhouetteActive
\t\t\t&& badAppleWhiteBg != null && badAppleWhiteBg.alpha > 0.02;
\t\tvar blackCover:Bool = (blackLayer != null && blackLayer.alpha > 0.02)
\t\t\t|| (blackbarLayer != null && blackbarLayer.alpha > 0.02)
\t\t\t|| (whiteLayer != null && whiteLayer.alpha > 0.02)
\t\t\t|| badAppleBackdropStuck
\t\t\t|| (southBlackFlashOverlay != null && southBlackFlashOverlay.alpha > 0.02);

\t\tvar cameraCover:Bool = blackDiagnosticHasBlackFx(camGame)
\t\t\t|| blackDiagnosticHasBlackFx(camHUD)
\t\t\t|| blackDiagnosticHasBlackFx(camOther);
\t\tvar upperBarStuck:Bool = southStillStrumUpperBar != null && southStillStrumUpperBar.y > -349;
\t\tvar lowerBarStuck:Bool = southStillStrumLowerBar != null && southStillStrumLowerBar.y < 719;
\t\tvar cinematicCover:Bool = upperBarStuck || lowerBarStuck;

\t\tif (!blackCover && !cameraCover && !cinematicCover)
\t\t{
\t\t\tlateBlackoutWatchSince = -1;
\t\t\treturn;
\t\t}

\t\t// Use wall-clock time because Conductor.songPosition can stop advancing
\t\t// during a stalled audio/gameplay section.
\t\tvar wallNow:Float = Date.now().getTime();
\t\tif (lateBlackoutWatchSince < 0)
\t\t{
\t\t\tlateBlackoutWatchSince = wallNow;
\t\t\treturn;
\t\t}
\t\t// Let the intended 1-second flashes finish before recovering a stuck layer.
\t\tif (wallNow - lateBlackoutWatchSince < 1250) return;

\t\tvar recovered:Array<String> = [];
\t\tif (blackLayer != null && blackLayer.alpha > 0.02)
\t\t{
\t\t\tFlxTween.cancelTweensOf(blackLayer);
\t\t\tblackLayer.alpha = 0;
\t\t\trecovered.push("stage black");
\t\t}
\t\tif (blackbarLayer != null && blackbarLayer.alpha > 0.02)
\t\t{
\t\t\tFlxTween.cancelTweensOf(blackbarLayer);
\t\t\tblackbarLayer.alpha = 0;
\t\t\trecovered.push("stage blackbar");
\t\t}
\t\tif (whiteLayer != null && whiteLayer.alpha > 0.02)
\t\t{
\t\t\tFlxTween.cancelTweensOf(whiteLayer);
\t\t\twhiteLayer.alpha = 0;
\t\t\trecovered.push("stage white");
\t\t}
\t\tif (badAppleBackdropStuck)
\t\t{
\t\t\tFlxTween.cancelTweensOf(badAppleWhiteBg);
\t\t\tbadAppleWhiteBg.alpha = 0;
\t\t\trecovered.push("Bad Apple backdrop");
\t\t}
\t\tif (southBlackFlashOverlay != null && southBlackFlashOverlay.alpha > 0.02)
\t\t{
\t\t\tFlxTween.cancelTweensOf(southBlackFlashOverlay);
\t\t\tsouthBlackFlashOverlay.alpha = 0;
\t\t\trecovered.push("black flash overlay");
\t\t}
\t\tif (upperBarStuck)
\t\t{
\t\t\tFlxTween.cancelTweensOf(southStillStrumUpperBar);
\t\t\tsouthStillStrumUpperBar.y = -350;
\t\t\trecovered.push("upper cinematic bar");
\t\t}
\t\tif (lowerBarStuck)
\t\t{
\t\t\tFlxTween.cancelTweensOf(southStillStrumLowerBar);
\t\t\tsouthStillStrumLowerBar.y = 720;
\t\t\trecovered.push("lower cinematic bar");
\t\t}
\t\tif (blackDiagnosticHasBlackFx(camGame))
\t\t{
\t\t\tcamGame.stopFX();
\t\t\trecovered.push("camGame FX");
\t\t}
\t\tif (blackDiagnosticHasBlackFx(camHUD))
\t\t{
\t\t\tcamHUD.stopFX();
\t\t\trecovered.push("camHUD FX");
\t\t}
\t\tif (blackDiagnosticHasBlackFx(camOther))
\t\t{
\t\t\tcamOther.stopFX();
\t\t\trecovered.push("camOther FX");
\t\t}

\t\tif (recovered.length > 0)
\t\t{
\t\t\tlateBlackoutRecoveries++;
\t\t\tblackDiagnosticLastEvent = "RECOVERED: " + recovered.join(", ");
\t\t\tblackDiagnosticLastEventTime = songMs;
\t\t\tblackDiagnosticEventHistory.push(blackDiagnosticLastEvent + " @ " + Std.string(Std.int(songMs)) + "ms");
\t\t\tif (blackDiagnosticEventHistory.length > 6) blackDiagnosticEventHistory.shift();
\t\t\ttrace("[Bloodbath overlay recovery] " + blackDiagnosticLastEvent);
\t\t}
\t\tlateBlackoutWatchSince = -1;
\t}

\tfunction updateSouthBlackDiagnosticPanel():Void
\t{
\t\tvar songMs:Float = Conductor.songPosition;
\t\tif (!generatedMusic || songMs < 350000)
\t\t{
\t\t\tif (southBlackDiagnosticPanel != null) southBlackDiagnosticPanel.visible = false;
\t\t\tif (southBlackDiagnosticText != null) southBlackDiagnosticText.visible = false;
\t\t\treturn;
\t\t}
\t\tif (southBlackDiagnosticPanel == null)
\t\t{
\t\t\tsouthBlackDiagnosticPanel = new FlxSprite(8, 8);
\t\t\tsouthBlackDiagnosticPanel.makeGraphic(500, 208, 0xDD080808);
\t\t\tsouthBlackDiagnosticPanel.scrollFactor.set(0, 0);
\t\t\tsouthBlackDiagnosticPanel.cameras = [camOther];
\t\t\tadd(southBlackDiagnosticPanel);
\t\t\tsouthBlackDiagnosticText = new FlxText(16, 14, 484, "", 11);
\t\t\tsouthBlackDiagnosticText.font = Paths.font("vcr.ttf");
\t\t\tsouthBlackDiagnosticText.color = FlxColor.WHITE;
\t\t\tsouthBlackDiagnosticText.scrollFactor.set(0, 0);
\t\t\tsouthBlackDiagnosticText.cameras = [camOther];
\t\t\tadd(southBlackDiagnosticText);
\t\t}
\t\tsouthBlackDiagnosticPanel.visible = true;
\t\tsouthBlackDiagnosticText.visible = true;
\t\tvar blackLayer:BGSprite = southAnimatedObjects.get("black");
\t\tvar blackbarLayer:BGSprite = southAnimatedObjects.get("blackbar");
\t\tvar whiteLayer:BGSprite = southAnimatedObjects.get("white");
\t\tvar historyStart:Int = blackDiagnosticEventHistory.length > 3 ? blackDiagnosticEventHistory.length - 3 : 0;
\t\tvar recentHistory:String = blackDiagnosticEventHistory.length > 0
\t\t\t? blackDiagnosticEventHistory.slice(historyStart).join("\\n")
\t\t\t: "none";
\t\tvar lastEventAge:Float = Math.max(0, (songMs - blackDiagnosticLastEventTime) / 1000);
\t\tvar report:String = "BLOODBATH BLACK-SCREEN DIAGNOSTICS  (visible from 5:50)\\n"
\t\t\t+ "TIME " + FlxStringUtil.formatTime(songMs / 1000, true)
\t\t\t+ "   ZOOM game=" + Std.string(blackDiagnosticCameraValue(camGame, "zoom"))
\t\t\t+ " hud=" + Std.string(blackDiagnosticCameraValue(camHUD, "zoom")) + "\\n"
\t\t\t+ "CAM FX black: game=" + blackDiagnosticHasBlackFx(camGame)
\t\t\t+ " hud=" + blackDiagnosticHasBlackFx(camHUD)
\t\t\t+ " other=" + blackDiagnosticHasBlackFx(camOther) + "\\n"
\t\t\t+ "COVERS alpha: black=" + Std.string(blackLayer == null ? -1 : blackLayer.alpha)
\t\t\t+ " blackbar=" + Std.string(blackbarLayer == null ? -1 : blackbarLayer.alpha)
\t\t\t+ " white=" + Std.string(whiteLayer == null ? -1 : whiteLayer.alpha)
\t\t\t+ " flash=" + Std.string(southBlackFlashOverlay == null ? -1 : southBlackFlashOverlay.alpha)
\t\t\t+ " BadApple=" + Std.string(badAppleWhiteBg == null ? -1 : badAppleWhiteBg.alpha) + "\\n"
\t\t\t+ "BARS y: top=" + Std.string(southStillStrumUpperBar == null ? -999 : southStillStrumUpperBar.y)
\t\t\t+ " bottom=" + Std.string(southStillStrumLowerBar == null ? -999 : southStillStrumLowerBar.y) + "\\n"
\t\t\t+ "LAST EVENT (" + Std.string(lastEventAge) + "s ago): " + blackDiagnosticLastEvent + "\\n"
\t\t\t+ "RECOVERIES: " + Std.string(lateBlackoutRecoveries) + "\\n"
\t\t\t+ "RECENT EVENTS:\\n" + recentHistory;
\t\tsouthBlackDiagnosticText.text = report;
\t}

\tfunction updateSouthOverlayRecovery():Void
\t{
\t\tupdateSouthBlackDiagnosticPanel();
\t\trecoverLateBlackout();
\t}
"""
    if "var lateBlackoutWatchSince:Float = -1;" not in source:
        if field_marker not in source:
            raise RuntimeError("Could not locate the South stage fields for blackout recovery.")
        source = source.replace(field_marker, fields, 1)

    update_marker = "\toverride public function update(elapsed:Float)\n\t{"
    update_code = update_marker + "\n\t\tupdateBadAppleCameraFollow();\n\t\tupdateSouthOverlayRecovery();"
    if "updateSouthOverlayRecovery();" not in source:
        if update_marker not in source:
            raise RuntimeError("Could not locate PlayState.update() for South overlay recovery.")
        source = source.replace(update_marker, update_code, 1)

    event_marker = "public function triggerEventNote(eventName:String, value1:String, value2:String) {\n\t\tswitch(eventName) {"
    event_code = """public function triggerEventNote(eventName:String, value1:String, value2:String) {
\t\tblackDiagnosticLastEvent = eventName + " [" + value1 + " | " + value2 + "]";
\t\tblackDiagnosticLastEventTime = Conductor.songPosition;
\t\tblackDiagnosticEventHistory.push(blackDiagnosticLastEvent + " @ " + Std.string(Std.int(Conductor.songPosition)) + "ms");
\t\tif (blackDiagnosticEventHistory.length > 6) blackDiagnosticEventHistory.shift();
\t\tswitch(eventName) {"""
    if "blackDiagnosticLastEvent = eventName" not in source:
        if event_marker not in source:
            raise RuntimeError("Could not locate triggerEventNote() for overlay event logging.")
        source = source.replace(event_marker, event_code, 1)

    required = (
        "function recoverLateBlackout():Void",
        "function updateSouthOverlayRecovery():Void",
        "Date.now().getTime()",
        "southBlackFlashOverlay.alpha = 0;",
        "southStillStrumUpperBar.y = -350;",
        "southStillStrumLowerBar.y = 720;",
        "updateSouthOverlayRecovery();",
    )
    missing = [item for item in required if item not in source]
    if missing:
        raise RuntimeError("South overlay recovery patch is incomplete: " + repr(missing))
    play_file.write_text(source, encoding="utf-8")



def patch_south_cinematic_bars(engine: Path) -> None:
    """Port the mod's Still Strum / With HUD top-and-bottom cinematic bars."""
    play_file = engine / "source" / "PlayState.hx"
    source = play_file.read_text(encoding="utf-8")

    field_marker = "\tvar badAppleWhiteBg:FlxSprite;"
    fields = field_marker + """
\tvar southStillStrumUpperBar:FlxSprite;
\tvar southStillStrumLowerBar:FlxSprite;
\tvar southStillStrumHudOriginalAlpha:Array<Float> = [];

\tfunction tweenSouthCinematicHudAlpha(sprite:FlxSprite, targetAlpha:Float, duration:Float):Void
\t{
\t\tif (sprite == null) return;
\t\tFlxTween.cancelTweensOf(sprite);
\t\tif (duration <= 0) sprite.alpha = targetAlpha;
\t\telse FlxTween.tween(sprite, {alpha: targetAlpha}, duration, {ease: FlxEase.linear});
\t}

\tfunction southStillStrumHudAlphaAt(index:Int):Float
\t{
\t\tif (southStillStrumHudOriginalAlpha != null && index >= 0 && index < southStillStrumHudOriginalAlpha.length)
\t\t\treturn southStillStrumHudOriginalAlpha[index];
\t\treturn 1;
\t}

\tfunction playSouthStillStrumCinematics(speed:Float, distance:Float, hideHud:Bool):Void
\t{
\t\tif (curStage != 'south' || southStillStrumUpperBar == null || southStillStrumLowerBar == null) return;

\t\tvar entering:Bool = distance > 0;
\t\tvar upperTarget:Float = entering ? -350 + distance : -350;
\t\tvar lowerTarget:Float = entering ? 720 - distance : 720;

\t\tFlxTween.cancelTweensOf(southStillStrumUpperBar);
\t\tFlxTween.cancelTweensOf(southStillStrumLowerBar);
\t\tif (speed <= 0)
\t\t{
\t\t\tsouthStillStrumUpperBar.y = upperTarget;
\t\t\tsouthStillStrumLowerBar.y = lowerTarget;
\t\t}
\t\telse if (entering)
\t\t{
\t\t\tFlxTween.tween(southStillStrumUpperBar, {y: upperTarget}, speed, {ease: FlxEase.quadOut});
\t\t\tFlxTween.tween(southStillStrumLowerBar, {y: lowerTarget}, speed, {ease: FlxEase.quadOut});
\t\t}
\t\telse
\t\t{
\t\t\tFlxTween.tween(southStillStrumUpperBar, {y: upperTarget}, speed, {ease: FlxEase.quadIn});
\t\t\tFlxTween.tween(southStillStrumLowerBar, {y: lowerTarget}, speed, {ease: FlxEase.quadIn});
\t\t}

\t\tif (!hideHud) return;
\t\tvar hudDuration:Float = entering ? Math.max(0, speed - 0.1) : speed + 0.1;
\t\ttweenSouthCinematicHudAlpha(healthBarBG, entering ? 0 : southStillStrumHudAlphaAt(0), hudDuration);
\t\ttweenSouthCinematicHudAlpha(healthBar, entering ? 0 : southStillStrumHudAlphaAt(1), hudDuration);
\t\ttweenSouthCinematicHudAlpha(scoreTxt, entering ? 0 : southStillStrumHudAlphaAt(2), hudDuration);
\t\ttweenSouthCinematicHudAlpha(iconP1, entering ? 0 : southStillStrumHudAlphaAt(3), hudDuration);
\t\ttweenSouthCinematicHudAlpha(iconP2, entering ? 0 : southStillStrumHudAlphaAt(4), hudDuration);
\t\ttweenSouthCinematicHudAlpha(timeBar, entering ? 0 : southStillStrumHudAlphaAt(5), hudDuration);
\t\ttweenSouthCinematicHudAlpha(timeBarBG, entering ? 0 : southStillStrumHudAlphaAt(6), hudDuration);
\t\ttweenSouthCinematicHudAlpha(timeTxt, entering ? 0 : southStillStrumHudAlphaAt(7), hudDuration);
\t}

\tfunction playSouthBorderBop(speed:Float, distance:Float):Void
\t{
\t\tif (curStage != 'south' || southStillStrumUpperBar == null || southStillStrumLowerBar == null || distance <= 0) return;

\t\tFlxTween.cancelTweensOf(southStillStrumUpperBar);
\t\tFlxTween.cancelTweensOf(southStillStrumLowerBar);
\t\tsouthStillStrumUpperBar.y = -350 + distance;
\t\tsouthStillStrumLowerBar.y = 720 - distance;
\t\tif (speed <= 0)
\t\t{
\t\t\tsouthStillStrumUpperBar.y = -350;
\t\t\tsouthStillStrumLowerBar.y = 720;
\t\t}
\t\telse
\t\t{
\t\t\tFlxTween.tween(southStillStrumUpperBar, {y: -350}, speed, {ease: FlxEase.expoOut});
\t\t\tFlxTween.tween(southStillStrumLowerBar, {y: 720}, speed, {ease: FlxEase.expoOut});
\t\t}
\t}
"""
    if "var southStillStrumUpperBar:FlxSprite;" not in source:
        if field_marker not in source:
            raise RuntimeError("Could not find the Bad Apple field insertion point for cinematic bars.")
        source = source.replace(field_marker, fields, 1)

    create_marker = "\t\tcallOnLuas('onCreatePost', []);"
    create_code = """\t\t// Native HTML5 version of the mod's onCreatePost cinematic bars.
\t\t// Match the original 1500x350 bars and their off-screen starting positions.
\t\tsouthStillStrumUpperBar = new FlxSprite(-110, -350);
\t\tsouthStillStrumUpperBar.makeGraphic(1500, 350, FlxColor.BLACK);
\t\tsouthStillStrumUpperBar.scrollFactor.set(0, 0);
\t\tsouthStillStrumUpperBar.cameras = [camHUD];
\t\tadd(southStillStrumUpperBar);

\t\tsouthStillStrumLowerBar = new FlxSprite(-110, 720);
\t\tsouthStillStrumLowerBar.makeGraphic(1500, 350, FlxColor.BLACK);
\t\tsouthStillStrumLowerBar.scrollFactor.set(0, 0);
\t\tsouthStillStrumLowerBar.cameras = [camHUD];
\t\tadd(southStillStrumLowerBar);

\t\t// Save the user's actual HUD alpha settings so the exit event restores them.
\t\tsouthStillStrumHudOriginalAlpha = [
\t\t\thealthBarBG == null ? 1 : healthBarBG.alpha,
\t\t\thealthBar == null ? 1 : healthBar.alpha,
\t\t\tscoreTxt == null ? 1 : scoreTxt.alpha,
\t\t\ticonP1 == null ? 1 : iconP1.alpha,
\t\t\ticonP2 == null ? 1 : iconP2.alpha,
\t\t\ttimeBar == null ? 1 : timeBar.alpha,
\t\t\ttimeBarBG == null ? 1 : timeBarBG.alpha,
\t\t\ttimeTxt == null ? 1 : timeTxt.alpha
\t\t];

\t\tcallOnLuas('onCreatePost', []);"""
    if "southStillStrumUpperBar = new FlxSprite(-110, -350);" not in source:
        if create_marker not in source:
            raise RuntimeError("Could not find PlayState.onCreatePost() for cinematic bar creation.")
        source = source.replace(create_marker, create_code, 1)

    event_marker = "case 'Camera Follow Pos':"
    event_code = """\t\t\tcase 'aCinematics (Still Strum)' | 'Cinematics (Still Strum)':
\t\t\t\tif (curStage == 'south')
\t\t\t\t{
\t\t\t\t\tvar stillStrumSpeed:Float = Std.parseFloat(value1);
\t\t\t\t\tvar stillStrumDistance:Float = Std.parseFloat(value2);
\t\t\t\t\tif (Math.isNaN(stillStrumSpeed) || stillStrumSpeed < 0) stillStrumSpeed = 0;
\t\t\t\t\tif (Math.isNaN(stillStrumDistance)) stillStrumDistance = 0;
\t\t\t\t\tplaySouthStillStrumCinematics(stillStrumSpeed, stillStrumDistance, true);
\t\t\t\t}

\t\t\tcase 'Cinematics (With HUD)':
\t\t\t\tif (curStage == 'south')
\t\t\t\t{
\t\t\t\t\tvar withHudSpeed:Float = Std.parseFloat(value1);
\t\t\t\t\tvar withHudDistance:Float = Std.parseFloat(value2);
\t\t\t\t\tif (Math.isNaN(withHudSpeed) || withHudSpeed < 0) withHudSpeed = 0;
\t\t\t\t\tif (Math.isNaN(withHudDistance)) withHudDistance = 0;
\t\t\t\t\tplaySouthStillStrumCinematics(withHudSpeed, withHudDistance, false);
\t\t\t\t}

\t\t\tcase 'Border Bop':
\t\t\t\tif (curStage == 'south')
\t\t\t\t{
\t\t\t\t\tvar borderBopSpeed:Float = Std.parseFloat(value1);
\t\t\t\t\tvar borderBopDistance:Float = Std.parseFloat(value2);
\t\t\t\t\tif (Math.isNaN(borderBopSpeed) || borderBopSpeed < 0) borderBopSpeed = 0;
\t\t\t\t\tif (Math.isNaN(borderBopDistance)) borderBopDistance = 0;
\t\t\t\t\tplaySouthBorderBop(borderBopSpeed, borderBopDistance);
\t\t\t\t}

\t\t\tcase 'Camera Follow Pos':"""
    if "case 'aCinematics (Still Strum)' | 'Cinematics (Still Strum)':" not in source:
        if len(source.split(event_marker)) - 1 != 1:
            raise RuntimeError("Expected one Camera Follow Pos event marker in PlayState.hx.")
        source = source.replace(event_marker, event_code, 1)

    required = [
        "var southStillStrumUpperBar:FlxSprite;",
        "southStillStrumUpperBar = new FlxSprite(-110, -350);",
        "function playSouthStillStrumCinematics(",
        "case 'aCinematics (Still Strum)' | 'Cinematics (Still Strum)':",
        "playSouthBorderBop(borderBopSpeed, borderBopDistance);"
    ]
    missing = [item for item in required if item not in source]
    if missing:
        raise RuntimeError("Cinematic bars patch incomplete: " + repr(missing))
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
        character_max_dimension=1280,
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

    # The upstream engine ships dozens of unrelated character definitions.
    # They are all placed in the default asset manifest and fetched during
    # preload, even though this one-song browser build never uses them. Keep
    # only chart/Lua characters plus the engine's default game-over character.
    character_names.add("bf-dead")
    character_dir = engine / "assets" / "preload" / "characters"
    removed_character_definitions = 0
    for unused_character_file in character_dir.glob("*.json"):
        if unused_character_file.stem not in character_names:
            unused_character_file.unlink()
            removed_character_definitions += 1

    icon_dir = engine / "assets" / "preload" / "images" / "icons"
    for character in sorted(character_names):
        character_file = engine / "assets" / "preload" / "characters" / f"{character}.json"
        if not character_file.is_file():
            raise FileNotFoundError(f"Missing chart character definition: {character_file}")
        character_data = json.loads(character_file.read_text(encoding="utf-8"))
        image_name = character_data.get("image", f"characters/{character}")
        relative_image = f"{image_name}.png"
        preload_images = engine / "assets" / "preload" / "images"
        image_file = preload_images / f"{image_name}.png"
        atlas_file = preload_images / f"{image_name}.xml"
        image_factor = image_report.get("scaleFactors", {}).get(relative_image, 1.0)

        # A few stock engine definitions (notably bf-dead) point at atlases
        # packaged in the shared library rather than the preload library.
        # The HTML5 Project.xml already preloads the shared library, so retain
        # those definitions and validate against their actual asset location.
        if not image_file.is_file() or not atlas_file.is_file():
            shared_images = engine / "assets" / "shared" / "images"
            shared_image_file = shared_images / f"{image_name}.png"
            shared_atlas_file = shared_images / f"{image_name}.xml"
            if shared_image_file.is_file() and shared_atlas_file.is_file():
                image_file = shared_image_file
                atlas_file = shared_atlas_file
                image_factor = 1.0
            else:
                raise FileNotFoundError(
                    f"Incomplete {character} sprite atlas: expected preload "
                    f"{preload_images / (image_name + '.png')} and "
                    f"{preload_images / (image_name + '.xml')}, or shared "
                    f"{shared_image_file} and {shared_atlas_file}"
                )

        scale_adjusted = False
        if image_factor < 1.0:
            original_scale = float(character_data.get("scale", 1.0))
            character_data["scale"] = round(original_scale / image_factor, 4)
            scale_adjusted = True

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
    patch_south_cinematic_bars(engine)
    patch_black_screen_diagnostics(engine)
    patch_botplay_pause_menu(engine)
    converted = convert_ogg_files(engine)
    song_cleanup = keep_only_bloodbath(engine)

    asset_bytes = sum(p.stat().st_size for p in (engine / "assets").rglob("*") if p.is_file())
    print(f"Prepared game root: {game}")
    print(f"Converted OGG files to MP3: {converted}")
    print("Bloodbath-only cleanup: " + json.dumps(song_cleanup, sort_keys=True))
    print(f"Removed unused stock character definitions from the preload manifest: {removed_character_definitions}")
    print("Browser image optimization: " + json.dumps(image_report, sort_keys=True))
    print("Graphics memory optimization: character atlases capped at 1280px; stage art remains capped at 2048px.")
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

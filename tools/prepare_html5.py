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


def install_custom_menu_art(repository_root: Path, image_dir: Path) -> dict[str, str]:
    """Install the repository's custom menu backgrounds at Psych's expected paths."""
    artwork = {
        "menu.png": "menuBG.png",
        "menublue.png": "menuBGBlue.png",
        "menudesat.png": "menuDesat.png",
        "menumagenta.png": "menuBGMagenta.png",
    }
    image_dir.mkdir(parents=True, exist_ok=True)
    for source_name, target_name in artwork.items():
        source_file = repository_root / source_name
        if not source_file.is_file():
            raise FileNotFoundError(
                f"Custom menu artwork is missing from the repository: {source_file}"
            )
        try:
            with Image.open(source_file) as image:
                if image.format != "PNG":
                    raise RuntimeError(
                        f"Custom menu artwork must be a PNG image: {source_file}"
                    )
                image.verify()
        except Exception as error:
            raise RuntimeError(
                f"Could not validate custom menu artwork {source_file}: {error}"
            ) from error

        target_file = image_dir / target_name
        shutil.copy2(source_file, target_file)
        if not target_file.is_file() or target_file.stat().st_size == 0:
            raise RuntimeError(
                f"Custom menu artwork was not copied to the engine: {target_file}"
            )
    return artwork


def patch_menu_background_fit(engine: Path) -> None:
    """Fit the custom 16:9 menu art to the actual game viewport on every menu screen."""
    main_file = engine / "source" / "MainMenuState.hx"
    main = main_file.read_text(encoding="utf-8")
    main_replacements = {
        "bg.setGraphicSize(Std.int(bg.width * 1.175));":
            "bg.setGraphicSize(FlxG.width, FlxG.height);",
        "magenta.setGraphicSize(Std.int(magenta.width * 1.175));":
            "magenta.setGraphicSize(FlxG.width, FlxG.height);",
    }
    for old, new in main_replacements.items():
        if old in main:
            main = main.replace(old, new, 1)
        elif new not in main:
            raise RuntimeError(f"Could not locate MainMenuState background sizing line: {old}")
    main_file.write_text(main, encoding="utf-8")

    achievements_file = engine / "source" / "AchievementsMenuState.hx"
    achievements = achievements_file.read_text(encoding="utf-8")
    old_achievement_size = "menuBG.setGraphicSize(Std.int(menuBG.width * 1.1));"
    new_achievement_size = "menuBG.setGraphicSize(FlxG.width, FlxG.height);"
    if old_achievement_size in achievements:
        achievements = achievements.replace(old_achievement_size, new_achievement_size, 1)
    elif new_achievement_size not in achievements:
        raise RuntimeError("Could not locate AchievementsMenuState background sizing line.")
    achievements_file.write_text(achievements, encoding="utf-8")

    # These screens use menuDesat without scaling. Explicitly fit it to the
    # logical game viewport so all four custom variants share one full-screen
    # framing, including browser windows with a non-native size.
    for state_name in ("FreeplayState.hx", "CreditsState.hx", "ModsMenuState.hx"):
        state_file = engine / "source" / state_name
        state = state_file.read_text(encoding="utf-8")
        load_line = "bg = new FlxSprite().loadGraphic(Paths.image('menuDesat'));"
        fit_lines = (
            load_line
            + "\n\t\tbg.setGraphicSize(FlxG.width, FlxG.height);"
            + "\n\t\tbg.updateHitbox();"
        )
        if load_line in state:
            state = state.replace(load_line, fit_lines, 1)
        elif "bg.setGraphicSize(FlxG.width, FlxG.height);" not in state:
            raise RuntimeError(f"Could not locate {state_name} menu background.")
        state_file.write_text(state, encoding="utf-8")


    # The purple Options screen and its child pages each construct their own
    # menuDesat background, separate from Freeplay/Credits/Mods.
    for state_name in (
        "options/OptionsState.hx",
        "options/BaseOptionsMenu.hx",
        "options/NotesSubState.hx",
        "options/ControlsSubState.hx",
    ):
        state_file = engine / "source" / state_name
        state = state_file.read_text(encoding="utf-8")
        load_line = "var bg:FlxSprite = new FlxSprite().loadGraphic(Paths.image('menuDesat'));"
        fit_lines = (
            load_line
            + "\n\t\tbg.setGraphicSize(FlxG.width, FlxG.height);"
            + "\n\t\tbg.updateHitbox();"
        )
        if load_line in state:
            state = state.replace(load_line, fit_lines, 1)
        elif "bg.setGraphicSize(FlxG.width, FlxG.height);" not in state:
            raise RuntimeError(f"Could not locate purple Options background in {state_name}.")
        state_file.write_text(state, encoding="utf-8")


def remove_unused_stan_v2_atlas(game_root: Path, image_dir: Path) -> list[str]:
    """Exclude an orphan Stan atlas that the game never references.

    The Windows package contains this extra PNG/XML pair in the general image
    directory, so Lime adds it to the HTML5 default preload manifest. The
    compiled game references the regular Stan and Stan R atlases instead.
    Avoid an unnecessary request for this orphaned pair on static hosts.
    """
    reference_roots = [
        game_root / "mods" / "data",
        game_root / "mods" / "characters",
        game_root / "mods" / "weeks",
        game_root / "assets" / "data",
    ]
    text_extensions = {".lua", ".json", ".hx", ".hscript", ".txt"}
    references = []
    for root in reference_roots:
        if not root.is_dir():
            continue
        for source_file in root.rglob("*"):
            if not source_file.is_file() or source_file.suffix.lower() not in text_extensions:
                continue
            try:
                source_text = source_file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "stan v2" in source_text.lower():
                references.append(source_file.relative_to(game_root).as_posix())

    if references:
        print(
            "Keeping stan v2 atlas because package source references it: "
            + json.dumps(references, sort_keys=True)
        )
        return []

    removed = []
    for extension in (".png", ".xml"):
        atlas_file = image_dir / "characters" / f"stan v2{extension}"
        if atlas_file.is_file():
            atlas_file.unlink()
            removed.append(atlas_file.relative_to(image_dir).as_posix())

    if removed:
        print("Removed unreferenced Stan atlas from HTML5 preload: " + json.dumps(removed))
    return removed


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
    # Keep hxCodec's native VLC backend out of the browser build. The
    # HTML5 build gets its own DOM <video> playback path below, while the
    # unrelated stock-week asset libraries are still removed.
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


def align_south_stage_layer_scales(stage_code: str, image_dir: Path) -> str:
    """Match paired South-stage atlases and lighting layers in world space.

    The lighting PNGs are higher-resolution exports than their animation
    atlases. Applying the same numeric scale to both made several overlays
    almost twice as large as the background frame they illuminate. Derive
    each overlay scale from the actual Sparrow frame and PNG dimensions.
    """
    def asset_dimensions(asset_name: str, animated: bool) -> tuple[float, float]:
        image_file = image_dir / f"{asset_name}.png"
        if not image_file.is_file():
            raise FileNotFoundError(f"South-stage alignment image is missing: {image_file}")

        if animated:
            atlas_file = image_file.with_suffix(".xml")
            if not atlas_file.is_file():
                raise FileNotFoundError(f"South-stage animation atlas is missing: {atlas_file}")
            try:
                root = ET.parse(atlas_file).getroot()
            except ET.ParseError as error:
                raise RuntimeError(f"Invalid South-stage animation atlas: {atlas_file}") from error
            frame = root.find(".//SubTexture")
            if frame is None:
                raise RuntimeError(f"South-stage animation atlas has no SubTexture entries: {atlas_file}")
            width = float(frame.get("frameWidth") or frame.get("width") or 0)
            height = float(frame.get("frameHeight") or frame.get("height") or 0)
        else:
            with Image.open(image_file) as image:
                width, height = map(float, image.size)

        if width <= 0 or height <= 0:
            raise RuntimeError(f"Invalid South-stage asset dimensions for {asset_name}: {width}x{height}")
        return width, height

    def sprite_scale(sprite_name: str) -> tuple[float, float]:
        pattern = re.compile(
            rf"(?m)^([ \t]*){re.escape(sprite_name)}\.scale\.set\(\s*"
            rf"([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)\s*\);"
        )
        match = pattern.search(stage_code)
        if not match:
            raise RuntimeError(f"Could not read South-stage scale for {sprite_name}.")
        return float(match.group(2)), float(match.group(3))

    def sprite_position(sprite_name: str) -> tuple[float, float]:
        pattern = re.compile(
            rf"(?m)^([ \t]*)(?:var\s+)?{re.escape(sprite_name)}"
            rf"(?:\s*:\s*BGSprite)?\s*=\s*new BGSprite\(\s*['\"][^'\"]+['\"]\s*,\s*"
            rf"([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)"
        )
        match = pattern.search(stage_code)
        if not match:
            raise RuntimeError(f"Could not read South-stage origin for {sprite_name}.")
        return float(match.group(2)), float(match.group(3))

    alignments = [
        # Animated main layer -> its full-resolution illumination PNG.
        ("southBackground", "bg", True, "southLightOverlay", "luzbg", False),
        ("southBackground3", "bg3", True, "southLightOverlay3", "bg3luz", False),
        ("southBackground4", "bg4", True, "southLightOverlay4", "bg4luz", False),
        ("southBackground4", "bg4", True, "southLightOverlay42", "bg4luz2", False),
        # The second scene's animated atlas, light, and snow all align to its
        # full-resolution static background.
        ("southBackground2", "bg2 s", False, "southBackground2Animated", "bg2", True),
        ("southBackground2", "bg2 s", False, "southLightOverlay2", "bg2luz", False),
        ("southBackground2", "bg2 s", False, "southSnow", "nieve", False),
    ]

    report = []
    for base_sprite, base_asset, base_animated, target_sprite, target_asset, target_animated in alignments:
        base_position = sprite_position(base_sprite)
        target_position = sprite_position(target_sprite)
        if base_position != target_position:
            raise RuntimeError(
                f"South-stage layer origins do not match: {base_sprite} {base_position} "
                f"vs {target_sprite} {target_position}."
            )

        base_width, base_height = asset_dimensions(base_asset, base_animated)
        target_width, target_height = asset_dimensions(target_asset, target_animated)
        base_scale_x, base_scale_y = sprite_scale(base_sprite)
        target_scale_x = base_width * base_scale_x / target_width
        target_scale_y = base_height * base_scale_y / target_height


        scale_pattern = re.compile(
            rf"(?m)^([ \t]*){re.escape(target_sprite)}\.scale\.set\(\s*"
            rf"[-+0-9.eE]+\s*,\s*[-+0-9.eE]+\s*\);"
        )
        scale_match = scale_pattern.search(stage_code)
        if not scale_match:
            raise RuntimeError(f"Could not update South-stage scale for {target_sprite}.")
        indent = scale_match.group(1)
        scale_line = (
            f"{indent}{target_sprite}.scale.set({target_scale_x:.6f}, {target_scale_y:.6f});"
        )
        stage_code = scale_pattern.sub(scale_line, stage_code, count=1)

        # Recompute hitbox dimensions for ordinary stage overlays. Kenny's
        # corrupted bg2 atlas is paired with a static image that keeps the
        # default sprite origin; updateHitbox() changes the atlas offset and
        # visibly shifts it relative to the clean image, so leave that one alone.
        if target_sprite != "southBackground2Animated":
            hitbox_pattern = re.compile(
                rf"(?m)^{re.escape(indent)}{re.escape(target_sprite)}\.updateHitbox\(\);$"
            )
            if not hitbox_pattern.search(stage_code):
                stage_code = stage_code.replace(
                    scale_line,
                    scale_line + f"\n{indent}{target_sprite}.updateHitbox();",
                    1,
                )

        report.append({
            "base": base_sprite,
            "target": target_sprite,
            "baseAssetSize": [base_width, base_height],
            "targetAssetSize": [target_width, target_height],
            "scale": [round(target_scale_x, 6), round(target_scale_y, 6)],
            "origin": list(base_position),
        })

    print("South-stage aligned layers: " + json.dumps(report, sort_keys=True))
    return stage_code


def patch_south_stage(
    engine: Path,
    image_report: dict[str, object],
    icon_changes: list[tuple[int, str, str, str]],
    character_names: set[str],
) -> None:
    play_file = engine / "source" / "PlayState.hx"
    source = play_file.read_text(encoding="utf-8")
    known_character_names = "[" + ", ".join(
        json.dumps(name) for name in sorted(character_names, key=str.lower)
    ) + "]"

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
	var southKnownCharacterNames:Array<String> = __KNOWN_CHARACTER_NAMES__;
	var southKennyBackgroundMode:Int = -1;
	var southKyleCharacter:Character;
	var southKyleCCharacter:Character;
	var southCraigCharacter:Character;
	var southCledyCharacter:Character;
	var southKyleCorruptionStarted:Bool = false;
	var southKyleCorruptionActive:Bool = false;
	var badAppleWhiteBg:FlxSprite;
	var badAppleSilhouetteActive:Bool = false;
	var badAppleLastCameraSection:Int = -1;
	var southCameraDadX:Float = -320;
	var southCameraDadY:Float = 400;
	var southCameraBoyfriendX:Float = 1200;
	var southCameraBoyfriendY:Float = 540;
	var southCameraVerticalOffset:Float = 50;
	var southCameraKennyLeftOffset:Float = 100;
	var southCameraDadOverride:Bool = false;
	var southCameraBoyfriendOverride:Bool = false;
	var southCameraSystemEnabled:Bool = false;
	var southCameraRotationEnabled:Bool = false;
	var southCameraBfOffset:Float = 60;
	var southCameraDadOffset:Float = 60;
	var southCameraDadOffset2:Float = 0;
	var southSmoothCameraEnabled:Bool = true;
	var southSmoothCameraZoomEnabled:Bool = true;
	var southSmoothCameraIntensity:Float = 0.6;
	var southSmoothCameraInitialized:Bool = false;
	var southSmoothCamScrollX:Float = 0;
	var southSmoothCamScrollY:Float = 0;
	var southSmoothRealCamScrollX:Float = 0;
	var southSmoothRealCamScrollY:Float = 0;
	var southSmoothCamZoom:Float = 1;
	var southSmoothRealCamZoom:Float = 1;
	var southCameraAngleTween:FlxTween;
	var southCameraAngleTarget:Float = 0;

	function fitSouthCutsceneOverlay(sprite:BGSprite):Void
	{
		if (sprite == null || sprite.frameWidth <= 0 || sprite.frameHeight <= 0) return;

		// The stage's anim/anim2/anim2 c/anim3 atlases have smaller 16:9
		// frames, but they are full-screen cinematic overlays, not corner sprites.
		// Fit the current atlas frame to the actual game viewport at runtime.
		sprite.setPosition(0, 0);
		sprite.scrollFactor.set(0, 0);
		sprite.scale.set(FlxG.width / sprite.frameWidth, FlxG.height / sprite.frameHeight);
		sprite.updateHitbox();
	}

	function hideSouthAngryKyle():Void
	{
		var stageKyle:BGSprite = southAnimatedObjects.get('kyle s');
		if (stageKyle != null)
		{
			FlxTween.cancelTweensOf(stageKyle);
			stageKyle.alpha = 0;
			stageKyle.visible = false;
		}
	}

	function setSouthKyleCorruptionState(corrupted:Bool):Void
	{
		if (curStage != 'south') return;
		if (corrupted) southKyleCorruptionStarted = true;
		southKyleCorruptionActive = corrupted;

		if (southKyleCharacter != null)
		{
			FlxTween.cancelTweensOf(southKyleCharacter);
			southKyleCharacter.alpha = corrupted ? 0 : 1;
			southKyleCharacter.visible = !corrupted;
		}
		if (southKyleCCharacter != null)
		{
			FlxTween.cancelTweensOf(southKyleCCharacter);
			southKyleCCharacter.alpha = corrupted ? 1 : 0;
			southKyleCCharacter.visible = corrupted;
		}
		if (corrupted) hideSouthAngryKyle();
	}

	function updateSouthKyleCorruptionTimeline():Void
	{
		if (curStage != 'south' || !generatedMusic) return;

		// The release chart's final lowercase "kyle" note section is the
		// corrupted form's turn: it starts at 87,916.7 ms and ends when the
		// following "Kyle Note" section begins at 100,083.3 ms.
		var songPosition:Float = Conductor.songPosition;
		var corruptedTurnActive:Bool = songPosition >= 87916.7 && songPosition < 100083.3;
		if (corruptedTurnActive != southKyleCorruptionActive)
			setSouthKyleCorruptionState(corruptedTurnActive);
	}

	function hideSouthExtraKyles():Void
	{
		if (southKyleCharacter != null)
		{
			FlxTween.cancelTweensOf(southKyleCharacter);
			southKyleCharacter.alpha = 0;
			southKyleCharacter.visible = false;
		}
		if (southKyleCCharacter != null)
		{
			FlxTween.cancelTweensOf(southKyleCCharacter);
			southKyleCCharacter.alpha = 0;
			southKyleCCharacter.visible = false;
		}
		var stageKyle:BGSprite = southAnimatedObjects.get('kyle s');
		if (stageKyle != null)
		{
			FlxTween.cancelTweensOf(stageKyle);
			stageKyle.alpha = 0;
			stageKyle.visible = false;
		}
	}

	function flashSouthStageLight(duration:Float):Void
	{
		if (curStage != 'south' || southAnimatedObjects == null) return;
		var lightKeys:Array<String> = ['bgluz', 'bgluz2', 'bgluz3', 'bgluz4', 'bgluz42'];
		for (lightKey in lightKeys)
		{
			var light:BGSprite = southAnimatedObjects.get(lightKey);
			if (light == null || light.alpha <= 0.01) continue;
			var restingAlpha:Float = light.alpha;
			FlxTween.cancelTweensOf(light);
			light.alpha = 1;
			var lightFadeDuration:Float = duration * 0.7;
			if (lightFadeDuration < 0.25) lightFadeDuration = 0.25;
			FlxTween.tween(light, {alpha: restingAlpha}, lightFadeDuration, {ease: FlxEase.quadOut});
		}
	}

	function resolveSouthCharacterName(requestedName:String):String
	{
		if (requestedName == null) return "";
		var cleanedName:String = requestedName.trim();
		var normalizedName:String = cleanedName.toLowerCase();
		for (knownName in southKnownCharacterNames)
		{
			if (knownName != null && knownName.toLowerCase() == normalizedName)
				return knownName;
		}
		return cleanedName;
	}

	function isSouthBackgroundLayer(key:String):Bool
	{
		return key == 'bg' || key == 'bg2' || key == 'bg2 n' || key == 'bg3' || key == 'bg4';
	}

	function setSouthKennyBackground(enabled:Bool, corrupted:Bool, duration:Float):Void
	{
		if (curStage != 'south' || southAnimatedObjects == null) return;
		if (Math.isNaN(duration) || duration < 0) duration = 0;

		var nextMode:Int = !enabled ? 0 : (corrupted ? 2 : 1);
		if (southKennyBackgroundMode == nextMode) return;
		southKennyBackgroundMode = nextMode;

		// The clean bg2 s still and corrupted bg2 atlas occupy the same stage
		// coordinates. Crossfade between them; never display both together.
		var cleanBackground:BGSprite = southAnimatedObjects.get('bg2');
		var corruptedBackground:BGSprite = southAnimatedObjects.get('bg2 n');
		var cleanTarget:Float = nextMode == 1 ? 1 : 0;
		var corruptedTarget:Float = nextMode == 2 ? 1 : 0;

		if (cleanBackground != null)
		{
			FlxTween.cancelTweensOf(cleanBackground);
			if (cleanTarget > 0.01) cleanBackground.visible = true;
			if (duration <= 0)
			{
				cleanBackground.alpha = cleanTarget;
				cleanBackground.visible = cleanTarget > 0.01;
			}
			else
			{
				FlxTween.tween(cleanBackground, {alpha: cleanTarget}, duration, {
					ease: FlxEase.sineInOut,
					onComplete: function(_:FlxTween) {
						cleanBackground.visible = cleanTarget > 0.01;
					}
				});
			}
		}

		if (corruptedBackground != null)
		{
			FlxTween.cancelTweensOf(corruptedBackground);
			if (corruptedTarget > 0.01) corruptedBackground.visible = true;
			if (duration <= 0)
			{
				corruptedBackground.alpha = corruptedTarget;
				corruptedBackground.visible = corruptedTarget > 0.01;
			}
			else
			{
				FlxTween.tween(corruptedBackground, {alpha: corruptedTarget}, duration, {
					ease: FlxEase.sineInOut,
					onComplete: function(_:FlxTween) {
						corruptedBackground.visible = corruptedTarget > 0.01;
					}
				});
			}
		}
	}

	function setSouthBackgroundLayer(activeKey:String, duration:Float):Void
	{
		if (curStage != 'south' || southAnimatedObjects == null) return;
		var normalizedKey:String = activeKey == null ? "" : activeKey.trim().toLowerCase();
		if (normalizedKey == 'bg2 n') normalizedKey = 'bg2';
		if (!isSouthBackgroundLayer(normalizedKey)) return;
		if (Math.isNaN(duration) || duration < 0) duration = 0;

		// Kenny's human-form playground uses the uploaded clean playground
		// image (the mod's full-resolution bg2 s). When Kenny returns to
		// kenny c, swap to the corrupted bg2 atlas at the identical position.
		var kennyBackgroundIsActive:Bool = normalizedKey == 'bg2';
		var kennyMonsterForm:Bool = dad != null && dad.curCharacter != null
			&& dad.curCharacter.toLowerCase() == 'kenny c';
		setSouthKennyBackground(kennyBackgroundIsActive, kennyMonsterForm, duration);

		var backgroundGroups:Array<Array<String>> = [
			['bg'],
			['bg3'],
			['bg4']
		];
		for (backgroundGroup in backgroundGroups)
		{
			var groupIsActive:Bool = backgroundGroup.indexOf(normalizedKey) >= 0;
			for (backgroundKey in backgroundGroup)
			{
				var backgroundSprite:BGSprite = southAnimatedObjects.get(backgroundKey);
				if (backgroundSprite == null) continue;
				var targetAlpha:Float = groupIsActive ? 1 : 0;
				FlxTween.cancelTweensOf(backgroundSprite);
				if (targetAlpha > 0.01) backgroundSprite.visible = true;
				if (duration <= 0)
				{
					backgroundSprite.alpha = targetAlpha;
					backgroundSprite.visible = targetAlpha > 0.01;
				}
				else
				{
					FlxTween.tween(backgroundSprite, {alpha: targetAlpha}, duration, {
						ease: FlxEase.sineInOut,
						onComplete: function(_:FlxTween) {
							backgroundSprite.visible = targetAlpha > 0.01;
						}
					});
				}
			}
		}
	}

	function updateSouthKennyFormBackground():Void
	{
		if (curStage != 'south' || !generatedMusic || Conductor.songPosition < 107083
			|| Conductor.songPosition >= 202750 || southAnimatedObjects == null) return;
		var cleanBackground:BGSprite = southAnimatedObjects.get('bg2');
		var corruptedBackground:BGSprite = southAnimatedObjects.get('bg2 n');
		// Wait until the second playground background has actually been activated.
		if ((cleanBackground == null || cleanBackground.alpha <= 0.01)
			&& (corruptedBackground == null || corruptedBackground.alpha <= 0.01)) return;

		var monsterForm:Bool = dad != null && dad.curCharacter != null
			&& dad.curCharacter.toLowerCase() == 'kenny c';
		setSouthKennyBackground(true, monsterForm, 0.55);
	}

	function playSouthCharacterAnimation(character:Character, requestedAnimation:String):Bool
	{
		if (character == null || requestedAnimation == null) return false;
		var animationName:String = requestedAnimation.trim();
		for (characterAnimation in character.animationsArray)
		{
			var candidate:String = characterAnimation.anim;
			if (candidate != null && candidate.toLowerCase() == animationName.toLowerCase())
			{
				animationName = candidate;
				break;
			}
		}
		if (character.animation.getByName(animationName) == null)
		{
			trace('[Bloodbath sprite event] Missing character animation "' + requestedAnimation + '" for ' + character.curCharacter);
			return false;
		}
		// The chart's explicit kyle/kyle c animation cue is the form-change
		// trigger. Keep exactly one Kyle form visible and remove the angry
		// kyle s overlay as soon as the corrupted form takes over.
		if (character == southKyleCCharacter)
			setSouthKyleCorruptionState(true);
		else if (character == southKyleCharacter)
			setSouthKyleCorruptionState(false);

		character.visible = true;
		character.alpha = 1;
		character.playAnim(animationName, true);
		character.specialAnim = true;
		return true;
	}

	function playSouthStageObjectAnimation(key:String, requestedAnimation:String):Bool
	{
		var normalizedKey:String = key == null ? "" : key.trim().toLowerCase();
		if (normalizedKey == 'kyle' || normalizedKey == 'kyle c' || normalizedKey == 'kylec')
		{
			var kyleTarget:Character = normalizedKey == 'kyle' ? southKyleCharacter : southKyleCCharacter;
			return playSouthCharacterAnimation(kyleTarget, requestedAnimation);
		}

		if (normalizedKey == 'craig') return playSouthCharacterAnimation(southCraigCharacter, requestedAnimation);
		if (normalizedKey == 'cledy') return playSouthCharacterAnimation(southCledyCharacter, requestedAnimation);

		var stageSpriteKey:String = normalizedKey;
		if (normalizedKey == 'bg2' && requestedAnimation != null && requestedAnimation.trim().length > 0)
			stageSpriteKey = 'bg2 n';
		var stageSprite:BGSprite = southAnimatedObjects.get(stageSpriteKey);
		if (stageSprite == null) return false;
		var animationName:String = requestedAnimation == null ? "" : requestedAnimation.trim();
		if (animationName.length > 0 && stageSprite.animation.getByName(animationName) == null)
		{
			// BGSprite animation aliases are registered explicitly by the stage
			// setup; this pinned Flixel build has no public animation-name iterator.
			trace('[Bloodbath sprite event] Missing stage animation "' + requestedAnimation + '" for ' + normalizedKey);
			return false;
		}

		// Switch to the dedicated animated Kyle atlas when he confronts Cartman.
		if (normalizedKey == 'kyle s')
		{
			// Once the corrupted Kyle has taken over, the angry overlay must
			// never be shown again later in the chart.
			if (southKyleCorruptionStarted) return false;
			hideSouthExtraKyles();
		}
		if (isSouthBackgroundLayer(normalizedKey)) setSouthBackgroundLayer(normalizedKey, 0.35);
		stageSprite.visible = true;
		stageSprite.alpha = 1;
		if (animationName.length > 0) stageSprite.animation.play(animationName, true);
		return true;
	}

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
    fields = fields.replace("__KNOWN_CHARACTER_NAMES__", known_character_names)
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
				southAnimatedObjects.set('sun', southSun);

				var southFogBack:BGSprite = new BGSprite('fog bg2', -1670, -1240);
				southFogBack.scale.set(3, 3);
				southFogBack.alpha = 0;
				add(southFogBack);
				southAnimatedObjects.set('wow1', southFogBack);

				// The desktop stage contains two separate Vignette sprites:
				// one slightly inset on the HUD camera, then a full-screen
				// overlay on camOther. Do not merge their coordinates/cameras.
				var southVignetteHud:BGSprite = new BGSprite('Vignette', -20, 0);
				southVignetteHud.cameras = [camHUD];
				southVignetteHud.alpha = 0;
				add(southVignetteHud);
				southAnimatedObjects.set('vignetteHud', southVignetteHud);

				var southWhite:BGSprite = new BGSprite('white', -1700, -500);
				southWhite.cameras = [camHUD];
				southWhite.alpha = 0;
				add(southWhite);
				southAnimatedObjects.set('white', southWhite);

				var southFog:BGSprite = new BGSprite('fog', -2000, -1000);
				southFog.scale.set(1.4, 1.4);
				southFog.alpha = 0;
				add(southFog);
				southAnimatedObjects.set('fog', southFog);

				var southBlack:BGSprite = new BGSprite('black', 0, 0);
				southBlack.cameras = [camHUD];
				southBlack.alpha = 0;
				add(southBlack);
				southAnimatedObjects.set('black', southBlack);

				var southBackground:BGSprite = new BGSprite('bg', -1670, -1540, 1, 1, ['bg a'], true);
				southBackground.scale.set(2.7, 2.7);
				southBackground.updateHitbox();
				southBackground.alpha = 1;
				southBackground.visible = true;
				add(southBackground);
				southAnimatedObjects.set('bg', southBackground);

				southLightOverlay = new BGSprite('luzbg', -1670, -1540);
				southLightOverlay.scale.set(2.7, 2.7);
				southLightOverlay.alpha = 0;
				add(southLightOverlay);
				southAnimatedObjects.set('bgluz', southLightOverlay);

				var southBackground3:BGSprite = new BGSprite('bg3', -1770, -940, 1, 1, ['idle'], true);
				southBackground3.scale.set(2.3, 2.3);
				southBackground3.alpha = 0;
				add(southBackground3);
				southAnimatedObjects.set('bg3', southBackground3);

				southLightOverlay3 = new BGSprite('bg3luz', -1770, -940);
				southLightOverlay3.scale.set(2.3, 2.3);
				southLightOverlay3.alpha = 0;
				add(southLightOverlay3);
				southAnimatedObjects.set('bgluz3', southLightOverlay3);

				var southBackground4:BGSprite = new BGSprite('bg4', -540, -740, 1, 1, ['bg4 a'], true);
				southBackground4.scale.set(1.7, 1.7);
				southBackground4.alpha = 0;
				add(southBackground4);
				southAnimatedObjects.set('bg4', southBackground4);

				southLightOverlay4 = new BGSprite('bg4luz', -540, -740);
				southLightOverlay4.scale.set(1.7, 1.7);
				southLightOverlay4.alpha = 0;
				add(southLightOverlay4);
				southAnimatedObjects.set('bgluz4', southLightOverlay4);

				southLightOverlay42 = new BGSprite('bg4luz2', -540, -740);
				southLightOverlay42.scale.set(1.7, 1.7);
				southLightOverlay42.alpha = 0;
				add(southLightOverlay42);
				southAnimatedObjects.set('bgluz42', southLightOverlay42);

				var southBackground2:BGSprite = new BGSprite('bg2 s', -1970, -1040);
				southBackground2.scale.set(2.7, 2.7);
				southBackground2.alpha = 0;
				add(southBackground2);
				southAnimatedObjects.set('bg2', southBackground2);

				var southBackground2Animated:BGSprite = new BGSprite('bg2', -1970, -1040, 1, 1, ['idle'], true);
				// The corrupted bg2 artwork matches the clean bg2 s artwork at
				// exactly 2x in both axes. Its 960x546 frame has extra canvas
				// height, so match artwork scale rather than total frame bounds.
				// The Python optimizer later compensates for the atlas resize.
				southBackground2Animated.scale.set(5.4, 5.3407);
				// Keep the same default sprite origin as the clean bg2 s still.
				// updateHitbox() changes the atlas offset and makes this layer
				// appear shifted even though its stage position and bounds match.
				southBackground2Animated.alpha = 0;
				add(southBackground2Animated);
				southAnimatedObjects.set('bg2 n', southBackground2Animated);

				southLightOverlay2 = new BGSprite('bg2luz', -1970, -1040);
				southLightOverlay2.scale.set(2.7, 2.7);
				southLightOverlay2.flipX = true;
				southLightOverlay2.alpha = 0;
				add(southLightOverlay2);
				southAnimatedObjects.set('bgluz2', southLightOverlay2);

				var southSnow:BGSprite = new BGSprite('nieve', -1970, -1040);
				southSnow.scale.set(2.7, 2.7);
				southSnow.alpha = 0;
				add(southSnow);
				southAnimatedObjects.set('nieve', southSnow);

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

                // The original scripts create two different Craig sprites.
                // south.lua's dormant stage prop is at (1700, 490); craig.lua's
                // custom character is separately created at (1670, 400).
                var southCraigStage:BGSprite = new BGSprite('characters/craig', 1700, 490, 1, 1, ['oh'], true);
                southCraigStage.animation.addByPrefix('anim', 'oh', 16, true);
                southCraigStage.scale.set(0.9, 0.9);
                southCraigStage.alpha = 0;
                add(southCraigStage);
                southAnimatedObjects.set('craig s', southCraigStage);

				// Native replacements for the extra Kyle characters created by
				// kyle.lua and kyle c.lua in the desktop mod.
				southKyleCCharacter = new Character(600, 100, 'kyle c');
				southKyleCCharacter.singDuration = 6;
				// The corrupted Kyle was being drawn on top of the active Kyle.
				// Keep this alternate sprite hidden unless a chart event explicitly uses it.
				southKyleCCharacter.alpha = 0;
				southKyleCCharacter.visible = false;
				// kyle c.lua uses 15 FPS for its animations.
				southKyleCCharacter.animation.getByName('idle').frameRate = 15;
				southKyleCCharacter.animation.getByName('singLEFT').frameRate = 15;
				southKyleCCharacter.animation.getByName('singDOWN').frameRate = 15;
				southKyleCCharacter.animation.getByName('singUP').frameRate = 15;
				southKyleCCharacter.animation.getByName('singRIGHT').frameRate = 15;
				add(southKyleCCharacter);

				southKyleCharacter = new Character(600, 100, 'kyle');
				southKyleCharacter.singDuration = 6;
				// kyle.lua creates the normal Kyle immediately in onCreate().
				// Keep him visible from the opening notes until the angry kyle s cut-in.
				southKyleCharacter.alpha = 1;
				southKyleCharacter.visible = true;
				southKyleCharacter.addOffset('singLEFT', 70, -48);
				// kyle.lua uses 20 FPS for all five animations.
				southKyleCharacter.animation.getByName('idle').frameRate = 20;
				southKyleCharacter.animation.getByName('singLEFT').frameRate = 20;
				southKyleCharacter.animation.getByName('singDOWN').frameRate = 20;
				southKyleCharacter.animation.getByName('singUP').frameRate = 20;
				southKyleCharacter.animation.getByName('singRIGHT').frameRate = 20;
				add(southKyleCharacter);

                // Restore both extra-character scripts as independent sprites
                // using their exact Lua world positions, scales and offsets.
                southCraigCharacter = new Character(1670, 400, 'craig');
                southCraigCharacter.singDuration = 6;
                southCraigCharacter.addOffset('idle', -10, -65);
                southCraigCharacter.addOffset('singLEFT', 69, -96);
                southCraigCharacter.addOffset('singDOWN', 25, -104);
                southCraigCharacter.addOffset('singUP', 110, -43);
                southCraigCharacter.addOffset('singRIGHT', -82, -70);
                southCraigCharacter.animation.getByName('idle').frameRate = 17;
                southCraigCharacter.animation.getByName('singLEFT').frameRate = 17;
                southCraigCharacter.animation.getByName('singDOWN').frameRate = 17;
                southCraigCharacter.animation.getByName('singUP').frameRate = 17;
                southCraigCharacter.animation.getByName('singRIGHT').frameRate = 17;
                southCraigCharacter.alpha = 0;
                southCraigCharacter.visible = false;
                add(southCraigCharacter);

                southCledyCharacter = new Character(700, 220, 'cledy');
                southCledyCharacter.singDuration = 6;
                southCledyCharacter.addOffset('idle', -10, -65);
                southCledyCharacter.addOffset('singLEFT', 77, -69);
                southCledyCharacter.addOffset('singDOWN', -3, -109);
                southCledyCharacter.addOffset('singUP', -15, -31);
                southCledyCharacter.addOffset('singRIGHT', -81, -62);
                southCledyCharacter.animation.getByName('idle').frameRate = 18;
                southCledyCharacter.animation.getByName('singLEFT').frameRate = 18;
                southCledyCharacter.animation.getByName('singDOWN').frameRate = 18;
                southCledyCharacter.animation.getByName('singUP').frameRate = 18;
                southCledyCharacter.animation.getByName('singRIGHT').frameRate = 18;
                southCledyCharacter.alpha = 0;
                southCledyCharacter.visible = false;
                add(southCledyCharacter);

				var southBlackbar:BGSprite = new BGSprite('blackbar', 0, 0);
				southBlackbar.cameras = [camHUD];
				southBlackbar.alpha = 0;
				add(southBlackbar);
				southAnimatedObjects.set('blackbar', southBlackbar);

				var southAnim:BGSprite = new BGSprite('anim', 0, 0, 1, 1, ['anim a'], true);
				southAnim.animation.addByPrefix('anim', 'anim a', 24, false);
				fitSouthCutsceneOverlay(southAnim);
				southAnim.cameras = [camHUD];
				southAnim.alpha = 0;
				add(southAnim);
				southAnimatedObjects.set('anim', southAnim);

				var southAnim2:BGSprite = new BGSprite('anim2', 0, 0, 1, 1, ['anim2 a'], true);
				southAnim2.animation.addByPrefix('anim', 'anim2 a', 24, false);
				fitSouthCutsceneOverlay(southAnim2);
				southAnim2.cameras = [camHUD];
				southAnim2.alpha = 0;
				add(southAnim2);
				southAnimatedObjects.set('anim2', southAnim2);

				var southAnim2C:BGSprite = new BGSprite('anim2 c', 0, 0, 1, 1, ['anim2 c a'], true);
				southAnim2C.animation.addByPrefix('anim', 'anim2 c a', 24, false);
				fitSouthCutsceneOverlay(southAnim2C);
				southAnim2C.cameras = [camHUD];
				southAnim2C.alpha = 0;
				add(southAnim2C);
				southAnimatedObjects.set('anim2 c', southAnim2C);

				var southAnim3:BGSprite = new BGSprite('anim3', 0, 0, 1, 1, ['anim3 idle'], true);
				southAnim3.animation.addByPrefix('anim', 'anim3 idle', 24, false);
				fitSouthCutsceneOverlay(southAnim3);
				southAnim3.cameras = [camHUD];
				southAnim3.alpha = 0;
				add(southAnim3);
				southAnimatedObjects.set('anim3', southAnim3);

				// Match the second desktop Vignette instance: full-screen and
				// anchored at the viewport origin on camOther.
				var southVignette:BGSprite = new BGSprite('Vignette', 0, 0);
				southVignette.cameras = [camOther];
				southVignette.alpha = 0;
				add(southVignette);
				southAnimatedObjects.set('vignette', southVignette);

			case 'stage': //Week 1"""
    # Increase each sprite's Haxe scale by the inverse of its PNG resize ratio.
    # This keeps stage layout/character size while using much smaller GPU textures.
    stage_sprite_assets = {
        "southSun": "sun",
        "southFogBack": "fog bg2",
        "southVignetteHud": "Vignette",
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
        "southCraigStage": "characters/craig",
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

    # The original Kyle Lua scripts create ordinary sprites at scale 1.0.
    # The HTML5 port uses Character objects (whose JSON scales are for normal
    # gameplay characters), so override those scales to the Lua sprite size,
    # compensating only for the PNG resize performed above.
    for sprite_name, image_name, lua_scale in (
        ("southKyleCharacter", "characters/kyle", 1.0),
        ("southKyleCCharacter", "characters/kyle c", 1.0),
        ("southCraigCharacter", "characters/craig", 0.9),
        ("southCledyCharacter", "characters/clyde", 1.9),
    ):
        factor = resize_factors.get(f"{image_name}.png", 1.0)
        if not isinstance(factor, (int, float)) or factor <= 0:
            raise RuntimeError(f"Invalid image resize factor for {image_name}: {factor!r}")
        final_scale = lua_scale / float(factor)
        declaration = re.compile(
            rf"(?m)^([ \t]*){re.escape(sprite_name)} = new Character\([^\n]*\);$"
        )
        match = declaration.search(stage_code)
        if not match:
            raise RuntimeError(f"Could not find custom Kyle character declaration: {sprite_name}")
        indent = match.group(1)
        scale_code = (
            f"\n{indent}{sprite_name}.scale.set({final_scale:.4f}, {final_scale:.4f});"
            f"\n{indent}{sprite_name}.updateHitbox();"
        )
        stage_code = stage_code[:match.end()] + scale_code + stage_code[match.end():]

    # Align every South-stage lighting/overlay layer to its actual atlas frame.
    # This also re-derives the bg2 animation scale instead of relying on hand-tuned
    # constants, so future image optimization cannot silently shift the layers.
    stage_code = align_south_stage_layer_scales(
        stage_code,
        engine / "assets" / "preload" / "images",
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
					setSouthBackgroundLayer('bg', 0.45);
					southLightOverlay.alpha = 0.5;
				case 1202:
					southLightOverlay.alpha = 0;
				case 1285:
					setSouthBackgroundLayer('bg2', 0.55);
					// Bring the second scene's light up from a low alpha to make the
					// 5:55-left Black effect land on the new background.
					FlxTween.cancelTweensOf(southLightOverlay2);
					southLightOverlay2.alpha = 0.22;
					FlxTween.tween(southLightOverlay2, {alpha: 1}, 0.38, {ease: FlxEase.quadOut});
					// Kyle's note section ends before this background transition.
					// Clear the normal/angry forms at the transition, but don't
					// erase corrupted Kyle if his charted takeover has already begun.
					if (southKyleCCharacter == null || !southKyleCCharacter.visible)
						hideSouthExtraKyles();
					else
						hideSouthAngryKyle();
				case 2325:
					southLightOverlay2.alpha = 0;
				case 2433:
					// Reveal the third background together with its matching light layer.
					setSouthBackgroundLayer('bg3', 0.55);
					southLightOverlay3.alpha = 0.75;
				case 3713:
					setSouthBackgroundLayer('bg4', 0.55);
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
					// Keep the corrupted alternate Kyle hidden during the opening section.
					// The normal Kyle remains visible until the charted kyle s confrontation.
					if (southKyleCCharacter != null) {
						southKyleCCharacter.alpha = 0;
						southKyleCCharacter.visible = false;
					}
                    if (southCraigCharacter != null) {
                        southCraigCharacter.alpha = 0;
                        southCraigCharacter.visible = false;
                    }
                    if (southCledyCharacter != null) {
                        southCledyCharacter.alpha = 0;
                        southCledyCharacter.visible = false;
                    }
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

        if (curStage == 'south' && (note.noteType == 'Clyde Note' || note.noteType == 'Craig Note'))
        {
            var extraOpponentCharacter:Character = note.noteType == 'Clyde Note' ? southCledyCharacter : southCraigCharacter;
            var extraOpponentAnim:String = singAnimations[Std.int(Math.abs(note.noteData))];
            if (extraOpponentCharacter != null && extraOpponentCharacter.animation.getByName(extraOpponentAnim) != null)
            {
                extraOpponentCharacter.visible = true;
                extraOpponentCharacter.alpha = 1;
                extraOpponentCharacter.playAnim(extraOpponentAnim, true);
                extraOpponentCharacter.holdTimer = 0;
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

        if (curStage == 'south' && (note.noteType == 'Clyde Note' || note.noteType == 'Craig Note'))
        {
            var extraPlayerCharacter:Character = note.noteType == 'Clyde Note' ? southCledyCharacter : southCraigCharacter;
            var extraPlayerAnim:String = singAnimations[Std.int(Math.abs(note.noteData))];
            if (extraPlayerCharacter != null && extraPlayerCharacter.animation.getByName(extraPlayerAnim) != null)
            {
                extraPlayerCharacter.visible = true;
                extraPlayerCharacter.alpha = 1;
                extraPlayerCharacter.playAnim(extraPlayerAnim, true);
                extraPlayerCharacter.holdTimer = 0;
            }
        }

        if (curStage == 'south' && (note.noteType == 'Stan Note' || note.noteType == 'stan'))
        {
            // Stan is the player character for this chart. These notes are
            // marked noAnimation so they must explicitly animate the active
            // player sprite, including the stan r Change Character section.
            var stanPlayerAnim:String = singAnimations[Std.int(Math.abs(note.noteData))];
            if (boyfriend != null && boyfriend.animation.getByName(stanPlayerAnim) != null)
            {
                boyfriend.playAnim(stanPlayerAnim, true);
                boyfriend.holdTimer = 0;
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
\t\t\t\t\tplaySouthStageObjectAnimation(value1, value2);

\t\t\tcase 'aBackground Change':
\t\t\t\tif (curStage == 'south')
\t\t\t\t{
\t\t\t\t\tvar backgroundDuration:Float = Std.parseFloat(value2);
\t\t\t\t\tif (Math.isNaN(backgroundDuration) || backgroundDuration < 0) backgroundDuration = 0.55;
\t\t\t\t\tsetSouthBackgroundLayer(value1, backgroundDuration);
\t\t\t\t}

\t\t\tcase 'Camera Follow Pos':"""
    if "case 'aObject Play Animation':" not in source:
        if event_marker not in source:
            raise RuntimeError("Could not find the PlayState custom-event insertion point.")
        source = source.replace(event_marker, event_code, 1)

    play_file.write_text(source, encoding="utf-8")


def patch_change_character_visibility(engine: Path) -> None:
    """Fully hide swapped-out character slots so Stan/Cartman do not linger."""
    play_file = engine / "source" / "PlayState.hx"
    source = play_file.read_text(encoding="utf-8")

    # All three slot swaps in Psych 0.6.3 keep the previous sprite in its
    # group. Set it fully hidden, then explicitly show the selected character.
    for character, character_map, keep_last_alpha in (
        ("boyfriend", "boyfriendMap", True),
        ("dad", "dadMap", False),
        ("gf", "gfMap", True),
    ):
        trailing = (
            rf"\n(?P=indent){character}\.alpha = lastAlpha;"
            if keep_last_alpha else ""
        )
        pattern = re.compile(
            rf"(?P<indent>\t*)var lastAlpha:Float = {character}\.alpha;\n"
            rf"(?P=indent){character}\.alpha = 0\.00001;\n"
            rf"(?P=indent){character} = {character_map}\.get\(value2\);"
            + trailing
        )

        def replace_swap(match: re.Match[str]) -> str:
            indent = match.group("indent")
            visible_line = f"{character}.visible = true;"
            if character == "boyfriend":
                # Only hide the incorrect Stan R character during the Kyle
                # exchange. Do not hide the whole BF slot: the following
                # Change Character event must show the next character normally.
                visible_line = (
                    "boyfriend.visible = !(curStage == 'south' "
                    "&& boyfriend.curCharacter.toLowerCase() == 'stan r' "
                    "&& Conductor.songPosition >= 69500 "
                    "&& Conductor.songPosition < 87944.4445);"
                )
            replacement = (
                f"var lastAlpha:Float = {character}.alpha;\n"
                f"{indent}{character}.alpha = 0;\n"
                f"{indent}{character}.visible = false;\n"
                f"{indent}{character} = {character_map}.get(value2);\n"
                f"{indent}{visible_line}"
            )
            if character == "boyfriend":
                # The BF group retains every previously used character. Hide
                # every inactive member after a swap so an earlier Stan sprite
                # cannot remain drawn over the newly selected Jimmy character.
                # Respect the specific Stan R visibility rule for the Kyle section.
                replacement += (
                    f"\n{indent}for (member in boyfriendGroup.members)"
                    f"\n{indent}{{"
                    f"\n{indent}\tif (member != null) member.visible = (member == boyfriend) && boyfriend.visible;"
                    f"\n{indent}}}"
                )
            if keep_last_alpha:
                replacement += f"\n{indent}{character}.alpha = lastAlpha;"
            return replacement

        source, changed = pattern.subn(replace_swap, source, count=1)
        if changed != 1:
            already_patched = (
                f"{character}.visible = false;" in source
                and f"{character}.visible = true;" in source
            )
            if not already_patched:
                raise RuntimeError(
                    f"Could not safely patch the {character} Change Character visibility branch."
                )

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

    camera_functions = """\tfunction southCameraAnimationName(character:Character):String
\t{
\t\tif (character == null || character.animation == null || character.animation.curAnim == null) return "";
\t\treturn character.animation.curAnim.name.toUpperCase();
\t}

\tfunction southCameraAnimationIs(animationName:String, direction:String):Bool
\t{
\t\treturn animationName != null && animationName.indexOf("SING" + direction) == 0;
\t}

\tfunction setSouthCameraAngle(targetAngle:Float):Void
\t{
\t\tif (camGame == null) return;
\t\tif (!southCameraRotationEnabled) targetAngle = 0;
\t\tif (southCameraAngleTween != null && Math.abs(southCameraAngleTarget - targetAngle) < 0.001) return;
\t\tif (southCameraAngleTween == null && Math.abs(camGame.angle - targetAngle) < 0.05)
\t\t{
\t\t\tsouthCameraAngleTarget = targetAngle;
\t\t\treturn;
\t\t}
\t\tif (southCameraAngleTween != null)
\t\t{
\t\t\tsouthCameraAngleTween.cancel();
\t\t\tsouthCameraAngleTween = null;
\t\t}
\t\tsouthCameraAngleTarget = targetAngle;
\t\tsouthCameraAngleTween = FlxTween.tween(camGame, {angle: targetAngle}, 0.2, {
\t\t\tease: FlxEase.linear,
\t\t\tonComplete: function(_:FlxTween) { southCameraAngleTween = null; }
\t\t});
\t}

\tfunction updateSouthScriptedCameraFollow():Void
\t{
\t\tif (curStage != 'south' || !generatedMusic || SONG == null || SONG.notes == null
\t\t\t|| curSection < 0 || curSection >= SONG.notes.length || camFollow == null) return;
\t\tif (badAppleSilhouetteActive)
\t\t{
\t\t\tif (!southCameraRotationEnabled) setSouthCameraAngle(0);
\t\t\treturn;
\t\t}
\t\tvar section = SONG.notes[curSection];
\t\tif (section == null) return;
\t\tvar targetX:Float = section.mustHitSection ? southCameraBoyfriendX : southCameraDadX;
\t\tvar targetY:Float = section.mustHitSection ? southCameraBoyfriendY : southCameraDadY;
\t\tif (!section.mustHitSection && !section.gfSection && dad != null && StringTools.contains(dad.curCharacter.toLowerCase(), "kenny"))
\t\t\ttargetX -= southCameraKennyLeftOffset;
\t\tvar targetAngle:Float = 0;
\t\tif (southCameraSystemEnabled)
\t\t{
\t\t\tif (!section.mustHitSection && section.gfSection)
\t\t\t{
\t\t\t\tsetSouthCameraAngle(0);
\t\t\t\treturn;
\t\t\t}
\t\t\tvar mainAnim:String = southCameraAnimationName(section.mustHitSection ? boyfriend : dad);
\t\t\tvar gfAnim:String = southCameraAnimationName(gf);
\t\t\tif (southCameraAnimationIs(mainAnim, "LEFT") || southCameraAnimationIs(gfAnim, "LEFT"))
\t\t\t{
\t\t\t\ttargetX += section.mustHitSection ? -southCameraBfOffset : -southCameraDadOffset - southCameraDadOffset2;
\t\t\t\ttargetAngle = 5;
\t\t\t}
\t\t\telse if (southCameraAnimationIs(mainAnim, "RIGHT") || southCameraAnimationIs(gfAnim, "RIGHT"))
\t\t\t{
\t\t\t\ttargetX += section.mustHitSection ? southCameraBfOffset : southCameraDadOffset + southCameraDadOffset2;
\t\t\t\ttargetAngle = -5;
\t\t\t}
\t\t\telse if (southCameraAnimationIs(mainAnim, "UP") || southCameraAnimationIs(gfAnim, "UP"))
\t\t\t\ttargetY -= section.mustHitSection ? southCameraBfOffset : southCameraDadOffset;
\t\t\telse if (southCameraAnimationIs(mainAnim, "DOWN") || southCameraAnimationIs(gfAnim, "DOWN"))
\t\t\t\ttargetY += section.mustHitSection ? southCameraBfOffset : southCameraDadOffset;
\t\t}
\t\ttargetY -= southCameraVerticalOffset;
\t\tcamFollow.set(targetX, targetY);
\t\tsetSouthCameraAngle(targetAngle);
\t}

\tfunction initializeSouthSmoothCameraState():Void
\t{
\t\tif (southSmoothCameraInitialized || curStage != 'south' || camFollowPos == null || camGame == null) return;
\t\tsouthSmoothCamScrollX = camFollowPos.x;
\t\tsouthSmoothCamScrollY = camFollowPos.y;
\t\tsouthSmoothRealCamScrollX = camFollowPos.x;
\t\tsouthSmoothRealCamScrollY = camFollowPos.y;
\t\tsouthSmoothCamZoom = camGame.zoom;
\t\tsouthSmoothRealCamZoom = camGame.zoom;
\t\tsouthSmoothCameraInitialized = true;
\t}

\tfunction restoreSouthSmoothCameraFrame():Void
\t{
\t\tif (curStage != 'south' || camFollowPos == null || camGame == null) return;
\t\tinitializeSouthSmoothCameraState();
\t\tif (!southSmoothCameraInitialized || !southSmoothCameraEnabled) return;
\t\tcamFollowPos.x -= southSmoothCamScrollX - southSmoothRealCamScrollX;
\t\tcamFollowPos.y -= southSmoothCamScrollY - southSmoothRealCamScrollY;
\t\tcamGame.zoom -= southSmoothCamZoom - southSmoothRealCamZoom;
\t}

\tfunction updateSouthSmoothCameraPost(elapsed:Float):Void
\t{
\t\tif (curStage != 'south' || camFollowPos == null || camGame == null) return;
\t\tinitializeSouthSmoothCameraState();
\t\tif (!southSmoothCameraInitialized) return;
\t\tvar realScrollX:Float = camFollowPos.x;
\t\tvar realScrollY:Float = camFollowPos.y;
\t\tvar realZoom:Float = camGame.zoom;
\t\tif (southSmoothCameraEnabled)
\t\t{
\t\t\tvar intensity:Float = southSmoothCameraIntensity;
\t\t\tif (Math.isNaN(intensity) || intensity <= 0) intensity = 0.6;
\t\t\tvar smoothFactor:Float = (1 / intensity) + 1;
\t\t\tvar followSpeed:Float = cameraSpeed;
\t\t\tif (Math.isNaN(followSpeed) || followSpeed <= 0) followSpeed = 1;
\t\t\tvar followLerp:Float = elapsed * 2.4 * smoothFactor * followSpeed;
\t\t\tif (followLerp < 0) followLerp = 0;
\t\t\tif (followLerp > 1) followLerp = 1;
\t\t\tsouthSmoothCamScrollX += (realScrollX - southSmoothCamScrollX) * followLerp;
\t\t\tsouthSmoothCamScrollY += (realScrollY - southSmoothCamScrollY) * followLerp;
\t\t\tcamFollowPos.setPosition(southSmoothCamScrollX, southSmoothCamScrollY);
\t\t\tif (southSmoothCameraZoomEnabled)
\t\t\t{
\t\t\t\tvar zoomDecay:Float = camZoomingDecay;
\t\t\t\tif (Math.isNaN(zoomDecay) || zoomDecay < 0) zoomDecay = 1;
\t\t\t\tvar zoomLerp:Float = elapsed * 3.125 * zoomDecay * smoothFactor;
\t\t\t\tif (zoomLerp < 0) zoomLerp = 0;
\t\t\t\tif (zoomLerp > 1) zoomLerp = 1;
\t\t\t\tsouthSmoothCamZoom += (realZoom - southSmoothCamZoom) * zoomLerp;
\t\t\t}
\t\t\telse southSmoothCamZoom = realZoom;
\t\t\tcamGame.zoom = southSmoothCamZoom;
\t\t}
\t\tsouthSmoothRealCamScrollX = realScrollX;
\t\tsouthSmoothRealCamScrollY = realScrollY;
\t\tsouthSmoothRealCamZoom = realZoom;
\t}

"""
    if "function updateSouthScriptedCameraFollow():Void" not in source:
        source = source.replace(function_marker, camera_functions + function_marker, 1)
    zoom_function = """\tfunction applySouthCameraZoom(zoomValue:Float):Void
\t{
\t\tif (Math.isNaN(zoomValue) || zoomValue <= 0 || zoomValue > 4) return;
\t\t// Change zoom without snapping camera scroll on every tween frame.
\t\t// Character follow is handled by Psych Engine's normal camera logic.
\t\tif (camGame != null) camGame.zoom = zoomValue;
\t}

\t"""
    if "function applySouthCameraZoom(zoomValue:Float):Void" not in source:
        if function_marker not in source:
            raise RuntimeError("Could not find the South camera zoom helper insertion point.")
        source = source.replace(function_marker, zoom_function + function_marker, 1)

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
    event_code = """\t\t\tcase 'afadein' | 'afadeout' | 'AFADEIN' | 'AFADEOUT':
\t\t\t\tif (curStage == 'south')
\t\t\t\t{
\t\t\t\t\tvar fadeKey:String = value1 == null ? "" : value1.toLowerCase();
\t\t\t\t\tvar fadeSprite:FlxSprite = southAnimatedObjects.get(fadeKey);
\t\t\t\t\tif (fadeSprite == null)
\t\t\t\t\t{
\t\t\t\t\t\tif (fadeKey == 'craig') fadeSprite = southCraigCharacter;
\t\t\t\t\telse if (fadeKey == 'cledy' || fadeKey == 'clyde') fadeSprite = southCledyCharacter;
\t\t\t\t}

\t\t\t\t\tif (fadeSprite != null)
\t\t\t\t\t{
\t\t\t\t\t\tvar fadeDuration:Float = Std.parseFloat(value2);
\t\t\t\t\t\tif (Math.isNaN(fadeDuration) || fadeDuration < 0) fadeDuration = 0;

\t\t\t\t\t\tvar fadeTarget:Float = eventName.toLowerCase() == 'afadein' ? 1 : 0;

\t\t\t\t\t\tFlxTween.cancelTweensOf(fadeSprite);
\t\t\t\t\t\tif (fadeDuration <= 0)
\t\t\t\t\t\t{
\t\t\t\t\t\t\tfadeSprite.alpha = fadeTarget;
\t\t\t\t\t\t\tfadeSprite.visible = fadeTarget > 0.01;
\t\t\t\t\t\t}
\t\t\t\t\t\telse
\t\t\t\t\t\t{
\t\t\t\t\t\t\tif (fadeTarget > 0.01) fadeSprite.visible = true;
\t\t\t\t\t\t\tFlxTween.tween(
\t\t\t\t\t\t\t\tfadeSprite,
\t\t\t\t\t\t\t\t{alpha: fadeTarget},
\t\t\t\t\t\t\t\tfadeDuration,
\t\t\t\t\t\t\t\t{
\t\t\t\t\t\t\t\t\tease: FlxEase.linear,
\t\t\t\t\t\t\t\t\tonComplete: function(_:FlxTween) {
\t\t\t\t\t\t\t\t\t\tfadeSprite.visible = fadeTarget > 0.01;
\t\t\t\t\t\t\t\t\t}
\t\t\t\t\t\t\t\t}
\t\t\t\t\t\t\t);
\t\t\t\t\t\t}
\t\t\t\t\t}
\t\t\t\t}

\t\t\tcase 'Flash Black' | 'aCameraFlashBlack' | 'CameraFlashBlack':
\t\t\t\tif (curStage == 'south')
\t\t\t\t{
\t\t\t\t\tvar blackFlashDuration:Float = Std.parseFloat(value1);
\t\t\t\t\tif (Math.isNaN(blackFlashDuration) || blackFlashDuration <= 0) blackFlashDuration = 1;
\t\t\t\t\t// This desktop-only custom event is not included in the HTML5 export.\n\t\t\t\t\t// Do not emulate it with a fully opaque overlay: that creates the late-song\n\t\t\t\t\t// black blink immediately after the Bad Apple transition.\n\t\t\t\t\tif (southBlackFlashOverlay != null)\n\t\t\t\t\t{\n\t\t\t\t\t\tFlxTween.cancelTweensOf(southBlackFlashOverlay);\n\t\t\t\t\t\tsouthBlackFlashOverlay.alpha = 0;\n\t\t\t\t\t}
\t\t\t\t}

\t\t\tcase 'Black effect':
\t\t\t\tif (curStage == 'south')
\t\t\t\t{
\t\t\t\t\t// The chart fires this custom event at 5:55 remaining,
\t\t\t\t\t// immediately before the bg2 transition. Treat the value as
\t\t\t\t\t// intensity and use one controlled flash instead of a long blackout.
\t\t\t\t\tvar blackEffectStrength:Float = Std.parseFloat(value1);
\t\t\t\t\tif (Math.isNaN(blackEffectStrength) || blackEffectStrength <= 0) blackEffectStrength = 3;
\t\t\t\t\tvar blackEffectDuration:Float = blackEffectStrength * 0.25;
\t\t\t\t\tif (blackEffectDuration > 0.9) blackEffectDuration = 0.9;
\t\t\t\t\tif (blackEffectDuration < 0.35) blackEffectDuration = 0.35;
\t\t\t\t\tflashSouthStageLight(blackEffectDuration);
\t\t\t\t\tcamGame.flash(FlxColor.WHITE, blackEffectDuration, null, true);
\t\t\t\t}

\t\t\tcase 'Camera Flash' | 'Flash' | 'Flash Camera' | 'Flash Red':
\t\t\t\tif (curStage == 'south')
\t\t\t\t{
\t\t\t\t\t// The chart's "Flash" event at 5:55 remaining was not supported
\t\t\t\t\t// by the HTML5 port, so the stage lights never pulsed there.
\t\t\t\t\tvar flashDuration:Float = Std.parseFloat(value1);
\t\t\t\t\tif (Math.isNaN(flashDuration) || flashDuration <= 0) flashDuration = 0.75;
\t\t\t\t\tif (flashDuration > 2.5) flashDuration = 2.5;
\t\t\t\t\tflashSouthStageLight(flashDuration);
\t\t\t\t\tvar flashColor:FlxColor = eventName.toLowerCase().trim() == 'flash red'
\t\t\t\t\t\t? FlxColor.RED : FlxColor.WHITE;
\t\t\t\t\tcamGame.flash(flashColor, flashDuration, null, true);
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



			case 'aMechi':
				if (curStage == 'south')
				{
					var cameraSystemValue:String = value1 == null ? "" : value1.toLowerCase().trim();
					if (cameraSystemValue == 'on') southCameraSystemEnabled = true;
					else if (cameraSystemValue == 'off') southCameraSystemEnabled = false;
					else if (cameraSystemValue == '') southCameraSystemEnabled = !southCameraSystemEnabled;
				}
			case 'arotation':
				if (curStage == 'south')
				{
					var rotationValue:String = value1 == null ? "" : value1.toLowerCase().trim();
					if (rotationValue == 'on') southCameraRotationEnabled = true;
					else if (rotationValue == 'off') southCameraRotationEnabled = false;
					else if (rotationValue == '') southCameraRotationEnabled = !southCameraRotationEnabled;
					if (!southCameraRotationEnabled) setSouthCameraAngle(0);
				}
			case 'camOfs':
				if (curStage == 'south')
				{
					var dadOffset:Float = value1 == null || value1.trim() == "" ? 60 : Std.parseFloat(value1);
					var bfOffset:Float = value2 == null || value2.trim() == "" ? 60 : Std.parseFloat(value2);
					if (!Math.isNaN(dadOffset)) southCameraDadOffset = dadOffset;
					if (!Math.isNaN(bfOffset)) southCameraBfOffset = bfOffset;
				}
			case 'acam smooth cfg':
				if (curStage == 'south')
				{
					var smoothConfig:String = value1 == null ? "" : value1.toLowerCase().trim();
					if (smoothConfig == 'on') southSmoothCameraEnabled = true;
					else if (smoothConfig == 'off') southSmoothCameraEnabled = false;
					if (smoothConfig.indexOf(',') >= 0)
					{
						if (smoothConfig.indexOf('on') >= 0) southSmoothCameraZoomEnabled = true;
						else if (smoothConfig.indexOf('off') >= 0) southSmoothCameraZoomEnabled = false;
					}
					var smoothIntensity:Float = value2 == null || value2.trim() == "" ? 0.6 : Std.parseFloat(value2);
					if (!Math.isNaN(smoothIntensity) && smoothIntensity > 0 && smoothIntensity <= 5)
						southSmoothCameraIntensity = smoothIntensity;
					if (smoothConfig == 'on' && camFollowPos != null && camGame != null)
					{
						southSmoothCamScrollX = camFollowPos.x;
						southSmoothCamScrollY = camFollowPos.y;
						southSmoothRealCamScrollX = camFollowPos.x;
						southSmoothRealCamScrollY = camFollowPos.y;
						southSmoothCamZoom = camGame.zoom;
						southSmoothRealCamZoom = camGame.zoom;
						southSmoothCameraInitialized = true;
					}
				}
			case 'South Smooth Add Camera Zoom':
				if (curStage == 'south' && camGame != null && ClientPrefs.camZooms && camGame.zoom < 1.35)
				{
					var addGameZoom:Float = Std.parseFloat(value1);
					var addHudZoom:Float = Std.parseFloat(value2);
					if (Math.isNaN(addGameZoom)) addGameZoom = 0.015;
					if (Math.isNaN(addHudZoom)) addHudZoom = 0.03;
					camGame.zoom += addGameZoom;
					if (camHUD != null) camHUD.zoom += addHudZoom;
				}

			case 'aofisetiBF' | 'ofisetiBF' | 'aofisetiBF-fest':
				if (curStage == 'south')
				{
					var newBoyfriendCameraX:Float = Std.parseFloat(value1);
					var newBoyfriendCameraY:Float = Std.parseFloat(value2);
					if (!Math.isNaN(newBoyfriendCameraX)) southCameraBoyfriendX = newBoyfriendCameraX;
					if (!Math.isNaN(newBoyfriendCameraY)) southCameraBoyfriendY = newBoyfriendCameraY;
					southCameraBoyfriendOverride = !Math.isNaN(newBoyfriendCameraX) && !Math.isNaN(newBoyfriendCameraY);
					if (eventName.toLowerCase() == 'aofisetibf-fest' && southCameraBoyfriendOverride)
					{
						camFollow.set(newBoyfriendCameraX, newBoyfriendCameraY - southCameraVerticalOffset);
						if (camFollowPos != null) camFollowPos.setPosition(newBoyfriendCameraX, newBoyfriendCameraY - southCameraVerticalOffset);
						if (camGame != null) camGame.scroll.set(newBoyfriendCameraX, newBoyfriendCameraY - southCameraVerticalOffset);
					}
				}

			case 'aofisetiDad' | 'ofisetiDad' | 'aofisetiDad-fest':
				if (curStage == 'south')
				{
					var newDadCameraX:Float = Std.parseFloat(value1);
					var newDadCameraY:Float = Std.parseFloat(value2);
					if (!Math.isNaN(newDadCameraX)) southCameraDadX = newDadCameraX;
					if (!Math.isNaN(newDadCameraY)) southCameraDadY = newDadCameraY;
					southCameraDadOverride = !Math.isNaN(newDadCameraX) && !Math.isNaN(newDadCameraY);
					if (eventName.toLowerCase() == 'aofisetidad-fest' && southCameraDadOverride)
					{
						camFollow.set(newDadCameraX, newDadCameraY - southCameraVerticalOffset);
						if (camFollowPos != null) camFollowPos.setPosition(newDadCameraX, newDadCameraY - southCameraVerticalOffset);
						if (camGame != null) camGame.scroll.set(newDadCameraX, newDadCameraY - southCameraVerticalOffset);
					}
				}

			case 'aCamaraZoom' | 'aCamaraZoom1' | 'aCamaraZoom3':
				if (curStage == 'south' && camGame != null)
				{
					var targetCameraZoom:Float = Std.parseFloat(value1);
					var cameraZoomDuration:Float = Std.parseFloat(value2);
					if (!Math.isNaN(targetCameraZoom) && targetCameraZoom > 0 && targetCameraZoom <= 4)
					{
						\t\t\t\t\t\tdefaultCamZoom = targetCameraZoom;
if (southCameraZoomTween != null)
						{
							southCameraZoomTween.cancel();
							southCameraZoomTween = null;
						}

						var currentCameraZoom:Float = camGame.zoom;
						if (Math.isNaN(currentCameraZoom) || currentCameraZoom <= 0 || currentCameraZoom > 4)
							currentCameraZoom = (defaultCamZoom > 0 && defaultCamZoom <= 4) ? defaultCamZoom : 0.6;

						if (Math.isNaN(cameraZoomDuration) || cameraZoomDuration <= 0)
						{
							applySouthCameraZoom(targetCameraZoom);
						}
						else
						{
							if (cameraZoomDuration > 30) cameraZoomDuration = 30;
							var zoomEvent:String = eventName.toLowerCase();
							var cameraZoomEase:Float->Float = zoomEvent == 'acamarazoom1'
								? FlxEase.expoOut
								: (zoomEvent == 'acamarazoom3' ? FlxEase.expoIn : FlxEase.sineInOut);
							southCameraZoomTween = FlxTween.num(currentCameraZoom, targetCameraZoom, cameraZoomDuration, {
								ease: cameraZoomEase,
								onComplete: function(_:FlxTween) {
									applySouthCameraZoom(targetCameraZoom);
									southCameraZoomTween = null;
								}
							}, function(zoomValue:Float) {
								applySouthCameraZoom(zoomValue);
							});
						}
					}
				}


			case "Bitto's zoom event":
				if (curStage == 'south' && camGame != null)
				{
					if (value1 != null && value1.toLowerCase().trim() == 'regular')
					{
						var regularZoom:Float = Std.parseFloat(value2);
						if (!Math.isNaN(regularZoom) && regularZoom > 0 && regularZoom <= 4)
						{
							if (southCameraZoomTween != null) southCameraZoomTween.cancel();
							\t\t\t\t\t\t\tdefaultCamZoom = regularZoom;
applySouthCameraZoom(regularZoom);
						}
					}
					else if (value1 != null && value1.toLowerCase().trim() == 'custom')
					{
						var zoomParts:Array<String> = (value2 == null ? "" : value2).split(',');
						if (zoomParts.length >= 2)
						{
							var bittoTarget:Float = Std.parseFloat(zoomParts[0]);
							var bittoDuration:Float = Std.parseFloat(zoomParts[1]);
							var bittoEaseName:String = zoomParts.length > 2 ? zoomParts[2].toLowerCase().trim() : 'linear';
							if (!Math.isNaN(bittoTarget) && bittoTarget > 0 && bittoTarget <= 4)
							{
								\t\t\t\t\t\t\t\tdefaultCamZoom = bittoTarget;
if (southCameraZoomTween != null) southCameraZoomTween.cancel();
								var bittoStart:Float = camGame.zoom;
								var bittoEase:Float->Float = FlxEase.linear;
								switch (bittoEaseName)
								{
									case 'quadin': bittoEase = FlxEase.quadIn;
									case 'quadout': bittoEase = FlxEase.quadOut;
									case 'quadinout': bittoEase = FlxEase.quadInOut;
									case 'sinein': bittoEase = FlxEase.sineIn;
									case 'sineout': bittoEase = FlxEase.sineOut;
									case 'sineinout': bittoEase = FlxEase.sineInOut;
									case 'expoin': bittoEase = FlxEase.expoIn;
									case 'expoout': bittoEase = FlxEase.expoOut;
									case 'expoinout': bittoEase = FlxEase.expoInOut;
									case 'circin': bittoEase = FlxEase.circIn;
									case 'circout': bittoEase = FlxEase.circOut;
									case 'backin': bittoEase = FlxEase.backIn;
									case 'backout': bittoEase = FlxEase.backOut;
								}
								if (Math.isNaN(bittoDuration) || bittoDuration <= 0)
								{
									applySouthCameraZoom(bittoTarget);
								}
								else
								{
									if (bittoDuration > 30) bittoDuration = 30;
									southCameraZoomTween = FlxTween.num(bittoStart, bittoTarget, bittoDuration, {
										ease: bittoEase,
										onComplete: function(_:FlxTween) {
											applySouthCameraZoom(bittoTarget);
											southCameraZoomTween = null;
										}
									}, function(zoomValue:Float) {
										applySouthCameraZoom(zoomValue);
									});
								}
							}
						}
					}
				}

			case 'Set Property':
				if (curStage == 'south' && camGame != null && value1 != null && value1.toLowerCase().trim() == 'defaultcamzoom')
				{
					var propertyZoom:Float = Std.parseFloat(value2);
					if (!Math.isNaN(propertyZoom) && propertyZoom > 0 && propertyZoom <= 4)
					{
						\t\t\t\t\t\tdefaultCamZoom = propertyZoom;
if (southCameraZoomTween != null) southCameraZoomTween.cancel();
						applySouthCameraZoom(propertyZoom);
					}
				}


			case 'acamera_target':
				if (curStage == 'south')
				{
					var requestedCameraSpeed:Float = Std.parseFloat(value2);
					if (!Math.isNaN(requestedCameraSpeed) && requestedCameraSpeed > 0 && requestedCameraSpeed <= 10)
						cameraSpeed = requestedCameraSpeed;

					var targetKey:String = value1 == null ? "" : value1.toLowerCase().trim();
					var targetCharacter:Character = null;
					switch (targetKey)
					{
						case 'bf' | 'boyfriend': targetCharacter = boyfriend;
						case 'dad' | 'opponent': targetCharacter = dad;
						case 'gf' | 'girlfriend': targetCharacter = gf;
					}
					if (targetCharacter != null && camFollow != null)
					{
						isCameraOnForcedPos = true;
						camFollow.set(
							targetCharacter.getMidpoint().x + targetCharacter.cameraPosition[0],
							targetCharacter.getMidpoint().y + targetCharacter.cameraPosition[1] - southCameraVerticalOffset
						);
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
        "case 'Camera Flash' | 'Flash' | 'Flash Camera' | 'Flash Red':",
        "case 'badapplelol':",
        "case 'aCamaraZoom' | 'aCamaraZoom1' | 'aCamaraZoom3':",
        "case \"Bitto's zoom event\":",
        "case 'Set Property':",
        "case 'acamera_target':",
    )
    missing = [item for item in required if item not in source]
    if missing:
        raise RuntimeError("South overlay/flash event patch is incomplete: " + repr(missing))
    move_camera_marker = "\tfunction moveCameraSection():Void {"
    move_camera_code = move_camera_marker + """
\t\tif (curStage == 'south')
\t\t{
\t\t\tif (isCameraOnForcedPos) return;
\t\t\tif (SONG != null && SONG.notes != null && SONG.notes[curSection] != null)
\t\t\t{
\t\t\t\t// Default to live character midpoints and JSON camera_position values.
\t\t\t\t// Charted aofiseti points override those targets only when supplied.
\t\t\t\tif (gf != null && SONG.notes[curSection].gfSection)
\t\t\t\t{
\t\t\t\t\tcamFollow.set(gf.getMidpoint().x, gf.getMidpoint().y);
\t\t\t\t\tcamFollow.x += gf.cameraPosition[0] + girlfriendCameraOffset[0];
\t\t\t\t\tcamFollow.y += gf.cameraPosition[1] + girlfriendCameraOffset[1] - southCameraVerticalOffset;
\t\t\t\t\ttweenCamIn();
\t\t\t\t}
\t\t\t\telse if (SONG.notes[curSection].mustHitSection)
\t\t\t\t{
\t\t\t\t\tif (southCameraBoyfriendOverride)
\t\t\t\t\t\tcamFollow.set(southCameraBoyfriendX, southCameraBoyfriendY);
\t\t\t\t\telse
\t\t\t\t\t\tmoveCamera(false);
\t\t\t\t}
\t\t\t\telse
\t\t\t\t{
\t\t\t\t\tif (southCameraDadOverride)
\t\t\t\t\t\tcamFollow.set(southCameraDadX, southCameraDadY);
\t\t\t\t\telse
\t\t\t\t\t\tmoveCamera(true);
\t\t\t\t}
\t\t\t\treturn;
\t\t\t}
\t\t}
\t\t"""
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

	function hideInactiveSouthCharacterGroups():Void
	{
		if (curStage != 'south') return;
		if (boyfriendGroup != null)
		{
			for (member in boyfriendGroup.members)
				if (member != null && member != boyfriend)
				{
					member.alpha = 0;
					member.visible = false;
				}
		}
		// Sweep cached BF map entries as well as the display group. This
		// explicitly clears old Stan R instances held by boyfriendMap.
		if (boyfriendMap != null)
		{
			for (characterName in boyfriendMap.keys())
			{
				var mappedBoyfriend:Boyfriend = boyfriendMap.get(characterName);
				if (mappedBoyfriend != null && mappedBoyfriend != boyfriend)
				{
					mappedBoyfriend.alpha = 0;
					mappedBoyfriend.visible = false;
				}
			}
		}
		if (dadGroup != null)
		{
			for (member in dadGroup.members)
				if (member != null && member != dad)
				{
					member.alpha = 0;
					member.visible = false;
				}
		}
		if (gfGroup != null)
		{
			for (member in gfGroup.members)
				if (member != null && member != gf)
				{
					member.alpha = 0;
					member.visible = false;
				}
		}
	}

	function updateSouthThirdPartCharacters():Void
	{
		if (curStage != 'south' || !generatedMusic) return;
		var songMs:Float = Conductor.songPosition;

		// Keep each Jimmy state aligned with the chart's exact Change Character
		// intervals: Jimmy W -> Jimmy -> slightly corrupted Jimmy C.
		var requiredJimmyCharacter:String = null;
		if (songMs >= 106333.333 && songMs < 149250)
			requiredJimmyCharacter = 'jimmy w';
		else if (songMs >= 149250 && songMs < 160083.333)
			requiredJimmyCharacter = 'jimmy';
		else if (songMs >= 160083.333 && songMs < 202250)
			requiredJimmyCharacter = 'jimmy c';

		if (requiredJimmyCharacter != null && boyfriend != null
			&& boyfriend.curCharacter.toLowerCase() != requiredJimmyCharacter)
		{
			if (!boyfriendMap.exists(requiredJimmyCharacter))
				addCharacterToList(requiredJimmyCharacter, 0);
			var chartJimmyCharacter:Boyfriend = boyfriendMap.get(requiredJimmyCharacter);
			if (chartJimmyCharacter != null)
			{
				boyfriend.alpha = 0;
				boyfriend.visible = false;
				boyfriend = chartJimmyCharacter;
				boyfriend.alpha = 1;
				boyfriend.visible = true;
				boyfriend.holdTimer = 0;
				if (iconP1 != null) iconP1.changeIcon(boyfriend.healthIcon);
				setOnLuas('boyfriendName', boyfriend.curCharacter);
			}
		}

		// Clyde's custom-note sequence runs from 3:28.083 to 4:13.417.
		// Keep the sprite present only through that section and its final sing hold.
		if (southCledyCharacter != null)
		{
			var clydeActive:Bool = songMs >= 208083 && songMs < 254600;
			southCledyCharacter.alpha = clydeActive ? 1 : 0;
			southCledyCharacter.visible = clydeActive;
		}

		// Craig enters on his first custom-note sequence and is faded by the
		// chart's "afadeout craig" event at 5:06.417.
		if (southCraigCharacter != null)
		{
			if (songMs < 212083 || songMs >= 307900)
			{
				southCraigCharacter.alpha = 0;
				southCraigCharacter.visible = false;
			}
			else if (songMs < 306417)
			{
				southCraigCharacter.alpha = 1;
				southCraigCharacter.visible = true;
			}
		}
	}

	function hideFinishedSouthCutsceneOverlays():Void
	{
		if (curStage != 'south' || southAnimatedObjects == null) return;
		for (overlayKey in ['anim', 'anim2', 'anim2 c', 'anim3'])
		{
			var overlay:BGSprite = southAnimatedObjects.get(overlayKey);
			if (overlay == null || !overlay.visible || overlay.animation == null || overlay.animation.curAnim == null)
				continue;
			if (!overlay.animation.curAnim.looped && overlay.animation.curAnim.finished)
			{
				FlxTween.cancelTweensOf(overlay);
				overlay.alpha = 0;
				overlay.visible = false;
			}
		}
	}

	function recoverLateBlackout():Void
\t{
\t\tvar songMs:Float = Conductor.songPosition;
\t\t// Keep the watchdog active from the reported 5:56 blackout until the end.
\t\tif (songMs < 356000)
\t\t{
\t\t\tlateBlackoutWatchSince = -1;
\t\t\treturn;
\t\t}

\t\tvar blackLayer:BGSprite = southAnimatedObjects.get('black');
\t\tvar blackbarLayer:BGSprite = southAnimatedObjects.get('blackbar');
\t\tvar whiteLayer:BGSprite = southAnimatedObjects.get('white');

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

\t\tvar wallNow:Float = Date.now().getTime();
\t\tif (lateBlackoutWatchSince < 0)
\t\t{
\t\t\tlateBlackoutWatchSince = wallNow;
\t\t\treturn;
\t\t}

\t\tif (wallNow - lateBlackoutWatchSince < 1250)
\t\t\treturn;

\t\t// Force-clear all known blackout covers after the watchdog confirms
\t\t// the late-song overlay condition has remained stuck for 1.25 seconds.
\t\tif (Conductor.songPosition >= 42000)
\t\t{
\t\t\tif (southAnimatedObjects != null)
\t\t\t{
\t\t\t\tvar recoverBlack:Dynamic = southAnimatedObjects.get('black');
\t\t\t\tvar recoverBlackbar:Dynamic = southAnimatedObjects.get('blackbar');
\t\t\t\tvar recoverWhite:Dynamic = southAnimatedObjects.get('white');

\t\t\t\tif (recoverBlack != null)
\t\t\t\t{
\t\t\t\t\tFlxTween.cancelTweensOf(recoverBlack);
\t\t\t\t\trecoverBlack.alpha = 0;
\t\t\t\t\trecoverBlack.visible = false;
\t\t\t\t}
\t\t\t\tif (recoverBlackbar != null)
\t\t\t\t{
\t\t\t\t\tFlxTween.cancelTweensOf(recoverBlackbar);
\t\t\t\t\trecoverBlackbar.alpha = 0;
\t\t\t\t\trecoverBlackbar.visible = false;
\t\t\t\t}
\t\t\t\tif (recoverWhite != null)
\t\t\t\t{
\t\t\t\t\tFlxTween.cancelTweensOf(recoverWhite);
\t\t\t\t\trecoverWhite.alpha = 0;
\t\t\t\t\trecoverWhite.visible = false;
\t\t\t\t}
\t\t\t}
\t\t\tif (southBlackFlashOverlay != null)
\t\t\t{
\t\t\t\tFlxTween.cancelTweensOf(southBlackFlashOverlay);
\t\t\t\tsouthBlackFlashOverlay.alpha = 0;
\t\t\t\tsouthBlackFlashOverlay.visible = false;
\t\t\t}
\t\t\tif (badAppleWhiteBg != null)
\t\t\t{
\t\t\t\tFlxTween.cancelTweensOf(badAppleWhiteBg);
\t\t\t\tbadAppleWhiteBg.alpha = 0;
\t\t\t\tbadAppleWhiteBg.visible = false;
\t\t\t}
\t\t\tif (camGame != null)
\t\t\t\tcamGame.zoom = defaultCamZoom;
\t\t}

\t\tvar recovered:Array<String> = [];

\t\tif (blackLayer != null && blackLayer.alpha > 0.02)
\t\t{
\t\t\tFlxTween.cancelTweensOf(blackLayer);
\t\t\tblackLayer.alpha = 0;
\t\t\tblackLayer.visible = false;
\t\t\trecovered.push("stage black");
\t\t}

\t\tif (blackbarLayer != null && blackbarLayer.alpha > 0.02)
\t\t{
\t\t\tFlxTween.cancelTweensOf(blackbarLayer);
\t\t\tblackbarLayer.alpha = 0;
\t\t\tblackbarLayer.visible = false;
\t\t\trecovered.push("stage blackbar");
\t\t}

\t\tif (whiteLayer != null && whiteLayer.alpha > 0.02)
\t\t{
\t\t\tFlxTween.cancelTweensOf(whiteLayer);
\t\t\twhiteLayer.alpha = 0;
\t\t\twhiteLayer.visible = false;
\t\t\trecovered.push("stage white");
\t\t}

\t\tif (badAppleBackdropStuck && badAppleWhiteBg != null)
\t\t{
\t\t\tFlxTween.cancelTweensOf(badAppleWhiteBg);
\t\t\tbadAppleWhiteBg.alpha = 0;
\t\t\tbadAppleWhiteBg.visible = false;
\t\t\trecovered.push("Bad Apple backdrop");
\t\t}

\t\tif (southBlackFlashOverlay != null && southBlackFlashOverlay.alpha > 0.02)
\t\t{
\t\t\tFlxTween.cancelTweensOf(southBlackFlashOverlay);
\t\t\tsouthBlackFlashOverlay.alpha = 0;
\t\t\tsouthBlackFlashOverlay.visible = false;
\t\t\trecovered.push("black flash overlay");
\t\t}

\t\tif (upperBarStuck && southStillStrumUpperBar != null)
\t\t{
\t\t\tFlxTween.cancelTweensOf(southStillStrumUpperBar);
\t\t\tsouthStillStrumUpperBar.y = -350;
\t\t\tsouthStillStrumUpperBar.alpha = 1;
\t\t\tsouthStillStrumUpperBar.visible = true;
\t\t\trecovered.push("upper cinematic bar");
\t\t}

\t\tif (lowerBarStuck && southStillStrumLowerBar != null)
\t\t{
\t\t\tFlxTween.cancelTweensOf(southStillStrumLowerBar);
\t\t\tsouthStillStrumLowerBar.y = 720;
\t\t\tsouthStillStrumLowerBar.alpha = 1;
\t\t\tsouthStillStrumLowerBar.visible = true;
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

\t\tif (camGame != null)
\t\t\tcamGame.zoom = defaultCamZoom;

\t\t// Last-resort visibility pass: run only after the watchdog has observed
\t\t// a persistent cover for 1.25 seconds. Do not cancel legitimate fades
\t\t// merely because the song has passed 5:56.
\t\tif (blackCover)
\t\t{
\t\t\tif (southAnimatedObjects != null)
\t\t\t{
\t\t\t\tvar clearNames:Array<String> = ['black', 'blackbar', 'white'];
\t\t\t\tfor (key in clearNames)
\t\t\t\t{
\t\t\t\t\tvar cover:BGSprite = southAnimatedObjects.get(key);
\t\t\t\t\tif (cover != null)
\t\t\t\t\t{
\t\t\t\t\t\tFlxTween.cancelTweensOf(cover);
\t\t\t\t\t\tcover.alpha = 0;
\t\t\t\t\t\tcover.visible = false;
\t\t\t\t\t}
\t\t\t\t}
\t\t\t}

\t\t\tif (southBlackFlashOverlay != null)
\t\t\t{
\t\t\t\tFlxTween.cancelTweensOf(southBlackFlashOverlay);
\t\t\t\tsouthBlackFlashOverlay.alpha = 0;
\t\t\t\tsouthBlackFlashOverlay.visible = false;
\t\t\t}

\t\t\t// Preserve the intended Bad Apple white silhouette while it is active.
\t\t\tif (!badAppleSilhouetteActive && badAppleWhiteBg != null)
\t\t\t{
\t\t\t\tFlxTween.cancelTweensOf(badAppleWhiteBg);
\t\t\t\tbadAppleWhiteBg.alpha = 0;
\t\t\t\tbadAppleWhiteBg.visible = false;
\t\t\t}
\t\t}

\t\tif (recovered.length > 0)
\t\t{
\t\t\tlateBlackoutRecoveries++;
\t\t\tblackDiagnosticLastEvent = "RECOVERED: " + recovered.join(", ");
\t\t\tblackDiagnosticLastEventTime = songMs;
\t\t\tblackDiagnosticEventHistory.push(blackDiagnosticLastEvent + " @ " + Std.string(Std.int(songMs)) + "ms");
\t\t\tif (blackDiagnosticEventHistory.length > 6)
\t\t\t\tblackDiagnosticEventHistory.shift();
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
\t\tupdateSouthThirdPartCharacters();
\t\tupdateSouthKennyFormBackground();
\t\thideInactiveSouthCharacterGroups();
\t\thideFinishedSouthCutsceneOverlays();
\t\tupdateSouthBlackDiagnosticPanel();
\t\trecoverLateBlackout();
\t}
"""
    if "var lateBlackoutWatchSince:Float = -1;" not in source:
        if field_marker not in source:
            raise RuntimeError("Could not locate the South stage fields for blackout recovery.")
        source = source.replace(field_marker, fields, 1)

    update_marker = "\toverride public function update(elapsed:Float)\n\t{"
    camera_update_marker = "\t\tcallOnLuas('onUpdatePost', [elapsed]);"
    if "updateSouthScriptedCameraFollow();" not in source:
        if source.count(camera_update_marker) != 1:
            raise RuntimeError("Could not locate the onUpdatePost point for the Bloodbath camera controller.")
        source = source.replace(
            camera_update_marker,
            "\t\tupdateSouthScriptedCameraFollow();\n" + camera_update_marker,
            1,
        )
    if "updateSouthSmoothCameraPost(elapsed);" not in source:
        if source.count(camera_update_marker) != 1:
            raise RuntimeError("Could not locate the onUpdatePost point for Bloodbath smooth camera.")
        source = source.replace(
            camera_update_marker,
            camera_update_marker + "\n\t\tupdateSouthSmoothCameraPost(elapsed);",
            1,
        )
    update_code = update_marker + "\n\t\trestoreSouthSmoothCameraFrame();\n\t\tupdateBadAppleCameraFollow();\n\t\tupdateSouthOverlayRecovery();\n\t\tupdateSouthKyleCorruptionTimeline();"
    if "updateSouthOverlayRecovery();" not in source:
        if update_marker not in source:
            raise RuntimeError("Could not locate PlayState.update() for South overlay recovery.")
        source = source.replace(update_marker, update_code, 1)

    event_marker = "public function triggerEventNote(eventName:String, value1:String, value2:String) {\n\t\tswitch(eventName) {"
    event_code = """public function triggerEventNote(eventName:String, value1:String, value2:String) {
\t\t// Normalize Bloodbath event names without changing unrelated Psych Engine events.
\t\tvar normalizedSouthEventName:String = eventName == null ? "" : eventName.toLowerCase().trim();
\t\tswitch (normalizedSouthEventName)
\t\t{
\t\t\tcase 'change character':
\t\t\t\teventName = 'Change Character';
\t\t\t\tvalue2 = resolveSouthCharacterName(value2);
\t\t\tcase 'aobject play animation':
\t\t\t\teventName = 'aObject Play Animation';
\t\t\tcase 'abackground change' | 'background change' | 'change background':
\t\t\t\teventName = 'aBackground Change';
\t\t\tcase 'afadein': eventName = 'afadein';
\t\t\tcase 'afadeout': eventName = 'afadeout';
\t\t\tcase 'acamarazoom': eventName = 'aCamaraZoom';
\t\t\tcase 'acamarazoom1': eventName = 'aCamaraZoom1';
\t\t\tcase 'acamarazoom3': eventName = 'aCamaraZoom3';
\t\t\tcase "bitto's zoom event": eventName = "Bitto's zoom event";
\t\t\tcase 'set property': eventName = 'Set Property';
\t\t\tcase 'acamera_target': eventName = 'acamera_target';
\t\t\tcase 'amechi': eventName = 'aMechi';
\t\t\tcase 'arotation': eventName = 'arotation';
\t\t\tcase 'camofs': eventName = 'camOfs';
\t\t\tcase 'acam smooth cfg': eventName = 'acam smooth cfg';
\t\t\tcase 'aofisetibf-fest': eventName = 'aofisetiBF-fest';
\t\t\tcase 'aofisetidad-fest': eventName = 'aofisetiDad-fest';
\t\t\tcase 'aofisetibf' | 'ofisetibf': eventName = 'aofisetiBF';
\t\t\tcase 'aofisetidad' | 'ofisetidad': eventName = 'aofisetiDad';
\t\t\tcase 'add camera zoom': eventName = 'South Smooth Add Camera Zoom';
\t\t\tcase 'play video': eventName = 'Play Video';
\t\t}
\t\tblackDiagnosticLastEvent = eventName + " [" + value1 + " | " + value2 + "]";
\t\tblackDiagnosticLastEventTime = Conductor.songPosition;
\t\tblackDiagnosticEventHistory.push(blackDiagnosticLastEvent + " @ " + Std.string(Std.int(Conductor.songPosition)) + "ms");
\t\tif (blackDiagnosticEventHistory.length > 6) blackDiagnosticEventHistory.shift();

\t\t// Clear stale South-stage overlay objects at the FX event boundary.
\t\tvar normalizedSouthFxEvent:String = eventName.toLowerCase();
\t\tif (curStage == 'south' && (normalizedSouthFxEvent == 'afadeout' || normalizedSouthFxEvent == 'afadein' || normalizedSouthFxEvent == 'bloomhigh' || normalizedSouthFxEvent == 'barrel'))
\t\t{
\t\t\tif (southAnimatedObjects != null)
\t\t\t{
\t\t\t\tvar coverNames:Array<String> = ['black', 'blackbar', 'white', 'barrel', 'bloom'];
\t\t\t\tfor (key in coverNames)
\t\t\t\t{
\t\t\t\t\tvar cover:Dynamic = southAnimatedObjects.get(key);
\t\t\t\t\tif (cover != null)
\t\t\t\t\t{
\t\t\t\t\t\tFlxTween.cancelTweensOf(cover);
\t\t\t\t\t\tcover.alpha = 0;
\t\t\t\t\t\tcover.visible = false;
\t\t\t\t\t}
\t\t\t\t}
\t\t\t}
\t\t\tif (southBlackFlashOverlay != null)
\t\t\t{
\t\t\t\tFlxTween.cancelTweensOf(southBlackFlashOverlay);
\t\t\t\tsouthBlackFlashOverlay.alpha = 0;
\t\t\t\tsouthBlackFlashOverlay.visible = false;
\t\t\t}
\t\t\tif (badAppleWhiteBg != null)
\t\t\t{
\t\t\t\tFlxTween.cancelTweensOf(badAppleWhiteBg);
\t\t\t\tbadAppleWhiteBg.alpha = 0;
\t\t\t\tbadAppleWhiteBg.visible = false;
\t\t\t}
\t\t\tif (camGame != null)
\t\t\t\tcamGame.zoom = defaultCamZoom;
\t\t}

\t\t#if !flash\n\t\tif (curStage == 'south') handleSouthShaderEvent(eventName, value1, value2);\n\t\t#end\n\t\tswitch(eventName) {"""
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

\t\t// Keep the song timer visible above cinematic effects and bars.
\t\t// Reordering preserves the same HUD objects and their original alpha values.
\t\tif (timeBarBG != null) { remove(timeBarBG, false); add(timeBarBG); }
\t\tif (timeBar != null) { remove(timeBar, false); add(timeBar); }
\t\tif (timeTxt != null) { remove(timeTxt, false); add(timeTxt); }

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

    # Expose the native editor and Botplay from the normal gameplay pause menu.
    if "Bloodbath: add Chart Editor to normal gameplay pause menu" not in source:
        charting_only_line = "\t\t\tmenuItemsOG.insert(5 + num, 'Toggle Botplay');\n"
        if charting_only_line not in source:
            raise RuntimeError("Could not find the Charting Mode-only Botplay menu entry.")
        source = source.replace(charting_only_line, "", 1)

        menu_marker = "\t\tmenuItems = menuItemsOG;"
        menu_addition = """\t\t// Bloodbath: add Chart Editor to normal gameplay pause menu.
\t\tif (!PlayState.chartingMode && !menuItemsOG.contains('Open Chart Editor'))
\t\t{
\t\t\tvar chartEditorIndex:Int = menuItemsOG.indexOf('Exit to menu');
\t\t\tif (chartEditorIndex < 0) chartEditorIndex = menuItemsOG.length;
\t\t\tmenuItemsOG.insert(chartEditorIndex, 'Open Chart Editor');
\t\t}
\t\t// Bloodbath: expose Botplay during normal gameplay as well as Charting Mode.
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

    if "case 'Open Chart Editor':" not in source:
        editor_case_marker = "\t\t\t\tcase 'Toggle Botplay':"
        editor_case = """\t\t\t\tcase 'Open Chart Editor':
\t\t\t\t\tPlayState.instance.openChartEditor();
\t\t\t\tcase 'Toggle Botplay':"""
        if source.count(editor_case_marker) != 1:
            raise RuntimeError("Could not find the shared Botplay pause-menu handler.")
        source = source.replace(editor_case_marker, editor_case, 1)

    if "case 'Open Chart Editor':" not in source or "PlayState.instance.openChartEditor();" not in source:
        raise RuntimeError("Chart Editor pause-menu action was not installed.")
    if "case 'Toggle Botplay':" not in source or "PlayState.instance.cpuControlled = !PlayState.instance.cpuControlled;" not in source:
        raise RuntimeError("Psych Engine's built-in Botplay toggle handler is missing.")
    pause_file.write_text(source, encoding="utf-8")

    # The upstream method already switches to editors.ChartingState using the
    # currently loaded PlayState.SONG. Make it callable by PauseSubState.
    play_file = engine / "source" / "PlayState.hx"
    play_source = play_file.read_text(encoding="utf-8")
    if "public function openChartEditor()" not in play_source:
        private_signature = "\tfunction openChartEditor()"
        if play_source.count(private_signature) != 1:
            raise RuntimeError("Could not uniquely locate PlayState.openChartEditor().")
        play_source = play_source.replace(private_signature, "\tpublic function openChartEditor()", 1)
    if "public function openChartEditor()" not in play_source:
        raise RuntimeError("PlayState.openChartEditor() was not made public.")
    play_file.write_text(play_source, encoding="utf-8")


def patch_chart_editor_optional_vocals(engine: Path) -> None:
    """Prevent the native Chart Editor from requesting a nonexistent vocal track."""
    chart_file = engine / "source" / "editors" / "ChartingState.hx"
    if not chart_file.is_file():
        raise FileNotFoundError(f"Psych Engine ChartingState source is missing: {chart_file}")

    code = chart_file.read_text(encoding="utf-8")
    old = """\t\tvar file:Dynamic = Paths.voices(currentSongName);
\t\tvocals = new FlxSound();
\t\tif (Std.isOfType(file, Sound) || OpenFlAssets.exists(file)) {
\t\t\tvocals.loadEmbedded(file);
\t\t\tFlxG.sound.list.add(vocals);
\t\t}"""
    new = """\t\t// Bloodbath is shipped as an instrumental-only chart. Do not call
\t\t// Paths.voices() unless the chart explicitly declares a vocal track:
\t\t// Paths.voices() resolves the asset immediately and throws when it is absent.
\t\tvar file:Dynamic = null;
\t\tvocals = new FlxSound();
\t\tif (_song != null && _song.needsVoices)
\t\t{
\t\t\tfile = Paths.voices(currentSongName);
\t\t\tif (Std.isOfType(file, Sound) || OpenFlAssets.exists(file))
\t\t\t{
\t\t\t\tvocals.loadEmbedded(file);
\t\t\t\tFlxG.sound.list.add(vocals);
\t\t\t}
\t\t}"""
    marker = "// Bloodbath instrumental-only Chart Editor audio guard"
    if marker not in code:
        if code.count(old) != 1:
            raise RuntimeError(
                "Could not uniquely locate ChartingState.loadSong() vocal loading block."
            )
        code = code.replace(old, new, 1)
        code = code.replace(
            "\t\t// Bloodbath is shipped as an instrumental-only chart.",
            "\t\t// Bloodbath instrumental-only Chart Editor audio guard\n"
            "\t\t// Bloodbath is shipped as an instrumental-only chart.",
            1,
        )

    if "if (_song != null && _song.needsVoices)" not in code:
        raise RuntimeError("Chart Editor optional-vocals guard was not installed.")
    if "var file:Dynamic = Paths.voices(currentSongName);" in code:
        raise RuntimeError("Unconditional Chart Editor vocal loading remains.")
    chart_file.write_text(code, encoding="utf-8")


def stage_html5_video_assets(game: Path, engine: Path) -> dict[str, int]:
    """Stage browser-playable cutscene videos outside Lime's preload manifest."""
    archive = engine / "html5-video-assets"
    if archive.exists():
        shutil.rmtree(archive)
    archive.mkdir(parents=True, exist_ok=True)

    # Search all common cutscene directories in the extracted package.
    video_dir_names = {"videos", "video", "cutscenes", "cutscene", "cinematics", "cinematic", "movies"}
    video_roots: list[Path] = []
    seen_roots: set[Path] = set()
    for candidate in game.rglob("*"):
        if not candidate.is_dir() or candidate.name.lower() not in video_dir_names:
            continue
        resolved = candidate.resolve()
        if resolved not in seen_roots:
            seen_roots.add(resolved)
            video_roots.append(candidate)

    browser_video_extensions = {".mp4", ".m4v", ".webm", ".ogv", ".mov"}
    transcode_video_extensions = {".avi", ".mkv", ".flv", ".mpg", ".mpeg", ".wmv", ".m2v"}
    copied: set[str] = set()
    copied_bytes = 0
    extension_counts: dict[str, int] = {}
    transcoded_files = 0

    for video_root in video_roots:
        for source_file in video_root.rglob("*"):
            if not source_file.is_file():
                continue
            source_extension = source_file.suffix.lower()
            if source_extension not in browser_video_extensions | transcode_video_extensions:
                continue

            relative = source_file.relative_to(video_root)
            target_relative = relative if source_extension in browser_video_extensions else relative.with_suffix(".mp4")
            target = archive / target_relative
            target.parent.mkdir(parents=True, exist_ok=True)
            relative_key = target_relative.as_posix()

            if target.exists():
                if source_extension in browser_video_extensions and target.read_bytes() == source_file.read_bytes():
                    continue
                raise RuntimeError("Conflicting cutscene videos resolve to the same published path: " + relative_key)

            if source_extension in browser_video_extensions:
                shutil.copy2(source_file, target)
                copied_bytes += source_file.stat().st_size
                extension_counts[source_extension] = extension_counts.get(source_extension, 0) + 1
            else:
                subprocess.run(
                    [
                        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(source_file), "-map", "0:v:0", "-map", "0:a?",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
                        "-crf", "20", "-c:a", "aac", "-b:a", "160k",
                        "-movflags", "+faststart", str(target),
                    ],
                    check=True,
                )
                copied_bytes += target.stat().st_size
                extension_counts[".mp4 (transcoded)"] = extension_counts.get(".mp4 (transcoded)", 0) + 1
                transcoded_files += 1
            copied.add(relative_key)

    return {
        "sourceDirectories": len(video_roots),
        "sourceFiles": len(copied),
        "sourceBytes": copied_bytes,
        "sourceExtensions": extension_counts,
        "transcodedFiles": transcoded_files,
    }


def patch_html5_video_cutscenes(engine: Path) -> None:
    """Use the browser's native video element and expose a chart event for MP4 cutscenes."""
    play_file = engine / "source" / "PlayState.hx"
    source = play_file.read_text(encoding="utf-8")

    start_marker = "\tpublic function startVideo(name:String)\n\t{"
    end_marker = "\n\tfunction startAndEnd()"
    if "HTML5 browser video cutscene handler" not in source:
        start = source.find(start_marker)
        end = source.find(end_marker, start + len(start_marker))
        if start < 0 or end < 0:
            raise RuntimeError("Could not locate PlayState.startVideo() for HTML5 cutscene playback.")

        replacement = r"""
	public function startVideo(name:String)
	{
		#if html5
		// HTML5 browser video cutscene handler. Avoid hxCodec/libVLC because it
		// is a native backend and cannot be linked into the JavaScript build.
		inCutscene = true;

		var videoName:String = name == null ? "" : name.trim();
		if (videoName.length <= 0)
		{
			FlxG.log.warn("Video cutscene was requested without a filename.");
			startAndEnd();
			return;
		}
		var lowerVideoName:String = videoName.toLowerCase();
		var hasVideoExtension:Bool = lowerVideoName.endsWith(".mp4") || lowerVideoName.endsWith(".m4v")
			|| lowerVideoName.endsWith(".webm") || lowerVideoName.endsWith(".ogv")
			|| lowerVideoName.endsWith(".ogg") || lowerVideoName.endsWith(".mov");
		if (!hasVideoExtension) videoName += ".mp4";
		if (videoName.startsWith("videos/")) videoName = "assets/" + videoName;
		else if (!videoName.startsWith("assets/videos/")) videoName = "assets/videos/" + videoName;

		var browserVideo:Dynamic = js.Browser.document.createElement("video");
		var playbackFinished:Bool = false;
		var resumeMusicAfterCutscene:Bool = FlxG.sound.music != null && FlxG.sound.music.playing;
		if (resumeMusicAfterCutscene) FlxG.sound.music.pause();

		var finishPlayback:Void->Void = null;
		finishPlayback = function()
		{
			if (playbackFinished) return;
			playbackFinished = true;

			try
			{
				browserVideo.pause();
				var videoParent:Dynamic = browserVideo.parentNode;
				if (videoParent != null) videoParent.removeChild(browserVideo);
			}
			catch (error:Dynamic)
			{
				FlxG.log.warn("Could not fully clean up the HTML5 cutscene video element.");
			}

			if (resumeMusicAfterCutscene && !endingSong && FlxG.sound.music != null)
				FlxG.sound.music.resume();

			startAndEnd();
		};

		browserVideo.autoplay = true;
		browserVideo.controls = false;
		browserVideo.preload = "auto";
		browserVideo.loop = false;
		browserVideo.muted = false;
		browserVideo.playsInline = true;
		browserVideo.src = videoName;

		browserVideo.style.position = "fixed";
		browserVideo.style.left = "0";
		browserVideo.style.top = "0";
		browserVideo.style.width = "100vw";
		browserVideo.style.height = "100vh";
		browserVideo.style.objectFit = "contain";
		browserVideo.style.backgroundColor = "#000";
		browserVideo.style.zIndex = "2147483647";
		browserVideo.style.pointerEvents = "auto";

		browserVideo.addEventListener("ended", function(event:Dynamic)
		{
			finishPlayback();
		});
		browserVideo.addEventListener("error", function(event:Dynamic)
		{
			FlxG.log.warn("Could not load HTML5 video cutscene: " + videoName);
			finishPlayback();
		});
		browserVideo.addEventListener("click", function(event:Dynamic)
		{
			// A click is a user gesture, allowing playback when the browser
			// initially blocks autoplay because the video has audio.
			try
			{
				browserVideo.play();
			}
			catch (error:Dynamic)
			{
				browserVideo.controls = true;
			}
		});

		var pageBody:Dynamic = js.Browser.document.body;
		if (pageBody == null)
		{
			FlxG.log.warn("Could not display HTML5 video cutscene: document body is unavailable.");
			finishPlayback();
			return;
		}
		pageBody.appendChild(browserVideo);

		try
		{
			var playAttempt:Dynamic = browserVideo.play();
			if (playAttempt != null)
			{
				var rejectHandler:Dynamic = Reflect.field(playAttempt, "catch");
				if (rejectHandler != null)
				{
					Reflect.callMethod(playAttempt, rejectHandler, [function(error:Dynamic)
					{
						FlxG.log.warn("Browser blocked automatic cutscene playback; click the video to start it.");
						browserVideo.controls = true;
					}]);
				}
			}
		}
		catch (error:Dynamic)
		{
			FlxG.log.warn("Browser could not start the HTML5 video cutscene; use the video controls to continue.");
			browserVideo.controls = true;
		}
		#elseif VIDEOS_ALLOWED
		inCutscene = true;

		var filepath:String = Paths.video(name);
		#if sys
		if(!FileSystem.exists(filepath))
		#else
		if(!OpenFlAssets.exists(filepath))
		#end
		{
			FlxG.log.warn('Couldnt find video file: ' + name);
			startAndEnd();
			return;
		}

		var video:MP4Handler = new MP4Handler();
		video.playVideo(filepath);
		video.finishCallback = function()
		{
			startAndEnd();
			return;
		}
		#else
		FlxG.log.warn('Platform not supported!');
		startAndEnd();
		return;
		#end
	}
""".strip()
        source = source[:start] + replacement + source[end:];

    if "HTML5 chart event: Play Video" not in source:
        trigger_start = source.find("public function triggerEventNote(")
        switch_marker = "\n\t\tswitch(eventName) {"
        switch_position = source.find(switch_marker, trigger_start)
        if trigger_start < 0 or switch_position < 0:
            raise RuntimeError("Could not locate triggerEventNote() for the Play Video chart event.")

        event_code = """
		// HTML5 chart event: Play Video. value1 is the MP4 filename, with or without .mp4.
		if (eventName != null && eventName.toLowerCase().trim() == "play video")
		{
			startVideo(value1);
			return;
		}
"""
        source = source[:switch_position] + event_code + source[switch_position:]

    required = (
        "HTML5 browser video cutscene handler",
        "HTML5 chart event: Play Video",
        'browserVideo.src = videoName;',
    )
    missing = [item for item in required if item not in source]
    if missing:
        raise RuntimeError("HTML5 video cutscene patch is incomplete: " + repr(missing))

    play_file.write_text(source, encoding="utf-8")


def stage_html5_shader_source_archive(game: Path, engine: Path) -> dict[str, int]:
    """Preserve every packaged shader directory outside Lime's runtime manifest.

    Psych Engine 0.6.3 gates its Lua runtime-shader loader behind the desktop
    sys target, and LUA_ALLOWED is not enabled for this HTML5 build. Keep the
    original shader sources and any companion files in a static archive so
    they are not lost, but do not put them in assets/preload where Lime would
    eagerly request them during startup.
    """
    archive = engine / "html5-shader-source-archive"
    if archive.exists():
        shutil.rmtree(archive)
    archive.mkdir(parents=True, exist_ok=True)

    shader_directories = [
        path for path in game.rglob("*")
        if path.is_dir() and path.name.lower() == "shaders"
    ]
    copied: set[str] = set()
    copied_bytes = 0

    def preserve_file(source_file: Path) -> None:
        nonlocal copied_bytes
        relative = source_file.relative_to(game)
        target = archive / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != source_file.read_bytes():
                raise RuntimeError(
                    "Conflicting packaged shader source paths: "
                    + relative.as_posix()
                )
            return
        shutil.copy2(source_file, target)
        copied.add(relative.as_posix())
        copied_bytes += source_file.stat().st_size

    # Preserve all files inside every directory named shaders, not just GLSL
    # source files; shader texture maps and include files may be required too.
    for shader_directory in shader_directories:
        for source_file in shader_directory.rglob("*"):
            if source_file.is_file():
                preserve_file(source_file)

    # Also retain shader source files stored outside a folder named shaders.
    shader_suffixes = (
        ".frag", ".vert", ".frag.txt", ".vert.txt", ".glsl",
        ".fx", ".fxh", ".shader",
    )
    for source_file in game.rglob("*"):
        if not source_file.is_file():
            continue
        lower_name = source_file.name.lower()
        if lower_name.endswith(shader_suffixes):
            preserve_file(source_file)

    return {
        "sourceDirectories": len(shader_directories),
        "sourceFiles": len(copied),
        "sourceBytes": copied_bytes,
    }


def strip_unsupported_html5_shader_assets(engine: Path) -> dict[str, int]:
    """Remove runtime GLSL files that Psych Engine 0.6.3 cannot load on HTML5.

    The engine's dynamic shader loader is guarded by the Haxe sys target.
    HTML5 does not define sys, and this web port does not run the mod's Lua
    scripts, so copied mod shader files are never usable. Leaving them in the
    preload asset tree causes Lime to request hundreds of unused URLs and can
    make the whole preload fail when the static host returns an intermittent
    503 for one of them.
    """
    paths_file = engine / "source" / "Paths.hx"
    source = paths_file.read_text(encoding="utf-8")
    source = source.replace(
        "return getPath('shaders/$key.frag.txt', TEXT, library);",
        "return getPath('shaders/$key.frag', TEXT, library);",
    )
    source = source.replace(
        "return getPath('shaders/$key.vert.txt', TEXT, library);",
        "return getPath('shaders/$key.vert', TEXT, library);",
    )
    paths_file.write_text(source, encoding="utf-8")

    removed_directories = 0
    removed_files = 0
    for shader_dir in (
        engine / "assets" / "shared" / "shaders",
        engine / "assets" / "shaders",
    ):
        if shader_dir.is_dir():
            removed_files += sum(1 for item in shader_dir.rglob("*") if item.is_file())
            shutil.rmtree(shader_dir)
            removed_directories += 1

    # Also catch shader source copied into an unexpected asset subfolder.
    for asset_file in list((engine / "assets").rglob("*")):
        if not asset_file.is_file():
            continue
        lower_name = asset_file.name.lower()
        is_embedded_runtime_shader = (
            asset_file.parent == engine / "assets" / "preload" / "shaders"
            and lower_name.endswith((".frag", ".vert"))
        )
        if lower_name.endswith((".frag", ".vert", ".frag.txt", ".vert.txt")) and not is_embedded_runtime_shader:
            asset_file.unlink()
            removed_files += 1

    runtime_shader_dir = engine / "assets" / "preload" / "shaders"
    leftovers = [
        path for path in (engine / "assets").rglob("*")
        if path.is_file()
        and path.name.lower().endswith((".frag", ".vert", ".frag.txt", ".vert.txt"))
        and not (path.parent == runtime_shader_dir and path.name.lower().endswith((".frag", ".vert")))
    ]
    if leftovers:
        raise RuntimeError(
            "HTML5 shader cleanup incomplete: "
            + ", ".join(str(path) for path in leftovers[:10])
        )

    return {
        "removedShaderDirectories": removed_directories,
        "removedShaderFiles": removed_files,
    }

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



def patch_runtime_shader_global_initializers(source_name: str, shader_text: str) -> str:
    """Move runtime-dependent global vec2 initializers into shader entry points.

    WebGL GLSL ES 1.00 rejects global initializers that depend on varyings or
    uniforms. The old Shadertoy ports used these as convenient globals; in
    HTML5 they must be local to main/mainImage. Helper functions that also use
    the resolution receive it as an explicit parameter.
    """
    first_function = re.search(
        r"(?m)^[ \t]*(?:void|float|vec[234]|mat[234])\s+\w+\s*\([^;{}]*\)\s*\{",
        shader_text,
    )
    if first_function is None:
        return shader_text

    global_prefix = shader_text[:first_function.start()]
    declaration_pattern = re.compile(
        r"(?m)^[ \t]*(vec2)\s+(uv|fragCoord|iResolution|realUV|destCoord)\s*=\s*"
        r"(openfl_TextureCoordv(?:\.xy)?|openfl_TextureCoordv\s*\*\s*openfl_TextureSize|openfl_TextureSize)\s*;\s*\n?"
    )
    declarations: list[tuple[str, str, str]] = []
    for match in declaration_pattern.finditer(global_prefix):
        declarations.append((match.group(1), match.group(2), match.group(3)))
    if not declarations:
        return shader_text

    # Remove the invalid global declarations before finding the entry point.
    shader_text = (
        declaration_pattern.sub("", global_prefix)
        + shader_text[first_function.start():]
    )

    entry = re.search(
        r"\bvoid\s+(?:main|mainImage)\s*\([^)]*\)\s*\{",
        shader_text,
    )
    if entry is None:
        raise RuntimeError(f"Could not find main/mainImage in {source_name} after removing globals.")

    # Find the matching entry-point brace, so local declarations in helper
    # functions do not make us incorrectly skip a required entry-point value.
    depth = 1
    cursor = entry.end()
    while cursor < len(shader_text) and depth > 0:
        if shader_text[cursor] == "{":
            depth += 1
        elif shader_text[cursor] == "}":
            depth -= 1
        cursor += 1
    if depth != 0:
        raise RuntimeError(f"Could not parse the entry-point body in {source_name}.")
    main_body = shader_text[entry.end():cursor - 1]

    local_declarations: list[str] = []
    for value_type, variable_name, initializer in declarations:
        if re.search(r"\b" + re.escape(value_type) + r"\s+" + re.escape(variable_name) + r"\s*=", main_body):
            continue
        local_declarations.append(f"{value_type} {variable_name} = {initializer};")

    if local_declarations:
        insertion = "\n    " + "\n    ".join(local_declarations) + "\n"
        shader_text = shader_text[:entry.end()] + insertion + shader_text[entry.end():]

    if source_name == "cross_hatch.frag":
        shader_text = shader_text.replace(
            "float lookup(vec2 p, float dx, float dy)",
            "float lookup(vec2 p, float dx, float dy, vec2 resolution)",
            1,
        )
        shader_text = shader_text.replace(
            "(p.xy + vec2(dx * d, dy * d)) / iResolution.xy",
            "(p.xy + vec2(dx * d, dy * d)) / resolution.xy",
            1,
        )
        for dx, dy in (
            ("-1.0", "-1.0"), ("-1.0", "0.0"), ("-1.0", "1.0"),
            ("1.0", "-1.0"), ("1.0", "0.0"), ("1.0", "1.0"),
            ("0.0", "-1.0"), ("0.0", "1.0"),
        ):
            old_call = f"lookup(p, {dx}, {dy})"
            new_call = f"lookup(p, {dx}, {dy}, iResolution)"
            shader_text = shader_text.replace(old_call, new_call)
    elif source_name == "pc.frag":
        shader_text = shader_text.replace(
            "vec3 GetScreenPixelColor(vec2 ul)",
            "vec3 GetScreenPixelColor(vec2 ul, vec2 resolution)",
            1,
        )
        shader_text = shader_text.replace(
            "1600.0/iResolution.x",
            "1600.0/resolution.x",
            1,
        )
        shader_text = shader_text.replace(
            "GetScreenPixelColor(vec2(ul.x+i/iResolution.x,ul.y))",
            "GetScreenPixelColor(vec2(ul.x+i/iResolution.x,ul.y), iResolution)",
        )

    return shader_text


def copy_south_runtime_shader_sources(mod: Path, engine: Path) -> dict[str, object]:
    """Copy only the shader sources actually requested by the Bloodbath Lua scripts.

    The complete ReShade collection remains archived as source material. The
    browser build embeds the 34 Psych runtime shaders referenced by
    ZNickShaders.lua and the glitch-note scripts, without preloading unrelated
    desktop ReShade .fx files.
    """
    shader_source = mod / "shaders"
    lua_directory = mod / "data" / "bloodbath"
    if not shader_source.is_dir() or not lua_directory.is_dir():
        raise FileNotFoundError("Bloodbath runtime shader sources or data scripts are missing.")

    shader_names: set[str] = set()
    shader_call = re.compile(
        r"""(?:createRuntimeShader|initLuaShader)\s*\(?\s*['"]([^'"]+)['"]""",
        re.IGNORECASE,
    )
    for lua_file in lua_directory.glob("*.lua"):
        lua_text = lua_file.read_text(encoding="utf-8", errors="replace")
        shader_names.update(name.strip() for name in shader_call.findall(lua_text) if name.strip())

    if not shader_names:
        raise RuntimeError("No runtime shader names were found in the Bloodbath Lua scripts.")

    destination = engine / "assets" / "preload" / "shaders"
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    missing: list[str] = []
    for name in sorted(shader_names, key=str.lower):
        found = False
        for extension in (".frag", ".vert"):
            source_file = shader_source / f"{name}{extension}"
            if source_file.is_file():
                # Keep the original release shader archive untouched, but
                # write WebGL-safe copies into the HTML5 preload directory.
                # GLSL ES 1.00 does not implicitly convert integer literals
                # when comparing them with float uniforms/vector components.
                shader_text = source_file.read_text(encoding="utf-8", errors="replace")
                compatibility_fixes = {
                    "bloom.frag": [
                        ("if (effect <= 0)", "if (effect <= 0.0)"),
                    ],
                    "SobelEffect.frag": [
                        ("if (strength <= 0)", "if (strength <= 0.0)"),
                        ("(1/openfl_TextureSize.xy)", "(1.0/openfl_TextureSize.xy)"),
                        ("(2*midLeft)", "(2.0*midLeft)"),
                        ("(2*midRight)", "(2.0*midRight)"),
                        ("(2*topMiddle)", "(2.0*topMiddle)"),
                        ("(2*bottomMiddle)", "(2.0*bottomMiddle)"),
                    ],
                    "barrelblur.frag": [
                        ("sumcol.a / num_iter", "sumcol.a / float(num_iter)"),
                    ],
                    "BarrelBlurEffect.frag": [
                        ("sumcol.a / num_iter", "sumcol.a / float(num_iter)"),
                    ],
                    "Gaussian Blur.frag": [
                        ("x < 1;", "x < 1.0;"),
                        ("y < 1;", "y < 1.0;"),
                    ],
                    "vcr with no glitch.frag": [
                        ("curUV.x<0", "curUV.x<0.0"),
                        ("curUV.x>1", "curUV.x>1.0"),
                        ("curUV.y<0", "curUV.y<0.0"),
                        ("curUV.y>1", "curUV.y>1.0"),
                    ],
                }
                for old_expression, webgl_expression in compatibility_fixes.get(source_file.name, []):
                    if old_expression in shader_text:
                        shader_text = shader_text.replace(old_expression, webgl_expression)

                if source_file.name == "Glitchy.frag":
                    # GLSL ES 1.00 requires a compile-time loop bound. The
                    # original shader uses the uniform AMT as its for-loop
                    # limit, so use a fixed 10-iteration loop and gate the
                    # original body with the same AMT condition.
                    original_loop = "for (float i = 0.0; i < 10.0 * AMT; i += 1.0) {"
                    fixed_loop = "for (int i = 0; i < 10; ++i) {"
                    if original_loop in shader_text:
                        loop_at = shader_text.index(original_loop)
                        line_start = shader_text.rfind("\n", 0, loop_at) + 1
                        indent = shader_text[line_start:loop_at]
                        new_header = (
                            indent + fixed_loop + "\n"
                            + indent + "    if (float(i) < 10.0 * AMT) {"
                        )
                        shader_text = (
                            shader_text[:line_start]
                            + new_header
                            + shader_text[loop_at + len(original_loop):]
                        )
                        channel_comment = "//do slight offset on one entire channel"
                        comment_at = shader_text.find(channel_comment, line_start)
                        close_at = shader_text.rfind("\n" + indent + "}", line_start, comment_at)
                        if comment_at < 0 or close_at < 0:
                            raise RuntimeError(
                                "Could not locate the end of the Glitchy.frag AMT loop body."
                            )
                        shader_text = (
                            shader_text[:close_at]
                            + "\n" + indent + "    }"
                            + shader_text[close_at:]
                        )
                    elif fixed_loop not in shader_text:
                        raise RuntimeError(
                            "Could not locate the original variable-bound loop in Glitchy.frag."
                        )

                if extension == ".frag":
                    shader_text = patch_runtime_shader_global_initializers(
                        source_file.name, shader_text
                    )

                target_file = destination / source_file.name
                target_file.write_text(shader_text, encoding="utf-8")
                copied.append(source_file.name)
                found = True
        if not found:
            missing.append(name)

    if missing:
        raise FileNotFoundError(
            "Bloodbath Lua scripts request shader sources that are missing: "
            + ", ".join(missing)
        )

    return {
        "shaderNames": sorted(shader_names, key=str.lower),
        "copiedFiles": copied,
        "copiedCount": len(copied),
    }


def patch_south_runtime_shaders(engine: Path) -> None:
    """Enable embedded FlxRuntimeShader sources and port Bloodbath's shader timeline to HTML5."""
    play_file = engine / "source" / "PlayState.hx"
    source = play_file.read_text(encoding="utf-8")

    runtime_start = source.find("\t#if (!flash && sys)\n\tpublic var runtimeShaders:")
    if runtime_start >= 0:
        runtime_end = source.find("\n\t#end\n", runtime_start)
        if runtime_end < 0:
            raise RuntimeError("Could not locate the end of Psych Engine's runtime shader block.")
        runtime_end += len("\n\t#end")
        runtime_block = """\t#if !flash
\tpublic var runtimeShaders:Map<String, Array<String>> = new Map<String, Array<String>>();
\tpublic function createRuntimeShader(name:String):FlxRuntimeShader
\t{
\t\tif (!ClientPrefs.shaders) return new FlxRuntimeShader(null, null, 100);
\t\tif (!runtimeShaders.exists(name) && !initLuaShader(name))
\t\t{
\t\t\tFlxG.log.warn('Shader ' + name + ' is missing from the embedded HTML5 assets.');
\t\t\treturn new FlxRuntimeShader(null, null, 100);
\t\t}
\t\tvar shaderSources:Array<String> = runtimeShaders.get(name);
\t\treturn new FlxRuntimeShader(shaderSources[0], shaderSources[1], 100);
\t}

\tpublic function initLuaShader(name:String, ?glslVersion:Int = 100):Bool
\t{
\t\tif (!ClientPrefs.shaders) return false;
\t\tif (runtimeShaders.exists(name)) return true;

\t\tvar fragmentPath:String = Paths.shaderFragment(name);
\t\tvar vertexPath:String = Paths.shaderVertex(name);
\t\tvar fragmentSource:String = null;
\t\tvar vertexSource:String = null;
\t\tif (OpenFlAssets.exists(fragmentPath))
\t\t\tfragmentSource = OpenFlAssets.getText(fragmentPath);
\t\tif (OpenFlAssets.exists(vertexPath))
\t\t\tvertexSource = OpenFlAssets.getText(vertexPath);

\t\tif (fragmentSource == null && vertexSource == null)
\t\t{
\t\t\tFlxG.log.warn('Missing embedded shader ' + name + ' (.frag/.vert).');
\t\t\treturn false;
\t\t}
\t\truntimeShaders.set(name, [fragmentSource, vertexSource]);
\t\treturn true;
\t}
\t#end"""
        source = source[:runtime_start] + runtime_block + source[runtime_end:]
    elif "public function initLuaShader(name:String, ?glslVersion:Int = 100):Bool" not in source:
        raise RuntimeError("Could not find Psych Engine's desktop-only runtime shader loader to port.")

    shader_fields = """\t#if !flash
\tvar southRuntimeShaderObjects:Map<String, FlxRuntimeShader> = new Map();
\tvar southShaderUniformValues:Map<String, Float> = new Map();
\tvar southShaderUniformTweens:Map<String, FlxTween> = new Map();
\tvar southShaderFeatureFlags:Map<String, Bool> = new Map();
\tvar southShaderClock:Float = 0;
\t#end
"""
    field_marker = "\tvar badAppleWhiteBg:FlxSprite;"
    if "var southRuntimeShaderObjects:" not in source:
        if field_marker not in source:
            raise RuntimeError("Could not find the South stage field insertion point for runtime shaders.")
        source = source.replace(field_marker, shader_fields + field_marker, 1)

    shader_methods = """\t#if !flash
\tfunction loadSouthRuntimeShader(key:String, sourceName:String):FlxRuntimeShader
\t{
\t\tvar shader:FlxRuntimeShader = createRuntimeShader(sourceName);
\t\tif (shader != null) southRuntimeShaderObjects.set(key, shader);
\t\treturn shader;
\t}

\tfunction setSouthShaderUniform(shaderKey:String, uniformName:String, value:Float):Void
\t{
\t\tvar shader:FlxRuntimeShader = southRuntimeShaderObjects.get(shaderKey);
\t\tif (shader == null || Math.isNaN(value)) return;
\t\tvar uniformKey:String = shaderKey + "." + uniformName;
\t\tsouthShaderUniformValues.set(uniformKey, value);
\t\tshader.setFloat(uniformName, value);
\t}

\tfunction tweenSouthShaderUniform(shaderKey:String, uniformName:String, target:Float, duration:Float):Void
\t{
\t\tif (Math.isNaN(target)) return;
\t\tvar shader:FlxRuntimeShader = southRuntimeShaderObjects.get(shaderKey);
\t\tif (shader == null) return;
\t\tvar uniformKey:String = shaderKey + "." + uniformName;
\t\tvar current:Float = southShaderUniformValues.exists(uniformKey)
\t\t\t? southShaderUniformValues.get(uniformKey) : target;
\t\tif (southShaderUniformTweens.exists(uniformKey))
\t\t{
\t\t\tvar oldTween:FlxTween = southShaderUniformTweens.get(uniformKey);
\t\t\tif (oldTween != null) oldTween.cancel();
\t\t\tsouthShaderUniformTweens.remove(uniformKey);
\t\t}
\t\tif (duration <= 0 || Math.isNaN(duration))
\t\t{
\t\t\tsetSouthShaderUniform(shaderKey, uniformName, target);
\t\t\treturn;
\t\t}
\t\tvar tweenState = {value: current};
\t\tvar activeTween:FlxTween = FlxTween.tween(tweenState, {value: target}, duration, {
\t\t\tease: FlxEase.linear,
\t\t\tonUpdate: function(_:FlxTween)
\t\t\t{
\t\t\t\tsetSouthShaderUniform(shaderKey, uniformName, tweenState.value);
\t\t\t},
\t\t\tonComplete: function(_:FlxTween)
\t\t\t{
\t\t\t\tsouthShaderUniformTweens.remove(uniformKey);
\t\t\t}
\t\t});
\t\tsouthShaderUniformTweens.set(uniformKey, activeTween);
\t}

\tfunction appendSouthShaderFilter(filters:Array<BitmapFilter>, shaderKey:String):Void
\t{
\t\tvar shader:FlxRuntimeShader = southRuntimeShaderObjects.get(shaderKey);
\t\tif (shader != null) filters.push(new ShaderFilter(shader));
\t}

\tfunction southShaderFeatureEnabled(key:String):Bool
\t{
\t\treturn southShaderFeatureFlags.exists(key) && southShaderFeatureFlags.get(key) == true;
\t}

\tfunction refreshSouthShaderFilters():Void
\t{
\t\tif (curStage != 'south' || !ClientPrefs.shaders) return;

\t\tvar gameFilters:Array<BitmapFilter> = [];
\t\tfor (key in ['bloomEffect', 'greyScale', 'barrel', 'blur', 'mosaic', 'chrom', 'colorola', 'vig', 'glitchy', 'sob'])
\t\t\tappendSouthShaderFilter(gameFilters, key);
\t\tif (southShaderFeatureEnabled('invert')) appendSouthShaderFilter(gameFilters, 'invert');
\t\tif (southShaderFeatureEnabled('pc')) appendSouthShaderFilter(gameFilters, 'pc');
\t\tappendSouthShaderFilter(gameFilters, 'mirrorRepeat');
\t\tfor (key in ['cross', 'smok', 'old tv', 'lightsrgb', 'effect weird', 'static', 'crt distorsionnew',
\t\t\t'starfield', 'colorswitching', 'tv', 'raytracingcube', 'blaze', 'gameboy', 'vcrshader',
\t\t\t'vcr with no glitch', 'rgbeffect3', 'chromaticRadialBlur'])
\t\t\tif (southShaderFeatureEnabled(key)) appendSouthShaderFilter(gameFilters, key);
\t\tappendSouthShaderFilter(gameFilters, 'color');
\t\tcamGame.setFilters(gameFilters);

\t\tvar hudFilters:Array<BitmapFilter> = [];
\t\tfor (key in ['bloomEffect', 'greyScale', 'barrelhud', 'blur', 'mosaic', 'chrom', 'colorola', 'scan', 'glitchy', 'sob'])
\t\t\tappendSouthShaderFilter(hudFilters, key);
\t\tappendSouthShaderFilter(hudFilters, 'mirrorRepeathud');
\t\tfor (key in ['cross', 'old tv', 'vcrshader', 'vcr with no glitch', 'rgbeffect3', 'chromaticRadialBlur'])
\t\t\tif (southShaderFeatureEnabled(key)) appendSouthShaderFilter(hudFilters, key);
\t\tcamHUD.setFilters(hudFilters);
\t}

\tfunction setSouthShaderFeature(key:String, enabled:Bool):Void
\t{
\t\tsouthShaderFeatureFlags.set(key, enabled);
\t\trefreshSouthShaderFilters();
\t}

\tfunction southShaderNumber(value:String, fallback:Float):Float
\t{
\t\tif (value == null || value.trim().length == 0) return fallback;
\t\tvar parsed:Float = Std.parseFloat(value);
\t\treturn Math.isNaN(parsed) ? fallback : parsed;
\t}

\tfunction southShaderPair(value:String, firstDefault:Float, secondDefault:Float):Array<Float>
\t{
\t\tif (value == null) return [firstDefault, secondDefault];
\t\tvar parts:Array<String> = value.split(',');
\t\tvar first:Float = parts.length > 0 ? southShaderNumber(parts[0], firstDefault) : firstDefault;
\t\tvar second:Float = parts.length > 1 ? southShaderNumber(parts[1], secondDefault) : secondDefault;
\t\treturn [first, second];
\t}

\tfunction initSouthBloodbathShaders():Void
\t{
\t\tif (curStage != 'south' || !ClientPrefs.shaders) return;

\t\tvar shaderDefinitions:Array<Array<String>> = [
\t\t\t['bloomEffect', 'bloom'],
\t\t\t['greyScale', 'greyScale'],
\t\t\t['barrel', 'barrelblur'],
\t\t\t['barrelhud', 'barrelblur'],
\t\t\t['blur', 'Gaussian Blur'],
\t\t\t['mosaic', 'mosaic'],
\t\t\t['chrom', 'ChromAbEffect'],
\t\t\t['colorola', 'ColorShader'],
\t\t\t['scan', 'scanline'],
\t\t\t['vig', 'vignette'],
\t\t\t['glitchy', 'Glitchy'],
\t\t\t['sob', 'SobelEffect'],
\t\t\t['mirrorRepeat', 'mirrorRepeat'],
\t\t\t['mirrorRepeathud', 'mirrorRepeat'],
\t\t\t['color', 'ColorOverrideEffect'],
\t\t\t['BarrelBlurEffect', 'BarrelBlurEffect'],
\t\t\t['cross', 'cross_hatch'],
\t\t\t['old tv', 'old tv'],
\t\t\t['lightsrgb', 'lightsrgb'],
\t\t\t['static', 'static'],
\t\t\t['effect weird', 'effect weird'],
\t\t\t['crt distorsionnew', 'crt distorsionnew'],
\t\t\t['starfield', 'starfield'],
\t\t\t['colorswitching', 'colorswitching'],
\t\t\t['tv', 'tv'],
\t\t\t['raytracingcube', 'raytracingcube'],
\t\t\t['smok', 'PerlinSmokeEffect'],
\t\t\t['chromaticRadialBlur', 'chromaticRadialBlur'],
\t\t\t['vcrshader', 'vcrshader'],
\t\t\t['vcr with no glitch', 'vcr with no glitch'],
\t\t\t['rgbeffect3', 'rgbeffect3'],
\t\t\t['blaze', 'blaze'],
\t\t\t['gameboy', 'gameboy'],
\t\t\t['invert', 'invert'],
\t\t\t['pc', 'pc'],
\t\t\t['NewGlitch2', 'NewGlitch2']
\t\t];
\t\tfor (definition in shaderDefinitions)
\t\t\tloadSouthRuntimeShader(definition[0], definition[1]);

\t\tsetSouthShaderUniform('bloomEffect', 'effect', 0);
\t\tsetSouthShaderUniform('bloomEffect', 'strength', 1);
\t\tsetSouthShaderUniform('bloomEffect', 'contrast', 1);
\t\tsetSouthShaderUniform('bloomEffect', 'brightness', 0);
\t\tsetSouthShaderUniform('greyScale', 'strength', 0);
\t\tfor (key in ['barrel', 'barrelhud'])
\t\t{
\t\t\tsetSouthShaderUniform(key, 'barrel', 0);
\t\t\tsetSouthShaderUniform(key, 'zoom', 1);
\t\t\tsetSouthShaderUniform(key, 'angle', 0);
\t\t\tsetSouthShaderUniform(key, 'x', 0);
\t\t\tsetSouthShaderUniform(key, 'y', 0);
\t\t\tvar barrelShader:FlxRuntimeShader = southRuntimeShaderObjects.get(key);
\t\t\tif (barrelShader != null) barrelShader.setBool('doChroma', false);
\t\t}
\t\tsetSouthShaderUniform('BarrelBlurEffect', 'barrel', 0);
\t\tsetSouthShaderUniform('BarrelBlurEffect', 'zoom', 1);
\t\tsetSouthShaderUniform('BarrelBlurEffect', 'angle', 0);
\t\tsetSouthShaderUniform('BarrelBlurEffect', 'x', 0);
\t\tsetSouthShaderUniform('BarrelBlurEffect', 'y', 0);
\t\tsetSouthShaderUniform('blur', 'strength', 0);
\t\tsetSouthShaderUniform('mosaic', 'strength', 0);
\t\tsetSouthShaderUniform('chrom', 'strength', 0);
\t\tsetSouthShaderUniform('colorola', 'zoom', 1);
\t\tsetSouthShaderUniform('colorola', 'red', 1);
\t\tsetSouthShaderUniform('colorola', 'green', 1);
\t\tsetSouthShaderUniform('colorola', 'blue', 1);
\t\tsetSouthShaderUniform('scan', 'strength', 0);
\t\tsetSouthShaderUniform('scan', 'pixelsBetweenEachLine', 0);
\t\tsetSouthShaderUniform('vig', 'strength', 0);
\t\tsetSouthShaderUniform('vig', 'size', 0);
\t\tsetSouthShaderUniform('vig', 'red', 0);
\t\tsetSouthShaderUniform('vig', 'green', 0);
\t\tsetSouthShaderUniform('vig', 'blue', 0);
\t\tsetSouthShaderUniform('glitchy', 'iTime', 0);
\t\tsetSouthShaderUniform('glitchy', 'AMT', 0);
\t\tsetSouthShaderUniform('glitchy', 'SPEED', 0);
\t\tsetSouthShaderUniform('sob', 'strength', 0);
\t\tsetSouthShaderUniform('sob', 'intensity', 1);
\t\tfor (key in ['mirrorRepeat', 'mirrorRepeathud'])
\t\t{
\t\t\tsetSouthShaderUniform(key, 'x', 0);
\t\t\tsetSouthShaderUniform(key, 'y', 0);
\t\t\tsetSouthShaderUniform(key, 'angle', 0);
\t\t\tsetSouthShaderUniform(key, 'zoom', 1);
\t\t}
\t\tsetSouthShaderUniform('color', 'red', 1);
\t\tsetSouthShaderUniform('color', 'green', 1);
\t\tsetSouthShaderUniform('color', 'blue', 1);
\t\tsetSouthShaderUniform('chromaticRadialBlur', 'Count', 0);
\t\tsetSouthShaderUniform('chromaticRadialBlur', 'blur', 0);
\t\tsetSouthShaderUniform('chromaticRadialBlur', 'falloff', 0);
\t\tfor (key in ['cross', 'old tv', 'lightsrgb', 'static', 'effect weird', 'crt distorsionnew',
\t\t\t'starfield', 'colorswitching', 'tv', 'raytracingcube', 'smok', 'vcrshader',
\t\t\t'vcr with no glitch', 'rgbeffect3', 'blaze', 'gameboy', 'invert', 'pc'])
\t\t\tsetSouthShaderUniform(key, 'iTime', 0);

\t\tfor (key in ['cross', 'old tv', 'lightsrgb', 'static', 'effect weird', 'crt distorsionnew',
\t\t\t'starfield', 'colorswitching', 'tv', 'raytracingcube', 'smok', 'vcrshader',
\t\t\t'vcr with no glitch', 'rgbeffect3', 'blaze', 'gameboy', 'invert', 'pc',
\t\t\t'chromaticRadialBlur'])
\t\t\tsouthShaderFeatureFlags.set(key, false);
\t\trefreshSouthShaderFilters();
\t}

\tfunction updateSouthBloodbathShaders(elapsed:Float):Void
\t{
\t\tif (curStage != 'south' || !ClientPrefs.shaders || southRuntimeShaderObjects == null) return;
\t\tsouthShaderClock += elapsed;
\t\tfor (key in ['cross', 'old tv', 'lightsrgb', 'static', 'effect weird', 'crt distorsionnew',
\t\t\t'starfield', 'colorswitching', 'tv', 'raytracingcube', 'smok', 'vcrshader',
\t\t\t'vcr with no glitch', 'rgbeffect3', 'blaze', 'gameboy', 'invert', 'pc'])
\t\t{
\t\t\tvar shader:FlxRuntimeShader = southRuntimeShaderObjects.get(key);
\t\t\tif (shader != null) shader.setFloat('iTime', southShaderClock);
\t\t}
\t\tvar glitchShader:FlxRuntimeShader = southRuntimeShaderObjects.get('glitchy');
\t\tif (glitchShader != null) glitchShader.setFloat('iTime', southShaderClock);
\t}

\tfunction handleSouthShaderEvent(eventName:String, value1:String, value2:String):Void
\t{
\t\tif (curStage != 'south' || !ClientPrefs.shaders || eventName == null) return;
\t\tvar eventKey:String = eventName.trim().toLowerCase();
\t\tvar toggle:Bool = value1 != null && value1.trim().toLowerCase() == 'on';
\t\tswitch (eventKey)
\t\t{
\t\t\tcase 'crt distorsionnew':
\t\t\t\tsetSouthShaderFeature('crt distorsionnew', toggle);
\t\t\tcase 'crazyshader':
\t\t\t\tsetSouthShaderFeature('vcrshader', toggle);
\t\t\tcase 'invert':
\t\t\t\tsetSouthShaderFeature('invert', toggle);
\t\t\tcase 'cross' | 'smoke' | 'old tv' | 'lightsrgb' | 'effect weird' | 'static' |
\t\t\t\t'starfield' | 'tv' | 'raytracingcube' | 'colorswitching' | 'blaze' | 'gameboy' | 'pc':
\t\t\t\tvar featureKey:String = eventKey == 'smoke' ? 'smok' : eventKey;
\t\t\t\tsetSouthShaderFeature(featureKey, toggle);
\t\t\tcase 'greyscale':
\t\t\t\ttweenSouthShaderUniform('greyScale', 'strength', southShaderNumber(value1, 0), southShaderNumber(value2, 0));
\t\t\tcase 'sobelintensity':
\t\t\t\ttweenSouthShaderUniform('sob', 'intensity', southShaderNumber(value1, 1), southShaderNumber(value2, 0));
\t\t\tcase 'sobeleffect':
\t\t\t\ttweenSouthShaderUniform('sob', 'strength', southShaderNumber(value1, 0), southShaderNumber(value2, 0));
\t\t\tcase 'sobelflash':
\t\t\t\tsetSouthShaderUniform('sob', 'strength', southShaderNumber(value1, 0));
\t\t\t\ttweenSouthShaderUniform('sob', 'strength', 0, southShaderNumber(value2, 0));
\t\t\tcase 'bloomhigh':
\t\t\t\tsetSouthShaderUniform('bloomEffect', 'effect', 0.8);
\t\t\t\tsetSouthShaderUniform('bloomEffect', 'strength', 1.6);
\t\t\t\ttweenSouthShaderUniform('bloomEffect', 'effect', 0, southShaderNumber(value1, 0));
\t\t\t\ttweenSouthShaderUniform('bloomEffect', 'strength', 0, southShaderNumber(value1, 0));
\t\t\tcase 'bloommed':
\t\t\t\tsetSouthShaderUniform('bloomEffect', 'effect', 0.5);
\t\t\t\tsetSouthShaderUniform('bloomEffect', 'strength', 1.3);
\t\t\t\ttweenSouthShaderUniform('bloomEffect', 'effect', 0, southShaderNumber(value1, 0));
\t\t\t\ttweenSouthShaderUniform('bloomEffect', 'strength', 0, southShaderNumber(value1, 0));
\t\t\tcase 'bloomlow':
\t\t\t\tsetSouthShaderUniform('bloomEffect', 'effect', 0.2);
\t\t\t\tsetSouthShaderUniform('bloomEffect', 'strength', 1);
\t\t\t\ttweenSouthShaderUniform('bloomEffect', 'effect', 0, southShaderNumber(value1, 0));
\t\t\t\ttweenSouthShaderUniform('bloomEffect', 'strength', 0, southShaderNumber(value1, 0));
\t\t\tcase 'bloomlow low':
\t\t\t\tsetSouthShaderUniform('bloomEffect', 'effect', 0.1);
\t\t\t\tsetSouthShaderUniform('bloomEffect', 'strength', 0.23);
\t\t\t\ttweenSouthShaderUniform('bloomEffect', 'effect', 0, southShaderNumber(value1, 0));
\t\t\t\ttweenSouthShaderUniform('bloomEffect', 'strength', 0, southShaderNumber(value1, 0));
\t\t\tcase 'bloom':
\t\t\t\tvar bloomValue:Float = southShaderNumber(value1, 0);
\t\t\t\ttweenSouthShaderUniform('bloomEffect', 'effect', bloomValue, southShaderNumber(value2, 0));
\t\t\t\ttweenSouthShaderUniform('bloomEffect', 'strength', bloomValue * 2, southShaderNumber(value2, 0));
\t\t\tcase 'barrel':
\t\t\t\tvar barrelPair:Array<Float> = southShaderPair(value1, 0, 0);
\t\t\t\tvar barrelDuration:Float = southShaderNumber(value2, 0);
\t\t\t\ttweenSouthShaderUniform('barrel', 'barrel', barrelPair[0], barrelDuration);
\t\t\t\ttweenSouthShaderUniform('barrelhud', 'barrel', barrelPair[1], barrelDuration);
\t\t\tcase 'barrelangle':
\t\t\t\tvar anglePair:Array<Float> = southShaderPair(value1, 0, 0);
\t\t\t\tvar angleDuration:Float = southShaderNumber(value2, 0);
\t\t\t\ttweenSouthShaderUniform('barrel', 'angle', anglePair[0], angleDuration);
\t\t\t\ttweenSouthShaderUniform('barrelhud', 'angle', anglePair[1], angleDuration);
\t\t\tcase 'barrelbeat':
\t\t\t\tvar beatPair:Array<Float> = southShaderPair(value1, 0, 0);
\t\t\t\tsetSouthShaderUniform('barrel', 'barrel', beatPair[0]);
\t\t\t\tsetSouthShaderUniform('barrelhud', 'barrel', beatPair[1]);
\t\t\t\ttweenSouthShaderUniform('barrel', 'barrel', 0, 0.4);
\t\t\t\ttweenSouthShaderUniform('barrelhud', 'barrel', 0, 0.4);
\t\t\tcase 'blur':
\t\t\t\ttweenSouthShaderUniform('blur', 'strength', southShaderNumber(value1, 0), southShaderNumber(value2, 0));
\t\t\tcase 'blurbeat':
\t\t\t\t// The release script tweens an undeclared bbStrength sprite;
\t\t\t\t// reproduce the intended one-shot blur and barrel-zoom pulse
\t\t\t\t// through the real embedded runtime shader uniforms.
\t\t\t\tsetSouthShaderUniform('blur', 'strength', 12.5);
\t\t\t\ttweenSouthShaderUniform('blur', 'strength', 0, 0.4);
\t\t\t\tsetSouthShaderUniform('barrel', 'zoom', 0.85);
\t\t\t\tsetSouthShaderUniform('barrelhud', 'zoom', 0.85);
\t\t\t\ttweenSouthShaderUniform('barrel', 'zoom', 1, 0.4);
\t\t\t\ttweenSouthShaderUniform('barrelhud', 'zoom', 1, 0.4);
\t\t\tcase 'chromaticabber':
\t\t\t\ttweenSouthShaderUniform('chrom', 'strength', southShaderNumber(value1, 0), southShaderNumber(value2, 0));
\t\t\tcase 'chrombeat':
\t\t\t\tsetSouthShaderUniform('chrom', 'strength', 0.009);
\t\t\t\ttweenSouthShaderUniform('chrom', 'strength', 0, 1);
\t\t\tcase 'aglitch':
\t\t\t\tsetSouthShaderUniform('glitchy', 'AMT', southShaderNumber(value1, 0));
\t\t\t\tsetSouthShaderUniform('glitchy', 'SPEED', southShaderNumber(value2, 0));
\t\t}
\t}
\t#end
"""
    if "function initSouthBloodbathShaders():Void" not in source:
        method_marker = "\tfunction set_songSpeed(value:Float):Float"
        if method_marker not in source:
            raise RuntimeError("Could not locate a safe PlayState method insertion point for South runtime shaders.")
        source = source.replace(method_marker, shader_methods + "\n" + method_marker, 1)

    create_marker = "\t\tsuper.create();\n"
    if "initSouthBloodbathShaders();" not in source:
        if create_marker not in source:
            raise RuntimeError("Could not find PlayState super.create() to initialize the shader stack.")
        source = source.replace(
            create_marker,
            create_marker + "\t\tif (curStage == 'south') initSouthBloodbathShaders();\n",
            1,
        )

    update_marker = "\t\tcallOnLuas('onUpdatePost', [elapsed]);"
    if "updateSouthBloodbathShaders(elapsed);" not in source:
        if update_marker not in source:
            raise RuntimeError("Could not find PlayState onUpdatePost update point for shader time uniforms.")
        source = source.replace(
            update_marker,
            update_marker + "\n\t\tupdateSouthBloodbathShaders(elapsed);",
            1,
        )


    play_file.write_text(source, encoding="utf-8")


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
    shader_archive_report = stage_html5_shader_source_archive(game, engine)
    video_archive_report = stage_html5_video_assets(game, engine)

    # Restore the matching runtime assets, including data and shaders. The
    # original copy list omitted these folders, which can leave valid package
    # references unresolved in the browser build.
    for folder in ("images", "characters", "stages", "sounds", "music", "data"):
        copy_tree(runtime_assets / folder, engine / "assets" / "preload" / folder)
    copy_tree(runtime_assets / "shared", engine / "assets" / "shared")
    copy_tree(runtime_assets / "fonts", engine / "assets" / "fonts")

    # Embed every non-script resource folder from the mod, not just JSON files.
    # This preserves atlases, XML/animation metadata, text data and other sidecars.
    copy_tree(mod / "images", engine / "assets" / "preload" / "images")
    remove_unused_stan_v2_atlas(game, engine / "assets" / "preload" / "images")
    custom_menu_art_report = install_custom_menu_art(
        Path(__file__).resolve().parents[1],
        engine / "assets" / "preload" / "images",
    )
    copy_tree(mod / "characters", engine / "assets" / "preload" / "characters")
    copy_tree(mod / "stages", engine / "assets" / "preload" / "stages")

    copy_tree(mod / "weeks", engine / "assets" / "preload" / "weeks")
    copy_tree(mod / "data" / "bloodbath", engine / "assets" / "preload" / "data" / "bloodbath")
    copy_tree(mod / "fonts", engine / "assets" / "fonts")
    copy_tree(mod / "sounds", engine / "assets" / "preload" / "sounds")
    copy_tree(mod / "songs" / "bloodbath", engine / "assets" / "songs" / "bloodbath")
    runtime_shader_asset_report = copy_south_runtime_shader_sources(mod, engine)

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
    patch_menu_background_fit(engine)
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
            if isinstance(event, list) and len(event) >= 3 and str(event[0]).strip().lower() == "change character":
                character_names.add(str(event[2]).strip())

    # The upstream engine ships dozens of unrelated character definitions.
    # They are all placed in the default asset manifest and fetched during
    # preload, even though this one-song browser build never uses them. Keep
    # only chart/Lua characters plus the engine's default game-over character.
    character_names.add("bf-dead")
    # Keep every complete mod character definition so runtime Change Character
    # events can select the correct sprite even when the name is not in the chart.
    mod_character_dir = mod / "characters"
    if mod_character_dir.is_dir():
        for mod_character_file in mod_character_dir.glob("*.json"):
            try:
                mod_character_data = json.loads(mod_character_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            mod_image_name = str(mod_character_data.get("image", f"characters/{mod_character_file.stem}"))
            mod_preload_images = engine / "assets" / "preload" / "images"
            mod_shared_images = engine / "assets" / "shared" / "images"
            mod_has_preload_atlas = (
                (mod_preload_images / f"{mod_image_name}.png").is_file()
                and (mod_preload_images / f"{mod_image_name}.xml").is_file()
            )
            mod_has_shared_atlas = (
                (mod_shared_images / f"{mod_image_name}.png").is_file()
                and (mod_shared_images / f"{mod_image_name}.xml").is_file()
            )
            if mod_has_preload_atlas or mod_has_shared_atlas:
                character_names.add(mod_character_file.stem)

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
    patch_south_stage(engine, image_report, icon_changes, character_names)
    patch_south_runtime_shaders(engine)
    patch_change_character_visibility(engine)
    patch_south_stage_fades(engine)
    patch_south_cinematic_bars(engine)
    patch_black_screen_diagnostics(engine)
    patch_botplay_pause_menu(engine)
    patch_chart_editor_optional_vocals(engine)
    patch_html5_video_cutscenes(engine)
    shader_report = strip_unsupported_html5_shader_assets(engine)
    print("Embedded Bloodbath runtime shader sources: " + json.dumps(runtime_shader_asset_report, sort_keys=True))
    print("Unsupported HTML5 runtime shader assets removed: " + json.dumps(shader_report, sort_keys=True))
    print("Preserved shader source archive (not runtime-preloaded): " + json.dumps(shader_archive_report, sort_keys=True))
    print("HTML5 MP4 cutscene assets: " + json.dumps(video_archive_report, sort_keys=True))
    converted = convert_ogg_files(engine)
    song_cleanup = keep_only_bloodbath(engine)

    asset_bytes = sum(p.stat().st_size for p in (engine / "assets").rglob("*") if p.is_file())
    print(f"Prepared game root: {game}")
    print(f"Converted OGG files to MP3: {converted}")
    print("Bloodbath-only cleanup: " + json.dumps(song_cleanup, sort_keys=True))
    print(f"Removed unused stock character definitions from the preload manifest: {removed_character_definitions}")
    print("Browser image optimization: " + json.dumps(image_report, sort_keys=True))
    print("Custom main-menu artwork installed: " + json.dumps(custom_menu_art_report, sort_keys=True))
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

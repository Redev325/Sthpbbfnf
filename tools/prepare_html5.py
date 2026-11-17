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


# ... [rest of existing file unchanged]

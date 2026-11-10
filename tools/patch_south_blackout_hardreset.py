#!/usr/bin/env python3
"""Hard-reset the South stage blackout layers in the generated Psych Engine source.

This guards against the specific HTML5 bug where the stage keeps one of the black/
white/fullscreen cover layers active after the 5:56 chart event. The overwrite is
applied after the stage recovery hook, and it clears the exact overlays that keep
fullscreen blackness stuck for the rest of the song.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def inject_hard_reset(target: Path) -> None:
    text = target.read_text(encoding="utf-8")
    if "clearSouthBlackoutHardReset" in text:
        print("Blackout hard reset already injected.")
        return

    marker = "function recoverLateBlackout():Void\n\t{\n"
    if marker not in text:
        raise RuntimeError("Could not find recoverLateBlackout() in PlayState.hx")

    insert = """function clearSouthBlackoutHardReset():Void
\t{
\t\tvar clearNames:Array<String> = ['black', 'blackbar', 'white'];
\t\tif (southAnimatedObjects != null)
\t\t{
\t\t\tfor (key in clearNames)
\t\t\t{
\t\t\t\tvar cover:Dynamic = southAnimatedObjects.get(key);
\t\t\t\tif (cover != null)
\t\t\t\t{
\t\t\t\t\tcover.alpha = 0;
\t\t\t\t\tcover.visible = false;
\t\t\t\t}
\t\t\t}
\t\t}
\n\t\tif (southBlackFlashOverlay != null)
\t\t{
\t\t\tsouthBlackFlashOverlay.alpha = 0;
\t\t\tsouthBlackFlashOverlay.visible = false;
\t\t}
\n\t\tif (southWhite != null)
\t\t{
\t\t\tsouthWhite.alpha = 0;
\t\t\tsouthWhite.visible = false;
\t\t}
\n\t\tif (southBlack != null)
\t\t{
\t\t\tsouthBlack.alpha = 0;
\t\t\tsouthBlack.visible = false;
\t\t}
\n\t\tif (southBlackbar != null)
\t\t{
\t\t\tsouthBlackbar.alpha = 0;
\t\t\tsouthBlackbar.visible = false;
\t\t}
\n\t\tif (southStillStrumUpperBar != null)
\t\t\tsouthStillStrumUpperBar.alpha = 1;
\n\t\tif (southStillStrumLowerBar != null)
\t\t\tsouthStillStrumLowerBar.alpha = 1;
\n\t\tif (camGame != null)
\t\t\tcamGame.zoom = defaultCamZoom;
\t}

"""

    text = text.replace(marker, insert + marker, 1)

    text = text.replace(
        "\t\trecoverLateBlackout();\n",
        "\t\tclearSouthBlackoutHardReset();\n\t\trecoverLateBlackout();\n",
        1,
    )

    target.write_text(text, encoding="utf-8")
    print("Injected South-stage blackout hard reset.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True, type=Path)
    args = parser.parse_args()

    target = args.engine.resolve() / "source" / "PlayState.hx"
    if not target.is_file():
        raise FileNotFoundError(f"PlayState.hx not found: {target}")

    inject_hard_reset(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

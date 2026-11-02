# South Park Bloodbath RTX — HTML5 port

This repository builds a browser version from the Windows release package attached to the GitHub Release.

## Build it

1. Open **Settings → Pages** and set **Build and deployment → Source** to **GitHub Actions**.
2. Open **Actions → Build South Park Bloodbath HTML5**.
3. Choose **Run workflow**, keep the release tag as `v1.0.0`, and start it.
4. When both jobs succeed, open:

   https://redev325.github.io/Sthpbbfnf/

The workflow downloads `fnf.bloodbath.RTX.fix.zip`, checks out Psych Engine 0.6.3, prepares the packaged mod resources, compiles the HTML5 target, and deploys the result to GitHub Pages.

## Current port limitations

- The supplied ZIP is a Windows Psych Engine 0.6.3 runtime plus mod resources, not the original Haxe source project or a prebuilt HTML5 export.
- The first browser build embeds the Bloodbath chart, characters, images, stage data, and instrumental track.
- The release does not include a vocal track, so this port sets the chart to instrumental-only.
- Psych Engine 0.6.3 disables its LuaJIT mod scripting on HTML5. The mod's 222 Lua files, custom stage choreography, shader effects, and custom chart events are not fully ported by this initial build.
- A successful compile/deploy is not the same as a fully verified playthrough. Test the browser build and inspect the Actions log before treating it as complete.

The Windows executable, Windows DLLs, ReShade files, and other desktop-only binaries are not copied into the web build.

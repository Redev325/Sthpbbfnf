#!/usr/bin/env python3
"""Add an unobtrusive runtime error panel to the built Bloodbath HTML5 page.

The panel remains hidden during normal play. It appears only when the browser
reports a JavaScript error, an unhandled promise rejection, or a lost WebGL
context, so a rendering failure can be distinguished from a black overlay.
"""
from __future__ import annotations

import argparse
from pathlib import Path


MARKER = "<!-- BLOODBATH_RUNTIME_DIAGNOSTICS -->"
INJECTION = r"""
<!-- BLOODBATH_RUNTIME_DIAGNOSTICS -->
<script>
(() => {
  if (window.__bloodbathDiagnosticsInstalled) return;
  window.__bloodbathDiagnosticsInstalled = true;

  const panel = document.createElement("section");
  panel.setAttribute("role", "alert");
  panel.setAttribute("aria-live", "assertive");
  panel.style.cssText = [
    "position:fixed",
    "left:12px",
    "bottom:12px",
    "z-index:2147483647",
    "display:none",
    "width:min(620px,calc(100vw - 24px))",
    "max-height:38vh",
    "overflow:hidden",
    "box-sizing:border-box",
    "padding:10px 12px",
    "border:1px solid #d97757",
    "border-radius:8px",
    "background:rgba(18,18,22,.96)",
    "color:#f4f4f5",
    "font:12px/1.45 monospace",
    "box-shadow:0 4px 24px rgba(0,0,0,.55)"
  ].join(";");

  const header = document.createElement("div");
  header.style.cssText = "display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:7px";

  const heading = document.createElement("strong");
  heading.textContent = "BLOODBATH RUNTIME DIAGNOSTIC";
  heading.style.cssText = "color:#ffb08a;font-size:12px";

  const dismiss = document.createElement("button");
  dismiss.type = "button";
  dismiss.textContent = "Dismiss";
  dismiss.style.cssText = "border:1px solid #666;border-radius:4px;padding:3px 8px;background:#29292f;color:#fff;font:inherit;cursor:pointer";
  dismiss.addEventListener("click", () => { panel.style.display = "none"; });

  const message = document.createElement("pre");
  message.style.cssText = "margin:0;max-height:calc(38vh - 48px);overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;color:#e5e7eb";

  header.append(heading, dismiss);
  panel.append(header, message);
  document.documentElement.appendChild(panel);

  function show(title, detail) {
    heading.textContent = "BLOODBATH RUNTIME DIAGNOSTIC — " + title;
    message.textContent = String(detail || "No additional details were supplied.");
    panel.style.display = "block";
  }

  window.addEventListener("error", (event) => {
    const location = event.filename
      ? event.filename + ":" + (event.lineno || 0) + ":" + (event.colno || 0)
      : "source location unavailable";
    const stack = event.error && event.error.stack ? "\n" + event.error.stack : "";
    show("JAVASCRIPT ERROR", (event.message || "Unknown script error") + "\n" + location + stack);
  }, true);

  window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason;
    const detail = reason && reason.stack ? reason.stack : String(reason);
    show("UNHANDLED PROMISE", detail);
  });

  const watchedCanvases = new WeakSet();
  function watchCanvas(canvas) {
    if (watchedCanvases.has(canvas)) return;
    watchedCanvases.add(canvas);

    canvas.addEventListener("webglcontextlost", (event) => {
      // Lime also prevents the default action so the browser may restore it.
      if (event.cancelable) event.preventDefault();
      show(
        "WEBGL CONTEXT LOST",
        "The browser stopped the game's graphics context while audio may continue. " +
        "This points to a GPU/texture-rendering failure rather than a chart or audio-file error.\n" +
        "Browser status: " + (event.statusMessage || "not provided") + "\n" +
        "Try reloading the game after closing other graphics-heavy tabs."
      );
    });

    canvas.addEventListener("webglcontextrestored", () => {
      show(
        "WEBGL CONTEXT RESTORED",
        "The browser restored graphics resources. If the game picture does not resume, reload the song."
      );
    });
  }

  function scanCanvases() {
    document.querySelectorAll("canvas").forEach(watchCanvas);
  }

  scanCanvases();
  const observer = new MutationObserver(scanCanvases);
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
</script>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", required=True, type=Path)
    args = parser.parse_args()

    html_file = args.html.resolve()
    if not html_file.is_file():
        raise FileNotFoundError(f"HTML5 entry page not found: {html_file}")

    html = html_file.read_text(encoding="utf-8")
    if MARKER in html:
        print("Bloodbath runtime diagnostics are already installed.")
        return 0

    if "</body>" not in html.lower():
        raise RuntimeError("Could not find the closing body tag in the HTML5 entry page.")

    closing = html.lower().rfind("</body>")
    html = html[:closing] + INJECTION + "\n" + html[closing:]
    html_file.write_text(html, encoding="utf-8")
    print("Installed hidden-unless-needed JavaScript and WebGL diagnostics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

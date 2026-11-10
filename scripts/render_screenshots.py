"""Regenerate the README screenshots from the offline demo (no API calls).

    python scripts/render_screenshots.py

Writes light and dark SVGs of the first screen and of a full demo turn to
assets/brand/. A neutral workspace path keeps local paths out of the images.
"""

from pathlib import Path

from repopilot.cli.demo import run_demo, run_first_screen

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "assets" / "brand"
WORKSPACE = Path("~/projects/RepoPilot")


def main():
    for theme, suffix in (("light", ""), ("dark", "-dark")):
        run_first_screen(WORKSPACE, BRAND / f"client-{theme}.svg", theme=theme,
                         title=f"RepoPilot · {theme} · first screen")
        run_demo(WORKSPACE, BRAND / f"terminal-preview{suffix}.svg", theme=theme,
                 title=f"RepoPilot · {theme} · offline demo turn")
    print(f"wrote 4 screenshots to {BRAND.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()

"""Pixel-art airliner seen from above: swept wings, engine pods, tailplane.

Two pixel rows share one terminal row through half-block glyphs, so each
pixel is roughly square and the plane keeps its proportions in a terminal.
"""

from rich.text import Text


# B body · L lit edge · D shaded edge · E engine pod · W windscreen
ICON_PIXELS = (
    ".......DBL...........",
    ".......DBBBL.........",
    ".D......DBBBLEE......",
    ".DBB....DBBBBBL......",
    "..LLLLLLLLLLLLLLWWL..",
    "DBBBBBBBBBBBBBBBBBWWB",
    "DBBBBBBBBBBBBBBBBBWWB",
    "..DDDDDDDDDDDDDDWWD..",
    ".DBB....DBBBBBL......",
    ".D......DBBBLEE......",
    ".......DBBBL.........",
    ".......DBL...........",
)
ICON_WIDTH = len(ICON_PIXELS[0])

# None leaves the pixel empty: without true colour the windscreen stays a gap
# so the cockpit still reads against the solid fuselage.
PIXEL_COLORS = {
    "light": {"B": "#1d4ed8", "L": "#3b82f6", "D": "#1e3a8a", "E": "#172554", "W": "#0b1640"},
    "dark": {"B": "#93b7ff", "L": "#cfe0ff", "D": "#5b84e0", "E": "#3a5bb8", "W": "#16254a"},
    # Unknown background: ANSI names, so the terminal theme picks the shades.
    "native": {"B": "bright_blue", "L": "bright_cyan", "D": "blue", "E": "blue", "W": None},
}
MONOCHROME = {"B": "default", "L": "default", "D": "default", "E": "default", "W": None}


def icon_mark(palette=None):
    """Render the plane; palette None draws it in the terminal foreground."""
    colors = PIXEL_COLORS.get(getattr(palette, "name", None), MONOCHROME)

    def color(pixel):
        return None if pixel == "." else colors[pixel]

    icon = Text(no_wrap=True)
    for index in range(0, len(ICON_PIXELS), 2):
        if index:
            icon.append("\n")
        upper, lower = ICON_PIXELS[index], ICON_PIXELS[index + 1]
        for top, bottom in zip(map(color, upper), map(color, lower)):
            if top is None and bottom is None:
                icon.append(" ")
            elif bottom is None:
                icon.append("▀", _style(top))
            elif top is None:
                icon.append("▄", _style(bottom))
            elif top == bottom:
                # Paint the background too so font metrics cannot cut seams.
                icon.append("█", _style(top, top))
            else:
                icon.append("▀", _style(top, bottom))
    return icon


def _style(fg, bg=None):
    if fg == "default":
        return "brand"
    return f"{fg} on {bg}" if bg else fg

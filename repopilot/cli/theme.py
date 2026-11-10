"""Terminal-aware palettes.

Unknown backgrounds keep the terminal's own foreground and use the 16 ANSI
colour names for accents, so the terminal's theme picks shades that suit
its background instead of RepoPilot guessing light or dark.
"""

import os
from dataclasses import dataclass

from rich.theme import Theme


@dataclass(frozen=True)
class Palette:
    name: str
    foreground: str
    secondary: str
    accent: str
    border: str
    surface: str
    background: str
    success: str
    error: str
    syntax: str

    def rich_theme(self):
        return Theme({
            "brand": "bold " + self.accent,
            "brand.pixel": "bold " + self.accent + (" on " + self.accent if self.accent != "default" else ""),
            "text": self.foreground, "heading": "bold " + self.foreground,
            # Without a known background, dim the terminal foreground rather
            # than guess a grey that may vanish on light or dark themes.
            "muted": "dim" if self.secondary == "default" else self.secondary,
            "border": self.border,
            "accent": "bold " + self.accent,
            "success": self.success, "error": self.error,
            # Inline code reads as a soft chip instead of heavy bold text.
            "markdown.code": self.accent + (" on " + self.surface if self.surface != "default" else ""),
            "markdown.h1": "bold underline " + self.accent,
            "markdown.h2": "bold " + self.accent,
            "markdown.h3": "bold " + self.foreground,
            "markdown.h4": "bold " + self.foreground,
            "markdown.h5": "italic " + self.foreground,
            "markdown.h6": "italic " + self.foreground,
            "markdown.item.bullet": self.accent,
            "markdown.item.number": self.accent,
            "markdown.block_quote": "dim" if self.secondary == "default" else self.secondary,
            "markdown.hr": self.border,
            "markdown.link": "underline " + self.accent,
            "markdown.link_url": "underline " + self.accent,
            "markdown.table.border": "dim" if self.secondary == "default" else self.secondary,
            "markdown.table.header": "bold " + self.foreground,
            **self.diff_styles(),
        })

    def diff_styles(self):
        """Whole-line red/green backgrounds, with stronger tints on changed words."""
        add, add_em, delete, delete_em = DIFF_BACKGROUNDS[self.name]
        if self.name == "native":
            # ANSI backgrounds vary by terminal theme: pin a readable foreground.
            return {"diff.add": "black on " + add, "diff.add.em": "bold black on " + add_em,
                    "diff.del": "bright_white on " + delete, "diff.del.em": "bold bright_white on " + delete_em,
                    "diff.gutter": "dim"}
        return {"diff.add": f"{self.foreground} on {add}", "diff.add.em": f"{self.foreground} on {add_em}",
                "diff.del": f"{self.foreground} on {delete}", "diff.del.em": f"{self.foreground} on {delete_em}",
                "diff.gutter": self.secondary}

    def prompt_styles(self):
        pt = _prompt_color
        base = f"fg:{pt(self.foreground)} bg:{pt(self.surface)}"
        return {
            "": f"fg:{pt(self.foreground)} bg:default",
            "composer": base,
            "frame.border": f"fg:{pt(self.border)} bg:default",
            "frame.label": f"bold fg:{pt(self.accent)} bg:default",
            "prompt": f"bold fg:{pt(self.accent)}",
            "placeholder": f"fg:{pt(self.accent)}",
            "input-hint": f"fg:{pt(self.secondary)} bg:default",
            # "!" terminal mode: the composer turns green with a $ prompt.
            "frame.border.shell": f"fg:{pt(self.success)} bg:default",
            "frame.label.shell": f"bold fg:{pt(self.success)} bg:default",
            "prompt.shell": f"bold fg:{pt(self.success)}",
            "input-hint.shell": f"fg:{pt(self.success)} bg:default",
            "completion-menu.completion": base,
            "completion-menu.meta.completion": base,
            "completion-menu.completion.current": "bold fg:#ffffff bg:#1d4ed8",
            "completion-menu.meta.completion.current": "fg:#ffffff bg:#1d4ed8",
            "auto-suggestion": f"italic fg:{pt(self.secondary)}",
            "search-toolbar": base,
        }


# Thinking shimmer: (label colour, highlight band). Light themes brighten the
# accent towards sky blue; dark themes lift it to white.
SHIMMER_COLORS = {
    "light": ("#1d4ed8", "#7cb7ff"),
    "dark": ("#7fa6f5", "#ffffff"),
}

# Terminal windows: (title bar, body). None draws a bordered box instead,
# since an ANSI-only terminal has no reliable light grey.
TERMINAL_COLORS = {
    "light": ("#e5e7eb", "#f3f4f6"),
    "dark": ("#232833", "#161a22"),
}


# added line, added word, removed line, removed word
DIFF_BACKGROUNDS = {
    "light": ("#dafbe1", "#aceebb", "#ffebe9", "#ffc1c0"),
    "dark": ("#123d26", "#1f6b3c", "#4a1a21", "#82293a"),
    "native": ("green", "bright_green", "red", "bright_red"),
}


PALETTES = {
    "light": Palette("light", "#172033", "#475569", "#1d4ed8", "#1d4ed8",
                     "#eff6ff", "#ffffff", "#166534", "#b91c1c", "friendly"),
    "dark": Palette("dark", "#f1f5f9", "#b6c2d2", "#93b7ff", "#93b7ff",
                    "#101e36", "#0c111b", "#86efac", "#fda4af", "monokai"),
    "native": Palette("native", "default", "default", "bright_blue", "default",
                      "default", "default", "green", "red", "monokai"),
}


def _prompt_color(color):
    """Translate Rich colour names to prompt_toolkit's ANSI names."""
    if color.startswith("#") or color == "default":
        return color
    return "ansi" + color.replace("_", "")


def palette_for(mode="auto", environ=None):
    if mode not in ("auto", "light", "dark"):
        raise ValueError("Theme must be auto, light or dark")
    if mode != "auto":
        return PALETTES[mode]
    environ = os.environ if environ is None else environ
    background = environ.get("COLORFGBG", "").split(";")[-1]
    if background.isdigit() and 0 <= int(background) <= 15:
        return PALETTES["light" if int(background) in (7, 15) else "dark"]
    # Do not infer a dark background from TERM or truecolor support.
    return PALETTES["native"]

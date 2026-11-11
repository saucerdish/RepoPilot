import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from repopilot.cli.config import save_theme
from repopilot.cli.theme import PALETTES, palette_for


def luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    return sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + .05) / (low + .05)


class ThemeTests(unittest.TestCase):
    def test_unknown_background_uses_native_foreground(self):
        palette = palette_for("auto", {"TERM": "xterm-256color", "COLORTERM": "truecolor"})
        self.assertEqual(palette.name, "native")
        self.assertEqual(palette.foreground, "default")
        self.assertEqual(palette.secondary, "default")
        self.assertEqual(palette.border, "default")
        # Accents use ANSI names so the terminal theme chooses readable shades.
        self.assertEqual(palette.accent, "bright_blue")
        self.assertEqual(palette.rich_theme().styles["muted"].dim, True)
        styles = palette.prompt_styles()
        self.assertEqual(styles["prompt"], "bold fg:ansibrightblue")
        from prompt_toolkit.styles import Style
        Style.from_dict(styles)

    def test_icon_uses_palette_colors_and_plain_monochrome(self):
        from rich.console import Console
        from repopilot.cli.brand import ICON_PIXELS, ICON_WIDTH, icon_mark
        for palette in (*PALETTES.values(), None):
            with self.subTest(palette=getattr(palette, "name", None)):
                icon = icon_mark(palette)
                rows = icon.plain.split("\n")
                self.assertEqual(len(rows), len(ICON_PIXELS) // 2)
                self.assertTrue(all(len(row) == ICON_WIDTH for row in rows))
                Console(file=open(os.devnull, "w"), force_terminal=True).print(icon)
        self.assertNotIn("bright_blue", str(icon_mark(None).spans))

    def test_known_background_and_explicit_selection(self):
        self.assertEqual(palette_for("auto", {"COLORFGBG": "0;15"}).name, "light")
        self.assertEqual(palette_for("auto", {"COLORFGBG": "15;0"}).name, "dark")
        self.assertEqual(palette_for("light", {"COLORFGBG": "15;0"}).name, "light")

    def test_text_and_borders_have_sufficient_contrast(self):
        for name in ("light", "dark"):
            palette = PALETTES[name]
            for background in (palette.background, palette.surface):
                for field in ("foreground", "secondary", "accent", "success", "error"):
                    with self.subTest(theme=name, background=background, field=field):
                        self.assertGreaterEqual(contrast(getattr(palette, field), background), 4.5)
                self.assertGreaterEqual(contrast(palette.border, background), 3)

    def test_thinking_shimmer_moves_and_timer_counts(self):
        from repopilot.cli.ui import Elapsed, format_elapsed
        now = [0.0]
        working = Elapsed("Thinking", "dark", clock=lambda: now[0])

        def brightest():
            text = working.__rich__()
            label = [span for span in text.spans if span.end <= len("Thinking…")]
            return max(label, key=lambda span: str(span.style)).start, text.plain

        first, plain = brightest()
        self.assertTrue(plain.startswith("Thinking…"))
        self.assertNotIn("0s", plain)
        now[0] = .4
        self.assertNotEqual(brightest()[0], first)
        now[0] = 75
        self.assertIn("1m 15s", working.__rich__().plain)
        self.assertEqual(format_elapsed(9.7), "9s")
        # ANSI-only terminals still get a band, as bold foreground.
        tick = [0.0]
        native = Elapsed("Thinking", "native", clock=lambda: tick[0])
        tick[0] = .4
        self.assertTrue(any(str(span.style) == "bold" for span in native.__rich__().spans))

    def test_theme_save_preserves_existing_provider_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "repopilot" / "config.json"
            config.parent.mkdir()
            config.write_text(json.dumps({"model": "existing-model", "env_file": "/existing/.env"}))
            with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(root)}):
                save_theme("light")
            saved = json.loads(config.read_text())
            self.assertEqual(saved, {"model": "existing-model", "env_file": "/existing/.env", "theme": "light"})

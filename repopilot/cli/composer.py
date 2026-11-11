"""Put the actual editable PromptSession buffer inside a visible frame."""

from prompt_toolkit.filters import is_done
from prompt_toolkit.data_structures import Point
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.layout import ConditionalContainer, DynamicContainer, HSplit, VSplit, Window
from prompt_toolkit.layout.containers import to_container
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.layout.utils import explode_text_fragments
from prompt_toolkit.utils import get_cwidth


class HideLeadingBang(Processor):
    """Hide the "!" that switches the composer to terminal mode.

    The character stays in the buffer (Backspace leaves terminal mode); the
    green frame and "$" prompt already say which mode is active.
    """

    def apply_transformation(self, ti):
        if ti.lineno != 0 or not ti.document.text.startswith("!"):
            return Transformation(ti.fragments)
        return Transformation(explode_text_fragments(ti.fragments)[1:],
                              source_to_display=lambda i: max(0, i - 1),
                              display_to_source=lambda i: i + 1)


class RoundedFrame:
    """Rounded box with a flush-left title, e.g. "╭─ Message ───╮".

    prompt_toolkit's Frame wraps titles in hard-coded "|" bars; this keeps
    the composer in line with the rounded Rich panels printed above it.
    title and border may be callables so the frame can react to the draft.
    """

    def __init__(self, body, title="", border="class:frame.border", style=""):
        self.body, self.title, self.border = body, title, border

        def border_style():
            return self.border() if callable(self.border) else self.border

        def fill(char, width=None):
            return Window(char=char, width=width, height=1, style=border_style)

        def label():
            title = self.title() if callable(self.title) else self.title
            return to_formatted_text(title) if title else []

        self.container = HSplit([
            VSplit([fill("╭", 1), fill("─", 1),
                    Window(FormattedTextControl(label), dont_extend_width=True, height=1,
                           style="class:frame.label"),
                    fill("─"), fill("╮", 1)], height=1),
            VSplit([Window(char="│", width=1, style=border_style),
                    DynamicContainer(lambda: self.body),
                    Window(char="│", width=1, style=border_style)], padding=0),
            VSplit([fill("╰", 1), fill("─"), fill("╯", 1)], height=1),
        ], style="class:frame " + style)

    def __pt_container__(self):
        return self.container


def _handle_resize(session):
    app = session.app
    original_resize = app._on_resize

    def resize():
        renderer = app.renderer
        previous = renderer._last_size
        screen = renderer._last_screen
        columns = max(1, session.output.get_size().columns)
        if previous and screen and columns < previous.columns:
            # Inline output reflows before SIGWINCH is delivered. Erase from
            # the reflowed cursor position, including wrapped border rows.
            cursor = renderer._cursor_pos
            y = cursor.y
            for row in range(screen.height):
                cells = screen.data_buffer.get(row, {})
                width = max((x + cell.width for x, cell in cells.items()
                             if cell.char != " " or renderer._style_string_has_style[cell.style]), default=0)
                y += max(0, (width - 1) // columns)
            renderer._cursor_pos = Point(x=min(cursor.x, columns - 1), y=y)
        original_resize()

    app._on_resize = resize


def frame_session(session, footer, title=" Message ", border="class:frame.border"):
    # Keep PromptSession's buffer, search toolbar and completion floats intact.
    # Only replace the input's container, so all editing/history bindings remain.
    root = session.app.layout.container
    input_window = session.app.layout.current_window
    def input_height():
        width = max(12, session.output.get_size().columns - 5)
        rows = sum(max(1, (get_cwidth(line) + 3 + width - 1) // width)
                   for line in session.default_buffer.document.lines)
        return Dimension.exact(min(9, max(4 if session.default_buffer.complete_state else 1, rows)))
    input_window.height = input_height
    editable = root.children[0]
    # Hidden prompt fragments can report max_width=0. The original vertical
    # layout ignored that value; Frame's horizontal borders must not use it.
    editable.content.width = lambda: Dimension.exact(max(1, session.output.get_size().columns - 2))
    frame = RoundedFrame(editable, title=title, border=border, style="class:composer")
    root.children[0] = to_container(frame)
    # PromptSession's default bottom toolbar requires a CPR-capable terminal.
    # This local footer also renders in IDE terminals that do not answer CPR.
    root.children[-1] = ConditionalContainer(
        Window(FormattedTextControl(footer), height=1, style="class:input-hint",
               wrap_lines=False, dont_extend_height=True),
        filter=~is_done,
    )
    session.composer_frame = frame
    _handle_resize(session)
    return session

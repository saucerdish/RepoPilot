"""Unified diffs drawn with whole-line red/green backgrounds and line numbers."""

import difflib
import re

from rich.table import Table
from rich.text import Text


HUNK = re.compile(r"^@@ -(\d+)((?:,\d+)?) \+(\d+)((?:,\d+)?) @@")
TOKEN = re.compile(r"\w+|\s+|[^\w\s]")
FILE_HEADER = ("diff --git", "index ", "--- ", "+++ ", "new file", "deleted file", "similarity ", "rename ")


def shift_hunk(line, offset):
    """Move a unified-diff hunk header down by offset lines."""
    match = HUNK.match(line) if offset else None
    if not match:
        return line
    old, old_count, new, new_count = match.groups()
    return (f"@@ -{int(old) + offset}{old_count} +{int(new) + offset}{new_count} @@"
            + line[match.end():])


def _count(value):
    return int(value[1:]) if value else 1


class DiffView:
    """Renderable unified diff in the style of a code-review tool."""

    def __init__(self, source, compact=False):
        self.lines = source.splitlines()
        # compact: the caller already names the file, so drop file headers
        # and show hunk breaks as a quiet "⋯" instead of the @@ header.
        self.compact = compact

    def _classify(self):
        """Yield (kind, line_number, text) where kind is add/del/ctx/hunk/file/note."""
        old = new = old_left = new_left = 0
        for line in self.lines:
            match = HUNK.match(line)
            if match:
                old, new = int(match.group(1)), int(match.group(3))
                old_left, new_left = _count(match.group(2)), _count(match.group(4))
                yield "hunk", None, line
            elif (old_left > 0 or new_left > 0) and not line.startswith("\\"):
                # Inside a hunk the header counts say which lines are body.
                if line.startswith("+"):
                    yield "add", new, line
                    new, new_left = new + 1, new_left - 1
                elif line.startswith("-"):
                    yield "del", old, line
                    old, old_left = old + 1, old_left - 1
                else:
                    yield "ctx", new, line
                    old, new, old_left, new_left = old + 1, new + 1, old_left - 1, new_left - 1
            # Outside hunks, or in hand-written snippets whose counts are off,
            # fall back to the line prefix without line numbers.
            elif line.startswith("\\"):
                yield "note", None, line
            elif line.startswith(FILE_HEADER):
                yield "file", None, line
            elif line.startswith("+"):
                yield "add", None, line
            elif line.startswith("-"):
                yield "del", None, line
            else:
                yield "ctx", None, line

    @staticmethod
    def _emphasis(rows):
        """Mark changed characters when a removed block is rewritten in place."""
        spans = {}
        index = 0
        while index < len(rows):
            if rows[index][0] != "del":
                index += 1
                continue
            start = index
            while index < len(rows) and rows[index][0] == "del":
                index += 1
            middle = index
            while index < len(rows) and rows[index][0] == "add":
                index += 1
            if index - middle != middle - start:
                continue
            for removed, added in zip(range(start, middle), range(middle, index)):
                # Compare whole words so highlights mark edits, not letters.
                a, b = TOKEN.findall(rows[removed][2][1:]), TOKEN.findall(rows[added][2][1:])
                matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
                if matcher.ratio() < 0.5:
                    continue
                for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                    if tag == "equal":
                        continue
                    for row, tokens, lo, hi in ((removed, a, i1, i2), (added, b, j1, j2)):
                        if hi > lo:
                            begin = 1 + sum(map(len, tokens[:lo]))
                            spans.setdefault(row, []).append((begin, begin + sum(map(len, tokens[lo:hi]))))
        return spans

    def __rich_console__(self, console, options):
        rows = list(self._classify())
        if self.compact:
            rows = [row for row in rows if row[0] != "file"]
            if rows and rows[0][0] == "hunk":
                rows = rows[1:]
            rows = [("gap", None, "⋯") if kind == "hunk" else (kind, number, line)
                    for kind, number, line in rows]
        spans = self._emphasis(rows)
        numbered = any(number for _, number, _ in rows)
        width = max((len(str(number)) for _, number, _ in rows if number), default=0)
        table = Table.grid(expand=True, padding=(0, 1) if numbered else 0)
        if numbered:
            table.add_column(justify="right", no_wrap=True, width=width)
        table.add_column(ratio=1)
        for index, (kind, number, line) in enumerate(rows):
            style = {"add": "diff.add", "del": "diff.del", "file": "heading",
                     "hunk": "muted", "note": "muted", "gap": "muted"}.get(kind, "text")
            text = Text(line)
            for start, end in spans.get(index, ()):
                text.stylize(style + ".em", start, end)
            cells = [text]
            if numbered:
                cells.insert(0, Text(str(number) if number else "", style="diff.gutter"))
            table.add_row(*cells, style=style if kind in ("add", "del") else None)
        yield table


def change_counts(source):
    """(+added, -removed) body lines of a unified diff."""
    rows = list(DiffView(source)._classify())
    return sum(kind == "add" for kind, _, _ in rows), sum(kind == "del" for kind, _, _ in rows)

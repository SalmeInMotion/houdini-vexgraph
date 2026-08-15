"""Turn a model's answer into something readable.

Models write Markdown whether or not you ask them to - headings, bullet lists,
numbered steps, fenced code. Escaping all of it and joining the lines produced
one unbroken wall of text with literal asterisks in it, which is the least
readable form the same words could possibly take.

This is not a general Markdown implementation and should not become one. It
covers what answers about VEX actually contain, and anything it does not
recognise survives as ordinary text rather than disappearing.
"""

from __future__ import annotations

import html
import re

from .. import help as vexhelp

_FENCE_RE = re.compile(r"^\s*```+\s*(\w+)?\s*$")
_BULLET_RE = re.compile(r"^\s*[-*+•]\s+(.*)$")
_NUMBER_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")
_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.*?)\s*#*\s*$")
_BOLD_RE = re.compile(r"\*\*(\S(?:[^*]*\S)?)\*\*")
_ITALIC_RE = re.compile(r"(?<![\w*])\*(\S(?:[^*\n]*\S)?)\*(?![\w*])")
_CODE_RE = re.compile(r"`([^`\n]+)`")

HEADING_COLOUR = "#d8d8d8"
CODE_BG = "#1b1b1b"


def inline(text: str) -> str:
    """Escaped text with `code`, **bold** and *italic* turned into markup.

    Code spans are handled first and their contents held aside, so an asterisk
    inside a snippet of VEX is not read as emphasis.
    """
    parts: list[str] = []
    last = 0
    for match in _CODE_RE.finditer(text):
        parts.append(_emphasis(html.escape(text[last:match.start()])))
        parts.append(f"<code style='color:#c8c8a0'>"
                     f"{vexhelp.highlight(match.group(1))}</code>")
        last = match.end()
    parts.append(_emphasis(html.escape(text[last:])))
    return "".join(parts)


def _emphasis(escaped: str) -> str:
    escaped = _BOLD_RE.sub(r"<b>\1</b>", escaped)
    return _ITALIC_RE.sub(r"<i>\1</i>", escaped)


def to_html(text: str) -> str:
    """One model answer, rendered as blocks rather than a single paragraph."""
    out: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []
    ordered = False
    code: list[str] | None = None

    def flush_paragraph() -> None:
        if paragraph:
            out.append(f"<p style='margin:6px 0'>"
                       f"{inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        if items:
            tag = "ol" if ordered else "ul"
            entries = "".join(f"<li style='margin:2px 0'>{inline(i)}</li>"
                              for i in items)
            out.append(f"<{tag} style='margin:6px 0 6px 18px; "
                       f"-qt-list-indent:1'>{entries}</{tag}>")
            items.clear()

    def flush() -> None:
        flush_paragraph()
        flush_list()

    for line in text.splitlines():
        fence = _FENCE_RE.match(line)
        if fence:
            if code is None:
                flush()
                code = []
            else:
                body = "\n".join(code)
                out.append(f"<pre style='background:{CODE_BG}; padding:8px; "
                           f"margin:6px 0'><code>"
                           f"{vexhelp.highlight(body)}</code></pre>")
                code = None
            continue
        if code is not None:
            code.append(line)
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            out.append(f"<p style='margin:10px 0 2px'>"
                       f"<b style='color:{HEADING_COLOUR}'>"
                       f"{inline(heading.group(2))}</b></p>")
            continue

        bullet = _BULLET_RE.match(line)
        number = _NUMBER_RE.match(line)
        if bullet or number:
            flush_paragraph()
            wanted = bool(number)
            if items and wanted != ordered:
                flush_list()
            ordered = wanted
            items.append(number.group(2) if number else bullet.group(1))
            continue

        if not line.strip():
            flush()
            continue

        # A line inside a list that is not itself an item continues that item;
        # models wrap long bullets and each wrapped line is not a new point.
        if items:
            items[-1] += " " + line.strip()
            continue
        paragraph.append(line.strip())

    if code is not None:                 # a fence the model never closed
        body = "\n".join(code)
        out.append(f"<pre style='background:{CODE_BG}; padding:8px; "
                   f"margin:6px 0'><code>{vexhelp.highlight(body)}</code></pre>")
    flush()
    return "\n".join(out)

"""Houdini's own VEX documentation, read out of the local install.

The help that ships with Houdini is the same text as the website, so there is
nothing to scrape and nothing to keep in sync: `$HFS/houdini/help/vex.zip` holds
one page per function, in SideFX's wiki markup. This module finds it, parses the
parts worth showing, and renders them as the HTML the detail pane displays.

Everything degrades to nothing rather than failing. A node with no page, a
Houdini that is not installed, a page in a format this does not recognise - each
of those just means less help is shown, never an error in the middle of editing.
"""

from __future__ import annotations

import functools
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

DOCS_BASE = "https://www.sidefx.com/docs/houdini"
DOCS_URL = DOCS_BASE + "/vex/functions/{name}.html"


@dataclass
class Page:
    """One VEX function's documentation, already broken into parts.

    The body is a list of blocks rather than a string because the code samples
    are interleaved with the prose that introduces them - `foreach` explains a
    form and then shows it. Flattening to text loses the samples, which are the
    most useful part of the page.
    """

    name: str
    summary: str = ""
    usages: list[tuple[str, str]] = field(default_factory=list)  # signature, note
    blocks: list[tuple[str, str]] = field(default_factory=list)  # kind, text
    related: list[tuple[str, str]] = field(default_factory=list)  # label, url

    @property
    def examples(self) -> list[str]:
        return [text for kind, text in self.blocks if kind == "code"]

    @property
    def url(self) -> str:
        return DOCS_URL.format(name=self.name)


def houdini_help_archive() -> Path | None:
    """The vex.zip of the newest install, or whichever $HFS is already set.

    Inside Houdini $HFS is always right, so it wins; outside, guess the newest
    install the same way the node generator does.
    """
    hfs = os.environ.get("HFS")
    if hfs:
        candidate = Path(hfs) / "houdini" / "help" / "vex.zip"
        if candidate.is_file():
            return candidate
    base = Path(r"C:\Program Files\Side Effects Software")
    if not base.is_dir():
        return None
    installs = sorted(
        (p for p in base.glob("Houdini *")
         if (p / "houdini" / "help" / "vex.zip").is_file()),
        key=lambda p: [int(x) for x in re.findall(r"\d+", p.name)])
    if not installs:
        return None
    return installs[-1] / "houdini" / "help" / "vex.zip"


@functools.lru_cache(maxsize=1)
def _archive() -> zipfile.ZipFile | None:
    path = houdini_help_archive()
    if path is None:
        return None
    try:
        return zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        return None


@functools.lru_cache(maxsize=512)
def page(function: str) -> Page | None:
    """The parsed help page for one VEX function, if Houdini ships one."""
    archive = _archive()
    if archive is None or not function:
        return None
    try:
        raw = archive.read(f"functions/{function}.txt").decode("utf8", "replace")
    except KeyError:
        return None
    return _parse(function, raw)


# --------------------------------------------------------------------- parsing

_SUMMARY_RE = re.compile(r'"""(.*?)"""', re.S)
_USAGE_RE = re.compile(r"^:usage:\s*`(.+?)`\s*$", re.M)
_CODE_RE = re.compile(r"\{\{\{\s*(?:#!\w+\s*)?(.*?)\}\}\}", re.S)


def _parse(name: str, raw: str) -> Page:
    result = Page(name=name)

    match = _SUMMARY_RE.search(raw)
    if match:
        result.summary = " ".join(match.group(1).split())

    sections = _sections(raw)
    head = sections.get("", "")

    # A run of `:usage:` lines is followed by the prose explaining them. That
    # prose belongs to the usage only when another usage comes after it;
    # otherwise it is the page's body, and attaching it to the last overload
    # would both mislabel it and print it twice.
    positions = [(m.start(), m.end(), m.group(1)) for m in _USAGE_RE.finditer(head)]
    for index, (_, end, signature) in enumerate(positions):
        if index + 1 < len(positions):
            note = _prose(head[end:positions[index + 1][0]])
        else:
            note = ""
        result.usages.append((signature.strip(), note))

    remainder = head[positions[-1][1]:] if positions else head
    remainder = _SUMMARY_RE.sub("", remainder)
    result.blocks = _blocks(remainder)
    result.blocks += _blocks(sections.get("examples", ""))

    # Two link forms: `[Vex:len]` for a sibling function, `[/vex/arrays]` for a
    # topic page. Only reading the first dropped "see also: Arrays" everywhere.
    related = sections.get("related", "")
    result.related = [(n, DOCS_URL.format(name=n))
                      for n in re.findall(r"\[Vex:(\w+)\]", related)]
    result.related += [(p.rstrip("/").rsplit("/", 1)[-1].title(),
                        f"{DOCS_BASE}/{p.strip('/')}.html")
                       for p in re.findall(r"\[(/[\w/]+)\]", related)]
    return result


def _blocks(text: str) -> list[tuple[str, str]]:
    """Split into prose and code, keeping the order they were written in."""
    out: list[tuple[str, str]] = []
    last = 0
    for match in _CODE_RE.finditer(text):
        out += _prose_blocks(text[last:match.start()])
        code = match.group(1).strip("\n")
        if code.strip():
            out.append(("code", code))
        last = match.end()
    out += _prose_blocks(text[last:])
    return out


def _prose_blocks(text: str) -> list[tuple[str, str]]:
    cleaned = _prose(text)
    out: list[tuple[str, str]] = []
    for block in re.split(r"\n\s*\n", cleaned):
        block = block.strip()
        if not block:
            continue
        # A subheading survives _prose as a short line of its own.
        if "\n" not in block and len(block) < 40 and not block.endswith("."):
            out.append(("heading", block))
        else:
            out.append(("prose", block))
    return out


def _sections(raw: str) -> dict[str, str]:
    """Split on `@name` headings; text before the first one is under ""."""
    parts: dict[str, str] = {}
    current = ""
    buffer: list[str] = []
    for line in raw.splitlines():
        heading = re.match(r"^@(\w+)", line)
        if heading:
            parts[current] = "\n".join(buffer)
            current = heading.group(1)
            buffer = []
            continue
        buffer.append(line)
    parts[current] = "\n".join(buffer)
    return parts


def _prose(text: str) -> str:
    """Strip the markup that would otherwise show up as literal punctuation."""
    text = _CODE_RE.sub("", text)
    text = re.sub(r"^\s*#\w[^\n]*$", "", text, flags=re.M)     # #type:, #group:
    text = re.sub(r"^\s*:\w[\w ]*:\s*$", "", text, flags=re.M)  # :tip: on its own
    # Wiki headings: `= append =` repeats the title, `== Simple form ==` is a
    # real subheading. Both were being printed with their equals signs.
    text = re.sub(r"^\s*=\s*[^=\n]+\s*=\s*$", "", text, flags=re.M)
    text = re.sub(r"^\s*={2,}\s*([^=\n]+?)\s*={2,}\s*$", r"\n\1\n", text, flags=re.M)
    text = re.sub(r"\[([^\]|]+)\|[^\]]+\]", r"\1", text)        # [label|target]
    text = re.sub(r"\[Vex:(\w+)\]", r"\1", text)
    text = re.sub(r"\[/?([\w/]+)\]", r"\1", text)
    text = re.sub(r"<<(.+?)>>", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ------------------------------------------------------------- highlighting

# The same colours the code pane uses, so a snippet reads the same wherever it
# appears in the tool.
_SYNTAX = (
    (re.compile(r"//[^\n]*"), "#6a9955"),
    (re.compile(r'"[^"\n]*"'), "#ce9178"),
    (re.compile(r"\b(if|else|for|foreach|while|do|break|continue|return|function)\b"),
     "#569cd6"),
    (re.compile(r"\b(int|float|vector|vector2|vector4|matrix|matrix2|matrix3"
                r"|string|dict|void)\b"), "#4ec9b0"),
    (re.compile(r"[fivpu234sd]?@\w+"), "#dcdcaa"),
    (re.compile(r"\b\d+\.?\d*\b"), "#b5cea8"),
)


def highlight(code: str) -> str:
    """VEX as coloured HTML.

    One pass that finds every match first and then rebuilds the string, rather
    than substituting rule by rule: replacing in stages would let a later rule
    match inside the colour markup an earlier one had just inserted.
    """
    spans: list[tuple[int, int, str]] = []
    for pattern, colour in _SYNTAX:
        for match in pattern.finditer(code):
            if not any(s < match.end() and match.start() < e for s, e, _ in spans):
                spans.append((match.start(), match.end(), colour))
    spans.sort()

    out: list[str] = []
    last = 0
    for start, end, colour in spans:
        out.append(_escape(code[last:start]))
        out.append(f"<span style='color:{colour}'>{_escape(code[start:end])}</span>")
        last = end
    out.append(_escape(code[last:]))
    return "".join(out)


# --------------------------------------------------------------------- display

def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")

HEADING = "#d8d8d8"
DIM = "#9a9a9a"
LINK = "#7fb2e5"
CODE_BG = "#1b1b1b"


def _rich(text: str) -> str:
    """Escaped prose with the wiki's inline markup turned into real formatting."""
    parts: list[str] = []
    last = 0
    for match in _INLINE_CODE_RE.finditer(text):
        parts.append(_emphasis(_escape(text[last:match.start()])))
        parts.append(f"<code style='color:#c8c8a0'>{highlight(match.group(1))}</code>")
        last = match.end()
    parts.append(_emphasis(_escape(text[last:])))
    return "".join(parts)


def _emphasis(escaped: str) -> str:
    """`*bold*` and `_italic_`, applied after escaping so tags survive."""
    escaped = re.sub(r"\*(\S(?:[^*\n]*\S)?)\*", r"<b>\1</b>", escaped)
    return re.sub(r"(?<![\w])_(\S(?:[^_\n]*\S)?)_(?![\w])", r"<i>\1</i>", escaped)


def _paragraphs(text: str) -> str:
    """Blank-line-separated prose, with short standalone lines read as headings."""
    return "".join(
        f"<p style='margin:10px 0 2px'><b style='color:{HEADING}'>"
        f"{_escape(block)}</b></p>" if kind == "heading"
        else f"<p style='margin:4px 0'>{_rich(block)}</p>"
        for kind, block in _prose_blocks(text))


def as_html(doc: Page, *, title: str = "") -> str:
    """Render a page for the detail pane.

    Signatures and examples are the two things worth reading closely, so they
    are syntax-coloured exactly as the code pane colours them; the prose stays
    quiet so those stand out.
    """
    out: list[str] = []
    if title:
        out.append(f"<hr><p><b style='color:{HEADING}'>{_escape(title)}</b></p>")
    if doc.summary:
        out.append(f"<p>{_rich(doc.summary)}</p>")

    if doc.usages:
        out.append(f"<p style='margin:10px 0 2px'><b style='color:{HEADING}'>"
                   f"How it is called</b></p>")
        for signature, note in doc.usages:
            out.append(f"<p style='margin:2px 0'><code>{highlight(signature)}"
                       f"</code></p>")
            if note:
                out.append(f"<div style='margin:0 0 6px 12px; color:{DIM}'>"
                           f"{_paragraphs(note)}</div>")

    for kind, text in doc.blocks:
        if kind == "code":
            out.append(f"<pre style='background:{CODE_BG}; padding:8px; "
                       f"margin:4px 0'><code>{highlight(text)}</code></pre>")
        elif kind == "heading":
            out.append(f"<p style='margin:10px 0 2px'><b style='color:{HEADING}'>"
                       f"{_escape(text)}</b></p>")
        else:
            out.append(f"<p style='margin:4px 0'>{_rich(text)}</p>")

    if doc.related:
        links = ", ".join(
            f"<a href='{url}' style='color:{LINK}'>{label}</a>"
            for label, url in doc.related)
        out.append(f"<p style='margin:10px 0 2px; color:{DIM}'>"
                   f"<b style='color:{HEADING}'>See also</b> {links}</p>")

    return "\n".join(out)

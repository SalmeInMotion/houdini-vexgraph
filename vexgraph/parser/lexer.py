"""VEX source into tokens.

Every token keeps the offsets it came from. That is not bookkeeping for error
messages — it is what makes the escape hatch possible: when a statement turns
out to be something the graph cannot express, the fallback node carries the
*original text*, byte for byte, rather than a reconstruction. Round-tripping
code you cannot represent is the difference between a parser that is useful
immediately and one that has to be finished before it is useful at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Kind(Enum):
    NUMBER = "number"
    STRING = "string"
    NAME = "name"
    ATTRIB = "attrib"        # @P, v@up, 3@xform, @opinput1_Cd
    HSCRIPT = "hscript"      # $PI, $F, $T - hscript vars Houdini expands
    OP = "op"
    PUNCT = "punct"
    KEYWORD = "keyword"
    TYPE = "type"
    END = "end"


KEYWORDS = {"if", "else", "for", "foreach", "while", "do", "break", "continue",
            "return", "function", "struct"}

TYPES = {"int", "float", "vector", "vector2", "vector4", "matrix", "matrix2",
         "matrix3", "string", "dict", "void", "bsdf"}

# Longest first: `<=` must win over `<`, `++` over `+`.
OPERATORS = (
    "->", "<<", ">>",
    "==", "!=", "<=", ">=", "&&", "||", "++", "--",
    "+=", "-=", "*=", "/=", "%=",
    "+", "-", "*", "/", "%", "=", "<", ">", "!", "?", ":", ".", "~", "^", "&", "|",
)

PUNCTUATION = "(){}[];,"

NUMBER_RE = re.compile(r"\d+\.\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?"
                       r"|\d+(?:[eE][-+]?\d+)?")
NAME_RE = re.compile(r"[A-Za-z_]\w*")
# A type prefix is a single character; `@` on its own means "figure the type out
# from the attribute's name", which is what a wrangle does too. The optional
# `[]` between the two is how an array attribute is spelled - `i[]@hits` is an
# int array - and not reading it stopped every snippet that uses one.
ATTRIB_RE = re.compile(r"([fivpu234sd]?)(\[\])?@(\w+)")


class LexError(SyntaxError):
    def __init__(self, message: str, offset: int, line: int):
        super().__init__(f"line {line}: {message}")
        self.offset = offset
        self.line = line


@dataclass(frozen=True)
class Token:
    kind: Kind
    text: str
    start: int
    end: int
    line: int
    # Attribute tokens only: the `v` of `v@up`, empty when the name carries it.
    prefix: str = ""
    # Attribute tokens only: `i[]@hits` binds an array rather than one value.
    is_array: bool = False

    def __repr__(self) -> str:
        return f"{self.kind.value}({self.text!r})"

    def is_(self, kind: Kind, text: str | None = None) -> bool:
        return self.kind is kind and (text is None or self.text == text)


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    line = 1
    length = len(source)

    while index < length:
        char = source[index]

        if char == "\n":
            line += 1
            index += 1
            continue
        if char.isspace():
            index += 1
            continue

        # Comments carry no meaning for the graph, but skipping them has to
        # count newlines or every later line number is wrong.
        if source.startswith("//", index):
            end = source.find("\n", index)
            index = length if end < 0 else end
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end < 0:
                raise LexError("unterminated /* comment", index, line)
            line += source.count("\n", index, end)
            index = end + 2
            continue

        start = index

        # VEX takes either quote, and hand-written snippets use `ch('parm')`
        # constantly. Only accepting double quotes was the single biggest
        # reason real code fell through to Inline VEX.
        if char in "\"'":
            quote = char
            index += 1
            while index < length and source[index] != quote:
                # A backslash escapes the next character, quote included.
                index += 2 if source[index] == "\\" else 1
            if index >= length:
                raise LexError("unterminated string", start, line)
            index += 1
            text = source[start:index]
            if quote == "'":
                # Normalised on the way in, so everything downstream - the
                # emitter included - only ever deals with one spelling.
                inner = text[1:-1].replace('"', '\\"')
                text = f'"{inner}"'
            tokens.append(Token(Kind.STRING, text, start, index, line))
            continue

        # `$PI`, `$F`, `$T`: hscript variables, which Houdini expands inside a
        # wrangle before VEX ever sees them. Rejecting the character threw the
        # whole snippet away; treating it as a value keeps the rest importable.
        if char == "$":
            match = NAME_RE.match(source, index + 1)
            if match:
                index = match.end()
                tokens.append(Token(Kind.HSCRIPT, source[start:index],
                                    start, index, line))
                continue

        # The regex itself requires an `@`, so a match here *is* an attribute.
        # The old guard looked one character ahead for it, which missed
        # `i[]@hits` where the `@` is three characters along.
        match = ATTRIB_RE.match(source, index)
        if match:
            index = match.end()
            tokens.append(Token(Kind.ATTRIB, match.group(3), start, index, line,
                                prefix=match.group(1),
                                is_array=bool(match.group(2))))
            continue

        match = NUMBER_RE.match(source, index)
        if match and char.isdigit() or (char == "." and match):
            index = match.end()
            tokens.append(Token(Kind.NUMBER, match.group(0), start, index, line))
            continue

        match = NAME_RE.match(source, index)
        if match:
            index = match.end()
            word = match.group(0)
            kind = (Kind.KEYWORD if word in KEYWORDS
                    else Kind.TYPE if word in TYPES else Kind.NAME)
            tokens.append(Token(kind, word, start, index, line))
            continue

        operator = next((op for op in OPERATORS if source.startswith(op, index)), None)
        if operator:
            index += len(operator)
            tokens.append(Token(Kind.OP, operator, start, index, line))
            continue

        if char in PUNCTUATION:
            index += 1
            tokens.append(Token(Kind.PUNCT, char, start, index, line))
            continue

        raise LexError(f"unexpected character {char!r}", index, line)

    tokens.append(Token(Kind.END, "", length, length, line))
    return tokens

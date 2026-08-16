"""Colours, metrics and fonts for the canvas.

Modelled on ComfyUI's node look, which is worth copying for a reason: the port
colour tells you what a wire carries before you read a single label, and the
inline value rows mean a node shows its settings without a properties panel to
go and look at. Both matter more here than they do there, because the audience
is someone who cannot fall back on reading the code.

One departure: arrays are drawn as squares rather than circles. A list of
vectors and a vector are different enough that colour alone should not have to
carry it.
"""

from __future__ import annotations

from PySide6 import QtGui

from .. import vextypes


def _c(value: str) -> QtGui.QColor:
    return QtGui.QColor(value)


# ------------------------------------------------------------------- canvas

BACKGROUND = _c("#1b1b1b")
GRID_DOT = _c("#262626")
GRID_SPACING = 26

# ------------------------------------------------------------------- nodes

NODE_BODY = _c("#353535")
NODE_BODY_SELECTED = _c("#3d3d3d")
NODE_TITLE_TEXT = _c("#d4d4d4")
# A node whose function Houdini documents. Tinted rather than decorated so it
# reads as "there is more here" without competing with the error colour, and so
# double-clicking for the help page is discoverable instead of being a secret.
NODE_TITLE_DOCUMENTED = _c("#8fc4ef")
NODE_LABEL_TEXT = _c("#a8a8a8")
NODE_OUTLINE = _c("#00000000")
NODE_OUTLINE_SELECTED = _c("#f0a53a")
NODE_SHADOW = _c("#00000070")
NODE_ERROR = _c("#e05a5a")
NODE_WARNING = _c("#d9a441")

ROW_BG = _c("#2a2a2a")
ROW_BG_HOVER = _c("#333333")
ROW_TEXT = _c("#c8c8c8")
ROW_VALUE_TEXT = _c("#e8e8e8")
ROW_ARROW = _c("#8a8a8a")

TEXT_AREA_BG = _c("#1e1e1e")
TEXT_AREA_TEXT = _c("#b9b9b9")

# ------------------------------------------------------------------ metrics
#
# Every size below is scaled live by UI_SCALE via the module __getattr__ at
# the bottom of this file, so `theme.ROW_HEIGHT` always reflects the current
# text-size setting without every call site having to know that. Colours are
# not in this dict and stay plain module attributes - scale is a text-size
# knob, not a recolour.

_METRICS = {
    "NODE_RADIUS": 8,
    "NODE_MIN_WIDTH": 190,
    "NODE_MAX_WIDTH": 340,
    "TITLE_HEIGHT": 30,
    "PORT_ROW_HEIGHT": 22,
    "ROW_HEIGHT": 24,
    "ROW_GAP": 5,
    "ROW_RADIUS": 11,
    "PADDING_X": 12,
    "PADDING_BOTTOM": 12,
    "PORT_RADIUS": 5,
    "PORT_HIT": 11,
    "EXEC_SIZE": 6,
    "LINK_WIDTH": 2.0,
    "LINK_WIDTH_EXEC": 2.6,
    "LINK_DOT": 3.2,
    "GRID_SPACING": 26,
}

# 100%. Scaling up by default was an answer to "everything looks small", but it
# made every fixed-height row too short for its own text, so labels came out
# clipped - a worse problem than small text, and one the user cannot fix by
# reaching for the control. The control is still there for going either way.
UI_SCALE = 1.0
_SCALE_BOUNDS = (0.75, 1.75)


def set_ui_scale(scale: float) -> None:
    global UI_SCALE
    UI_SCALE = max(_SCALE_BOUNDS[0], min(_SCALE_BOUNDS[1], scale))


def get_ui_scale() -> float:
    return UI_SCALE


def __getattr__(name: str):
    # PEP 562: only consulted when normal module-attribute lookup misses, so
    # this never shadows the real globals (colours, functions) defined above.
    if name in _METRICS:
        return _METRICS[name] * UI_SCALE
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# ------------------------------------------------------------------- wires

EXEC_COLOUR = _c("#e6e6e6")

# One colour per VEX type. Chosen to stay apart at small sizes on a dark
# background, and to keep related types related: the vectors are all warm, the
# matrices all violet.
TYPE_COLOURS = {
    "int": _c("#6fd08c"),
    "float": _c("#7fc4e8"),
    "vector2": _c("#f0c04a"),
    "vector": _c("#f0993a"),
    "vector4": _c("#e0714a"),
    "matrix2": _c("#a99ae0"),
    "matrix3": _c("#9b8ad8"),
    "matrix": _c("#8c7ad0"),
    "string": _c("#e07ab0"),
    "dict": _c("#8fa4b0"),
}
UNKNOWN_TYPE = _c("#9a9a9a")


def type_colour(vex_type: str) -> QtGui.QColor:
    return TYPE_COLOURS.get(vextypes.element_type(vex_type), UNKNOWN_TYPE)


def is_list(vex_type: str) -> bool:
    return vextypes.is_array(vex_type)


# -------------------------------------------------------------------- fonts

def ui_font(size: int = 9, bold: bool = False) -> QtGui.QFont:
    font = QtGui.QFont()
    font.setPointSizeF(max(1.0, size * UI_SCALE))
    font.setBold(bold)
    return font


def mono_font(size: int = 8) -> QtGui.QFont:
    font = QtGui.QFont("Consolas")
    font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
    font.setPointSizeF(max(1.0, size * UI_SCALE))
    return font


# The right-hand code pane. Deliberately closer to an editor than to the
# canvas: it is meant to be read as code, because reading it is how someone
# who does not write VEX starts to.
CODE_BG = _c("#1e1e1e")
CODE_TEXT = _c("#d4d4d4")
CODE_COMMENT = _c("#6a9955")
CODE_KEYWORD = _c("#569cd6")
CODE_TYPE = _c("#4ec9b0")
CODE_NUMBER = _c("#b5cea8")
CODE_STRING = _c("#ce9178")
CODE_ATTRIB = _c("#dcdcaa")
CODE_HIGHLIGHT = _c("#2f3a4a")

PANEL_BG = _c("#252525")
PANEL_TEXT = _c("#c0c0c0")


def scrollbar_qss() -> str:
    """Scrollbars that show where to grab, and only once.

    Qt's rule: the moment a stylesheet paints a QScrollBar's background - and
    a blanket `QWidget { background: ... }` does exactly that - Qt stops
    drawing the whole scrollbar natively and renders only the sub-controls
    the sheet mentions. Mentioning none of them is what produced arrows at
    both ends of both the groove and the bar (four of them, scattered) with
    a handle too dark to find. So every sub-control is named here: the step
    buttons and their arrows are given zero size, the pages are left blank,
    and what remains is one clearly visible handle.
    """
    return """
        QScrollBar:vertical { background: #1b1b1b; width: 13px; margin: 0; }
        QScrollBar:horizontal { background: #1b1b1b; height: 13px; margin: 0; }
        QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
            background: #555555; border-radius: 4px; margin: 2px; }
        QScrollBar::handle:vertical { min-height: 30px; }
        QScrollBar::handle:horizontal { min-width: 30px; }
        QScrollBar::handle:hover { background: #6d6d6d; }
        QScrollBar::handle:pressed { background: #7f8fa0; }
        QScrollBar::add-line, QScrollBar::sub-line {
            width: 0px; height: 0px; border: none; background: none; }
        QScrollBar::up-arrow, QScrollBar::down-arrow,
        QScrollBar::left-arrow, QScrollBar::right-arrow {
            width: 0px; height: 0px; background: none; }
        QScrollBar::add-page, QScrollBar::sub-page { background: none; }
    """

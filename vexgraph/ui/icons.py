"""Houdini's own VOP icons, read from the local install.

The icons live in `$HFS/houdini/config/Icons/icons.zip` as SVGs. They are read
from the user's install at runtime rather than copied into this project: that
keeps the repository free of SideFX artwork, and the icons always match the
Houdini the tool is running inside.

The mapping is by hand and deliberately partial. Our nodes are named for tasks
("Closest Point On Surface") and VOP's are named for functions, so most pairings
are a judgement about what a node *means*, not a string match. A node with no
entry simply draws without an icon.
"""

from __future__ import annotations

import functools
import os
import re
import zipfile
from pathlib import Path

from PySide6 import QtCore, QtGui, QtSvg

# Our node type -> the VOP icon that means the same thing. Left out on purpose
# where nothing fits: a wrong icon is worse than none, because it is read as a
# claim about what the node does.
VOP_ICONS = {
    "absolute": "abs",
    "add": "add",
    "add_point": "addpoint",
    "attrib_get": "importattrib",
    "attrib_set": "addattrib",
    "blend": "mix",
    "both_true": "and",
    "break_if": "block_end_breakif",
    "clamp_value": "clamp",
    "cosine": "cosine",
    "cross_product": "cross",
    "current_frame": "timing",
    "distance_between": "distance",
    "divide": "divide",
    "dot_product": "dot",
    "either_true": "or",
    "element_count": "npoints",
    "element_number": "global",
    "fit_range": "fit",
    "for_range": "block_begin_for",
    "foreach": "block_begin_foreach",
    "identity_matrix": "makexform",
    "if": "block_begin_if",
    "if_else": "twoway",
    "in_group": "ingroup",
    "is_equal": "compare",
    "is_greater": "compare",
    "is_less": "compare",
    "largest": "max",
    "length": "length",
    "list_append": "append",
    "list_item": "getelement",
    "list_length": "len",
    "make_vector": "floattovec",
    "modulo": "modulo",
    "multiply": "multiply",
    "nearest_point": "neighbour",
    "nearest_points": "neighbourcount",
    "negate": "negate",
    "noise_value": "unifiednoise",
    "normalize": "normalize",
    "not_true": "not",
    "point_count": "npoints",
    "position_on_primitive": "primattrib",
    "power": "pow",
    "random_number": "random",
    "random_vector": "nrandom",
    "read_point_attribute": "importattrib",
    "read_prim_attribute": "primattrib",
    "remove_point": "removepoint",
    "rotate_matrix": "rotate",
    "round_to_int": "rint",
    "scale": "scale",
    "sine": "sine",
    "skip_if": "block_end_breakif",
    "smallest": "min",
    "split_vector": "vectofloat",
    "square_root": "sqrt",
    "start": "global",
    "subtract": "subtract",
    "transform_by_matrix": "xform",
    "var_get": "bind",
    "var_make": "constant",
    "var_set": "bind",
    "write_point_attribute": "addattrib",
}


def houdini_icon_archive() -> Path | None:
    hfs = os.environ.get("HFS")
    if hfs:
        candidate = Path(hfs) / "houdini" / "config" / "Icons" / "icons.zip"
        if candidate.is_file():
            return candidate
    base = Path(r"C:\Program Files\Side Effects Software")
    if not base.is_dir():
        return None
    installs = sorted(
        (p for p in base.glob("Houdini *")
         if (p / "houdini" / "config" / "Icons" / "icons.zip").is_file()),
        key=lambda p: [int(x) for x in re.findall(r"\d+", p.name)])
    return installs[-1] / "houdini" / "config" / "Icons" / "icons.zip" \
        if installs else None


@functools.lru_cache(maxsize=1)
def _archive() -> zipfile.ZipFile | None:
    path = houdini_icon_archive()
    if path is None:
        return None
    try:
        return zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        return None


@functools.lru_cache(maxsize=256)
def _svg(vop_name: str) -> bytes | None:
    archive = _archive()
    if archive is None:
        return None
    try:
        return archive.read(f"VOP/{vop_name}.svg")
    except KeyError:
        return None


@functools.lru_cache(maxsize=512)
def node_icon(node_type: str, size: int, tint: str = "") -> QtGui.QPixmap | None:
    """The icon for one node type at one size, or None if there is not one.

    Cached per size because the canvas asks for the same icon on every repaint
    and rasterising an SVG each time would be felt while panning.
    """
    vop = VOP_ICONS.get(node_type)
    if vop is None and node_type.startswith("vex_"):
        # Generated nodes are named for their function, and a good number of
        # VOPs carry that same name.
        vop = node_type[4:]
    if vop is None:
        return None
    data = _svg(vop)
    if data is None:
        return None

    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(data))
    if not renderer.isValid():
        return None
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    renderer.render(painter)
    painter.end()

    if tint:
        # Houdini's icons are drawn for its own grey panels; on the darker node
        # header they need lifting or they read as smudges.
        tinted = QtGui.QPixmap(pixmap.size())
        tinted.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(tinted)
        painter.drawPixmap(0, 0, pixmap)
        painter.setCompositionMode(
            QtGui.QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QtGui.QColor(tint))
        painter.end()
        return tinted
    return pixmap


def available() -> bool:
    """Whether icons can be shown at all - false outside a Houdini install."""
    return _archive() is not None

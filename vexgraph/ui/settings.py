"""One place that knows where preferences live.

Everything persistent - fold states, Learn progress, canvas preferences,
the assistant's last question - goes through `store()`. That is not
ceremony: it is what lets a test point the whole app at a scratch file
instead of writing into the real one. A test run that rearranges the user's
actual editor is a bug, and it happened before this existed.
"""

from __future__ import annotations

from PySide6 import QtCore

ORGANISATION = "VEXgraph"
APPLICATION = "VEXgraph"


def store() -> QtCore.QSettings:
    return QtCore.QSettings(ORGANISATION, APPLICATION)

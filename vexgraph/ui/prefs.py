"""User preferences that change how the canvas behaves, kept across runs.

Small on purpose: only the things a person actually asked to tune. Each
setting applies immediately - the modules that consume these values read
them at use time, so mutating them here is enough - and persists through
the same QSettings store the panel already uses for its fold state.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from . import layout, settings, theme

_DEFAULTS = {
    "grid_spacing": theme.GRID_SPACING,
    "column_gap": layout.COLUMN_GAP,
    "row_gap": layout.ROW_GAP,
}


def load() -> None:
    """Apply stored preferences. Called once when the editor opens."""
    store = settings.store()
    apply_values(
        int(store.value("prefs/grid_spacing", _DEFAULTS["grid_spacing"])),
        int(store.value("prefs/column_gap", _DEFAULTS["column_gap"])),
        int(store.value("prefs/row_gap", _DEFAULTS["row_gap"])))


def apply_values(grid: int, column_gap: int, row_gap: int) -> None:
    theme.GRID_SPACING = max(4, grid)
    layout.COLUMN_GAP = max(10, column_gap)
    layout.ROW_GAP = max(10, row_gap)


def save(grid: int, column_gap: int, row_gap: int) -> None:
    store = settings.store()
    store.setValue("prefs/grid_spacing", grid)
    store.setValue("prefs/column_gap", column_gap)
    store.setValue("prefs/row_gap", row_gap)
    apply_values(grid, column_gap, row_gap)


class PreferencesDialog(QtWidgets.QDialog):
    """The gear button's dialog: canvas tuning, applied on OK."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        form = QtWidgets.QFormLayout(self)

        def spin(minimum: int, maximum: int, value: int,
                 tip: str) -> QtWidgets.QSpinBox:
            box = QtWidgets.QSpinBox()
            box.setRange(minimum, maximum)
            box.setValue(value)
            box.setToolTip(tip)
            return box

        self.grid = spin(4, 100, theme.GRID_SPACING,
                         "The grid nodes settle onto when you drop them.")
        self.column_gap = spin(40, 400, layout.COLUMN_GAP,
                               "Horizontal air between columns when a graph "
                               "is laid out (Tidy, imports).")
        self.row_gap = spin(10, 200, layout.ROW_GAP,
                            "Vertical air between nodes in a column.")
        form.addRow("Grid size", self.grid)
        form.addRow("Column spacing", self.column_gap)
        form.addRow("Row spacing", self.row_gap)

        note = QtWidgets.QLabel(
            "Spacing applies the next time a graph is laid out.")
        note.setWordWrap(True)
        form.addRow(note)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        restore = buttons.addButton(
            "Defaults", QtWidgets.QDialogButtonBox.ButtonRole.ResetRole)
        restore.clicked.connect(self._restore_defaults)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _restore_defaults(self) -> None:
        self.grid.setValue(_DEFAULTS["grid_spacing"])
        self.column_gap.setValue(_DEFAULTS["column_gap"])
        self.row_gap.setValue(_DEFAULTS["row_gap"])

    def accept(self) -> None:
        save(self.grid.value(), self.column_gap.value(), self.row_gap.value())
        super().accept()

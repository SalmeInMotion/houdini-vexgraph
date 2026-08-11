"""Put VEXgraph on sys.path and make its panel findable.

Named 123.py so Houdini runs it whatever Python it ships with. Code lives in
<root>/python rather than python3.11libs on purpose: Houdini only scans the
libs folder matching its own interpreter, so a version bump would silently stop
importing this.

There is no __file__ here - Houdini exec()s these scripts - so the root comes
from the environment variable the package sets.
"""

import os
import sys

import hou


def _register() -> None:
    root = os.environ.get("VEXGRAPH_ROOT", "")
    if not root:
        print("VEXgraph: VEXGRAPH_ROOT is not set; the package did not load.")
        return

    for path in (os.path.join(root, "houdini", "python"), root):
        if path not in sys.path and os.path.isdir(path):
            sys.path.insert(0, path)

    # A .pypanel that auto-loads still does not appear in the tab "+" menu
    # unless it is added explicitly, and a panel nobody can find is a panel
    # that does not exist.
    try:
        panel_file = hou.findFile("python_panels/vexgraph.pypanel")
        interfaces = hou.pypanel.interfacesInFile(panel_file)
        if interfaces:
            # Both sides of this deal in interface *names*: interfacesInFile
            # hands back objects, menuInterfaces/setMenuInterfaces take strings.
            existing = list(hou.pypanel.menuInterfaces())
            added = [i.name() for i in interfaces if i.name() not in existing]
            if added:
                hou.pypanel.setMenuInterfaces(tuple(existing) + tuple(added))
    except (hou.OperationFailed, AttributeError) as exc:
        print(f"VEXgraph: could not add the panel to the menu ({exc}). "
              f"Use the shelf tool instead.")


_register()

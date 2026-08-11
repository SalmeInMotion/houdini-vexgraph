"""Write the Houdini package file that makes VEXgraph load.

Run with any Python:  python houdini/install.py

Deliberately explicit about two things this machine has caught people out with
before: Documents is redirected to OneDrive, so the packages folder is not where
it looks like it should be; and the JSON must be written without a BOM, because
a BOM breaks Houdini's package parser.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def documents_dir() -> Path:
    """The real Documents folder, following the OneDrive redirect."""
    if sys.platform == "win32":
        try:
            import ctypes.wintypes  # noqa: PLC0415

            buffer = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
            # CSIDL_PERSONAL = 5, SHGFP_TYPE_CURRENT = 0
            ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer)
            if buffer.value:
                return Path(buffer.value)
        except Exception:
            pass
    return Path.home() / "Documents"


# The launcher points HOUDINI_PACKAGE_DIR at one shared folder for every build,
# so a package dropped there serves all of them and sits alongside the other
# tools instead of being copied per version.
LAUNCHER_PACKAGES = Path(r"C:\IA\Tools\Houdini\houdini-launcher\packages")


def launcher_dir() -> Path | None:
    override = os.environ.get("HOUDINI_LAUNCHER_PACKAGES")
    if override and Path(override).is_dir():
        return Path(override)
    return LAUNCHER_PACKAGES if LAUNCHER_PACKAGES.is_dir() else None


def package_dirs(version: str = "") -> list[Path]:
    documents = documents_dir()
    candidates = sorted(documents.glob("houdini*.*"))
    if version:
        candidates = [c for c in candidates if c.name == f"houdini{version}"]
    return [c / "packages" for c in candidates if c.is_dir()]


def package_contents() -> dict:
    # The explicit env form rather than the "path" shorthand: the shorthand is
    # not reliable in H21 on this setup. Nothing here names a Houdini version -
    # the package is the same for 21 and 22, and verified to load on both.
    return {
        "enable": True,
        "load_package_once": True,
        "env": [
            {"VEXGRAPH_ROOT": str(ROOT).replace("\\", "/")},
            {"HOUDINI_PATH": [
                {"value": str(ROOT / "houdini").replace("\\", "/"),
                 "method": "prepend"}]},
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="",
                        help="only install for this Houdini version, e.g. 21.0")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--per-version", action="store_true",
                        help="install into each houdiniXX.X/packages instead of "
                             "the launcher's shared folder")
    args = parser.parse_args()

    launcher = None if (args.per_version or args.version) else launcher_dir()
    if launcher is not None:
        targets = [launcher]
    else:
        targets = package_dirs(args.version)
    if not targets:
        print(f"No Houdini preference folders found under {documents_dir()}",
              file=sys.stderr)
        return 1

    payload = json.dumps(package_contents(), indent=2)
    for directory in targets:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "vexgraph.json"
        if args.uninstall:
            if path.exists():
                path.unlink()
                print(f"removed {path}")
            continue
        # utf-8 with no BOM, written explicitly: a BOM here stops Houdini
        # reading the package at all.
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
        print(f"wrote {path}")

    # Installed in the launcher's folder *and* a prefs folder means the package
    # loads twice, which registers the panel twice. Say so rather than leaving
    # a duplicate that only shows up as odd behaviour later.
    if launcher is not None and not args.uninstall:
        stale = [d / "vexgraph.json" for d in package_dirs()
                 if (d / "vexgraph.json").is_file()]
        if stale:
            print("\nAlso found older per-version copies, which would load a "
                  "second time. Remove them with:")
            for path in stale:
                print(f"  del \"{path}\"")

    if not args.uninstall:
        print("\nRestart Houdini, then either:")
        print("  - the VEXgraph shelf tool (Shelves > VEXgraph), or")
        print("  - New Pane Tab Type > Python Panel > VEXgraph")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

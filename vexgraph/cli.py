"""Drive the core without a canvas.

Useful on its own while the editor does not exist yet, and useful afterwards:
`catalog` is how the assistant will be shown what nodes it may choose from, and
`build` is the same call the panel makes when it writes into a wrangle.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .graph import ERROR, Graph
from .nodedefs import default_registry
from .vccmap import build as build_graph


def _print_issues(issues, prefix: str = "") -> None:
    for issue in issues:
        mark = "error" if issue.severity == ERROR else "warning"
        where = f" [{issue.node_id}]" if issue.node_id else ""
        print(f"{prefix}{mark}{where}: {issue.message}", file=sys.stderr)


def cmd_build(args) -> int:
    registry = default_registry()
    graph = Graph.load(Path(args.graph), registry)
    emission, compiled = build_graph(graph, check=not args.no_check)

    if emission.issues:
        _print_issues(emission.issues)
    if not emission.ok:
        return 1

    if args.out:
        Path(args.out).write_text(emission.code, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(emission.code, end="")

    if compiled.skipped:
        print(f"(not compile-checked: {compiled.skipped})", file=sys.stderr)
    elif compiled.ok:
        print("vcc: compiles", file=sys.stderr)
    else:
        _print_issues(compiled.issues, prefix="vcc ")
        return 1
    return 0


def cmd_import(args) -> int:
    """VEX in, graph out — the direction that makes this a translator."""
    from .parser import import_vex  # noqa: PLC0415

    registry = default_registry()
    source = (Path(args.source).read_text(encoding="utf-8") if args.source
              else sys.stdin.read())
    report = import_vex(source, registry)
    print(report.summary(), file=sys.stderr)
    for reason in dict.fromkeys(report.reasons):
        print(f"  kept as-is: {reason}", file=sys.stderr)

    if args.out:
        report.graph.save(Path(args.out))
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(report.graph.to_json())
    return 0


def cmd_nodes(args) -> int:
    registry = default_registry()
    query = " ".join(args.query)
    found = registry.search(query, limit=args.limit,
                            tier=args.tier or None) if query else list(registry)
    for definition in found[:args.limit]:
        tier = "" if definition.tier == 1 else "  (vex)"
        print(f"{definition.type:<28} {definition.label}{tier}")
        if definition.summary:
            print(f"{'':<28} {definition.summary}")
    if not found:
        print("nothing matched", file=sys.stderr)
        return 1
    return 0


def cmd_show(args) -> int:
    registry = default_registry()
    definition = registry.get(args.type)
    if definition is None:
        print(f"no node type {args.type!r}", file=sys.stderr)
        return 1

    print(f"{definition.label}  ({definition.type})")
    print(f"  category : {definition.category}")
    print(f"  kind     : {definition.kind}")
    if definition.summary:
        print(f"  summary  : {definition.summary}")
    if definition.help:
        print(f"  help     : {definition.help}")
    for param in definition.params:
        menu = f"  one of: {', '.join(param.menu)}" if param.menu else ""
        print(f"  setting  : {param.name} = {param.default!r}{menu}")
    for socket in definition.inputs:
        default = "required" if socket.default is None else f"default {socket.default}"
        print(f"  in       : {socket.name}: {socket.type}  ({default})")
    for socket in definition.outputs:
        scope = f"  only inside {socket.scope}" if socket.scope else ""
        print(f"  out      : {socket.name}: {socket.type}{scope}")
    for body in definition.exec_bodies:
        print(f"  body     : {body}")
    if definition.code or definition.expr:
        print("  emits    :")
        for line in (definition.expr or definition.code).split("\n"):
            print(f"      {line}")
    return 0


def cmd_catalog(args) -> int:
    """A compact machine-readable palette.

    Written for a model to choose from: one entry per node, sockets and settings
    named, nothing about layout or colour. Keeping it small matters, because it
    has to fit in a local model's context alongside the user's request.
    """
    registry = default_registry()
    entries = []
    for definition in sorted(registry, key=lambda d: (d.tier, d.category, d.label)):
        if args.tier and definition.tier != args.tier:
            continue
        entry = {
            "type": definition.type,
            "label": definition.label,
            "kind": definition.kind,
            "category": definition.category,
        }
        if definition.summary:
            entry["summary"] = definition.summary
        if definition.params:
            entry["settings"] = {p.name: (list(p.menu) if p.menu else p.default)
                                 for p in definition.params}
        if definition.inputs:
            entry["in"] = {s.name: s.type for s in definition.inputs}
        if definition.outputs:
            entry["out"] = {s.name: s.type for s in definition.outputs}
        if definition.exec_bodies:
            entry["bodies"] = list(definition.exec_bodies)
        entries.append(entry)

    output = json.dumps(entries, indent=None if args.compact else 1)
    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"{len(entries)} nodes -> {args.out}", file=sys.stderr)
    else:
        print(output)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vexgraph", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="turn a graph into VEX")
    build.add_argument("graph")
    build.add_argument("--out", default="")
    build.add_argument("--no-check", action="store_true",
                       help="skip the vcc compile check")
    build.set_defaults(func=cmd_build)

    importer = sub.add_parser("import", help="turn VEX into a graph")
    importer.add_argument("source", nargs="?", help="a .vex file (stdin if omitted)")
    importer.add_argument("--out", default="", help="write a .vexgraph.json here")
    importer.set_defaults(func=cmd_import)

    nodes = sub.add_parser("nodes", help="search the palette")
    nodes.add_argument("query", nargs="*")
    nodes.add_argument("--limit", type=int, default=20)
    nodes.add_argument("--tier", type=int, default=0,
                       help="1 for the curated nodes, 2 for raw VEX functions")
    nodes.set_defaults(func=cmd_nodes)

    show = sub.add_parser("show", help="everything about one node type")
    show.add_argument("type")
    show.set_defaults(func=cmd_show)

    catalog = sub.add_parser("catalog", help="the palette as JSON, for a model")
    catalog.add_argument("--tier", type=int, default=0)
    catalog.add_argument("--out", default="")
    catalog.add_argument("--compact", action="store_true")
    catalog.set_defaults(func=cmd_catalog)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

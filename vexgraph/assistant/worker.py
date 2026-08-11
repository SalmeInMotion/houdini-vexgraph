"""Run one assistant request in a separate process.

Houdini embeds its own Python, and the Anthropic SDK's compiled dependencies
are built per interpreter version — vendoring them would mean one copy per
Houdini release and a new one every upgrade. Running the request in this
project's own interpreter avoids all of that: nothing is installed into
Houdini, and the same code serves H21, H22, and whatever comes next.

Reads a request as JSON on stdin, writes the result as JSON on stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vexgraph.assistant.agent import Assistant  # noqa: E402
from vexgraph.assistant.providers import ProviderError, get  # noqa: E402
from vexgraph.graph import Graph  # noqa: E402
from vexgraph.nodedefs import default_registry  # noqa: E402


def run(request: dict) -> dict:
    registry = default_registry()
    provider = get(request.get("provider", "Claude"))

    ready, why = provider.available()
    if not ready:
        return {"ok": False, "problems": [why]}

    current = None
    if request.get("graph"):
        current = Graph.from_dict(request["graph"], registry)

    assistant = Assistant(registry, provider)

    if request.get("mode") == "explain":
        return {"ok": True, "answer": assistant.explain(request["text"], current)}

    result = assistant.build_graph(request["text"], current)
    return {
        "ok": result.ok,
        "graph": result.graph.to_dict() if result.graph else None,
        "code": result.code,
        "notes": result.notes,
        "problems": result.problems,
        "tries": result.tries,
        "provider": result.provider,
    }


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "problems": [f"bad request: {exc}"]}))
        return 1

    try:
        result = run(request)
    except ProviderError as exc:
        result = {"ok": False, "problems": [str(exc)]}
    except Exception as exc:                       # noqa: BLE001 - must not crash
        result = {"ok": False, "problems": [f"{type(exc).__name__}: {exc}"]}

    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

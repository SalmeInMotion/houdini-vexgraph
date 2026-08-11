"""What the model is allowed to choose from.

The whole safety argument for this feature rests on this file. A model asked to
write VEX invents functions that do not exist — plausibly, and often. A model
asked to pick node types from a closed list cannot: every name it returns is
checked against the registry before anything is built, and a name that is not
there is an error the model gets handed back, not code that reaches a wrangle.

So the catalogue has to be complete enough to work from and small enough to
send. Tier 1 is always included in full (~60 nodes). Tier 2 is 1275 nodes and
would swamp the context, so only the entries matching the request come along.
"""

from __future__ import annotations

import re

from ..nodedefs import NodeDef, Registry

# How many generated VEX-function nodes to attach for a given request. Enough
# to cover the long tail, small enough that tier 1 still dominates the prompt.
TIER2_MATCHES = 40


def describe(definition: NodeDef, *, verbose: bool = True) -> str:
    """One node, as a few compact lines."""
    head = f"{definition.type} | {definition.label} | {definition.kind}"
    lines = [head]
    if verbose and definition.summary:
        lines.append(f"  {definition.summary}")

    for param in definition.params:
        if param.menu:
            choices = "|".join(param.menu)
            lines.append(f"  set {param.name} = {param.default!r} one of [{choices}]")
        else:
            lines.append(f"  set {param.name} = {param.default!r}")

    for socket in definition.inputs:
        need = "required" if socket.default is None else f"default {socket.default}"
        lines.append(f"  in  {socket.name}: {socket.type} ({need})")
    for socket in definition.outputs:
        scope = f" [only inside {socket.scope}]" if socket.scope else ""
        lines.append(f"  out {socket.name}: {socket.type}{scope}")
    for body in definition.exec_bodies:
        lines.append(f"  body {body}")
    return "\n".join(lines)


def relevant_tier2(registry: Registry, request: str,
                   limit: int = TIER2_MATCHES) -> list[NodeDef]:
    """VEX-function nodes whose name or summary echoes the request.

    Deliberately generous: a false positive costs a few tokens, a false
    negative means the model cannot express what was asked and starts
    improvising with tier-1 nodes that do not quite fit.
    """
    words = {w for w in re.split(r"\W+", request.lower()) if len(w) > 3}
    if not words:
        return []
    scored: list[tuple[int, NodeDef]] = []
    for definition in registry:
        if definition.tier != 2:
            continue
        name = definition.label.lower()
        haystack = f"{name} {definition.summary.lower()}"
        hits = sum(1 for w in words if w in haystack)
        if not hits:
            continue
        # A word appearing in the function's own name is worth far more than
        # one appearing in its description.
        scored.append((hits * 10 + sum(5 for w in words if w in name), definition))
    scored.sort(key=lambda pair: (-pair[0], pair[1].type))
    return [d for _, d in scored[:limit]]


def build(registry: Registry, request: str = "") -> str:
    """The catalogue to put in front of the model for this request."""
    tier1 = sorted((d for d in registry if d.tier == 1),
                   key=lambda d: (d.category, d.label))

    sections: list[str] = ["# Nodes you may use", ""]
    category = ""
    for definition in tier1:
        if definition.category != category:
            category = definition.category
            sections.append(f"## {category}")
        sections.append(describe(definition))
    sections.append("")

    extra = relevant_tier2(registry, request) if request else []
    if extra:
        sections.append("## Raw VEX functions (use only when no node above fits)")
        for definition in extra:
            sections.append(describe(definition, verbose=False))
        sections.append("")

    return "\n".join(sections)


def size_estimate(text: str) -> str:
    """Rough sense of the prompt cost, for the UI to show."""
    return f"{len(text) // 1000} KB, about {len(text) // 4} tokens"

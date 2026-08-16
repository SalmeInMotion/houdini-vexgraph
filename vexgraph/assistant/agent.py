"""Turn a plain-language request into a graph, and refuse to accept a bad one.

The model never writes VEX. It returns a graph — node types drawn from a closed
catalogue, wired by socket name — and this module checks every part of it
against the registry before anything is built. A node type that does not exist,
a socket that is not on that node, a wire between incompatible types: each is
caught here, described in the model's own terms, and handed back for another
attempt.

Only after the graph validates does the deterministic emitter turn it into VEX,
and only if `vcc` accepts that VEX is the result offered to the user. The model
picks; the compiler decides.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .. import vextypes
from ..codegen import generate
from ..graph import ERROR, Graph, Node
from ..nodedefs import Registry
from ..parser import import_vex
from ..vccmap import check_source, compile_check
from . import catalog

MAX_ATTEMPTS = 3

SYSTEM = """You build node graphs for VEXgraph, a visual editor that generates \
VEX for Houdini Attribute Wrangle nodes.

You do NOT write VEX. You choose nodes from the catalogue you are given and wire
them together. A deterministic compiler turns your graph into VEX afterwards.

Rules:
- Use ONLY node types listed in the catalogue. Never invent a type.
- Use ONLY the socket names listed for that node. Never invent a socket.
- Exactly one `start` node, and it must be present.
- Nodes that DO something (kind `statement` or `scope`) must be joined into a
  sequence with exec links: {"from": "a", "out": "exec", "to": "b", "in": "exec",
  "exec": true}. The chain begins at the start node.
- A `scope` node runs its body through its body pin, e.g. out "then" or "body".
- Nodes of kind `pure` are NOT in the exec sequence. They are wired by their
  values only, and are computed wherever they are needed.
- An input either gets a data link or a value in `params`, a list of pairs:
  "params": [{"name": "attrib", "value": "Cd"}]. A value is written exactly
  as VEX would: 0.5, {1, 0, 0}, "myname".
- An output marked "only inside <body>" may only be used by nodes that run
  inside that body.
- Prefer the task-level nodes over raw VEX functions. Reach for a raw VEX
  function only when nothing else expresses the operation.

Return only the JSON object. No prose outside it, no markdown fence."""

REPAIR = """That graph was rejected. Fix the problems and return the whole \
graph again.

Problems:
{problems}

Return only the corrected JSON object."""

# ------------------------------------------------------- the write-VEX route
#
# The catalogue route above asks the model to choose from ~1300 invented node
# types under a JSON schema — squarely outside a local model's training
# distribution, and schema-constrained decoding on a grammar that size is what
# made a 32B model sit for 25 minutes. This route asks for the one thing every
# model has seen thousands of times: plain VEX. The parser then lowers it onto
# the canvas (per-statement, never fatal — what does not map becomes an Inline
# VEX node), and vcc remains the final judge either way. The model writes; the
# importer draws; the compiler decides.

VEX_SYSTEM = """You write VEX for a Houdini Attribute Wrangle node.

Rules:
- First line: a comment stating what to run over, e.g. `// run over: points`
  (one of: points, primitives, vertices, detail, numbers).
- Then the VEX statements, exactly as they would be typed into the wrangle.
- Plain statements only: no function definitions, no structs. Keep it simple
  and idiomatic - attributes via @ bindings (v@P, f@pscale, i@id, @Cd),
  standard VEX functions.
- Only use VEX functions that genuinely exist. If you are unsure a function
  exists, build the same result from ones you are certain of. An invented
  function name is the single worst failure here.
- Write only what was asked. No defensive checks, no extra attributes, no
  commented-out alternatives.
- Channel parameters (chf/chv/chi) are welcome where a value should be
  tweakable.
- Return ONLY the code. No prose before or after, no markdown fence.

VEX syntax that is commonly got wrong:
- Arrays are declared C-style, with the brackets after the name:
      int pts[] = nearpoints(0, @P, 0, 5);     correct
      int[] pts = nearpoints(0, @P, 0, 5);     WRONG, will not compile
- Array length is len(), not length(). length() is the magnitude of a vector.
- Strings use double quotes only.
- There is no ternary chaining or `else if` shorthand beyond standard C."""

VEX_REPAIR = """That VEX was rejected by the compiler. Fix it and return the \
whole snippet again (same format: run-over comment first, code only).

Compiler errors:
{problems}"""

RUN_OVER_RE = re.compile(
    r"//\s*run\s*over\s*:?\s*(points|primitives|vertices|detail|numbers)",
    re.IGNORECASE)


def strip_vex(text: str) -> tuple[str, str]:
    """(code, run_over) from a model reply that may carry fences or prose."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced = re.search(r"```[a-zA-Z]*\s*\n(.*?)\n?```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    match = RUN_OVER_RE.search(text)
    run_over = match.group(1).lower() if match else "points"
    if match:
        text = (text[:match.start()] + text[match.end():]).strip()
    return text, run_over

# The shape the model must return. Positions are deliberately absent: the editor
# lays the graph out itself, and asking a model for coordinates wastes tokens on
# something it has no way to judge.
REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "notes": {
            "type": "string",
            "description": "One or two sentences describing what the graph does.",
        },
        "run_over": {
            "type": "string",
            "enum": ["points", "primitives", "vertices", "detail", "numbers"],
        },
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string"},
                    # A list of pairs, not an open map: Claude's structured
                    # outputs rejects `additionalProperties` carrying a schema
                    # ("must be false"), and every provider can emit a list.
                    "params": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "value": {"type": "string"},
                            },
                            "required": ["name", "value"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "type"],
                "additionalProperties": False,
            },
        },
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "out": {"type": "string"},
                    "to": {"type": "string"},
                    "in": {"type": "string"},
                    "exec": {"type": "boolean"},
                },
                "required": ["from", "out", "to", "in"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["notes", "run_over", "nodes", "links"],
    "additionalProperties": False,
}


def _param_pairs(raw) -> list[tuple[str, str]]:
    """Node params from a reply, whichever shape the model chose.

    The schema asks for a list of {name, value} pairs (an open map is not
    expressible under Claude's structured outputs), but local models often
    reply with the plain object anyway - and both mean the same thing.
    """
    if isinstance(raw, dict):
        return [(str(k), str(v)) for k, v in raw.items()]
    if isinstance(raw, list):
        return [(str(p.get("name", "")), str(p.get("value", "")))
                for p in raw if isinstance(p, dict) and p.get("name")]
    return []


@dataclass
class Attempt:
    raw: str = ""
    problems: list[str] = field(default_factory=list)


@dataclass
class Result:
    ok: bool
    graph: Graph | None = None
    code: str = ""
    notes: str = ""
    problems: list[str] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    provider: str = ""

    @property
    def tries(self) -> int:
        return len(self.attempts)


def strip_fence(text: str) -> str:
    """Models add markdown fences and thinking tags even when told not to."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    fenced = re.match(r"^```[a-zA-Z]*\s*\n(.*?)\n?```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    # A model that adds a sentence before the JSON is still recoverable.
    start, end = text.find("{"), text.rfind("}")
    if start > 0 and end > start:
        text = text[start:end + 1]
    return text


class GraphBuilder:
    """Validates a model's reply and turns it into a Graph."""

    def __init__(self, registry: Registry) -> None:
        self.registry = registry

    def build(self, reply: dict) -> tuple[Graph | None, list[str]]:
        problems: list[str] = []
        graph = Graph(self.registry, run_over=reply.get("run_over", "points"))

        entries = reply.get("nodes") or []
        if not entries:
            return None, ["The graph has no nodes."]

        for entry in entries:
            problems += self._add_node(graph, entry)
        if problems:
            return None, problems

        for link in reply.get("links") or []:
            problems += self._add_link(graph, link)
        if problems:
            return None, problems

        starts = [n for n in graph.nodes.values()
                  if not graph.definition(n).exec_in]
        if not starts:
            problems.append(
                "There is no `start` node. Add one and begin the exec chain from it.")
        elif len(starts) > 1:
            problems.append(
                f"There are {len(starts)} start nodes; there must be exactly one.")

        return (graph, problems) if not problems else (None, problems)

    def _add_node(self, graph: Graph, entry: dict) -> list[str]:
        node_id = str(entry.get("id", "")).strip()
        node_type = str(entry.get("type", "")).strip()
        if not node_id:
            return ["A node has no id."]
        if node_id in graph.nodes:
            return [f"Two nodes share the id {node_id!r}."]

        definition = self.registry.get(node_type)
        if definition is None:
            near = self._suggest(node_type)
            hint = f" Did you mean {', '.join(near)}?" if near else ""
            return [f"There is no node type {node_type!r}.{hint}"]

        node = Node(id=node_id, type=node_type)
        graph.nodes[node_id] = node

        problems: list[str] = []
        for key, value in _param_pairs(entry.get("params")):
            problems += self._check_param(graph, node, key, str(value))
        return problems

    def _suggest(self, node_type: str) -> list[str]:
        """Nearest real node types to an invented one.

        Searching the whole invented name usually finds nothing, because search
        requires every word to match — `get_closest_point` has a "get" that no
        real node's text contains. Falling back to the individual words is what
        turns a dead end into a usable correction.
        """
        words = [w for w in node_type.replace("_", " ").split() if len(w) > 2]
        found = self.registry.search(" ".join(words), limit=3)
        if not found:
            seen: dict[str, None] = {}
            for word in words:
                for definition in self.registry.search(word, limit=2, tier=1):
                    seen.setdefault(definition.type, None)
            return list(seen)[:3]
        return [d.type for d in found]

    def _check_param(self, graph: Graph, node: Node, key: str,
                     value: str) -> list[str]:
        definition = graph.definition(node)
        param = definition.param(key)
        socket = definition.input(key)
        if param is None and socket is None:
            names = [p.name for p in definition.params] + \
                    [s.name for s in definition.inputs]
            return [f"{node.type} has no setting or input called {key!r}. "
                    f"It has: {', '.join(names) or 'none'}."]
        if param is not None and param.menu and value not in param.menu:
            return [f"{node.type}.{key} is {value!r}; it must be one of "
                    f"{', '.join(param.menu)}."]
        node.params[key] = value
        return []

    def _add_link(self, graph: Graph, link: dict) -> list[str]:
        source, target = str(link.get("from", "")), str(link.get("to", ""))
        out_name, in_name = str(link.get("out", "")), str(link.get("in", ""))
        is_exec = bool(link.get("exec", False))

        for node_id in (source, target):
            if node_id not in graph.nodes:
                return [f"A link refers to node {node_id!r}, which does not exist."]

        source_def = graph.definition(source)
        target_def = graph.definition(target)

        # An exec link is recognised by the pins it uses, not by the flag; a
        # model that forgets the flag has still expressed the right intent.
        looks_exec = (out_name == "exec" or out_name in source_def.exec_bodies) \
            and in_name == "exec"
        if looks_exec:
            if not target_def.exec_in:
                return [f"{target} ({target_def.type}) cannot be run from a "
                        f"sequence; it has no exec input."]
            graph.connect(source, out_name, target, in_name, is_exec=True)
            return []

        if is_exec:
            # These messages go back to the model to correct itself, so each one
            # says what to do instead rather than only what was wrong.
            return [f"The link {source}.{out_name} -> {target}.{in_name} is "
                    f"marked exec, but exec links must join a pin literally "
                    f"named 'exec' (or a loop body pin) on both ends. "
                    f"{target_def.type} is a value node and never runs in "
                    f"sequence: wire it into an input instead, with "
                    f"is_exec false."]

        if source_def.output(out_name) is None:
            names = [s.name for s in source_def.outputs]
            if not names:
                return [f"{source_def.type} produces no value, so nothing can "
                        f"be read from it. If you meant to use a variable you "
                        f"made with var_make, read it back with a var_get node "
                        f"whose 'name' param matches."]
            return [f"{source_def.type} has no output {out_name!r}. "
                    f"Its outputs are: {', '.join(names)}."]
        if target_def.input(in_name) is None:
            names = [s.name for s in target_def.inputs]
            return [f"{target_def.type} has no input {in_name!r}. "
                    f"It has: {', '.join(names) or 'none'}."]

        refusal = graph.check_connection(source, out_name, target, in_name)
        if refusal:
            return [f"{source}.{out_name} cannot drive {target}.{in_name}: {refusal}"]
        graph.connect(source, out_name, target, in_name)
        return []


class Assistant:
    def __init__(self, registry: Registry, provider) -> None:
        self.registry = registry
        self.provider = provider
        self.builder = GraphBuilder(registry)

    def build_graph(self, request: str, current: Graph | None = None, *,
                    max_attempts: int = MAX_ATTEMPTS) -> Result:
        """Ask for a graph, check it, and retry with the problems if it fails."""
        prompt = self._prompt(request, current)
        attempts: list[Attempt] = []
        messages = [{"role": "user", "content": prompt}]

        for _ in range(max_attempts):
            raw = self.provider.complete(SYSTEM, messages, REPLY_SCHEMA)
            attempt = Attempt(raw=raw)
            attempts.append(attempt)

            reply, problems = self._parse(raw)
            if reply is not None:
                graph, problems = self.builder.build(reply)
                if graph is not None:
                    problems = self._check_output(graph)
                    if not problems:
                        return Result(True, graph, generate(graph).code,
                                      reply.get("notes", ""), [], attempts,
                                      self.provider.name)
            attempt.problems = problems

            messages = messages[:1] + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": REPAIR.format(
                    problems="\n".join(f"- {p}" for p in problems))},
            ]

        return Result(False, None, "", "", attempts[-1].problems if attempts
                      else ["No reply."], attempts, self.provider.name)

    def build_graph_via_vex(self, request: str, current: Graph | None = None, *,
                            max_attempts: int = MAX_ATTEMPTS) -> Result:
        """Ask for VEX, compile it, and lower it onto the canvas.

        The default route for local models (see the module comment above
        VEX_SYSTEM), and available to any provider. The repair loop feeds the
        model the compiler's own line-numbered errors against its own code -
        the one kind of correction every model has been trained on.
        """
        parts = []
        if current is not None and len(current.nodes) > 1:
            emission = generate(current)
            if emission.code.strip():
                parts.append("# The code as it stands\n")
                parts.append("Modify this snippet. Return the whole snippet, "
                             "not a patch.\n")
                parts.append(emission.code)
                parts.append("")
        parts.append(f"# Request\n\n{request}")
        messages = [{"role": "user", "content": "\n".join(parts)}]

        attempts: list[Attempt] = []
        for _ in range(max_attempts):
            raw = self.provider.complete(VEX_SYSTEM, messages, None)
            attempt = Attempt(raw=raw)
            attempts.append(attempt)

            code, run_over = strip_vex(raw)
            problems: list[str] = []
            if not code.strip():
                problems = ["The reply contained no code."]
            else:
                compiled = check_source(code)
                if not compiled.ok:
                    problems = [i.message for i in compiled.issues] \
                        or [compiled.raw.strip()]

            if not problems:
                report = import_vex(code, self.registry)
                graph = report.graph
                graph.run_over = run_over
                # Belt and braces: what the importer drew must emit and compile
                # too. If it does not, the fault is ours, not the model's -
                # its code compiled. Sending it the emitter's errors would ask
                # it to fix something it never wrote, so keep its snippet whole
                # in one Inline VEX node instead. Fewer nodes, working code.
                if self._check_output(graph):
                    graph = self._as_one_inline(code, run_over)
                    if not self._check_output(graph):
                        return Result(True, graph, generate(graph).code,
                                      "Kept as one Inline VEX node: the code "
                                      "works, but VEXgraph could not break it "
                                      "into nodes.", [], attempts,
                                      self.provider.name)
                else:
                    return Result(True, graph, generate(graph).code,
                                  report.summary(), [], attempts,
                                  self.provider.name)
                problems = ["The importer could not represent that code."]

            attempt.problems = problems
            messages = messages[:1] + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": VEX_REPAIR.format(
                    problems="\n".join(f"- {p}" for p in problems))},
            ]

        return Result(False, None, "", "", attempts[-1].problems if attempts
                      else ["No reply."], attempts, self.provider.name)

    def _as_one_inline(self, code: str, run_over: str) -> Graph:
        """The whole snippet as a single Inline VEX node."""
        graph = Graph(self.registry, run_over=run_over)
        start = graph.add("start", "start")
        node = graph.add("inline_vex", "inline_1", code=code)
        graph.connect(start.id, "exec", node.id, "exec", is_exec=True)
        return graph

    def _parse(self, raw: str) -> tuple[dict | None, list[str]]:
        try:
            reply = json.loads(strip_fence(raw))
        except (json.JSONDecodeError, ValueError) as exc:
            return None, [f"That was not valid JSON ({exc})."]
        if not isinstance(reply, dict):
            return None, ["The reply must be a JSON object."]
        return reply, []

    def _check_output(self, graph: Graph) -> list[str]:
        """Emit and compile. The compiler is the final say, as it is for a human."""
        emission = generate(graph)
        errors = [i.message for i in emission.issues if i.severity == ERROR]
        if errors:
            return errors
        result = compile_check(emission, graph)
        if not result.ok:
            return [f"The generated VEX did not compile: {result.raw.strip()}"]
        return []

    def _prompt(self, request: str, current: Graph | None) -> str:
        parts = [catalog.build(self.registry, request)]
        if current is not None and len(current.nodes) > 1:
            parts.append("# The graph as it stands\n")
            parts.append("Modify this graph. Return the whole graph, not a patch.\n")
            parts.append(json.dumps(self._summarise(current), indent=1))
            parts.append("")
        parts.append(f"# Request\n\n{request}")
        return "\n".join(parts)

    @staticmethod
    def _summarise(graph: Graph) -> dict:
        """The current graph without positions, which the model has no use for."""
        return {
            "run_over": graph.run_over,
            "nodes": [{"id": n.id, "type": n.type, "params": n.params}
                      for n in graph.nodes.values()],
            "links": [{"from": l.from_node, "out": l.from_socket,
                       "to": l.to_node, "in": l.to_socket, "exec": l.is_exec}
                      for l in graph.links],
        }

    # ------------------------------------------------------------- explaining

    def explain(self, question: str, current: Graph | None = None) -> str:
        """A plain answer, for 'what node do I need to...' questions."""
        parts = [catalog.build(self.registry, question)]
        if current is not None and len(current.nodes) > 1:
            parts.append("# The graph as it stands\n")
            parts.append(json.dumps(self._summarise(current), indent=1))
        parts.append(f"# Question\n\n{question}")
        system = (
            "You help someone who does not write code build node graphs in "
            "VEXgraph. Answer their question in plain language, naming the nodes "
            "from the catalogue they should use and how to wire them. Be brief "
            "and concrete. Do not write VEX; the point of the tool is that they "
            "do not have to read it.")
        return self.provider.complete(
            system, [{"role": "user", "content": "\n".join(parts)}], None)

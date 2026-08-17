"""Audit every snippet: how well does each survive becoming nodes?

Run it after touching the importer, the emitter or the node library:

    .venv/Scripts/python.exe tools/audit_corpus.py

The number that matters is the broken count - a snippet that becomes nodes
and then emits VEX the compiler rejects. That must stay at zero; everything
else is a question of how many nodes it took.

Baseline at the time of writing: 222 clean / 43 partial / 13 all inline /
0 broken, out of 278 importable snippets.

For each of the 414 snippets:
  1. does the ORIGINAL compile as a wrangle?        (if not: not our problem)
  2. import -> how much fell back to Inline VEX, and why
  3. emit the graph's VEX -> does the ROUND TRIP compile?
  4. bucket and tally everything

Buckets:
  original_broken  - the snippet itself does not compile (context/inputs)
  clean            - all nodes, round trip compiles
  clean_broken     - all nodes BUT the round trip does not compile  << worst
  partial          - some inline, round trip compiles
  partial_broken   - some inline and the round trip does not compile
  all_inline       - nothing translated
  import_error     - the importer raised
"""
import io
import json
import sys
from pathlib import Path
from collections import Counter, defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf8")

from vexgraph import nodedefs, snippets
from vexgraph.codegen import generate
from vexgraph.parser.lower import import_vex
from vexgraph.vccmap import check_source

reg = nodedefs.default_registry()

buckets = defaultdict(list)
reasons = Counter()
reason_examples = defaultdict(list)
roundtrip_errors = defaultdict(list)

items = [s for s in snippets.load() if s.code.strip()]
print(f"snippets: {len(items)}", flush=True)

for i, s in enumerate(items):
    if i % 50 == 0:
        print(f"  ...{i}", flush=True)
    original = check_source(s.code)
    if original.checked and not original.ok:
        buckets["original_broken"].append(s.name)
        continue

    try:
        r = import_vex(s.code, reg)
    except Exception as exc:
        buckets["import_error"].append(f"{s.name}: {type(exc).__name__} {exc}")
        continue

    for reason in r.reasons:
        reasons[reason] += 1
        if len(reason_examples[reason]) < 3:
            reason_examples[reason].append(s.name)

    emission = generate(r.graph)
    if not emission.ok:
        rt_ok = False
        rt_err = "; ".join(str(x) for x in emission.issues[:2])
    else:
        rt = check_source(emission.code)
        rt_ok = (not rt.checked) or rt.ok
        rt_err = "; ".join(str(x) for x in rt.issues[:2]) if not rt_ok else ""

    if r.inlined == 0:
        key = "clean" if rt_ok else "clean_broken"
    elif r.inlined >= r.total:
        key = "all_inline"
    else:
        key = "partial" if rt_ok else "partial_broken"
    buckets[key].append(s.name)
    if not rt_ok and key != "all_inline":
        roundtrip_errors[rt_err.split(":")[-1].strip()[:90]].append(s.name)

print()
print("== BUCKETS ==")
for k in ("clean", "partial", "all_inline", "clean_broken", "partial_broken",
          "original_broken", "import_error"):
    print(f"  {k:16s} {len(buckets[k]):4d}")

print()
print("== ROUND-TRIP FAILURES (translated code that no longer compiles) ==")
for err, names in sorted(roundtrip_errors.items(), key=lambda kv: -len(kv[1]))[:15]:
    print(f"  {len(names):3d}  {err}")
    for n in names[:3]:
        print(f"         e.g. {n}")

print()
print("== INLINE REASONS ==")
for reason, count in reasons.most_common(25):
    print(f"  {count:4d}  {reason}")
    for n in reason_examples[reason]:
        print(f"         e.g. {n}")

out = {
    "buckets": {k: v for k, v in buckets.items()},
    "reasons": dict(reasons),
}
with open(sys.argv[1] if len(sys.argv) > 1 else "audit_out.json", "w",
          encoding="utf8") as f:
    json.dump(out, f, indent=1, ensure_ascii=False)
print("\nsaved detail json")

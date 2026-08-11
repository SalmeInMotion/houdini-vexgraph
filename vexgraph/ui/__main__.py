import sys

from .panel import run_standalone

raise SystemExit(run_standalone(sys.argv[1] if len(sys.argv) > 1 else ""))

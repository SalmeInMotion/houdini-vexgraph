"""How much VRAM is in use, and letting go of a local model without a restart.

Ollama keeps a model resident after answering so the next question is fast. That
is the right default until you want the card back for a render, at which point
there is no obvious way to ask for it.

Releasing is deliberately narrow: `keep_alive: 0` unloads *one named model* and
nothing else. The Ollama server stays up, other models stay loaded, and nothing
about the tool's state changes - so pressing the button can never cost more than
the reload time of the model it names.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

OLLAMA_URL = "http://127.0.0.1:11434"
# "localhost" resolves to ::1 first on Windows and Ollama only listens on IPv4,
# so every call would eat the IPv6 timeout before falling back.


@dataclass(frozen=True)
class Usage:
    used_mb: int
    total_mb: int
    device: str = ""

    @property
    def fraction(self) -> float:
        return self.used_mb / self.total_mb if self.total_mb else 0.0

    def __str__(self) -> str:
        if not self.total_mb:
            return "VRAM: unknown"
        return (f"VRAM {self.used_mb / 1024:.1f} / {self.total_mb / 1024:.1f} GB"
                f"  ({self.fraction * 100:.0f}%)")


def gpu_usage() -> Usage | None:
    """Total VRAM in use on the first GPU, or None without an NVIDIA card."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,name",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    if not out:
        return None
    parts = [p.strip() for p in out[0].split(",")]
    try:
        return Usage(int(parts[0]), int(parts[1]), parts[2] if len(parts) > 2 else "")
    except (ValueError, IndexError):
        return None


def loaded_models(url: str = OLLAMA_URL, timeout: int = 3) -> list[tuple[str, int]]:
    """Which models Ollama currently holds in memory, and how big each one is."""
    try:
        with urllib.request.urlopen(f"{url}/api/ps", timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return []
    return [(m.get("name", "?"), int(m.get("size_vram", 0) or 0))
            for m in data.get("models", [])]


def release(model: str = "", url: str = OLLAMA_URL, timeout: int = 15) -> str:
    """Unload one model - or every loaded model when none is named.

    Returns a sentence for the log. Never raises: this is a convenience, and
    failing to free memory must not interrupt whatever else is happening.
    """
    targets = [model] if model else [name for name, _ in loaded_models(url)]
    if not targets:
        return "No local model is loaded."

    freed: list[str] = []
    for name in targets:
        payload = json.dumps({"model": name, "keep_alive": 0}).encode("utf-8")
        request = urllib.request.Request(
            f"{url}/api/generate", data=payload,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout):
                freed.append(name)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            return f"Could not reach Ollama to release {name} ({exc})."
    return f"Released {', '.join(freed)}. The GPU is free; the next question reloads it."

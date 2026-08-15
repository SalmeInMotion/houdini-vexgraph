"""Where the answer comes from: Claude, or a model running on this machine.

Two providers, deliberately interchangeable. The local one costs nothing and
keeps everything on the machine; Claude is markedly better at reading a vague
request and picking the right nodes, which is the part that matters most for
someone who cannot check the result by reading the VEX.

Credentials are read from the environment. The panel can ask for a key and put
it in this process's environment for the session, and will save it to your
Windows user environment only if you tick the box that says so. It is never
written into this project, and never logged - the repository is public, and a
key in a file there is a key on the internet.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Protocol

# 127.0.0.1 rather than localhost: on Windows localhost resolves to IPv6 first,
# Ollama listens on IPv4 only, and every request pays a ~2 s connect timeout.
OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_LOCAL_MODEL = "qwen3:32b"

# Opus 5 is the right tier for this: choosing correctly from a 1300-entry
# catalogue on a vague request is exactly where the difference shows.
DEFAULT_CLAUDE_MODEL = "claude-opus-5"

# Offered in the model menu, best first. Labels say what the trade actually is,
# because "which model" is otherwise a guess for anyone who does not follow
# model releases.
CLAUDE_MODELS = (
    ("claude-opus-5", "Opus 5 — best at picking the right nodes"),
    ("claude-sonnet-5", "Sonnet 5 — faster and cheaper, usually as good"),
    ("claude-haiku-4-5-20251001", "Haiku 4.5 — quickest, simple requests only"),
)

# Which request features each model actually accepts. Sending one a feature it
# does not have is a 400, not a quiet no-op: Haiku 4.5 predates both adaptive
# thinking and the effort parameter, so every request to it failed with
# "adaptive thinking is not supported on this model" until this table existed.
# An unknown model gets the plain request, which every model accepts.
MODEL_FEATURES = {
    "claude-opus-5": frozenset({"thinking", "effort", "fallbacks"}),
    "claude-sonnet-5": frozenset({"thinking", "effort"}),
    "claude-haiku-4-5-20251001": frozenset(),
}


def _ollama_answers(url: str = OLLAMA_URL, timeout: int = 2) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=timeout):
            return True
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def start_ollama(url: str = OLLAMA_URL, wait: float = 25.0) -> tuple[bool, str]:
    """Start Ollama and wait for it to answer.

    "Ollama is not running - start it and try again" is a instruction to go and
    do something in another window, which is exactly the sort of errand the
    tool should run itself. Starting it costs one process and about a second.

    Detached deliberately: the server should outlive whichever request woke it,
    so the next one finds it already up.
    """
    if _ollama_answers(url):
        return True, ""

    executable = shutil.which("ollama")
    if executable is None:
        return False, ("Ollama does not appear to be installed - there is no "
                       "`ollama` on PATH. Install it from ollama.com, or use "
                       "Claude instead.")

    options: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        # No console window, and not tied to this process's lifetime.
        options["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                    | getattr(subprocess, "DETACHED_PROCESS", 0))
    else:
        options["start_new_session"] = True
    try:
        subprocess.Popen([executable, "serve"], **options)
    except OSError as exc:
        return False, f"Could not start Ollama: {exc}"

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if _ollama_answers(url):
            return True, ""
        time.sleep(0.4)
    return False, (f"Ollama was started but did not answer at {url} within "
                   f"{wait:.0f} seconds.")


def _can_chat(name: str, url: str, timeout: int) -> bool:
    """Whether this model answers /api/chat at all.

    Ollama happily lists embedding models alongside chat models, but sending
    one a conversation is a bare `HTTP Error 400: Bad Request` with nothing in
    it to explain why - which is exactly what picking `bge-m3` from the menu
    produced. `/api/show` reports `capabilities: ['embedding']` for those, so
    they can be kept out of the menu instead of failing when chosen.

    Anything unreadable is kept: an Ollama too old to report capabilities
    should not lose you the whole menu.
    """
    request = urllib.request.Request(
        f"{url}/api/show",
        data=json.dumps({"model": name}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            shown = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return True
    capabilities = shown.get("capabilities")
    if not capabilities:
        return True
    return "completion" in capabilities


def installed_local_models(url: str = OLLAMA_URL,
                           timeout: int = 3) -> list[dict]:
    """What Ollama has pulled that can hold a conversation, and what it costs.

    Read live rather than tabulated here: the figure depends on the exact
    quantisation pulled, and a number written into this file would be wrong for
    someone else's machine on the day they read it.
    """
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=timeout) as response:
            tags = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return []
    out = []
    for model in tags.get("models", ()):
        name = model.get("name")
        if not name or not _can_chat(name, url, timeout):
            continue
        details = model.get("details") or {}
        if not model_tier({"parameters": details.get("parameter_size", "")}):
            continue                  # too small to be trusted; see model_tier
        out.append({
            "name": name,
            # Weights on disk. VRAM is this plus the KV cache for the context,
            # so the real cost is a little higher - close enough to plan by.
            "bytes": int(model.get("size", 0) or 0),
            "parameters": details.get("parameter_size", ""),
            "quantisation": details.get("quantization_level", ""),
        })
    return sorted(out, key=lambda m: m["name"])


# Below this many billion parameters, a model does not merely answer worse -
# it answers confidently wrong. Asked what the Get Attribute node is for, a
# 2.6B model invented a `get_attribute("color")` function that does not exist
# in VEX, and a 1.5B one replied in JavaScript. Neither failure is visible to
# someone who cannot check the answer, which is exactly who this tool is for,
# so models that small are not offered at all.
MINIMUM_BILLIONS = 4.0

# Where a model stops being cheap and starts being capable. Measured on this
# project rather than guessed: 8B and 12B models answered questions about a
# node correctly, 32B and up also chose nodes well.
HIGH_END_BILLIONS = 20.0


def _billions(model: dict) -> float:
    """Parameter count from Ollama's own label, e.g. "32.8B" -> 32.8."""
    match = re.match(r"\s*([\d.]+)\s*([BM])", str(model.get("parameters", "")),
                     re.IGNORECASE)
    if not match:
        return 0.0
    size = float(match.group(1))
    return size / 1000 if match.group(2).upper() == "M" else size


def model_tier(model: dict) -> str:
    """"" for unusable, "value" or "high-end" for the rest."""
    size = _billions(model)
    if size and size < MINIMUM_BILLIONS:
        return ""
    return "high-end" if size >= HIGH_END_BILLIONS else "value"


TIER_LABELS = {"value": "good value", "high-end": "high-end"}


def describe_local_model(model: dict) -> str:
    """One line for the menu: what it is, what it costs, what tier it is in."""
    gigabytes = model["bytes"] / 1e9
    parts = [model["name"]]
    tier = TIER_LABELS.get(model_tier(model))
    if tier:
        parts.append(tier)
    if gigabytes:
        parts.append(f"~{gigabytes:.1f} GB VRAM")
    if model.get("parameters"):
        parts.append(model["parameters"])
    return "  ·  ".join(parts)


def local_model_advice(model: dict, total_vram_gb: float = 0.0) -> str:
    """Whether this model can do the job, said plainly.

    Measured on this project rather than guessed: a 12B model answered in 26 s
    and wired the wrong attribute; a 32B model did not finish in 25 minutes
    under schema-constrained decoding. Nothing local has been good enough at
    choosing from a 1300-node catalogue, and saying so is more useful than a
    menu that implies the choice matters.
    """
    gigabytes = model["bytes"] / 1e9
    lines = []
    if model_tier(model) == "high-end":
        lines.append("High-end: the best local answers, and the slowest.")
    else:
        lines.append("Good value: quick, and reliable for explaining a node.")
    if total_vram_gb and gigabytes > total_vram_gb * 0.9:
        lines.append(f"Will not fit in {total_vram_gb:.0f} GB of VRAM and will "
                     f"spill to system memory, which is very slow.")
    elif total_vram_gb and gigabytes > total_vram_gb * 0.5:
        lines.append(f"Takes over half of your {total_vram_gb:.0f} GB - expect "
                     f"the rest of Houdini to feel it while it is loaded.")
    lines.append(
        "Small models (under ~8 GB) are quick but tend to pick a plausible "
        "wrong node. Larger ones choose better and get slow enough to be "
        "painful. For building a graph, Claude is the one that works; the "
        "local option is for when you have no connection.")
    return " ".join(lines)


class ProviderError(RuntimeError):
    """Something went wrong that the user can act on."""


class Provider(Protocol):
    name: str

    def available(self) -> tuple[bool, str]: ...

    def complete(self, system: str, messages: list[dict],
                 schema: dict | None) -> str: ...


# ------------------------------------------------------------------- Claude

class ClaudeProvider:
    """Claude via the official SDK."""

    name = "Claude"

    def __init__(self, model: str = DEFAULT_CLAUDE_MODEL,
                 effort: str = "high") -> None:
        self.model = model
        self.effort = effort

    def available(self) -> tuple[bool, str]:
        try:
            import anthropic  # noqa: F401, PLC0415
        except ImportError:
            return False, ("The anthropic package is not installed for this "
                           "Python. Run: pip install anthropic")
        if not (os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            return False, ("ANTHROPIC_API_KEY is not set. Set it in your "
                           "environment and restart Houdini. VEXgraph never "
                           "stores your key.")
        return True, ""

    def complete(self, system: str, messages: list[dict],
                 schema: dict | None) -> str:
        ready, why = self.available()
        if not ready:
            raise ProviderError(why)

        import anthropic  # noqa: PLC0415

        client = anthropic.Anthropic()
        features = MODEL_FEATURES.get(self.model, frozenset())

        request: dict = {
            "model": self.model,
            "max_tokens": 16000,
            "system": system,
            "messages": messages,
        }

        output_config: dict = {}
        if "effort" in features:
            output_config["effort"] = self.effort
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        if output_config:
            request["output_config"] = output_config

        if "thinking" in features:
            request["thinking"] = {"type": "adaptive"}
        if "fallbacks" in features:
            # A policy decline is re-served by the fallback model inside the
            # same call rather than surfacing as an empty answer.
            request["betas"] = ["server-side-fallback-2026-07-01"]
            request["fallbacks"] = "default"

        try:
            # Streamed because the catalogue makes this a long-input request,
            # and a non-streaming call at this size risks an HTTP timeout.
            with client.beta.messages.stream(**request) as stream:
                response = stream.get_final_message()
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"Claude returned {exc.status_code}: "
                                f"{exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"Could not reach Claude: {exc}") from exc

        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            raise ProviderError(
                f"Claude declined this request{f' ({category})' if category else ''}.")

        return "".join(block.text for block in response.content
                       if block.type == "text")


# ------------------------------------------------------------------- local

class OllamaProvider:
    """A model running on this machine, through Ollama.

    Uses urllib rather than requests so it works unchanged inside Houdini's
    Python without installing anything.
    """

    name = "Local"

    def __init__(self, model: str = DEFAULT_LOCAL_MODEL,
                 url: str = OLLAMA_URL, timeout: int = 600,
                 autostart: bool = True) -> None:
        self.model = model
        self.url = url
        self.timeout = timeout
        self.autostart = autostart
        self._start_attempted = False

    def _post(self, path: str, payload: dict, timeout: int | None = None) -> dict:
        request = urllib.request.Request(
            f"{self.url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _tags(self) -> dict | None:
        try:
            with urllib.request.urlopen(f"{self.url}/api/tags", timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def available(self) -> tuple[bool, str]:
        tags = self._tags()
        if tags is None and self.autostart and not self._start_attempted:
            # Once per provider: a machine without Ollama should not pay the
            # startup wait again on every retry.
            self._start_attempted = True
            started, why = start_ollama(self.url)
            if not started:
                return False, why
            tags = self._tags()
        if tags is None:
            return False, (f"Ollama is not answering at {self.url}. "
                           f"Start it and try again.")
        names = {m["name"] for m in tags.get("models", ())}
        if self.model not in names and self.model.split(":")[0] not in {
                n.split(":")[0] for n in names}:
            return False, (f"The model {self.model!r} is not installed. "
                           f"Install it with: ollama pull {self.model}")
        return True, ""

    def complete(self, system: str, messages: list[dict],
                 schema: dict | None) -> str:
        ready, why = self.available()
        if not ready:
            raise ProviderError(why)

        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {"num_ctx": 32768, "temperature": 0.1},
        }
        if schema is not None:
            # Ollama takes a JSON schema here and constrains decoding to it,
            # which is the local equivalent of structured outputs.
            payload["format"] = schema

        try:
            reply = self._post("/api/chat", payload)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise ProviderError(f"The local model failed: {exc}") from exc
        return reply.get("message", {}).get("content", "")


# ------------------------------------------------------------------ registry

def all_providers() -> dict[str, Provider]:
    """Every provider by name.

    Not called `providers`: that shadowed this module wherever the package was
    imported as `from ..assistant import providers`, so callers silently got
    the function instead of the module and only found out at the first
    attribute access.
    """
    return {"Claude": ClaudeProvider(), "Local": OllamaProvider()}


def get(name: str, model: str = "") -> Provider:
    try:
        provider = all_providers()[name]
    except KeyError:
        raise ProviderError(f"There is no provider called {name!r}") from None
    if model:
        provider.model = model
    return provider

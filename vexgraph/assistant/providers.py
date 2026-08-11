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


def installed_local_models(url: str = OLLAMA_URL,
                           timeout: int = 3) -> list[str]:
    """Model names Ollama already has, so the menu offers only real choices."""
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=timeout) as response:
            tags = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return []
    return sorted(m.get("name", "") for m in tags.get("models", ()) if m.get("name"))


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
        output_config: dict = {"effort": self.effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}

        try:
            # Streamed because the catalogue makes this a long-input request,
            # and a non-streaming call at this size risks an HTTP timeout.
            with client.beta.messages.stream(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config=output_config,
                # A policy decline is re-served by the fallback model inside
                # the same call rather than surfacing as an empty answer.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
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
                 url: str = OLLAMA_URL, timeout: int = 600) -> None:
        self.model = model
        self.url = url
        self.timeout = timeout

    def _post(self, path: str, payload: dict, timeout: int | None = None) -> dict:
        request = urllib.request.Request(
            f"{self.url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def available(self) -> tuple[bool, str]:
        try:
            with urllib.request.urlopen(f"{self.url}/api/tags", timeout=5) as response:
                tags = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError):
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

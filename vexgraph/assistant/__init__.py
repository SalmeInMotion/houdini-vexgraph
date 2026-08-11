"""The assistant: describe what you want, get a graph you can inspect and edit.

The model chooses nodes from a closed catalogue and never writes VEX, so it
cannot invent a function that does not exist. Everything it returns is checked
against the registry, emitted by the same deterministic compiler the editor
uses, and put past `vcc` before it is offered.
"""

from .agent import Assistant, GraphBuilder, Result
from .providers import (ClaudeProvider, OllamaProvider, ProviderError,
                        all_providers, get)

__all__ = ["Assistant", "ClaudeProvider", "GraphBuilder", "OllamaProvider",
           "ProviderError", "Result", "get", "all_providers"]

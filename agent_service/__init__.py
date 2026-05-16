"""yume agent service.

Localhost helper that runs the foreground orchestrator, the worker manager,
and the Hermes bridge. The Swift app speaks to it over a single JSON-WS
channel. See docs/spec.md sections 7.1, 9, and 10.
"""

__version__ = "0.1.0"

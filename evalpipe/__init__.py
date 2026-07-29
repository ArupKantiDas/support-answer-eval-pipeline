"""Support-answer evaluation pipeline.

Deterministic checks live in `rules` and `scoring`; the LLM is used only for
case-level qualitative judgment (`llm`). `pipeline` enforces stage ordering.
"""

__version__ = "1.0.0"

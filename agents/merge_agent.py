"""Generate a validated merge patch through a local Ollama model."""

from __future__ import annotations

import json
import os
from typing import Any

from schemas.models import ContextBundle, LLMPatch

DEFAULT_MODEL = "qwen2.5-coder:3b"


class MergeAgentError(RuntimeError):
    """Raised when Ollama is unavailable or returns an invalid patch."""


def build_prompt(context: ContextBundle, feedback: str | None = None) -> str:
    """Create a constrained prompt from extracted context and optional feedback."""
    schema = LLMPatch.schema()  # Works with the project's current Pydantic v1 models.
    sections: dict[str, Any] = context.dict()
    prompt = (
        "Resolve the merge conflict using only the supplied source context. "
        "Return one JSON object matching this schema exactly. Preserve behavior "
        "outside the target, do not invent unavailable dependencies. Use "
        "target_import with the exact existing import text when replacing an "
        "import; omit it to add a new import. Use patch_operations for import "
        "and helper edits.\n"
        f"JSON schema:\n{json.dumps(schema, indent=2)}\n"
        f"Conflict context:\n{json.dumps(sections, ensure_ascii=False, indent=2)}"
    )
    if feedback:
        prompt += f"\nVerification feedback from the previous attempt:\n{feedback[:8_000]}"
    return prompt


def generate_patch(
    context: ContextBundle,
    *,
    model: str = DEFAULT_MODEL,
    host: str | None = None,
    feedback: str | None = None,
    client: Any | None = None,
) -> LLMPatch:
    """Ask Ollama for a schema-constrained patch and validate its response.

    ``client`` is injectable for deterministic tests. The Ollama package is
    imported only when a real request is made, so parser-only installs work.
    """
    if client is None:
        try:
            from ollama import Client
        except ImportError as exc:
            raise MergeAgentError("The Ollama Python package is required (pip install ollama)") from exc
        try:
            client = Client(host=host or os.getenv("OLLAMA_HOST", "http://localhost:11434"))
        except Exception as exc:
            raise MergeAgentError(f"Could not initialize Ollama client: {exc}") from exc

    try:
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": build_prompt(context, feedback)}],
            format=LLMPatch.schema(),
            options={"temperature": 0},
            stream=False,
        )
        message = response.get("message") if isinstance(response, dict) else response.message
        content = message.get("content") if isinstance(message, dict) else message.content
        return LLMPatch.parse_raw(content)
    except Exception as exc:
        raise MergeAgentError(f"Ollama patch generation failed: {exc}") from exc

from __future__ import annotations

import abc
import json
import logging
from typing import Any, Iterable

from anthropic import Anthropic


class LLMClient(abc.ABC):
    """Abstract interface for language models used in the pipeline."""

    @abc.abstractmethod
    def complete(self, prompt: str, **kwargs: Any) -> str:
        raise NotImplementedError


class EchoLLM(LLMClient):
    """Development stub that simply bounces prompts back."""

    def complete(self, prompt: str, **kwargs: Any) -> str:
        placeholder = {
            "premise": "Stub premise",
            "controversy_summary": "Stub controversy",
            "withheld_context": "Stub withheld",
            "final_reveal": "Stub reveal",
            "beats": [],
        }
        return json.dumps(placeholder)


class OpenAILLM(LLMClient):
    """Placeholder for an OpenAI-compatible client."""

    def __init__(self, client: Any, model: str) -> None:
        self.client = client
        self.model = model

    def complete(self, prompt: str, **kwargs: Any) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": "You are a helpful story crafter."}, {"role": "user", "content": prompt}],
            **kwargs,
        )
        return response.choices[0].message.content  # type: ignore[index]


logger = logging.getLogger(__name__)


class ClaudeLLM(LLMClient):
    """Claude Sonnet 4.5 wrapper using the Anthropics Messages API."""

    def __init__(
        self,
        client: Anthropic,
        model: str,
        system_prompt: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
    ) -> None:
        self.client = client
        self.model = model
        self.system_prompt = system_prompt or (
            "You are an investigative video writer. You craft suspenseful but factual narratives, "
            "introducing controversy early, delaying context responsibly, and returning structured JSON responses."
        )
        self.max_tokens = max_tokens
        self.temperature = temperature

    def complete(self, prompt: str, **kwargs: Any) -> str:
        params: dict[str, Any] = {
            "model": self.model,
            "system": kwargs.pop("system", self.system_prompt),
            "max_tokens": kwargs.pop(
                "max_tokens", kwargs.pop("max_output_tokens", self.max_tokens)
            ),
            "temperature": kwargs.pop("temperature", self.temperature),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt,
                        }
                    ],
                }
            ],
        }
        params.update(kwargs)
        response = self.client.messages.create(**params)
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "max_tokens":
            logger.warning(
                "Claude response truncated by max_tokens; consider increasing limit (current=%s)",
                params.get("max_tokens"),
            )
        return _collect_text(response.content)


def _collect_text(blocks: Iterable[Any]) -> str:
    parts: list[str] = []
    for block in blocks:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text = getattr(block, "text", "")
            parts.append(text)
    return "".join(parts)

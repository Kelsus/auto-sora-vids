from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

from openai import BadRequestError, OpenAI
from openai.types.responses import Response

from .chart_ai_prompt import ChartCodeSpec, build_chart_codegen_prompt

logger = logging.getLogger(__name__)


@dataclass
class OpenAIChartClient:
    model: str = "gpt-5"
    api_key_env: str = "OPENAI_API_KEY"
    response_timeout: float = 180.0
    store_responses: bool = False
    assistant_id: Optional[str] = None  # kept for backward compatibility
    _client: OpenAI = field(init=False, repr=False)
    _api_key: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"Missing OpenAI API key. Set {self.api_key_env} in your environment or disable OpenAI charts."
            )
        self._api_key = api_key
        self._client = OpenAI(api_key=api_key, timeout=self.response_timeout)

    def generate_chart(self, spec: ChartCodeSpec, output_dir: Path, slug: str) -> Optional[Path]:
        """Generate a chart using OpenAI's Responses API with the code interpreter tool."""
        output_dir.mkdir(parents=True, exist_ok=True)
        prompt = build_chart_codegen_prompt(spec)

        logger.debug("Submitting chart code job via Responses API model=%s", self.model)

        fallback_model = "gpt-4o"
        candidate_models = [self.model]
        if fallback_model not in candidate_models:
            candidate_models.append(fallback_model)

        for model in candidate_models:
            result = self._run_chart_job(prompt, slug, [model])
            if not result:
                continue
            response, model_used = result
            file_ref = self._extract_chart_file_reference(response)
            if file_ref:
                if model_used != self.model:
                    logger.info(
                        "Using fallback model %s for chart beat %s", model_used, slug
                    )
                return self._download_container_file(file_ref, output_dir, slug)
            logger.warning(
                "Model %s did not return a downloadable chart for %s; trying next option",
                model_used,
                slug,
            )

        logger.warning("Responses API returned no downloadable chart for %s", slug)
        return None

    def _extract_chart_file_reference(self, response: Response) -> Optional[Tuple[str, str]]:
        container_id: Optional[str] = None
        file_id: Optional[str] = None

        # Look for explicit container/file references in assistant messages
        for item in response.output:
            if getattr(item, "type", None) != "message":
                continue
            for content in getattr(item, "content", []) or []:
                annotations = getattr(content, "annotations", []) or []
                for annotation in annotations:
                    annotation_type = getattr(annotation, "type", None)
                    if annotation_type == "container_file_citation":
                        container_id = getattr(annotation, "container_id", None)
                        file_id = getattr(annotation, "file_id", None)
                        break
                if container_id and file_id:
                    break
            if container_id and file_id:
                break

        if not (container_id and file_id):
            return None
        return container_id, file_id

    def _download_container_file(
        self,
        file_ref: Tuple[str, str],
        output_dir: Path,
        slug: str,
    ) -> Optional[Path]:
        container_id, file_id = file_ref
        try:
            content = self._client.containers.files.content.retrieve(
                container_id=container_id,
                file_id=file_id,
            )
            data = content.read()
        except Exception as exc:
            logger.error(
                "Failed to download chart file (container=%s file=%s): %s",
                container_id,
                file_id,
                exc,
            )
            return None

        target = output_dir / f"{slug}.png"
        target.write_bytes(data)
        logger.info("Downloaded chart to %s", target)
        return target

    def _run_chart_job(
        self,
        prompt: str,
        slug: str,
        models: list[str],
    ) -> Optional[Tuple[Response, str]]:
        for model in models:
            try:
                response = self._client.responses.create(
                    model=model,
                    instructions=(
                        "You are an expert data visualization coder. "
                        "Generate charts exactly as specified using Python and matplotlib or seaborn. "
                        "Save the final visualization to a PNG file before finishing."
                    ),
                    input=prompt,
                    tools=[{"type": "code_interpreter", "container": {"type": "auto"}}],
                    tool_choice={"type": "code_interpreter"},
                    store=self.store_responses,
                    include=["code_interpreter_call.outputs"],
                )
                return response, model
            except BadRequestError as exc:
                message = str(exc)
                unsupported = "Hosted tool 'code_interpreter' is not supported" in message
                if not unsupported:
                    logger.error("OpenAI chart generation failed for %s: %s", slug, exc)
                    return None
                logger.warning(
                    "Model %s does not support code interpreter for charts (%s); skipping",
                    model,
                    exc,
                )
            except Exception as exc:
                logger.error("Error generating chart with OpenAI for %s: %s", slug, exc, exc_info=True)
                return None
        return None


__all__ = ["OpenAIChartClient"]

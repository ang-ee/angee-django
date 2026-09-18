"""Bounded document extraction over Ollama's native structured-output endpoint."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Sequence
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from django.conf import settings

from angee.workflows_extraction.engines import (
    DocumentPart,
    DocumentPipelineError,
    DocumentResult,
    DocumentSource,
    ExtractionEngine,
    PageImage,
    PageResult,
    RecognitionResult,
)
from angee.workflows_extraction.routing import (
    acquire_native_parts,
    derive_text_claims,
    mapping_object,
    mapping_prompt,
    recognize_pages,
)


class GlmOllamaEngine(ExtractionEngine):
    """Acquire native evidence, recognize scanned pages, then map the whole document."""

    key = "glm"
    label = "GLM-OCR (local Ollama)"
    pipeline_version = "document-v1"
    document_engine = True

    @staticmethod
    def _base_url(model: Any | None) -> str:
        if model is None:
            raise ValueError("GLM OCR requires an inference model.")
        provider = model.provider
        if str(provider.backend_class) != "ollama":
            raise ValueError("GLM OCR requires an Ollama inference provider.")
        base_url = str(provider.base_url or "http://localhost:11434/v1").rstrip("/")
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("GLM OCR only sends document evidence to a loopback Ollama endpoint.")
        return base_url

    def validate_model(self, model: Any | None, *, role: Literal["mapping", "recognition"]) -> None:
        """Own local-provider restrictions for setup and actual inference alike."""

        self._base_url(model)
        super().validate_model(model, role=role)

    def _generate(
        self,
        prompt: str,
        *,
        model: Any,
        config: dict[str, Any],
        timeout: float,
        page: PageImage | None = None,
        schema: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Own endpoint validation, the vendor envelope, and operator-safe failures."""

        self.validate_model(model, role="recognition" if page is not None else "mapping")
        base_url = self._base_url(model)
        if timeout <= 0:
            raise TimeoutError("Document extraction exceeded its configured timeout.")
        request: dict[str, Any] = {
            "model": str(model.provider_model_name),
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0},
            "keep_alive": config.get("keep_alive", "5m"),
        }
        if config.get("max_tokens") is not None:
            max_tokens = int(config["max_tokens"])
            if max_tokens <= 0:
                raise ValueError("GLM OCR max_tokens must be positive.")
            request["options"]["num_predict"] = max_tokens
        if page is not None:
            request["images"] = [base64.b64encode(page.image_bytes).decode("ascii")]
        if schema is not None:
            request["format"] = schema
        started = time.monotonic()
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout), trust_env=False, follow_redirects=False) as client:
                response = client.post(f"{base_url.removesuffix('/v1')}/api/generate", json=request)
                response.raise_for_status()
                envelope = response.json()
            result = envelope["response"]
            if not isinstance(result, str):
                raise ValueError("Ollama response must be text.")
            metadata = {
                "duration_ms": round((time.monotonic() - started) * 1000),
                "done_reason": str(envelope.get("done_reason") or ""),
                "total_duration_ns": (
                    int(envelope["total_duration"]) if envelope.get("total_duration") is not None else None
                ),
                "prompt_tokens": (
                    int(envelope["prompt_eval_count"]) if envelope.get("prompt_eval_count") is not None else None
                ),
                "input_tokens": (
                    int(envelope["prompt_eval_count"]) if envelope.get("prompt_eval_count") is not None else None
                ),
                "output_tokens": int(envelope["eval_count"]) if envelope.get("eval_count") is not None else None,
            }
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            raise RuntimeError(f"GLM OCR request failed ({type(error).__name__}).") from None
        return result, metadata

    def recognize_page(
        self,
        page: PageImage,
        *,
        model: Any,
        config: dict[str, Any],
        timeout: float,
    ) -> RecognitionResult:
        text, metadata = self._generate("Text Recognition:", page=page, model=model, config=config, timeout=timeout)
        return RecognitionResult(text.strip(), metadata["duration_ms"], metadata)

    def extract_page(
        self,
        page: PageImage,
        schema: dict[str, Any],
        *,
        model: Any,
        config: dict[str, Any],
        timeout: float,
    ) -> PageResult:
        """Retain the page-engine API for consumers that explicitly extract one page."""

        prompt = str(config.get("prompt") or "Copy printed values into the declared JSON schema: ")
        text, metadata = self._generate(
            prompt + json.dumps(schema, sort_keys=True, ensure_ascii=False),
            page=page,
            schema=schema,
            model=model,
            config=config,
            timeout=timeout,
        )
        try:
            value = mapping_object(text)
        except (TypeError, ValueError) as error:
            raise RuntimeError(f"GLM structured output failed ({type(error).__name__}).") from None
        return PageResult(value, metadata["duration_ms"], metadata)

    def map_text_parts(
        self,
        parts: Sequence[DocumentPart],
        schema: dict[str, Any],
        *,
        model: Any | None,
        config: dict[str, Any],
        timeout: float,
    ) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], dict[str, Any]]:
        """Map retained native/structured/recognized evidence; derive claims locally."""

        prompt = mapping_prompt(parts, schema, config)
        try:
            text, metadata = self._generate(prompt, schema=schema, model=model, config=config, timeout=timeout)
        except (RuntimeError, TimeoutError, ValueError) as error:
            raise DocumentPipelineError(
                "Text schema mapping request failed.", parts=parts,
                stage="mapping_request", code=type(error).__name__,
            ) from None
        try:
            value = mapping_object(text)
        except (TypeError, ValueError) as error:
            raise DocumentPipelineError(
                "Text schema mapping response was invalid.", parts=parts,
                stage="mapping_response", code=type(error).__name__,
            ) from None
        return value, derive_text_claims(value, parts), metadata

    def extract_document(
        self,
        sources: Sequence[DocumentSource],
        schema: dict[str, Any],
        *,
        model: Any | None,
        recognition_model: Any | None,
        config: dict[str, Any],
        timeout: float,
    ) -> DocumentResult:
        """Bound the entire pipeline by one deadline and retain evidence on failure.

        A dedicated recognition model is optional; existing single-model callers
        use their configured model for recognition and mapping.
        """

        started = time.monotonic()
        acquired = acquire_native_parts(
            sources,
            dpi=int(settings.ANGEE_EXTRACTION_DPI),
            max_edge=int(settings.ANGEE_EXTRACTION_MAX_EDGE),
            max_pages=int(settings.ANGEE_EXTRACTION_MAX_PAGES),
        )
        recognized = recognize_pages(
            acquired.recognition_pages,
            engine=self,
            model=recognition_model or model,
            config=config,
            timeout=timeout - (time.monotonic() - started),
            acquired_parts=acquired.parts,
        )
        parts = (*acquired.parts, *recognized)
        value, claims, metadata = self.map_text_parts(
            parts,
            schema,
            model=model,
            config=config,
            timeout=timeout - (time.monotonic() - started),
        )
        return DocumentResult(
            value,
            parts,
            claims,
            used_model_roles=("mapping", "recognition")
            if recognized and recognition_model is not None
            else ("mapping",),
            duration_ms=round((time.monotonic() - started) * 1000),
            engine_metadata=metadata,
        )

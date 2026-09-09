"""Bounded GLM-OCR adapter over Ollama's native structured-output endpoint."""

from __future__ import annotations

import base64
import json
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from angee.workflows_ocr.engines import OcrEngine, PageImage, PageResult, RecognitionResult


class GlmOllamaEngine(OcrEngine):
    """Extract one page with a catalogue-bound local Ollama GLM model."""

    key = "glm"
    label = "GLM-OCR (local Ollama)"

    def recognize_page(
        self, page: PageImage, *, model: Any, config: dict[str, Any], timeout: float
    ) -> RecognitionResult:
        provider = model.provider
        if str(provider.backend_class) != "ollama":
            raise ValueError("GLM OCR requires an Ollama inference provider.")
        base_url = str(provider.base_url or "http://localhost:11434/v1").rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.username or parsed.password:
            raise ValueError("GLM OCR only sends document pages to a loopback Ollama endpoint.")
        started = time.monotonic()
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout), trust_env=False, follow_redirects=False) as client:
                response = client.post(
                    f"{base_url.removesuffix('/v1')}/api/generate",
                    json={
                        "model": str(model.provider_model_name),
                        "prompt": "Text Recognition:",
                        "images": [base64.b64encode(page.image_bytes).decode("ascii")],
                        "stream": False,
                        "options": {"temperature": 0},
                        "keep_alive": config.get("keep_alive", "5m"),
                    },
                )
                response.raise_for_status()
                envelope = response.json()
            text = str(envelope["response"]).strip()
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            raise RuntimeError(f"GLM text recognition failed ({type(error).__name__}).") from None
        return RecognitionResult(
            text=text,
            duration_ms=round((time.monotonic() - started) * 1000),
            engine_metadata={"done_reason": str(envelope.get("done_reason") or "")},
        )

    def extract_page(
        self,
        page: PageImage,
        schema: dict[str, Any],
        *,
        model: Any,
        config: dict[str, Any],
        timeout: float,
    ) -> PageResult:
        provider = model.provider
        if str(provider.backend_class) != "ollama":
            raise ValueError("GLM OCR requires an Ollama inference provider.")
        base_url = str(provider.base_url or "http://localhost:11434/v1").rstrip("/")
        parsed = urlsplit(base_url)
        if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.username or parsed.password:
            raise ValueError("GLM OCR only sends document pages to a loopback Ollama endpoint.")
        native_root = base_url.removesuffix("/v1")
        prompt = str(
            config.get("prompt")
            or "Extract the document into exactly this JSON schema. Preserve printed values "
            "verbatim and use null for absent optional values. Schema: "
        )
        prompt = f"{prompt}{json.dumps(schema, sort_keys=True, ensure_ascii=False)}"
        started = time.monotonic()
        try:
            with httpx.Client(timeout=httpx.Timeout(timeout), trust_env=False, follow_redirects=False) as client:
                response = client.post(
                    f"{native_root}/api/generate",
                    json={
                        "model": str(model.provider_model_name),
                        "prompt": prompt,
                        "images": [base64.b64encode(page.image_bytes).decode("ascii")],
                        "format": schema,
                        "stream": False,
                        "options": {"temperature": 0},
                        "keep_alive": config.get("keep_alive", "5m"),
                    },
                )
                response.raise_for_status()
                envelope = response.json()
            value = json.loads(envelope["response"])
            if not isinstance(value, dict):
                raise ValueError("structured output root is not an object")
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            # Provider bodies and model output can contain source values. Expose
            # only the exception class; page evidence remains confined to rows.
            raise RuntimeError(f"GLM OCR request failed ({type(error).__name__}).") from None
        return PageResult(
            value=value,
            duration_ms=round((time.monotonic() - started) * 1000),
            engine_metadata={
                "done_reason": str(envelope.get("done_reason") or ""),
                "total_duration_ns": int(envelope.get("total_duration") or 0),
                "prompt_tokens": int(envelope.get("prompt_eval_count") or 0),
                "output_tokens": int(envelope.get("eval_count") or 0),
            },
        )

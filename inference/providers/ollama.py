"""Ollama provider wrapping /api/generate."""
from __future__ import annotations

import logging
import time
from typing import Any, Mapping

import requests

from engine.operators.image_processor import ImageProcessor
from inference.providers.base import InferenceProvider
from inference.types import InferenceRequest, InferenceResponse

logger = logging.getLogger(__name__)


def resolve_ollama_base_urls(model_config: Mapping[str, Any]) -> list[str]:
    """Primary endpoint or optional ``ollama_endpoints`` / ``ollama_ports`` (+ ``ollama_host``)."""
    raw_eps = model_config.get("ollama_endpoints")
    raw_ports = model_config.get("ollama_ports")
    host = str(model_config.get("ollama_host") or "http://127.0.0.1").rstrip("/")
    if raw_eps:
        out = [str(e).strip().rstrip("/") for e in raw_eps if str(e).strip()]
        if out:
            return out
    if raw_ports:
        ports_out: list[str] = []
        for p in raw_ports:
            try:
                ports_out.append(f"{host}:{int(p)}")
            except (TypeError, ValueError):
                continue
        if ports_out:
            return ports_out
    ep = str(model_config.get("endpoint") or "http://localhost:11434").strip().rstrip("/")
    return [ep]


def verify_ollama_connection(base_urls: list[str], model_name: str, *, tags_timeout: int = 5) -> None:
    """Verify Ollama server(s) are running and log if the requested model tag is missing."""
    for base in base_urls:
        try:
            response = requests.get(f"{base.rstrip('/')}/api/tags", timeout=tags_timeout)
            if response.status_code == 200:
                models = response.json().get("models", [])
                model_names = [m.get("name", "").split(":")[0] for m in models]
                if model_name not in model_names:
                    logger.warning(
                        "Model '%s' not found at %s. Available: %s",
                        model_name,
                        base,
                        model_names,
                    )
            else:
                logger.warning("Ollama server %s returned status %s", base, response.status_code)
        except requests.ConnectionError:
            logger.error(
                "Cannot connect to Ollama at %s. Make sure Ollama is running.",
                base,
            )
            raise
        except Exception as e:
            logger.warning("Error verifying Ollama connection at %s: %s", base, e)


class OllamaProvider(InferenceProvider):
    PROVIDER_ID = "ollama"

    def supports_batch(self) -> bool:
        # Ollama HTTP API is one generate per request; queue uses single-job path (no coalescing wait).
        return False

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        port: int | None = None,
        host: str = "http://127.0.0.1",
        temperature: float,
        num_predict: int,
        timeout: int,
        max_retries: int,
        retry_delay: float,
    ) -> None:
        if endpoint:
            self.endpoint = endpoint.rstrip("/")
        elif port is not None:
            self.endpoint = f"{host.rstrip('/')}:{int(port)}"
        else:
            raise ValueError("OllamaProvider requires ``endpoint`` or ``port``")
        self.temperature = temperature
        self.num_predict = num_predict
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def generate(self, request: InferenceRequest, *, model_name: str) -> InferenceResponse:
        thumb_side = request.metadata.get("vlm_thumbnail_max_side")
        max_size: tuple[int, int] = (768, 768)
        if thumb_side is not None:
            try:
                s = max(160, min(2048, int(thumb_side)))
                max_size = (s, s)
            except (TypeError, ValueError):
                pass

        img_base64 = ImageProcessor.get_optimized_base64(request.image_path, max_size=max_size)
        url = f"{self.endpoint}/api/generate"
        read_cap = request.metadata.get("inference_read_timeout_seconds")
        read_timeout = int(read_cap) if read_cap is not None else int(self.timeout)
        read_timeout = max(1, read_timeout)
        connect_t = min(60, max(10, read_timeout // 3))
        req_timeout = (connect_t, read_timeout)
        num_predict = int(request.metadata.get("num_predict", self.num_predict))

        attempts = self.max_retries + 1
        for attempt in range(attempts):
            payload: dict[str, Any] = {
                "model": model_name,
                "prompt": request.prompt,
                "images": [img_base64],
                "stream": False,
                "options": {
                    "temperature": self.temperature,
                    "num_predict": num_predict,
                },
            }
            try:
                response = requests.post(url, json=payload, timeout=req_timeout)
                response.raise_for_status()
                result = response.json()
                return InferenceResponse(
                    status="success",
                    text=result.get("response", "").strip(),
                    model=model_name,
                    metadata={
                        "eval_count": result.get("eval_count"),
                        "prompt_eval_count": result.get("prompt_eval_count"),
                        # Ollama reports durations in nanoseconds: prefill vs decode wall time.
                        "prompt_eval_duration": result.get("prompt_eval_duration"),
                        "eval_duration": result.get("eval_duration"),
                    },
                )
            except (requests.Timeout, requests.ConnectionError) as e:
                if attempt + 1 < attempts:
                    time.sleep(self.retry_delay)
                    continue
                return InferenceResponse(status="error", error=str(e), model=model_name)
            except requests.HTTPError as e:
                sc = e.response.status_code if e.response is not None else None
                return InferenceResponse(
                    status="error",
                    error=str(e),
                    model=model_name,
                    metadata={"http_status": sc},
                )
            except Exception as e:
                return InferenceResponse(
                    status="error",
                    error=str(e),
                    model=model_name,
                )


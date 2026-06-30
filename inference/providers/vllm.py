"""
OpenAI-compatible provider (vLLM / TGI / OpenAI) wrapping ``POST /v1/chat/completions``.

vLLM serves an OpenAI-compatible HTTP API and does **continuous batching server-side**, so the
client just needs to send concurrent single-image requests; the queue's ``num_workers`` controls
admission. Token usage from ``usage.{prompt,completion}_tokens`` is mapped onto the same
``prompt_eval_count`` / ``eval_count`` metadata keys Ollama uses, so the ``model_runs`` token/cost
closeloop and ``scripts/load_test.py`` work unchanged across backends.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Mapping

import requests

from engine.operators.image_processor import ImageProcessor
from inference.providers.base import InferenceProvider
from inference.types import InferenceRequest, InferenceResponse

logger = logging.getLogger(__name__)


def resolve_vllm_base_urls(model_config: Mapping[str, Any]) -> list[str]:
    """Primary endpoint or optional ``vllm_endpoints`` / ``vllm_ports`` (+ ``vllm_host``)."""
    raw_eps = model_config.get("vllm_endpoints") or model_config.get("openai_endpoints")
    raw_ports = model_config.get("vllm_ports")
    host = str(model_config.get("vllm_host") or "http://127.0.0.1").rstrip("/")
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
    ep = str(model_config.get("endpoint") or "http://localhost:8000").strip().rstrip("/")
    return [ep]


def chat_completions_url(endpoint: str) -> str:
    """Normalize a base URL to its ``/v1/chat/completions`` path (idempotent)."""
    base = str(endpoint or "").strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


class VLLMProvider(InferenceProvider):
    """OpenAI-compatible chat-completions backend (default target: a local vLLM server)."""

    PROVIDER_ID = "vllm"

    def supports_batch(self) -> bool:
        # vLLM batches server-side (continuous batching); no client-side request coalescing.
        return False

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        temperature: float,
        num_predict: int,
        timeout: int,
        max_retries: int,
        retry_delay: float,
        api_key: str | None = None,
    ) -> None:
        if not endpoint:
            raise ValueError("VLLMProvider requires ``endpoint`` (e.g. http://localhost:8000)")
        self.url = chat_completions_url(endpoint)
        self.temperature = temperature
        self.num_predict = num_predict
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.api_key = (api_key or "").strip() or None

    def _max_size(self, request: InferenceRequest) -> tuple[int, int]:
        thumb_side = request.metadata.get("vlm_thumbnail_max_side")
        if thumb_side is None:
            return (768, 768)
        try:
            s = max(160, min(2048, int(thumb_side)))
            return (s, s)
        except (TypeError, ValueError):
            return (768, 768)

    def generate(self, request: InferenceRequest, *, model_name: str) -> InferenceResponse:
        img_base64 = ImageProcessor.get_optimized_base64(request.image_path, max_size=self._max_size(request))
        read_cap = request.metadata.get("inference_read_timeout_seconds")
        read_timeout = max(1, int(read_cap) if read_cap is not None else int(self.timeout))
        connect_t = min(60, max(10, read_timeout // 3))
        req_timeout = (connect_t, read_timeout)
        num_predict = int(request.metadata.get("num_predict", self.num_predict))

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": request.prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img_base64}"},
                        },
                    ],
                }
            ],
            "max_tokens": num_predict,
            "temperature": self.temperature,
            "stream": False,
        }
        # JSON mode: instruct vLLM to guarantee well-formed JSON output.
        # Enabled via request metadata ``json_mode=True`` or provider config ``json_mode=True``.
        # Requires a model that supports guided decoding (Qwen2-VL, LLaVA-Next, etc.).
        if request.metadata.get("json_mode"):
            payload["response_format"] = {"type": "json_object"}

        attempts = self.max_retries + 1
        for attempt in range(attempts):
            try:
                response = requests.post(self.url, json=payload, headers=headers, timeout=req_timeout)
                response.raise_for_status()
                result = response.json()
                return self._parse_success(result, model_name)
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
                return InferenceResponse(status="error", error=str(e), model=model_name)
        return InferenceResponse(status="error", error="unreachable", model=model_name)

    def generate_structured(
        self,
        request: "InferenceRequest",
        *,
        model_name: str,
        response_model: type,
    ) -> Any:
        """Structured output via ``instructor`` + Pydantic — guaranteed schema conformance.

        Requires ``openai`` and ``instructor`` packages.  Falls back to ``generate()`` +
        manual Pydantic validation when instructor is unavailable.

        Example::

            from inference.schemas import Stage3FullResponse
            result: Stage3FullResponse = provider.generate_structured(
                request, model_name=model_name, response_model=Stage3FullResponse
            )
            parsed = result.to_parsed_dict()
        """
        try:
            import instructor
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "generate_structured requires 'openai' and 'instructor'; "
                "run: pip install openai instructor"
            ) from exc

        base_url = self.url.removesuffix("/chat/completions")
        oai_client = OpenAI(base_url=base_url, api_key=self.api_key or "NONE")
        client = instructor.from_openai(oai_client, mode=instructor.Mode.JSON)

        img_base64 = ImageProcessor.get_optimized_base64(
            request.image_path, max_size=self._max_size(request)
        )
        num_predict = int(request.metadata.get("num_predict", self.num_predict))
        return client.chat.completions.create(
            model=model_name,
            response_model=response_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": request.prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img_base64}"},
                        },
                    ],
                }
            ],
            max_tokens=num_predict,
            temperature=self.temperature,
        )

    @staticmethod
    def _parse_success(result: Mapping[str, Any], model_name: str) -> InferenceResponse:
        choices = result.get("choices") or []
        text = ""
        if choices:
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                # Some servers return content parts; concatenate text parts.
                text = "".join(
                    str(part.get("text", "")) for part in content if isinstance(part, dict)
                )
        usage = result.get("usage") or {}
        return InferenceResponse(
            status="success",
            text=text.strip(),
            model=str(result.get("model") or model_name),
            metadata={
                # Map OpenAI usage onto the Ollama-shaped keys the token/cost closeloop reads.
                "prompt_eval_count": usage.get("prompt_tokens"),
                "eval_count": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "finish_reason": (choices[0].get("finish_reason") if choices else None),
            },
        )

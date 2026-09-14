from __future__ import annotations

import os
import base64
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class ChatJsonClient(Protocol):
    def complete_json(self, *, system_prompt: str, user_prompt: str) -> str:
        ...


@dataclass(slots=True)
class LlmClientConfig:
    model: str = "qwen3.6-plus"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key_env_names: tuple[str, ...] = (
        "TEXTBOOK_BUILDER_LLM_API_KEY",
        "DASHSCOPE_API_KEY",
    )
    api_key: str = ""
    temperature: float = 0.2
    extra_body: dict[str, object] = field(
        default_factory=lambda: {"enable_thinking": False}
    )

    def resolved_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        for env_name in self.api_key_env_names:
            value = os.getenv(env_name, "").strip()
            if value:
                return value
        return ""


class OpenAICompatibleChatClient:
    def __init__(self, config: LlmClientConfig | None = None) -> None:
        self._config = config or LlmClientConfig()
        self.last_call_metadata: dict[str, object] = {}
        self._response_format_fallback = False

    def complete_json(self, *, system_prompt: str, user_prompt: str) -> str:
        api_key = self._config.resolved_api_key()
        if not api_key:
            raise RuntimeError(
                "LLM API key is not configured. Set TEXTBOOK_BUILDER_LLM_API_KEY "
                "or DASHSCOPE_API_KEY before running live model extraction."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "The openai package is required for live LLM extraction."
            ) from exc

        client = OpenAI(api_key=api_key, base_url=self._config.base_url)
        completion = self._create_completion(
            client,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        self.last_call_metadata = self._completion_metadata(completion)
        return str(completion.choices[0].message.content or "")

    def complete_json_with_images(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_paths: list[str | Path],
    ) -> str:
        api_key = self._config.resolved_api_key()
        if not api_key:
            raise RuntimeError(
                "LLM API key is not configured. Set TEXTBOOK_BUILDER_LLM_API_KEY "
                "or DASHSCOPE_API_KEY, or enter it in the local workbench."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "The openai package is required for live LLM extraction."
            ) from exc

        content: list[dict[str, object]] = [{"type": "text", "text": user_prompt}]
        for image_path in image_paths:
            path = Path(image_path)
            mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("utf-8")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                }
            )

        client = OpenAI(api_key=api_key, base_url=self._config.base_url)
        completion = self._create_completion(
            client,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
        )
        self.last_call_metadata = self._completion_metadata(completion)
        return str(completion.choices[0].message.content or "")

    def _create_completion(self, client: object, *, messages: list[dict[str, object]]) -> object:
        self._response_format_fallback = False
        kwargs = {
            "model": self._config.model,
            "messages": messages,
            "temperature": self._config.temperature,
            "extra_body": self._config.extra_body,
        }
        try:
            return client.chat.completions.create(
                **kwargs,
                response_format={"type": "json_object"},
            )
        except Exception:
            self._response_format_fallback = True
            return client.chat.completions.create(**kwargs)

    def _completion_metadata(self, completion: object) -> dict[str, object]:
        choices = getattr(completion, "choices", []) or []
        first_choice = choices[0] if choices else None
        usage = getattr(completion, "usage", None)
        metadata: dict[str, object] = {
            "response_id": str(getattr(completion, "id", "") or ""),
            "model": str(getattr(completion, "model", "") or self._config.model),
            "created": getattr(completion, "created", None),
            "finish_reason": str(getattr(first_choice, "finish_reason", "") or ""),
            "response_format_fallback": self._response_format_fallback,
        }
        if usage is not None:
            metadata["usage"] = {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            }
        return metadata

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.config import settings


class LLMStructuredClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.deepseek_api_key
        self.base_url = (base_url or settings.deepseek_base_url).rstrip("/")
        self.model = model or settings.deepseek_model
        self.timeout = timeout or settings.llm_timeout_seconds

    def _url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def complete_json(
        self,
        system: str,
        user: str,
    ) -> dict[str, Any] | None:
        if not self.api_key:
            return None

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            response = httpx.post(
                self._url(),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            text = str(response.json()["choices"][0]["message"]["content"]).strip()
            return _parse_json_object(text)
        except Exception:
            return None

    def complete_text(
        self,
        system: str,
        user: str,
    ) -> str | None:
        if not self.api_key:
            return None
        try:
            response = httpx.post(
                self._url(),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"]).strip()
        except Exception:
            return None


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None

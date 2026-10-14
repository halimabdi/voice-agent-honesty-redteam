"""Shared OpenRouter chat-completion client."""
import os

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def chat(model: str, messages: list, temperature: float = 0.8, max_tokens: int = 100) -> str:
    api_key = os.environ["OPENROUTER_API_KEY"]
    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()

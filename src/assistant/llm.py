"""LLM providers behind one tiny interface: `complete(system, messages) -> str`.

* `AnthropicLLM` (Claude) and `OpenAILLM` call the official SDKs. Keys come from environment variables or
  Streamlit secrets (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`); they are never written anywhere.
* `MockLLM` needs no key and no network: it answers with the SQL of the closest worked example. It is what the
  tests and GitHub Actions use, and what the app falls back to without a key (an honest "demo mode" that shows
  the whole pipeline: retrieval, SQL guard, execution, chart).

`resolve_llm()` picks the provider: LLM_PROVIDER if set, otherwise Claude if its key exists, otherwise OpenAI,
otherwise the mock.
"""
from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Protocol

from src.assistant.retriever import KnowledgeRetriever

DEFAULT_MODELS = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-5-mini", "mock": "nearest-example"}
TASK_MARKER = "### Task:"


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    provider: str
    model: str

    def complete(self, system: str, messages: list[dict]) -> str: ...


def _friendly(vendor: str, exc: Exception) -> str:
    """API error with a plain explanation of the usual causes. Always contains 'API error' (run_eval stops on it)."""
    text = str(exc)
    if "401" in text or "authentication" in text.lower() or "invalid x-api-key" in text.lower():
        hint = "the API key was rejected. Check that the whole key is saved, without spaces, in the secrets."
    elif "credit balance" in text.lower():
        hint = "the API account has no credit left (Plans & Billing in the console)."
    elif "429" in text or "rate" in text.lower():
        hint = "too many requests right now. Wait a moment and ask again."
    else:
        hint = "the request failed."
    return f"{vendor} API error: {hint} ({text[:200]})"


class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, api_key: str | None = None, model: str | None = None, client=None, max_tokens: int = 8000):
        self.model = model or DEFAULT_MODELS["anthropic"]
        self.max_tokens = max_tokens
        if client is None:
            import anthropic  # imported lazily: not needed for tests or the mock

            client = anthropic.Anthropic(api_key=api_key)
        self.client = client

    def complete(self, system: str, messages: list[dict]) -> str:
        try:
            resp = self.client.messages.create(model=self.model, max_tokens=self.max_tokens, system=system,
                                               messages=messages)
        except Exception as exc:  # network, auth, rate limit...
            raise LLMError(_friendly("Claude", exc)) from exc
        # Sonnet 5.5 always thinks first: keep only the text blocks of the reply
        return "".join(block.text for block in resp.content if getattr(block, "type", "text") == "text").strip()


class OpenAILLM:
    provider = "openai"

    def __init__(self, api_key: str | None = None, model: str | None = None, client=None):
        self.model = model or DEFAULT_MODELS["openai"]
        if client is None:
            import openai

            client = openai.OpenAI(api_key=api_key)
        self.client = client

    def complete(self, system: str, messages: list[dict]) -> str:
        try:
            resp = self.client.chat.completions.create(
                model=self.model, messages=[{"role": "system", "content": system}, *messages])
        except Exception as exc:
            raise LLMError(_friendly("OpenAI", exc)) from exc
        return (resp.choices[0].message.content or "").strip()


class MockLLM:
    """Offline stand-in: returns the SQL of the most similar worked example. Deterministic, no key, no network."""

    provider = "mock"
    model = DEFAULT_MODELS["mock"]

    def __init__(self, retriever: KnowledgeRetriever):
        self.retriever = retriever

    def complete(self, system: str, messages: list[dict]) -> str:
        last = messages[-1]["content"]
        task = re.search(rf"{re.escape(TASK_MARKER)}\s*(\w+)", last)
        task = task.group(1) if task else "write_sql"
        if task == "write_sql":
            question = re.search(r"Question:\s*(.+)", last)
            example = self.retriever.closest_example(question.group(1) if question else last)
            if example is None:
                return json.dumps({"sql": None, "notes": "Demo mode: no worked example is close to this question."})
            return json.dumps({"sql": example.sql, "chart": None,
                               "notes": f'Demo mode (no LLM): reused the closest worked example, "{example.question}".'})
        if task == "fix_sql":
            return json.dumps({"sql": None, "notes": "Demo mode cannot repair a query."})
        return ""  # explanations fall back to a deterministic summary of the result


def _setting(name: str, secrets: Mapping | None) -> str | None:
    value = os.environ.get(name)
    if not value and secrets is not None:
        try:
            value = secrets.get(name)
        except Exception:  # Streamlit raises if no secrets file exists
            value = None
    value = str(value).strip().strip('"').strip() if value else ""  # pasted keys often carry spaces or newlines
    return value or None


def resolve_llm(retriever: KnowledgeRetriever, provider: str | None = None, secrets: Mapping | None = None) -> LLM:
    provider = (provider or _setting("LLM_PROVIDER", secrets) or "").lower() or None
    anthropic_key = _setting("ANTHROPIC_API_KEY", secrets)
    openai_key = _setting("OPENAI_API_KEY", secrets)
    model = _setting("LLM_MODEL", secrets)
    if provider is None:
        provider = "anthropic" if anthropic_key else "openai" if openai_key else "mock"
    if provider == "anthropic":
        if not anthropic_key:
            raise LLMError("LLM_PROVIDER is 'anthropic' but ANTHROPIC_API_KEY is not set.")
        return AnthropicLLM(anthropic_key, model)
    if provider == "openai":
        if not openai_key:
            raise LLMError("LLM_PROVIDER is 'openai' but OPENAI_API_KEY is not set.")
        return OpenAILLM(openai_key, model)
    if provider == "mock":
        return MockLLM(retriever)
    raise LLMError(f"Unknown LLM_PROVIDER '{provider}' (use anthropic, openai or mock).")

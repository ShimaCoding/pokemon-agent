import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

PROVIDERS: dict[str, dict] = {
    "groq": {
        "model_id": "groq/llama-3.1-70b-versatile",
        "env_var": "GROQ_API_KEY",
        "label": "Groq (llama-3.1-70b)",
        "order": 1,
    },
    "gemini": {
        "model_id": "gemini/gemini-2.5-flash",
        "env_var": "GEMINI_API_KEY",
        "label": "Gemini (gemini-2.5-flash)",
        "order": 2,
    },
    "openai": {
        "model_id": "openai/gpt-4o-mini",
        "env_var": "OPENAI_API_KEY",
        "label": "OpenAI (gpt-4o-mini)",
        "order": 3,
    },
}


def get_available_providers() -> list[dict]:
    """Return providers sorted by order whose API key env var is set."""
    available = []
    for name, config in PROVIDERS.items():
        api_key = os.environ.get(config["env_var"], "").strip()
        if api_key:
            available.append({"name": name, **config, "api_key": api_key})
    return sorted(available, key=lambda p: p["order"])


def build_litellm_router(provider_name: Optional[str] = None):
    """
    Build and return a LiteLLM Router.

    If provider_name is given and available, returns a single-model Router (no fallback).
    If provider_name is None or unavailable, returns a Router with all available providers.

    All entries use the shared model_name alias "agent-model" so LiteLLMModel can use
    model_id="agent-model" regardless of which providers are active.

    Raises RuntimeError if no providers are configured.
    """
    from litellm import Router

    available = get_available_providers()
    if not available:
        raise RuntimeError(
            "No LLM providers configured. Set at least one of: "
            + ", ".join(p["env_var"] for p in PROVIDERS.values())
        )

    # Pin to a specific provider if requested and available
    if provider_name and provider_name in PROVIDERS:
        pinned = next((p for p in available if p["name"] == provider_name), None)
        if pinned:
            model_list = [
                {
                    "model_name": "agent-model",
                    "litellm_params": {
                        "model": pinned["model_id"],
                        "api_key": pinned["api_key"],
                    },
                }
            ]
            router = Router(
                model_list=model_list,
                num_retries=3,
                retry_after=2,
                allowed_fails=2,
                cooldown_time=60,
                routing_strategy="least-busy",
            )
            return router, pinned["label"]

    # Full router with all available providers and automatic fallback
    model_list = [
        {
            "model_name": "agent-model",
            "litellm_params": {
                "model": p["model_id"],
                "api_key": p["api_key"],
            },
        }
        for p in available
    ]
    router = Router(
        model_list=model_list,
        num_retries=3,
        retry_after=2,
        allowed_fails=2,
        cooldown_time=60,
        routing_strategy="least-busy",
    )
    # Label reflects the primary (first-priority) provider
    primary_label = available[0]["label"]
    return router, f"{primary_label} (auto-fallback)"

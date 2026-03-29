import importlib.metadata as _meta
import os
import sys
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# Guard against known-malicious LiteLLM versions (supply chain attack, March 2026).
# Versions 1.82.7 and 1.82.8 exfiltrate API keys; refuse to start.
_litellm_version = _meta.version("litellm")
if _litellm_version in ("1.82.7", "1.82.8"):
    sys.exit(
        f"SECURITY ERROR: litellm=={_litellm_version} contains a supply chain compromise "
        "that exfiltrates API keys. Downgrade to litellm==1.82.6 immediately and rotate "
        "any keys that may have been exposed."
    )

PROVIDERS: dict[str, dict] = {
    "groq": {
        "model_id": "groq/llama-3.3-70b-versatile",
        "env_var": "GROQ_API_KEY",
        "label": "Groq (llama-3.3-70b)",
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
    "openrouter": {
        "model_ids": [
            "openrouter/stepfun/step-3.5-flash:free",
            "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
            "openrouter/z-ai/glm-4.5-air:free",
            "openrouter/arcee-ai/trinity-mini:free",
            "openrouter/minimax/minimax-m2.5:free",
            "openrouter/openai/gpt-oss-120b:free",
            "openrouter/meta-llama/llama-3.3-70b-instruct:free",
            "openrouter/openai/gpt-oss-20b:free",
        ],
        "env_var": "OPENROUTER_API_KEY",
        "label": "OpenRouter (free tier)",
        "order": 4,
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


def _build_router_model_list(providers: list[dict]) -> list[dict]:
    """
    Expand providers into LiteLLM Router deployment entries, all aliased to
    "agent-model" so the Strands agent never needs provider-aware logic.

    Providers with a single model_id produce one entry; providers with model_ids
    produce one entry per model. OpenRouter deployments inject the HTTP-Referer
    and X-Title headers required by OpenRouter's routing layer — LiteLLM strips
    unknown top-level keys, so the headers must live inside litellm_params.
    """
    site_url = os.environ.get("OR_SITE_URL", "")
    app_name = os.environ.get("OR_APP_NAME", "pokemon-agent")

    model_list: list[dict] = []
    for p in providers:
        model_ids: list[str] = p.get("model_ids") or [p["model_id"]]
        for model_id in model_ids:
            litellm_params: dict = {
                "model": model_id,
                "api_key": p["api_key"],
            }
            if p["name"] == "openrouter":
                extra_headers: dict = {"X-Title": app_name}
                if site_url:
                    extra_headers["HTTP-Referer"] = site_url
                litellm_params["extra_headers"] = extra_headers
            model_list.append(
                {"model_name": "agent-model", "litellm_params": litellm_params}
            )
    return model_list


def build_litellm_router(provider_name: Optional[str] = None):
    """
    Build and return a LiteLLM Router.

    If provider_name is given and available, returns a single-provider Router (no fallback).
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
            model_list = _build_router_model_list([pinned])
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
    model_list = _build_router_model_list(available)
    router = Router(
        model_list=model_list,
        num_retries=3,
        retry_after=2,
        allowed_fails=2,
        cooldown_time=60,
        routing_strategy="least-busy",
    )
    primary_label = available[0]["label"]
    return router, f"{primary_label} (auto-fallback)"

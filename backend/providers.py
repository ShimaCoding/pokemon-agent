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
        "model_ids": [
            "groq/llama-3.3-70b-versatile",
        ],
        "env_var": "GROQ_API_KEY",
        "label": "Groq (llama-3.3-70b-versatile)",
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


def _build_router_model_list(providers: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Expand providers into LiteLLM Router deployment entries.

    Each provider gets its own model group name (e.g. "groq-models") so that
    LiteLLM Router's ``fallbacks`` parameter can trigger cross-provider failover.
    All models within a provider share that provider's group name so the Router
    can load-balance across them before giving up and moving to the next group.

    Returns:
        (model_list, group_names) — group_names preserves provider order.
    """
    site_url = os.environ.get("OR_SITE_URL", "")
    app_name = os.environ.get("OR_APP_NAME", "pokemon-agent")

    model_list: list[dict] = []
    group_names: list[str] = []
    for p in providers:
        group_name = f"{p['name']}-models"
        group_names.append(group_name)
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
                {"model_name": group_name, "litellm_params": litellm_params}
            )
    return model_list, group_names


def build_litellm_router(provider_name: Optional[str] = None):
    """
    Build and return a LiteLLM Router.

    If provider_name is given and available, returns a single-provider Router
    (no cross-provider fallback) with model group name ``"agent-model"``.

    If provider_name is None or unavailable, returns a Router with all available
    providers using per-provider model group names and explicit ``fallbacks`` so
    that when one provider exhausts its retries the Router automatically moves to
    the next provider instead of raising.

    Returns:
        (Router, primary_model_group, provider_label)
        primary_model_group is the model group name the caller must pass to
        router.acompletion() / router.completion().

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
            model_list, _ = _build_router_model_list([pinned])
            # Rename the single group to the generic alias so callers are uniform
            for entry in model_list:
                entry["model_name"] = "agent-model"
            router = Router(
                model_list=model_list,
                num_retries=3,
                retry_after=2,
                allowed_fails=1,
                cooldown_time=3600,
                routing_strategy="least-busy",
            )
            return router, "agent-model", pinned["label"]

    # Full router with all available providers and explicit cross-provider fallbacks.
    # Each provider gets its own model group; the first group is the primary target
    # and the rest are listed as fallbacks so LiteLLM Router actually switches
    # providers once the primary group exhausts its retries.
    model_list, group_names = _build_router_model_list(available)
    primary = group_names[0]
    fallbacks = [{primary: group_names[1:]}] if len(group_names) > 1 else []
    router = Router(
        model_list=model_list,
        fallbacks=fallbacks,
        num_retries=3,
        retry_after=2,
        allowed_fails=1,
        cooldown_time=3600,
        routing_strategy="least-busy",
    )
    primary_label = available[0]["label"]
    return router, primary, f"{primary_label} (auto-fallback)"

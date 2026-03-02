"""LiteLLM router configuration — two model tiers with fallbacks.

Tier 1 (fast): High-throughput, lower-cost tasks — summarisation, classification,
               UI config generation.
Tier 2 (reasoning): Complex analysis, experiment design, feature proposals.
"""

from __future__ import annotations

import os
from typing import Any

import litellm
from litellm import Router

# Suppress litellm verbose logging in production
litellm.set_verbose = os.getenv("LITELLM_VERBOSE", "false").lower() == "true"


def _build_model_list() -> list[dict[str, Any]]:
    """Build the model list from environment variables with sensible defaults."""
    # ---- Fast tier ----
    fast_primary = os.getenv("LLM_FAST_PRIMARY", "gpt-4o-mini")
    fast_fallback = os.getenv("LLM_FAST_FALLBACK", "claude-3-5-haiku-20241022")

    # ---- Reasoning tier ----
    reasoning_primary = os.getenv("LLM_REASONING_PRIMARY", "gpt-4o")
    reasoning_fallback = os.getenv("LLM_REASONING_FALLBACK", "claude-sonnet-4-20250514")

    return [
        # Fast tier
        {
            "model_name": "fast",
            "litellm_params": {
                "model": fast_primary,
                "api_key": os.getenv("OPENAI_API_KEY", ""),
                "max_tokens": 4096,
                "temperature": 0.3,
            },
            "model_info": {"id": "fast-primary"},
        },
        {
            "model_name": "fast",
            "litellm_params": {
                "model": fast_fallback,
                "api_key": os.getenv("ANTHROPIC_API_KEY", ""),
                "max_tokens": 4096,
                "temperature": 0.3,
            },
            "model_info": {"id": "fast-fallback"},
        },
        # Reasoning tier
        {
            "model_name": "reasoning",
            "litellm_params": {
                "model": reasoning_primary,
                "api_key": os.getenv("OPENAI_API_KEY", ""),
                "max_tokens": 8192,
                "temperature": 0.2,
            },
            "model_info": {"id": "reasoning-primary"},
        },
        {
            "model_name": "reasoning",
            "litellm_params": {
                "model": reasoning_fallback,
                "api_key": os.getenv("ANTHROPIC_API_KEY", ""),
                "max_tokens": 8192,
                "temperature": 0.2,
            },
            "model_info": {"id": "reasoning-fallback"},
        },
    ]


def create_llm_router() -> Router:
    """Create a LiteLLM Router with fallback-based routing.

    The router tries the primary model first and falls back to the
    secondary on failure or timeout.
    """
    model_list = _build_model_list()

    router = Router(
        model_list=model_list,
        routing_strategy="simple-shuffle",  # tries in order, falls back on error
        num_retries=2,
        timeout=60,
        retry_after=5,
        fallbacks=[
            {"fast": ["fast"]},
            {"reasoning": ["reasoning"]},
        ],
    )
    return router


# Module-level singleton — import and use directly.
llm_router = create_llm_router()


async def chat_completion(
    model_tier: str,
    messages: list[dict[str, str]],
    **kwargs: Any,
) -> str:
    """Convenience wrapper that calls the LLM and returns the assistant message content.

    Args:
        model_tier: "fast" or "reasoning"
        messages: Chat messages in OpenAI format
        **kwargs: Additional parameters forwarded to litellm

    Returns:
        The assistant's response text.
    """
    response = await llm_router.acompletion(
        model=model_tier,
        messages=messages,
        **kwargs,
    )
    return response.choices[0].message.content

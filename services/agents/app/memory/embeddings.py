"""Embedding utilities using LiteLLM for text-embedding-3-small."""

from __future__ import annotations

import logging
import os

import litellm

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIMENSIONS = 1536


async def embed(text: str) -> list[float]:
    """Embed a single text string and return a 1536-dimensional vector.

    Uses LiteLLM's async embedding endpoint, which routes to the configured
    provider (OpenAI by default).
    """
    response = await litellm.aembedding(
        model=EMBEDDING_MODEL,
        input=[text],
    )
    return response.data[0]["embedding"]


async def embed_batch(texts: list[str], batch_size: int = 64) -> list[list[float]]:
    """Embed multiple texts, processing in batches to respect API limits.

    Args:
        texts: List of strings to embed.
        batch_size: Maximum number of texts per API call.

    Returns:
        List of embedding vectors, one per input text.
    """
    all_embeddings: list[list[float]] = []

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        response = await litellm.aembedding(
            model=EMBEDDING_MODEL,
            input=batch,
        )
        # LiteLLM returns data in the same order as input
        batch_embeddings = [item["embedding"] for item in response.data]
        all_embeddings.extend(batch_embeddings)

    return all_embeddings

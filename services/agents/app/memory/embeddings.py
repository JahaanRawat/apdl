"""Embedding utilities using the OpenAI SDK for text-embedding-3-small."""

from __future__ import annotations

import logging
import os

import openai

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIMENSIONS = 1536

_client: openai.AsyncOpenAI | None = None


def _get_client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
    return _client


async def embed(text: str) -> list[float]:
    """Embed a single text string and return a 1536-dimensional vector."""
    client = _get_client()
    response = await client.embeddings.create(model=EMBEDDING_MODEL, input=[text])
    return response.data[0].embedding


async def embed_batch(texts: list[str], batch_size: int = 64) -> list[list[float]]:
    """Embed multiple texts, processing in batches to respect API limits.

    Args:
        texts: List of strings to embed.
        batch_size: Maximum number of texts per API call.

    Returns:
        List of embedding vectors, one per input text.
    """
    client = _get_client()
    all_embeddings: list[list[float]] = []

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        response = await client.embeddings.create(model=EMBEDDING_MODEL, input=batch)
        batch_embeddings = [item.embedding for item in response.data]
        all_embeddings.extend(batch_embeddings)

    return all_embeddings

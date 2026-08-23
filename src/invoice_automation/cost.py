"""Cost accounting for LLM calls.

Neither provider implementation exposes an exact tokenizer, so token counts here are a
deliberate approximation (~4 characters per token, an English-prose average) rather than
the provider's own billed count. Good enough for a controller comparing invoices and
vendors against each other; not a substitute for the provider's own invoice.
"""

from __future__ import annotations

from decimal import Decimal

from .config import DEFAULT_TOKEN_RATE, MODEL_TOKEN_RATES


def estimate_tokens(text: str) -> int:
    """Rough token estimate for `text`. Empty text still costs one token, so a call
    with a trivial payload is never recorded as free."""
    return max(1, len(text) // 4)


def compute_cost(model: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
    """USD cost of one call, from the configured per-1K-token rates for `model`.

    An unlisted model falls back to `DEFAULT_TOKEN_RATE` — an estimate is still useful
    even for a model this table hasn't been updated for yet.
    """
    prompt_rate, completion_rate = MODEL_TOKEN_RATES.get(model, DEFAULT_TOKEN_RATE)
    return (Decimal(prompt_tokens) / Decimal(1000) * prompt_rate) + (
        Decimal(completion_tokens) / Decimal(1000) * completion_rate
    )

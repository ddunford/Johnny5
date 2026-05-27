"""Per-call cost estimation, in USD.

Cost feeds the daily budget governor and "tired" degradation, so every call is
priced before it's logged. Local Ollama inference is free; cloud providers are
priced per million tokens. Rates are kept here (versioned) and updated when a
provider changes pricing.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


class TokenPrice:
    """Price per 1M tokens, input and output, in USD."""

    def __init__(self, input_per_million: str, output_per_million: str) -> None:
        self.input_per_million = Decimal(input_per_million)
        self.output_per_million = Decimal(output_per_million)


# Keyed by (provider, model). Anything not listed (i.e. local Ollama) is free.
PRICING: dict[tuple[str, str], TokenPrice] = {
    # Groq pricing for llama-3.3-70b-versatile (USD / 1M tokens).
    ("groq", "llama-3.3-70b-versatile"): TokenPrice("0.59", "0.79"),
}

_MILLION = Decimal(1_000_000)
_CENT_PRECISION = Decimal("0.000001")


def estimate_cost(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> Decimal:
    """Estimate the USD cost of a call. Returns 0 for unpriced (local) models."""
    price = PRICING.get((provider, model))
    if price is None:
        return Decimal("0.000000")
    cost = (
        Decimal(prompt_tokens) / _MILLION * price.input_per_million
        + Decimal(completion_tokens) / _MILLION * price.output_per_million
    )
    return cost.quantize(_CENT_PRECISION, rounding=ROUND_HALF_UP)

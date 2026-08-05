"""How the backend measures a prompt against a token budget.

Owner: Jerome & Richard

No issue scopes a tokenizer dependency, so budgets are enforced against a character estimate
(ADR 0006). It lives beside the providers because tokens are a model concept, and it is
deliberately the only place the ratio is applied.
"""

from __future__ import annotations

import math


def estimate_tokens(text: str, characters_per_token: int) -> int:
    return math.ceil(len(text) / characters_per_token)


def characters_for(tokens: int, characters_per_token: int) -> int:
    return tokens * characters_per_token

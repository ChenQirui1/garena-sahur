"""Costs require an injected, saved pricing snapshot."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.telemetry.cost_calculator import (
    MissingModelPriceError,
    PricingError,
    PricingTable,
    TokenRates,
    UsageTotals,
    calculate_costs,
)


def call(
    provider: str | None,
    model: str | None,
    input_tokens: int,
    output_tokens: int,
) -> dict[str, object]:
    return {
        "record_type": "model_call",
        "provider": provider,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def pricing() -> PricingTable:
    return PricingTable.from_mapping(
        version="synthetic-2026-08-09",
        currency="synthetic_cost_units",
        label="fixed benchmark normalization; not a live provider price",
        rates={
            "mock": {
                "strong": {
                    "input_per_million": "2.0",
                    "output_per_million": "4.0",
                },
                "weak": TokenRates.per_million(
                    input_rate="0.5", output_rate="1.0"
                ),
            }
        },
    )


def test_actual_and_caller_supplied_all_strong_projection_stay_separate() -> None:
    summary = calculate_costs(
        [
            call("mock", "strong", 100, 20),
            call("mock", "weak", 200, 10),
        ],
        pricing(),
        baseline_provider="mock",
        baseline_model="strong",
        projected_baseline_usage=UsageTotals(
            calls=5,
            input_tokens=1_000,
            output_tokens=100,
        ),
    )

    assert summary.pricing_version == "synthetic-2026-08-09"
    assert summary.currency == "synthetic_cost_units"
    assert summary.actual_routed.calls == 2
    assert summary.actual_routed_cost == Decimal("0.000390")
    assert summary.projected_baseline.calls == 5
    assert summary.projected_baseline_cost == Decimal("0.002400")
    assert summary.projection_basis == "caller_supplied_all_strong_model_usage"
    serialized = summary.as_dict()
    assert serialized["actual_routed"] != serialized["projected_baseline"]
    assert serialized["baseline"] == {
        "provider": "mock",
        "model": "strong",
        "projection_basis": "caller_supplied_all_strong_model_usage",
    }


def test_omitted_projection_is_honestly_labelled_as_repriced_observed_usage() -> None:
    summary = calculate_costs(
        [call("mock", "weak", 100, 10)],
        pricing(),
        baseline_provider="mock",
        baseline_model="strong",
    )

    assert summary.projection_basis == "observed_model_call_usage_repriced"
    assert summary.actual_routed_cost == Decimal("0.000060")
    assert summary.projected_baseline_cost == Decimal("0.000240")


def test_unpriced_token_usage_is_never_silently_counted_as_free() -> None:
    with pytest.raises(MissingModelPriceError, match="provider='other'"):
        calculate_costs(
            [call("other", "unknown", 10, 1)],
            pricing(),
            baseline_provider="mock",
            baseline_model="strong",
        )


def test_rates_must_be_finite_and_non_negative() -> None:
    with pytest.raises(PricingError, match="finite"):
        TokenRates(input_per_token=Decimal("NaN"), output_per_token=Decimal(0))
    with pytest.raises(PricingError, match="non-negative"):
        TokenRates(input_per_token=Decimal("-1"), output_per_token=Decimal(0))

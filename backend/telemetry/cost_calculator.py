"""Pure actual-routed and projected-baseline model cost calculation.

Owner: Elson & Daniel

There are deliberately no model names or live prices in this module.  A caller
must inject a versioned pricing table, including its currency (or synthetic unit),
so a saved benchmark remains reproducible after provider prices change.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import TypeAlias

ModelKey: TypeAlias = tuple[str, str]
DecimalInput: TypeAlias = Decimal | int | float | str

PER_MILLION = Decimal("1000000")


class PricingError(ValueError):
    """Base class for invalid or incomplete injected pricing."""


class MissingModelPriceError(PricingError):
    """A model-call fact has no corresponding injected rate."""


class CostRecordError(ValueError):
    """A model-call record cannot be costed safely."""


def _decimal(value: DecimalInput, field: str) -> Decimal:
    if isinstance(value, bool):
        raise PricingError(f"{field} must be a decimal number")
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PricingError(f"{field} must be a decimal number") from exc
    if not number.is_finite():
        raise PricingError(f"{field} must be finite")
    if number < 0:
        raise PricingError(f"{field} must be non-negative")
    return number


@dataclass(frozen=True, slots=True)
class TokenRates:
    """Input and output prices expressed per single token."""

    input_per_token: Decimal
    output_per_token: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "input_per_token",
            _decimal(self.input_per_token, "input_per_token"),
        )
        object.__setattr__(
            self,
            "output_per_token",
            _decimal(self.output_per_token, "output_per_token"),
        )

    @classmethod
    def per_million(
        cls,
        *,
        input_rate: DecimalInput,
        output_rate: DecimalInput,
    ) -> TokenRates:
        """Build rates from the unit providers commonly publish."""

        return cls(
            input_per_token=_decimal(input_rate, "input_rate") / PER_MILLION,
            output_per_token=_decimal(output_rate, "output_rate") / PER_MILLION,
        )

    @property
    def input_per_million(self) -> Decimal:
        return self.input_per_token * PER_MILLION

    @property
    def output_per_million(self) -> Decimal:
        return self.output_per_token * PER_MILLION

    def as_dict(self) -> dict[str, str]:
        return {
            "input_per_token": str(self.input_per_token),
            "output_per_token": str(self.output_per_token),
        }


# A descriptive synonym used by some consumers.
ModelRates = TokenRates


def _rates_from_value(value: object) -> TokenRates:
    if isinstance(value, TokenRates):
        return value
    if not isinstance(value, Mapping):
        raise PricingError("each model rate must be TokenRates or an object")

    if "input_per_token" in value or "output_per_token" in value:
        try:
            return TokenRates(
                input_per_token=value["input_per_token"],  # type: ignore[arg-type]
                output_per_token=value["output_per_token"],  # type: ignore[arg-type]
            )
        except KeyError as exc:
            raise PricingError(
                "both input_per_token and output_per_token are required"
            ) from exc

    input_key = (
        "input_per_million"
        if "input_per_million" in value
        else "input_per_million_tokens"
    )
    output_key = (
        "output_per_million"
        if "output_per_million" in value
        else "output_per_million_tokens"
    )
    try:
        return TokenRates.per_million(
            input_rate=value[input_key],  # type: ignore[arg-type]
            output_rate=value[output_key],  # type: ignore[arg-type]
        )
    except KeyError as exc:
        raise PricingError(
            "rate requires per-token or per-million input and output values"
        ) from exc


def _normalise_rates(rates: Mapping[object, object]) -> dict[ModelKey, TokenRates]:
    normalised: dict[ModelKey, TokenRates] = {}
    for raw_key, raw_value in rates.items():
        if isinstance(raw_key, tuple) and len(raw_key) == 2:
            provider, model = raw_key
            if not isinstance(provider, str) or not isinstance(model, str):
                raise PricingError("pricing tuple keys must contain strings")
            normalised[(provider, model)] = _rates_from_value(raw_value)
            continue

        # Also accept the convenient {provider: {model: rates}} form.
        if not isinstance(raw_key, str) or not isinstance(raw_value, Mapping):
            raise PricingError(
                "rates must use (provider, model) keys or nested provider/model objects"
            )
        provider = raw_key
        for model, nested_rate in raw_value.items():
            if not isinstance(model, str):
                raise PricingError("model names must be strings")
            normalised[(provider, model)] = _rates_from_value(nested_rate)

    return normalised


@dataclass(frozen=True, slots=True)
class PricingTable:
    """An immutable snapshot of injected model rates."""

    version: str
    rates: Mapping[ModelKey, TokenRates]
    currency: str = "USD"
    label: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise PricingError("pricing version must be a non-empty string")
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise PricingError("pricing currency/unit must be a non-empty string")
        if self.label is not None and (
            not isinstance(self.label, str) or not self.label.strip()
        ):
            raise PricingError("pricing label must be a non-empty string or None")
        copied = _normalise_rates(self.rates)
        if not copied:
            raise PricingError("pricing table must contain at least one model rate")
        object.__setattr__(self, "rates", MappingProxyType(copied))

    @classmethod
    def from_mapping(
        cls,
        *,
        version: str,
        rates: Mapping[object, object],
        currency: str = "USD",
        label: str | None = None,
    ) -> PricingTable:
        """Construct from tuple-keyed or nested provider/model mappings."""

        return cls(
            version=version,
            rates=_normalise_rates(rates),
            currency=currency,
            label=label,
        )

    def rate_for(self, provider: str, model: str) -> TokenRates:
        try:
            return self.rates[(provider, model)]
        except KeyError as exc:
            raise MissingModelPriceError(
                f"no price in {self.version!r} for provider={provider!r}, "
                f"model={model!r}"
            ) from exc

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "currency": self.currency,
            "label": self.label,
            "rates": [
                {
                    "provider": provider,
                    "model": model,
                    **rate.as_dict(),
                }
                for (provider, model), rate in sorted(self.rates.items())
            ],
        }

    # ``to_record`` makes persistence intent explicit for benchmark metadata.
    def to_record(self) -> dict[str, object]:
        return self.as_dict()


@dataclass(frozen=True, slots=True)
class UsageTotals:
    calls: int
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        for field_name in ("calls", "input_tokens", "output_tokens"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CostRecordError(f"{field_name} must be a non-negative integer")

    def as_dict(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def _usage(value: UsageTotals | Mapping[str, object]) -> UsageTotals:
    if isinstance(value, UsageTotals):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("projected_baseline_usage must be UsageTotals or a mapping")
    try:
        return UsageTotals(
            calls=value["calls"],  # type: ignore[arg-type]
            input_tokens=value["input_tokens"],  # type: ignore[arg-type]
            output_tokens=value["output_tokens"],  # type: ignore[arg-type]
        )
    except KeyError as exc:
        raise CostRecordError(
            "projected baseline usage requires calls, input_tokens, and output_tokens"
        ) from exc


@dataclass(frozen=True, slots=True)
class ModelCost:
    provider: str | None
    model: str | None
    calls: int
    input_tokens: int
    output_tokens: int
    input_cost: Decimal
    output_cost: Decimal
    total_cost: Decimal

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "input_cost": str(self.input_cost),
            "output_cost": str(self.output_cost),
            "total_cost": str(self.total_cost),
        }


@dataclass(frozen=True, slots=True)
class CostTotals:
    calls: int
    input_tokens: int
    output_tokens: int
    input_cost: Decimal
    output_cost: Decimal
    total_cost: Decimal
    by_model: tuple[ModelCost, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "input_cost": str(self.input_cost),
            "output_cost": str(self.output_cost),
            "total_cost": str(self.total_cost),
            "by_model": [entry.as_dict() for entry in self.by_model],
        }


@dataclass(frozen=True, slots=True)
class CostSummary:
    """Two explicitly labelled costs calculated under one pricing snapshot."""

    pricing_version: str
    currency: str
    pricing_label: str | None
    actual_routed: CostTotals
    projected_baseline: CostTotals
    baseline_provider: str
    baseline_model: str
    projection_basis: str

    @property
    def actual_routed_cost(self) -> Decimal:
        return self.actual_routed.total_cost

    @property
    def projected_baseline_cost(self) -> Decimal:
        return self.projected_baseline.total_cost

    def as_dict(self) -> dict[str, object]:
        return {
            "pricing_version": self.pricing_version,
            "currency": self.currency,
            "pricing_label": self.pricing_label,
            "actual_routed": self.actual_routed.as_dict(),
            "projected_baseline": self.projected_baseline.as_dict(),
            "baseline": {
                "provider": self.baseline_provider,
                "model": self.baseline_model,
                "projection_basis": self.projection_basis,
            },
        }


def _record_integer(record: Mapping[str, object], field: str) -> int:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CostRecordError(f"{field} must be a non-negative integer")
    return value


def _model_cost(
    provider: str | None,
    model: str | None,
    usage: UsageTotals,
    pricing: PricingTable,
) -> ModelCost:
    if provider is None or model is None:
        if usage.input_tokens or usage.output_tokens:
            raise MissingModelPriceError(
                "a call with token usage must identify its provider and model"
            )
        input_cost = Decimal(0)
        output_cost = Decimal(0)
    else:
        rate = pricing.rate_for(provider, model)
        input_cost = rate.input_per_token * usage.input_tokens
        output_cost = rate.output_per_token * usage.output_tokens
    return ModelCost(
        provider=provider,
        model=model,
        calls=usage.calls,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        input_cost=input_cost,
        output_cost=output_cost,
        total_cost=input_cost + output_cost,
    )


def _totals(costs: Iterable[ModelCost]) -> CostTotals:
    entries = tuple(costs)
    return CostTotals(
        calls=sum(entry.calls for entry in entries),
        input_tokens=sum(entry.input_tokens for entry in entries),
        output_tokens=sum(entry.output_tokens for entry in entries),
        input_cost=sum((entry.input_cost for entry in entries), start=Decimal(0)),
        output_cost=sum((entry.output_cost for entry in entries), start=Decimal(0)),
        total_cost=sum((entry.total_cost for entry in entries), start=Decimal(0)),
        by_model=entries,
    )


def calculate_costs(
    records: Iterable[Mapping[str, object]],
    pricing: PricingTable,
    *,
    baseline_provider: str,
    baseline_model: str,
    projected_baseline_usage: UsageTotals | Mapping[str, object] | None = None,
) -> CostSummary:
    """Calculate measured routed cost and a separately labelled projection.

    When ``projected_baseline_usage`` is supplied, it should contain the calls
    and tokens that an all-candidates/strong-model run would consume.  If it is
    omitted, observed model-call usage is repriced at the baseline model; the
    returned ``projection_basis`` says so explicitly and never presents that as
    an all-candidates measurement.
    """

    if not isinstance(pricing, PricingTable):
        raise TypeError("pricing must be a versioned PricingTable")
    if not baseline_provider or not baseline_model:
        raise ValueError("baseline provider and model must be non-empty strings")

    usage_by_model: dict[tuple[str | None, str | None], list[int]] = {}
    for record_index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            raise CostRecordError(f"record {record_index} must be an object")
        if record.get("record_type") != "model_call":
            continue
        provider = record.get("provider")
        model = record.get("model")
        if provider is not None and not isinstance(provider, str):
            raise CostRecordError("provider must be a string or null")
        if model is not None and not isinstance(model, str):
            raise CostRecordError("model must be a string or null")
        key = (provider, model)
        usage = usage_by_model.setdefault(key, [0, 0, 0])
        usage[0] += 1
        usage[1] += _record_integer(record, "input_tokens")
        usage[2] += _record_integer(record, "output_tokens")

    actual_entries = []
    for (provider, model), raw_usage in sorted(
        usage_by_model.items(),
        key=lambda item: (item[0][0] or "", item[0][1] or ""),
    ):
        actual_entries.append(
            _model_cost(
                provider,
                model,
                UsageTotals(*raw_usage),
                pricing,
            )
        )
    actual = _totals(actual_entries)

    if projected_baseline_usage is None:
        baseline_usage = UsageTotals(
            calls=actual.calls,
            input_tokens=actual.input_tokens,
            output_tokens=actual.output_tokens,
        )
        projection_basis = "observed_model_call_usage_repriced"
    else:
        baseline_usage = _usage(projected_baseline_usage)
        projection_basis = "caller_supplied_all_strong_model_usage"

    projected = _totals(
        [_model_cost(baseline_provider, baseline_model, baseline_usage, pricing)]
    )
    return CostSummary(
        pricing_version=pricing.version,
        currency=pricing.currency,
        pricing_label=pricing.label,
        actual_routed=actual,
        projected_baseline=projected,
        baseline_provider=baseline_provider,
        baseline_model=baseline_model,
        projection_basis=projection_basis,
    )


def calculate_cost(
    records: Iterable[Mapping[str, object]],
    pricing: PricingTable,
    *,
    baseline_provider: str,
    baseline_model: str,
    projected_baseline_usage: UsageTotals | Mapping[str, object] | None = None,
) -> CostSummary:
    """Singular alias for :func:`calculate_costs`."""

    return calculate_costs(
        records,
        pricing,
        baseline_provider=baseline_provider,
        baseline_model=baseline_model,
        projected_baseline_usage=projected_baseline_usage,
    )


__all__ = [
    "CostRecordError",
    "CostSummary",
    "CostTotals",
    "MissingModelPriceError",
    "ModelCost",
    "ModelRates",
    "PricingError",
    "PricingTable",
    "TokenRates",
    "UsageTotals",
    "calculate_cost",
    "calculate_costs",
]

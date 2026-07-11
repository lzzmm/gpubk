from __future__ import annotations

from fractions import Fraction
import re
from typing import Optional


DEFAULT_SHARE_UNITS = 1


def normalize_share_units(value: Optional[int], capacity_units: int) -> int:
    units = DEFAULT_SHARE_UNITS if value is None else _whole_number(value, "share units")
    if units < 1 or units > capacity_units:
        raise ValueError(f"share units must be between 1 and {capacity_units}")
    return units


def parse_share_units(value: str | int, capacity_units: int) -> int:
    if isinstance(value, int):
        return normalize_share_units(value, capacity_units)
    raw = str(value).strip().lower().replace(" ", "")
    if not raw:
        raise ValueError("share must be an integer, fraction such as 3/4, or percentage such as 75%")
    try:
        if raw.endswith("%"):
            ratio = Fraction(raw[:-1]) / 100
            return _ratio_to_units(ratio, capacity_units)
        if "/" in raw:
            return _ratio_to_units(Fraction(raw), capacity_units)
        return normalize_share_units(int(raw), capacity_units)
    except (ValueError, ZeroDivisionError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("share "):
            raise
        raise ValueError(
            f"invalid share {value!r}; use units 1-{capacity_units}, a fraction, or a percentage"
        ) from exc


def reservation_share_units(reservation: dict, capacity_units: int) -> int:
    raw = reservation.get("share_units", DEFAULT_SHARE_UNITS)
    try:
        units = _whole_number(raw, "share units")
    except (TypeError, ValueError):
        return max(1, capacity_units)
    if units < 1:
        return max(1, capacity_units)
    return units


def share_text(units: int, capacity_units: int) -> str:
    return f"{units}/{capacity_units}"


def share_example(capacity_units: int) -> str:
    if capacity_units <= 1:
        return "1"
    return f"{capacity_units - 1}/{capacity_units}"


def share_units_for_peer_limit(peer_count: int, capacity_units: int) -> int:
    if capacity_units < 2:
        raise ValueError("share-with requires a shared capacity of at least 2")
    peers = _whole_number(peer_count, "share-with")
    if peers < 1 or peers >= capacity_units:
        raise ValueError(
            f"share-with must be between 1 and {capacity_units - 1}; use exclusive for zero peers"
        )
    return capacity_units - peers


def inferred_share_memory_mb(usable_memory_mb: int, capacity_units: int, share_units: int) -> int:
    return max(1, usable_memory_mb * share_units // max(1, capacity_units))


def _ratio_to_units(ratio: Fraction, capacity_units: int) -> int:
    if ratio <= 0 or ratio > 1:
        raise ValueError("share fraction must be greater than 0 and at most 1")
    units = ratio * capacity_units
    if units.denominator != 1:
        raise ValueError(
            f"share must map to whole capacity units for a {capacity_units}-unit server"
        )
    return normalize_share_units(units.numerator, capacity_units)


def _whole_number(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a whole number")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        return int(value)
    raise ValueError(f"{label} must be a whole number")

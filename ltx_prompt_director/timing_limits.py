"""Workspace sequence-duration budgets shared by timeline edits and AI results."""
from __future__ import annotations
import math


def sequence_limit(definition: dict) -> float | None:
    if "max_sequence_duration" in definition:
        return definition["max_sequence_duration"]
    engine = definition.get("engine", "ltx")
    return 20.0 if engine == "ltx" else 15.0 if engine.startswith("minimax") else None


def fit_sequence(durations: list[float], maximum: float | None) -> list[float]:
    values = [float(value) for value in durations]
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Segment durations must be finite.")
    values = [max(0.01, value) for value in values]
    if maximum is None or not values or sum(values) <= maximum:
        return values
    # Preserve earlier timing when shortening the final segment alone is sufficient.
    minimum = min(0.01, maximum / len(values))
    tail = maximum - sum(values[:-1])
    if tail >= minimum:
        values[-1] = tail
        return values
    # Otherwise scale the original plan proportionally, respecting minimum durations.
    result = [0.0] * len(values)
    active = set(range(len(values)))
    budget = maximum
    while active:
        factor = budget / sum(values[i] for i in active)
        pinned = [i for i in active if values[i] * factor < minimum]
        if not pinned:
            for i in active:
                result[i] = values[i] * factor
            break
        for i in pinned:
            result[i] = minimum
            budget -= minimum
            active.remove(i)
    # Absorb floating-point residue without ever exceeding the configured budget.
    result[max(range(len(result)), key=result.__getitem__)] -= max(0.0, sum(result) - maximum)
    return result

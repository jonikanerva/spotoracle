"""Pure-python accuracy metrics for the SpotOracle backtest harness.

Dependency-free (stdlib ``math`` only), mirroring the rest of the repo's
no-numpy policy — the forecast's primary use case is *automation* (picking the
cheap/expensive hours), so the rank-based metrics here are the primary signal
and the absolute-error metrics (mae/rmse/bias) are reference only.

Every function takes two equal-length ``list[float]`` (predicted, actual)
already aligned by the caller, and returns a plain float (or ``None`` where the
metric is undefined, e.g. a constant series has no rank correlation).
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Absolute-error metrics (reference)
# ---------------------------------------------------------------------------


def mae(pred: list[float], act: list[float]) -> float:
    """Mean absolute error."""
    _check(pred, act)
    return sum(abs(p - a) for p, a in zip(pred, act)) / len(pred)


def rmse(pred: list[float], act: list[float]) -> float:
    """Root mean squared error."""
    _check(pred, act)
    return math.sqrt(sum((p - a) ** 2 for p, a in zip(pred, act)) / len(pred))


def bias(pred: list[float], act: list[float]) -> float:
    """Mean signed error (predicted - actual). Positive => over-prediction."""
    _check(pred, act)
    return sum(p - a for p, a in zip(pred, act)) / len(pred)


# ---------------------------------------------------------------------------
# Rank metrics (primary — automation cares about ordering, not absolute level)
# ---------------------------------------------------------------------------


def _average_ranks(values: list[float]) -> list[float]:
    """1-based ranks with ties resolved to the group's average rank.

    e.g. ``[10, 20, 20, 40]`` -> ``[1.0, 2.5, 2.5, 4.0]``.
    """
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0  # average of the tie group, 1-based
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def spearman(pred: list[float], act: list[float]) -> float | None:
    """Spearman rank correlation (Pearson correlation of average ranks).

    Returns ``None`` when either series is constant (zero rank variance) — this
    is the flat-baseline case, where rank correlation is genuinely undefined
    rather than zero.
    """
    _check(pred, act)
    n = len(pred)
    if n < 2:
        return None
    rp = _average_ranks(pred)
    ra = _average_ranks(act)
    mp = sum(rp) / n
    ma = sum(ra) / n
    cov = sum((rp[i] - mp) * (ra[i] - ma) for i in range(n))
    vp = sum((x - mp) ** 2 for x in rp)
    va = sum((x - ma) ** 2 for x in ra)
    if vp == 0.0 or va == 0.0:
        return None
    return cov / math.sqrt(vp * va)


def _extreme_index_sets(
    values: list[float], cheap_frac: float, exp_frac: float
) -> tuple[set[int], set[int]]:
    """Indices of the cheapest ``cheap_frac`` and most-expensive ``exp_frac``.

    Ascending stable sort by ``(value, index)`` so ties resolve deterministically.
    At least one index lands in each set.
    """
    n = len(values)
    n_cheap = max(1, round(n * cheap_frac))
    n_exp = max(1, round(n * exp_frac))
    order = sorted(range(n), key=lambda i: (values[i], i))
    cheap = set(order[:n_cheap])
    exp = set(order[n - n_exp:])
    return cheap, exp


def precision_at_n(
    pred: list[float], act: list[float], n: int, mode: str = "cheap"
) -> float | None:
    """Fraction of the predicted N-cheapest (or N-most-expensive) entries that
    are genuinely in the actual N-cheapest (or N-most-expensive) set.

    This is the core automation metric: "if I act on the N hours the model
    flagged cheapest, how many were actually among the cheapest N?". ``mode`` is
    ``"cheap"`` (smallest values) or ``"peak"`` (largest values).

    Returns ``None`` when the prediction is constant: a flat series has no
    meaningful cheapest/most-expensive ranking, so reporting a number (driven by
    arbitrary tie-breaking) would be misleading — the degenerate case must be
    visible, not dressed up as ~0.6 by index order.
    """
    _check(pred, act)
    if mode not in ("cheap", "peak"):
        raise ValueError("mode must be 'cheap' or 'peak'")
    total = len(pred)
    if n <= 0:
        raise ValueError("n must be >= 1")
    if len(set(pred)) <= 1:
        return None
    n = min(n, total)
    reverse = mode == "peak"
    pred_set = set(sorted(range(total), key=lambda i: (pred[i], i), reverse=reverse)[:n])
    act_set = set(sorted(range(total), key=lambda i: (act[i], i), reverse=reverse)[:n])
    return len(pred_set & act_set) / n


def classification_hit_rate(
    pred: list[float],
    act: list[float],
    cheap_frac: float = 0.25,
    exp_frac: float = 0.25,
) -> float | None:
    """Among the hours the model labelled cheap or expensive (by quantile),
    the fraction whose actual label agrees.

    Mid-priced hours are excluded — automations only act on the extremes, so
    this measures "when the model says cheap/expensive, is it right?". Returns
    ``None`` for a constant prediction (no meaningful extremes) or if no action
    hours exist (degenerate tiny input).
    """
    _check(pred, act)
    if len(set(pred)) <= 1:
        return None
    pred_cheap, pred_exp = _extreme_index_sets(pred, cheap_frac, exp_frac)
    act_cheap, act_exp = _extreme_index_sets(act, cheap_frac, exp_frac)
    action = pred_cheap | pred_exp
    if not action:
        return None
    hits = sum(
        1
        for i in action
        if (i in pred_cheap and i in act_cheap) or (i in pred_exp and i in act_exp)
    )
    return hits / len(action)


# ---------------------------------------------------------------------------


def _check(pred: list[float], act: list[float]) -> None:
    if len(pred) != len(act):
        raise ValueError(f"length mismatch: {len(pred)} vs {len(act)}")
    if not pred:
        raise ValueError("empty series")

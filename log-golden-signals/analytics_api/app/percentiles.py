"""
Rank-based interpolation for P50, P95, P99.

Algorithm (no external stats libs):
  Given a sorted list of N values:
    rank = p/100 * (N - 1)          # 0-based fractional rank
    lo   = floor(rank)
    hi   = ceil(rank)
    frac = rank - lo

    percentile = values[lo] + frac * (values[hi] - values[lo])

  Example with N=10 values [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]:
    P50: rank = 0.50 * 9 = 4.5 → lo=4, hi=5, frac=0.5
         P50 = 50 + 0.5*(60-50) = 55.0
    P95: rank = 0.95 * 9 = 8.55 → lo=8, hi=9, frac=0.55
         P95 = 90 + 0.55*(100-90) = 95.5
    P99: rank = 0.99 * 9 = 8.91 → lo=8, hi=9, frac=0.91
         P99 = 90 + 0.91*(100-90) = 99.1
"""

import math
from typing import Optional


def percentile(sorted_values: list[float], p: float) -> Optional[float]:
    """Compute the p-th percentile of a pre-sorted list (0 ≤ p ≤ 100)."""
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return sorted_values[0]
    rank = (p / 100.0) * (n - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    frac = rank - lo
    return sorted_values[lo] + frac * (sorted_values[hi] - sorted_values[lo])

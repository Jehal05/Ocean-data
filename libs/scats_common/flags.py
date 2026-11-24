"""Quality flag scheme (Argo / IODE) and the rule for combining flags.

Severity order, low to high:  0 < 1 < 2 < 5 < 8 < 9 < 3 < 4.   The worst flag wins.
"""
from __future__ import annotations

import numpy as np

FLAG_MEANINGS = {0: "no_qc", 1: "good", 2: "probably_good", 3: "probably_bad", 4: "bad",
                 5: "value_changed", 8: "estimated", 9: "missing"}
FLAG_VALUES = sorted(FLAG_MEANINGS)

NO_QC, GOOD, PROBABLY_GOOD, PROBABLY_BAD, BAD, CHANGED, ESTIMATED, MISSING = 0, 1, 2, 3, 4, 5, 8, 9

_ORDER = [0, 1, 2, 5, 8, 9, 3, 4]                       # low severity -> high severity
_RANK = np.zeros(10, dtype=np.int8)
for _r, _f in enumerate(_ORDER):
    _RANK[_f] = _r
_FROM_RANK = np.array(_ORDER, dtype=np.int8)

# flags kept as rows in PostgreSQL qc_flag (everything except "no QC" and "good")
PERSISTED = (2, 3, 4, 5, 8, 9)


def rank_of(flag) -> np.ndarray:
    """Severity rank of a flag or an array of flags."""
    return _RANK[np.asarray(flag, dtype=np.int64)]


def combine(old, new) -> np.ndarray:
    """Element-wise worst-wins combination of two flag arrays (or scalars)."""
    old, new = np.broadcast_arrays(np.asarray(old, dtype=np.int8), np.asarray(new, dtype=np.int8))
    return np.where(_RANK[new] > _RANK[old], new, old).astype(np.int8)


def worst(flags) -> int:
    """Worst flag in an array. Empty input gives 0."""
    flags = np.asarray(flags)
    if flags.size == 0:
        return 0
    return int(_FROM_RANK[_RANK[flags.astype(np.int64)].max()])

"""One passing and one failing case for EACH of the ten QC tests. check_phase2.py runs these nodes by name
(test_each[<test_id>_pass] and test_each[<test_id>_fail]); a test without both cases fails the acceptance check."""
import numpy as np
import pytest

from scats_qc import ALL_TESTS, run_qc
from test_qc import T0, found, profile, series

REF = "2026-11-24T00:00:00Z"
H = lambda k: T0 + np.timedelta64(k, "h")                                      # noqa: E731
NS = np.datetime64


def cases():
    clean_t = [H(i) for i in range(6)]
    jump_t = list(clean_t); jump_t[3] = H(100)
    return {
        "missing_value": (lambda: series(wind_speed=[1.0, 2.0, 3.0]), lambda: series(wind_speed=[1.0, np.nan, 3.0])),
        "format_consistency": (lambda: series(wind_speed=[1.0, 2.0]), lambda: series(wind_speed=[1.0, np.inf])),
        "global_range": (lambda: series(wind_speed=[1.0, 75.0]), lambda: series(wind_speed=[1.0, 75.5])),
        "regional_range": (lambda: series(air_temperature=[25.0, 45.0]), lambda: series(air_temperature=[25.0, 50.0])),
        "spike": (lambda: series(air_temperature=[20, 20, 20, 30, 30, 30]), lambda: series(air_temperature=[25, 25, 25, 33, 25, 25])),
        "position_on_land": (lambda: profile(lat=(15.0,), lon=(65.0,)), lambda: profile(lat=(21.0,), lon=(78.0,))),
        "position_range": (lambda: profile(lat=(90.0,), lon=(180.0,)), lambda: profile(lat=(95.0,), lon=(65.0,))),
        "timestamp_valid": (lambda: series(times=[NS("2025-01-01", "ns")] * 2, wind_speed=[1.0, 1.0]),
                            lambda: series(times=[NS("2090-01-01", "ns")] * 2, wind_speed=[1.0, 1.0])),
        "timestamp_monotonic": (lambda: series(times=clean_t, wind_speed=[1.0] * 6), lambda: series(times=jump_t, wind_speed=[1.0] * 6)),
        "density_inversion": (lambda: profile(temp=[[29, 28, 27, 26]], pres=[[5, 10, 20, 50]]),
                              lambda: profile(temp=[[28.0, 28.0, 33.0, 28.0]], pres=[[5, 10, 20, 50]])),
    }


CASES = cases()


def test_every_qc_test_has_a_pair_of_cases():
    assert set(CASES) == {t.test_id for t in ALL_TESTS}


@pytest.mark.parametrize("test_id,kind", [(i, k) for i in CASES for k in ("pass", "fail")],
                         ids=[f"{i}_{k}" for i in CASES for k in ("pass", "fail")])
def test_each(test_id, kind):
    ds = CASES[test_id][0 if kind == "pass" else 1]()
    hits = found(run_qc(ds, reference_time=REF), test_id)
    assert (len(hits) == 0) if kind == "pass" else (len(hits) >= 1), f"{test_id} {kind}: {len(hits)} findings"

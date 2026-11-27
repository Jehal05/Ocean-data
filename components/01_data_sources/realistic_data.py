"""The `realistic` profile of make_synthetic_data.py: a larger, messier, physically plausible SYNTHETIC data set.

Nothing here is observation. Every file says so (SYNTH_NOTE in an attribute or a header line), every platform is fictional
("SYN-A001", "SYN-BUOY-AS01", "SYN-CRUISE-2024-02", ship "RV Synthetic Explorer"), and the whole run is deterministic from one seed.

Region 5S-25N, 50E-100E, window 2024-01-01 .. 2025-12-31 (two monsoon cycles).
  argo_profiles   40 floats in 3 regional files, 10-day cycles, 0-2000 dbar, 105 levels, provider flags with realistic rates
  ship_ctd        6 cruises, one CSV each, about 150 casts, 1000-4000 m
  sst_grid        0.25 deg daily analysed SST, packed int16 like GHRSST, cloud gaps, monsoon cycle and upwelling
  buoy_met_wave   5 moored buoys, hourly, monsoon wind reversals, 4 cyclone events, outages, one silent for three weeks
  bathymetry      0.1 deg elevation over the whole region
Deliberate faults (spikes, impossible values, swapped positions, timestamp errors, density inversions, and faults the QC
framework does not cover: stuck values, drift, an offset) are listed in the ground-truth file written next to the data
(data/blind_errors/realistic/answer_key.csv). The labels are never inside the data files.
"""
from __future__ import annotations

import csv
from pathlib import Path

import gsw
import numpy as np
import pandas as pd
import xarray as xr
from global_land_mask import globe
from scipy import ndimage

SEED = 20261127
LAT_MIN, LAT_MAX, LON_MIN, LON_MAX = -5.0, 25.0, 50.0, 100.0
START = pd.Timestamp("2024-01-01")
NDAYS = 731                                           # 2024 is a leap year: 2024-01-01 .. 2025-12-31
SYNTH_NOTE = "SYNTHETIC DATA generated for software testing. NOT real observations."
FILL_ARGO, FILL_CSV = 99999.0, -999
YEAR = 365.25

KEY: list[dict] = []                                  # the ground truth of the deliberate faults


def log(file, kind, rec, var, lvl, etype, orig, inj, test, band, expect="detect", notes="", cast_id="", depth_m=""):
    KEY.append({"error_id": f"R{len(KEY) + 1:03d}", "file": file, "record_kind": kind, "record_index": int(rec),
                "level_index": "" if lvl in ("-", "", None) else int(lvl), "cast_id": cast_id, "depth_m": depth_m,
                "variable": var, "error_type": etype, "original_value": orig, "injected_value": inj,
                "expected_primary_test": test, "notes": notes, "magnitude_band": band, "expect": expect})


def is_ocean(lat, lon, margin=0.0):
    if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
        return False
    if globe.is_land(lat, lon):
        return False
    if margin:
        return not any(globe.is_land(lat + a, lon + b) for a in (-margin, margin) for b in (-margin, margin))
    return True


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.asarray(x, dtype=float)))


def gauss(x, c, w):
    return np.exp(-(((np.asarray(x, dtype=float) - c) / w) ** 2))


# ------------------------------------------------------------------ physics ----
def bob_weight(lat, lon):
    """0 in the Arabian Sea and equatorial ocean, 1 in the Bay of Bengal and Andaman Sea."""
    return sigmoid((np.asarray(lon) - 80.0) / 1.5) * sigmoid((np.asarray(lat) - 4.0) / 1.5)


def sst_clim(lat, lon, day):
    """Sea surface temperature (degC): monsoon cycle, warm pool, winter cooling in the north, summer upwelling."""
    lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    doy = np.asarray(day, dtype=float) % YEAR
    bob = bob_weight(lat, lon)
    base = 28.9 - 0.012 * np.abs(lat) ** 1.5
    north = np.clip(lat - 6.0, 0, None)
    winter = 0.5 * (1 + np.cos(2 * np.pi * (doy - 20.0) / YEAR))
    cool = 0.20 * north * winter * (1 - 0.35 * bob)
    warm_pool = 1.0 * gauss(doy, 110, 40) * sigmoid((lat + 1) / 2)
    autumn = 0.4 * gauss(doy, 295, 30) * sigmoid((lat - 2) / 2)
    monsoon = -0.9 * gauss(doy, 210, 45) * sigmoid((lat - 5) / 2)
    sw = gauss(doy, 215, 35)
    up = (4.0 * sw * gauss(lon, 53.0, 3.5) * gauss(lat, 8.0, 4.5) + 3.2 * sw * gauss(lon, 59.0, 3.5) * gauss(lat, 19.5, 3.0)
          + 1.6 * sw * gauss(lon, 74.0, 2.0) * gauss(lat, 10.0, 4.0))
    return base - cool + warm_pool + autumn + monsoon - up + 0.5 * bob


def mixed_layer(lat, lon, day):
    doy = np.asarray(day, dtype=float) % YEAR
    winter = 0.5 * (1 + np.cos(2 * np.pi * (doy - 15.0) / YEAR))
    bob = bob_weight(lat, lon)
    arabian = 25.0 + 60.0 * winter * np.clip((np.asarray(lat) - 5.0) / 15.0, 0.15, 1.0)
    bay = 18.0 + 22.0 * winter
    return (1 - bob) * arabian + bob * bay


def surface_salinity(lat, lon, day):
    lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    doy = np.asarray(day, dtype=float) % YEAR
    bob = bob_weight(lat, lon)
    winter = 0.5 * (1 + np.cos(2 * np.pi * (doy - 20.0) / YEAR))
    arabian = np.clip(35.5 + 0.07 * (lat - 8.0), 34.8, 36.6) + 0.2 * winter * np.clip((lat - 10) / 10, 0, 1)
    equatorial = 34.9 - 0.5 * gauss(doy, 250, 60)
    fresh = np.clip((lat - 6.0) / 12.0, 0, 1) * (0.55 + 0.45 * gauss(doy, 290, 60))
    bay = 34.3 - 3.2 * fresh
    open_water = np.where(lat < 4.0, equatorial, arabian)
    return (1 - bob) * open_water + bob * bay


LEVELS_Z = np.concatenate([np.arange(5, 100, 5), np.arange(100, 500, 10), np.arange(500, 1000, 20), np.arange(1000, 1951, 50)]).astype(float)   # 104 levels, the deepest at 1950 m (about 1966 dbar)


def make_ts_profile(lat, lon, day, z, rng):
    """In-situ temperature and practical salinity at depths z (m, positive down); stably stratified before faults are injected."""
    z = np.asarray(z, dtype=float)
    sst = float(sst_clim(lat, lon, day))
    mld = float(mixed_layer(lat, lon, day))
    heave = rng.normal(0, 5.0) * z / (z + 150.0)                       # internal waves lift and lower the thermocline
    zz = np.clip(z + heave, 0, None)
    t_deep = 1.5 + 3.5 * np.exp(-zz / 1100.0) + 17.0 * np.exp(-zz / 380.0)
    thick = 45.0
    w = 0.5 * (1 - np.tanh((zz - (mld + thick / 2)) / (thick / 3)))
    temp = t_deep + (sst - t_deep) * w
    s_surf = float(surface_salinity(lat, lon, day))
    s_deep = 34.72 + 1.3 * np.exp(-zz / 250.0)
    ws = 0.5 * (1 - np.tanh((zz - mld) / 22.0))
    sal = s_deep + (s_surf - s_deep) * ws
    arabian = 1.0 - float(bob_weight(lat, lon))
    sal = sal + 0.35 * arabian * float(sigmoid((lat - 10) / 2)) * np.exp(-(((zz - 90.0) / 45.0) ** 2))      # high-salinity water of the Arabian Sea
    d_gulf = float(np.hypot(lon - 60.0, lat - 23.0))                    # Persian Gulf water, near 250 m
    d_red = float(np.hypot(lon - 52.0, lat - 12.0))                     # Red Sea water, deeper
    pg = np.exp(-(((zz - 260.0) / 70.0) ** 2))
    rs = np.exp(-(((zz - 700.0) / 130.0) ** 2))
    sal = sal + 0.55 * np.exp(-d_gulf / 10.0) * pg + 0.45 * np.exp(-d_red / 12.0) * rs
    temp = temp + 1.6 * np.exp(-d_gulf / 10.0) * pg + 1.2 * np.exp(-d_red / 12.0) * rs
    temp = temp + 0.008 * rng.standard_t(6, z.size)                      # heavy-tailed, small
    sal = sal + 0.0015 * rng.standard_t(6, z.size)
    return temp, remove_inversions(temp, sal, z, lat, lon)


def sigma0(temp, sal, z, lat, lon):
    p = gsw.p_from_z(-np.asarray(z), lat)
    sa = gsw.SA_from_SP(sal, p, lon, lat)
    return gsw.sigma0(sa, gsw.CT_from_t(sa, temp, p))


def remove_inversions(temp, sal, z, lat, lon):
    sal = sal.copy()
    for _ in range(3000):
        inv = np.diff(sigma0(temp, sal, z, lat, lon)) < 0.0
        if not inv.any():
            return sal
        sal[1:][inv] += 0.0015
    raise RuntimeError("could not make a clean profile statically stable")


# ------------------------------------------------------------- 1. Argo-style ----
ARGO_REGIONS = {                       # key -> (file stem, prefix, count, lat box, lon box, drift scale deg/day)
    "arabian_sea": ("argo_synthetic_arabian_sea_2024_2025", "A", 20, (5.0, 23.0), (52.0, 76.0), 0.040),
    "bay_of_bengal": ("argo_synthetic_bay_of_bengal_2024_2025", "B", 15, (5.0, 19.0), (82.0, 94.0), 0.035),
    "equatorial": ("argo_synthetic_equatorial_2024_2025", "E", 5, (-4.0, 3.0), (56.0, 95.0), 0.060),
}
LEVEL_FLAG_RATES = {"3": 0.015, "4": 0.006, "2": 0.030}               # per level, single; clusters are added on top


def _float_track(rng, lat_box, lon_box, drift):
    for _ in range(500):
        lat, lon = rng.uniform(*lat_box), rng.uniform(*lon_box)
        if is_ocean(lat, lon, margin=1.0):
            break
    else:
        raise RuntimeError("no ocean start position")
    v = rng.normal(0, drift, 2)
    track = []
    for _ in range(80):
        track.append((lat, lon))
        for _try in range(40):
            v = 0.8 * v + rng.normal(0, drift * 0.6, 2)
            nlat, nlon = lat + v[0] * 10.0, lon + v[1] * 10.0         # one 10-day cycle
            if is_ocean(nlat, nlon, margin=0.25) and lat_box[0] - 1 <= nlat <= lat_box[1] + 1 and lon_box[0] - 1 <= nlon <= lon_box[1] + 1:
                lat, lon = nlat, nlon
                break
            v = -0.5 * v
    return track


def _provider_flags(rng, n_lev, levels_present):
    f = np.full(n_lev, 1, dtype=np.int8)
    u = rng.random(n_lev)                                         # disjoint bands: 4 (0.6 %), 3 (1.5 %), 2 (3.0 %)
    f[u < LEVEL_FLAG_RATES["4"] + LEVEL_FLAG_RATES["3"] + LEVEL_FLAG_RATES["2"]] = 2
    f[u < LEVEL_FLAG_RATES["4"] + LEVEL_FLAG_RATES["3"]] = 3
    f[u < LEVEL_FLAG_RATES["4"]] = 4
    if rng.random() < 0.05:                                       # a block the provider found questionable
        i0 = int(rng.integers(5, n_lev - 25))
        f[i0:i0 + int(rng.integers(4, 18))] = np.maximum(f[i0:i0 + 1], 2) if rng.random() < 0.7 else 3
    f[~levels_present] = 9
    return f


def make_argo(out: Path, rng):
    paths, total = [], 0
    for region, (stem, prefix, count, lat_box, lon_box, drift) in ARGO_REGIONS.items():
        rows = []
        late = set(rng.choice(count, size=max(1, count // 7), replace=False).tolist())
        dead = {int(i): int(rng.integers(25, 60)) for i in rng.choice(count, size=max(1, count // 7), replace=False)}
        for f in range(count):
            wmo = f"SYN-{prefix}{f + 1:03d}"
            track = _float_track(rng, lat_box, lon_box, drift)
            first = int(rng.integers(0, 10)) if f not in late else int(rng.integers(60, 360))
            cyc = 0
            for k in range(80):
                day = first + 10.0 * k + rng.uniform(-0.3, 0.3)
                if day >= NDAYS - 1 or (f in dead and k >= dead[f]):
                    break
                cyc += 1
                if rng.random() < 0.03:                              # a missed transmission
                    continue
                rows.append((wmo, cyc, day, track[k][0], track[k][1]))
        n_prof, n_lev = len(rows), LEVELS_Z.size
        temp = np.full((n_prof, n_lev), FILL_ARGO, "float32")
        psal = np.full((n_prof, n_lev), FILL_ARGO, "float32")
        pres = np.full((n_prof, n_lev), FILL_ARGO, "float32")
        tqc = np.full((n_prof, n_lev), b"1", dtype="S1")
        sqc = np.full((n_prof, n_lev), b"1", dtype="S1")
        for i, (_, _, day, lat, lon) in enumerate(rows):
            t, s = make_ts_profile(lat, lon, day, LEVELS_Z, rng)
            present = np.ones(n_lev, bool)
            if rng.random() < 0.06:                                  # a shallow cycle: no data below 1000 dbar
                present[LEVELS_Z > 1000] = False
            drop = rng.random(n_lev) < 0.001                         # a few levels lost by the sensor
            present &= ~drop
            temp[i, present], psal[i, present] = t[present], s[present]
            pres[i] = gsw.p_from_z(-LEVELS_Z, lat).astype("float32")
            pres[i, ~present & (LEVELS_Z > 1000)] = FILL_ARGO
            for qc, fl in ((tqc, _provider_flags(rng, n_lev, present)), (sqc, _provider_flags(rng, n_lev, present))):
                qc[i] = np.char.mod("%d", fl).astype("S1")
        if len(rows) > 12:                                           # the newest profiles of some floats are not checked yet (flag 0)
            for i in rng.choice(len(rows), size=max(2, len(rows) // 150), replace=False):
                tqc[i], sqc[i] = b"0", b"0"
        paths.append(_write_argo(out, stem, region, rows, pres, temp, psal, tqc, sqc, rng))
        total += n_prof
    return paths, total


def _write_argo(out, stem, region, rows, pres, temp, psal, tqc, sqc, rng):
    n_prof = len(rows)
    rows = list(rows)
    file = f"{stem}.nc"
    # ---- deliberate faults (the QC framework should find these) ----
    fault_plan = {"arabian_sea": dict(spk_t=6, spk_s=4, rng=3, miss=2, inv=3, land=2, bad_pos=1, ts_dup=1, ts_jump=1, ts_back=1, stuck=3, drift=1, offset=1),
                  "bay_of_bengal": dict(spk_t=4, spk_s=3, rng=2, miss=1, inv=2, land=1, bad_pos=1, ts_dup=1, ts_jump=1, ts_back=1, stuck=2, drift=1, offset=0),
                  "equatorial": dict(spk_t=2, spk_s=1, rng=1, miss=1, inv=1, land=0, bad_pos=0, ts_dup=0, ts_jump=0, ts_back=0, stuck=1, drift=0, offset=0)}[region]
    juld_days = np.array([r[2] for r in rows])
    lat = np.array([r[3] for r in rows])
    lon = np.array([r[4] for r in rows])
    platforms = np.array([r[0] for r in rows])
    by_float: dict[str, list[int]] = {}
    for i, p in enumerate(platforms):
        by_float.setdefault(p, []).append(i)
    used: set[int] = set()

    def pick(n_min=1, deep=False, platform=None):
        for _ in range(2000):
            i = int(rng.integers(3, n_prof - 3))
            if i in used or (platform and platforms[i] != platform):
                continue
            ok = [j for j in (i - 1, i, i + 1) if j not in used and platforms[j] == platforms[i]]
            if len(ok) == 3 and (temp[i] < 90000).sum() > 90:
                used.update(ok)
                return i
        raise RuntimeError("could not place a fault")

    def level(i, lo=3, hi=100):
        return int(rng.integers(lo, hi))

    # spikes: 1.3 to 3 times the Argo test-9 threshold, shallow (< 500 dbar) or deep
    for k in range(fault_plan["spk_t"]):
        i = pick()
        shallow = k % 2 == 0
        lv = level(i, 5, 55) if shallow else level(i, 78, 100)
        mag = (6.0 if shallow else 2.0) * float(rng.choice([1.3, 1.8, 2.5, 3.0])) * float(rng.choice([-1, 1]))
        orig = float(temp[i, lv])
        temp[i, lv] = np.float32(orig + mag)
        log(file, "profile_index", i, "TEMP", lv, "spike", round(orig, 4), round(float(temp[i, lv]), 4), "spike", "well_above" if abs(mag) > 8 else "just_above",
            notes=f"{abs(mag) / (6.0 if shallow else 2.0):.2f} x the {'shallow' if shallow else 'deep'} temperature threshold")
    for k in range(fault_plan["spk_s"]):
        i = pick()
        shallow = k % 2 == 0
        lv = level(i, 5, 55) if shallow else level(i, 78, 100)
        mag = (0.9 if shallow else 0.3) * float(rng.choice([1.4, 2.0, 3.0])) * float(rng.choice([-1, 1]))
        orig = float(psal[i, lv])
        psal[i, lv] = np.float32(orig + mag)
        log(file, "profile_index", i, "PSAL", lv, "spike", round(orig, 4), round(float(psal[i, lv]), 4), "spike", "just_above",
            notes=f"{abs(mag) / (0.9 if shallow else 0.3):.2f} x the {'shallow' if shallow else 'deep'} salinity threshold")
    for k in range(fault_plan["rng"]):
        i = pick()
        lv = level(i, 5, 60)
        if k % 2 == 0:
            orig = float(psal[i, lv]); psal[i, lv] = 52.0
            log(file, "profile_index", i, "PSAL", lv, "out_of_range", round(orig, 4), 52.0, "global_range", "just_above_global", notes="salinity valid_max exceeded")
        else:
            orig = float(temp[i, lv]); temp[i, lv] = 61.0
            log(file, "profile_index", i, "TEMP", lv, "out_of_range", round(orig, 4), 61.0, "global_range", "just_above_global", notes="temperature valid_max exceeded")
    for k in range(fault_plan["miss"]):
        i = pick()
        lv = level(i, 5, 90)
        orig = float(temp[i, lv]); temp[i, lv] = FILL_ARGO
        log(file, "profile_index", i, "TEMP", lv, "missing", round(orig, 4), "(fill)", "missing_value", "n/a", notes="value replaced by the fill value, flag left as 1")
    for k in range(fault_plan["inv"]):
        i = pick()
        lv = level(i, 78, 98)
        mag = float(rng.choice([0.4, 0.8, 1.2]))
        orig = float(temp[i, lv]); temp[i, lv] = np.float32(orig + mag)
        log(file, "profile_index", i, "TEMP", lv, "density_inversion", round(orig, 4), round(float(temp[i, lv]), 4), "density_inversion", "just_above" if mag < 0.9 else "well_above",
            notes=f"deep +{mag} degC")
    # positions: onto land, and two swapped latitude/longitude pairs
    land_spots = [(25.2, 68.5, "Sindh, Pakistan"), (20.0, 78.0, "central India"), (23.0, 90.0, "Bangladesh"), (14.0, 76.0, "Karnataka, India")]
    for k in range(fault_plan["land"]):
        i = pick()
        olat, olon = lat[i], lon[i]
        nlat, nlon, where = land_spots[k % len(land_spots)]
        lat[i], lon[i] = nlat, nlon
        log(file, "profile_index", i, "LATITUDE/LONGITUDE", "", "position_on_land", f"{olat:.3f},{olon:.3f}", f"{nlat:.3f},{nlon:.3f}", "position_on_land", "n/a", notes=where)
    for k in range(fault_plan["bad_pos"]):
        for _try in range(200):
            i = pick()
            olat, olon = lat[i], lon[i]
            if olon > 90.0 or globe.is_land(float(olon), float(olat)):
                break
            used.difference_update({i - 1, i, i + 1})
        lat[i], lon[i] = olon, olat
        if olon > 90.0:                                              # latitude and longitude swapped: a latitude of more than 90
            log(file, "profile_index", i, "LATITUDE/LONGITUDE", "", "position_invalid", f"{olat:.3f},{olon:.3f}", f"{lat[i]:.3f},{lon[i]:.3f}", "position_range", "just_above_global",
                notes="latitude and longitude swapped")
        else:
            log(file, "profile_index", i, "LATITUDE/LONGITUDE", "", "position_on_land", f"{olat:.3f},{olon:.3f}", f"{lat[i]:.3f},{lon[i]:.3f}", "position_on_land", "swapped_pair",
                notes="latitude and longitude swapped: the swapped point is on land")
    # timestamps (inside one float: the monotonic test works per platform)
    for kind, n in (("ts_dup", fault_plan["ts_dup"]), ("ts_jump", fault_plan["ts_jump"]), ("ts_back", fault_plan["ts_back"])):
        for _ in range(n):
            i = pick()
            o = float(juld_days[i])
            if kind == "ts_dup":
                juld_days[i] = juld_days[i - 1]; et, notes = "timestamp_duplicate", "same time as the previous profile"
            elif kind == "ts_jump":
                juld_days[i] = o + 40.0; et, notes = "timestamp_jump_forward", "+40 days, later than its successor"
            else:
                juld_days[i] = o - 30.0; et, notes = "timestamp_non_monotonic", "-30 days, earlier than two predecessors"
            log(file, "profile_index", i, "JULD", "", et, round(o, 4), round(float(juld_days[i]), 4), "timestamp_monotonic", "n/a", notes=notes)
    # faults the QC framework does not look for (it works on one profile or one record at a time): stuck sensor, drift, offset
    for _ in range(fault_plan["stuck"]):
        i = pick()
        lv = level(i, 60, 80)
        n = int(rng.integers(10, 18))
        v = float(temp[i, lv])
        temp[i, lv:lv + n] = np.float32(v)
        log(file, "profile_index", i, "TEMP", lv, "stuck_value", round(v, 4), round(v, 4), "none", "n/a", expect="not_covered", notes=f"{n} identical values in a row")
    for _ in range(fault_plan["drift"]):
        f = str(rng.choice([p for p, ix in by_float.items() if len(ix) > 40]))
        ix = by_float[f][-25:]
        for k, i in enumerate(ix):
            psal[i][psal[i] < 90000] += np.float32(0.15 * (k + 1) / len(ix))
        log(file, "profile_index", ix[0], "PSAL", "", "salinity_drift", "", f"+0.15 psu over the last {len(ix)} cycles of {f}", "none", "n/a", expect="not_covered",
            notes=f"float {f}, profiles {ix[0]}..{ix[-1]}")
    for _ in range(fault_plan["offset"]):
        f = str(rng.choice([p for p, ix in by_float.items() if len(ix) > 30]))
        ix = by_float[f][10:]
        for i in ix:
            pres[i][pres[i] < 90000] += np.float32(4.0)
        log(file, "profile_index", ix[0], "PRES", "", "pressure_offset", "", f"+4 dbar on every profile of {f} from cycle 11", "none", "n/a", expect="not_covered",
            notes=f"float {f}, profiles {ix[0]}..{ix[-1]}")

    juld = np.array([(START + pd.Timedelta(days=float(d)) - pd.Timestamp("1950-01-01")) / pd.Timedelta(days=1) for d in juld_days])
    ds = xr.Dataset(
        {"PLATFORM_NUMBER": (("N_PROF",), platforms.astype("S8")),
         "CYCLE_NUMBER": (("N_PROF",), np.array([r[1] for r in rows], dtype="int32")),
         "JULD": (("N_PROF",), juld, {"long_name": "Julian day (UTC) of the station relative to REFERENCE_DATE_TIME",
                                      "units": "days since 1950-01-01 00:00:00 UTC", "_FillValue": 999999.0}),
         "LATITUDE": (("N_PROF",), lat, {"units": "degree_north", "_FillValue": 99999.0}),
         "LONGITUDE": (("N_PROF",), lon, {"units": "degree_east", "_FillValue": 99999.0}),
         "PRES": (("N_PROF", "N_LEVELS"), pres, {"long_name": "Sea water pressure, equals 0 at sea-level", "units": "decibar", "_FillValue": 99999.0}),
         "TEMP": (("N_PROF", "N_LEVELS"), temp, {"long_name": "Sea temperature in-situ ITS-90 scale", "units": "degree_Celsius", "_FillValue": 99999.0}),
         "PSAL": (("N_PROF", "N_LEVELS"), psal, {"long_name": "Practical salinity", "units": "psu", "_FillValue": 99999.0}),
         "TEMP_QC": (("N_PROF", "N_LEVELS"), tqc, {"long_name": "quality flag"}),
         "PSAL_QC": (("N_PROF", "N_LEVELS"), sqc, {"long_name": "quality flag"})},
        attrs={"title": f"Synthetic Argo-style profiles, {region.replace('_', ' ')}, 2024-2025", "institution": "none (synthetic)", "source": "make_synthetic_data.py --profile realistic",
               "comment": SYNTH_NOTE, "is_synthetic": "true", "Conventions": "Argo-3.1 (style only)", "history": "generated offline"})
    path = out / "argo_profiles" / file
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(path)
    return path


# ----------------------------------------------------------- 2. Ship CTD CSV ----
CRUISES = [   # (id, start, line as (lat0, lon0, lat1, lon1), stations, max depth m)
    ("SYN-CRUISE-2024-02", "2024-02-06", (15.0, 58.0, 15.0, 73.0), 26, 3000),
    ("SYN-CRUISE-2024-05", "2024-05-12", (15.0, 83.5, 15.0, 90.0), 22, 2500),
    ("SYN-CRUISE-2024-09", "2024-09-03", (0.0, 56.0, 0.0, 80.0), 30, 4000),
    ("SYN-CRUISE-2025-01", "2025-01-14", (4.0, 62.0, 21.0, 62.0), 28, 3500),
    ("SYN-CRUISE-2025-05", "2025-05-20", (6.0, 88.0, 18.0, 88.0), 24, 3000),
    ("SYN-CRUISE-2025-10", "2025-10-08", (11.0, 66.0, 21.0, 66.0), 20, 1000),
]


def ctd_depths(maxd):
    return np.concatenate([np.arange(0, 100, 2.0), np.arange(100, 500, 5.0), np.arange(500, 1000, 10.0), np.arange(1000, maxd + 1, 25.0)])


def make_ship_ctd(out: Path, rng):
    paths, total = [], 0
    for cr, (cid, start, (la0, lo0, la1, lo1), n_st, maxd) in enumerate(CRUISES):
        depth = ctd_depths(maxd)
        t0 = pd.Timestamp(start) + pd.Timedelta(hours=3)
        frames = []
        for s in range(n_st):
            f = s / (n_st - 1)
            lat, lon = la0 + (la1 - la0) * f, lo0 + (lo1 - lo0) * f
            if not is_ocean(lat, lon, margin=0.25):                      # nudge a station off a coast
                for dl in (0.5, -0.5, 1.0, -1.0, 1.5):
                    if is_ocean(lat + (dl if la0 != la1 else 0), lon + (dl if la0 == la1 else 0), margin=0.25):
                        lat, lon = lat + (dl if la0 != la1 else 0), lon + (dl if la0 == la1 else 0)
                        break
            when = t0 + pd.Timedelta(hours=float(s * rng.uniform(5.5, 8.5)))
            day = (when - START) / pd.Timedelta(days=1)
            t, sal = make_ts_profile(lat, lon, day, depth, rng)
            t = t + rng.normal(0, 0.002, depth.size)                     # shipboard CTDs are quieter than floats
            p = gsw.p_from_z(-depth, lat)
            sa = gsw.SA_from_SP(sal, p, lon, lat)
            ct = gsw.CT_from_t(sa, t, p)
            frames.append(pd.DataFrame({
                "cast_id": f"SYN-C{cr + 1}{s + 1:02d}", "station": f"S{s + 1:02d}", "timestamp": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "lat": round(lat, 4), "lon": round(lon, 4), "depth_m": depth, "temp_C": t.round(4), "sal_PSU": sal.round(4),
                "sound_vel_m_s": gsw.sound_speed(sa, ct, p).round(3)}))
        df = pd.concat(frames, ignore_index=True)
        file = f"ctd_casts_synthetic_cruise{cr + 1:02d}_{start[:4]}.csv"
        _ctd_faults(file, df, cr, rng)
        path = out / "ship_ctd" / file
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as fh:
            fh.write(f"# {SYNTH_NOTE}\n# synthetic: true\n# cruise: {cid}\n# ship: RV Synthetic Explorer (fictional)\n# fill_value: -999\n")
            fh.write("# units: depth_m=m, temp_C=degC, sal_PSU=PSS-78, sound_vel_m_s=m/s\n")
            df.to_csv(fh, index=False, lineterminator="\n", na_rep="")
        paths.append(path)
        total += len(df)
    return paths, total


def _ctd_faults(file, df, cr, rng):
    casts = df["cast_id"].unique().tolist()
    plan = {0: dict(spk=2, rng=1, miss=1, inv=1), 1: dict(spk=1, rng=0, miss=1, inv=0), 2: dict(spk=1, rng=1, miss=0, inv=1),
            3: dict(spk=2, rng=0, miss=1, inv=1), 4: dict(spk=1, rng=1, miss=0, inv=0), 5: dict(spk=1, rng=0, miss=0, inv=0)}[cr]
    done: set[str] = set()

    def target(lo_d, hi_d):
        for _ in range(500):
            c = casts[int(rng.integers(1, len(casts) - 1))]
            if c in done:
                continue
            sub = df.index[(df["cast_id"] == c) & (df["depth_m"] >= lo_d) & (df["depth_m"] <= hi_d)]
            if len(sub) > 4:
                done.add(c)
                i = int(sub[int(rng.integers(2, len(sub) - 2))])
                return c, i
        raise RuntimeError("no CTD target")

    for k in range(plan["spk"]):
        shallow = k % 2 == 0
        c, i = target(10, 450) if shallow else target(520, 900)
        col, thr = ("temp_C", 6.0 if shallow else 2.0) if k % 3 != 2 else ("sal_PSU", 0.9 if shallow else 0.3)
        mag = thr * float(rng.choice([1.3, 2.0, 3.0])) * float(rng.choice([-1, 1]))
        orig = float(df.at[i, col]); df.at[i, col] = round(orig + mag, 4)
        log(file, "csv_data_row", i, col, int(i - df.index[df["cast_id"] == c][0]), "spike", orig, df.at[i, col], "spike", "just_above",
            cast_id=c, depth_m=float(df.at[i, "depth_m"]), notes=f"{abs(mag) / thr:.2f} x the threshold")
    for _ in range(plan["rng"]):
        c, i = target(10, 400)
        orig = float(df.at[i, "sal_PSU"]); df.at[i, "sal_PSU"] = 52.0
        log(file, "csv_data_row", i, "sal_PSU", int(i - df.index[df["cast_id"] == c][0]), "out_of_range", orig, 52.0, "global_range", "just_above_global",
            cast_id=c, depth_m=float(df.at[i, "depth_m"]), notes="salinity valid_max exceeded")
    for _ in range(plan["miss"]):
        c, i = target(10, 400)
        orig = float(df.at[i, "temp_C"]); df.at[i, "temp_C"] = np.nan
        log(file, "csv_data_row", i, "temp_C", int(i - df.index[df["cast_id"] == c][0]), "missing", orig, "(empty)", "missing_value", "n/a",
            cast_id=c, depth_m=float(df.at[i, "depth_m"]), notes="empty cell")
    for _ in range(plan["inv"]):
        c, i = target(520, 900)
        orig = float(df.at[i, "temp_C"]); df.at[i, "temp_C"] = round(orig + 0.8, 4)
        log(file, "csv_data_row", i, "temp_C", int(i - df.index[df["cast_id"] == c][0]), "density_inversion", orig, df.at[i, "temp_C"], "density_inversion", "just_above",
            cast_id=c, depth_m=float(df.at[i, "depth_m"]), notes="deep +0.8 degC")


# ------------------------------------------------------------------ 3. SST grid ----
def make_sst(out: Path, rng):
    lat = np.arange(LAT_MIN, LAT_MAX + 1e-6, 0.25)
    lon = np.arange(LON_MIN, LON_MAX + 1e-6, 0.25)
    la, lo = np.meshgrid(lat, lon, indexing="ij")
    land = globe.is_land(la, lo)
    near_coast = ndimage.binary_dilation(land, iterations=1)
    base_fields = [ndimage.gaussian_filter(rng.normal(size=la.shape), 6) for _ in range(4)]
    base_fields = [f / f.std() for f in base_fields]
    cube = np.empty((NDAYS, lat.size, lon.size), dtype="float32")
    rng.normal(size=(NDAYS, 3, 3))                                  # (drawn and unused; kept so the random stream, and so every file, stays as published)
    for d in range(NDAYS):
        ph1, ph2 = 2 * np.pi * d / 45.0, 2 * np.pi * d / 110.0
        eddies = 0.45 * (np.cos(ph1) * base_fields[0] + np.sin(ph1) * base_fields[1]) + 0.35 * (np.cos(ph2) * base_fields[2] + np.sin(ph2) * base_fields[3])
        field = sst_clim(la, lo, d) + eddies + 0.06 * rng.standard_normal(la.shape)
        monsoon = float(gauss(d % YEAR, 215, 60))                       # clouds: more gaps in the monsoon (about 6 % in winter, 25 % in July)
        p_gap = 0.06 + 0.19 * monsoon
        noise = ndimage.gaussian_filter(rng.normal(size=la.shape), 3.0)
        noise = noise / noise.std()
        gap = noise > np.quantile(noise, 1 - p_gap)
        field[gap | land | near_coast & (rng.random(la.shape) < 0.35)] = np.nan
        cube[d] = field + 273.15
        rng.normal(size=(1, 1, 1))
    time = pd.date_range(START, periods=NDAYS, freq="D")
    ds = xr.Dataset(
        {"analysed_sst": (("time", "lat", "lon"), cube, {"long_name": "analysed sea surface temperature", "standard_name": "sea_surface_temperature", "units": "kelvin"})},
        coords={"time": time, "lat": ("lat", lat, {"units": "degrees_north", "standard_name": "latitude"}),
                "lon": ("lon", lon, {"units": "degrees_east", "standard_name": "longitude"})},
        attrs={"title": "Synthetic gridded SST, 0.25 deg daily, 2024-2025", "Conventions": "CF-1.8", "comment": SYNTH_NOTE, "is_synthetic": "true",
               "source": "make_synthetic_data.py --profile realistic"})
    path = out / "sst_grid" / "sst_synthetic_0p25deg_2024_2025.nc"
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(path, encoding={"analysed_sst": {"dtype": "int16", "scale_factor": 0.01, "add_offset": 273.15, "_FillValue": -32768,
                                                  "zlib": True, "complevel": 4, "shuffle": True, "chunksizes": (1, lat.size, lon.size)}})
    return path, cube.shape


# ----------------------------------------------------------- 4. Buoy met-ocean ----
BUOYS = [   # (id, lat, lon, sea)
    ("AS01", 15.0, 68.0, "arabian"), ("AS02", 19.0, 67.0, "arabian"), ("AS03", 8.5, 65.5, "arabian"),
    ("BB01", 14.0, 88.5, "bay"), ("BB02", 18.5, 89.5, "bay")]
CYCLONES = [   # (peak time, which buoys feel it with what strength 0..1)
    ("2024-05-24T06:00", {"BB01": 0.45, "BB02": 1.0}), ("2024-06-10T18:00", {"AS02": 1.0, "AS01": 0.5}),
    ("2025-05-30T12:00", {"AS02": 0.55, "AS01": 1.0}), ("2025-11-04T00:00", {"BB01": 1.0, "BB02": 0.6})]


def _ar1(rng, n, phi, sigma):
    e = rng.normal(0, sigma * np.sqrt(1 - phi ** 2), n)
    return ndimage.lfilter(e, phi) if hasattr(ndimage, "lfilter") else _lfilter(e, phi)


def _lfilter(e, phi):
    from scipy.signal import lfilter
    return lfilter([1.0], [1.0, -phi], e)


def make_buoy(out: Path, rng):
    times = pd.date_range(START, periods=NDAYS * 24, freq="h")
    n = len(times)
    hours = np.arange(n, dtype=float)
    doy = (hours / 24.0) % YEAR
    paths, total = [], 0
    for bi, (bid, lat, lon, sea) in enumerate(BUOYS):
        assert is_ocean(lat, lon, margin=0.5), bid
        sw = gauss(doy, 195, 55)                                         # south-west monsoon, June to September
        ne = gauss(doy, 10, 45) + gauss(doy, 360, 45)                    # north-east monsoon, November to February
        u = (sw * 8.5 * np.cos(np.radians(45)) - ne * 4.5 * np.cos(np.radians(45)))   # u, v of the monsoon wind: towards the NE in summer, towards the SW in winter
        v = (sw * 8.5 * np.sin(np.radians(45)) - ne * 4.5 * np.sin(np.radians(45)))
        if sea == "bay":
            u, v = 0.8 * u, 0.8 * v
        u = u + _ar1(rng, n, 0.985, 1.7) + 0.7 * np.sin(2 * np.pi * (hours - 14) / 24.0)
        v = v + _ar1(rng, n, 0.985, 1.7) + 0.4 * np.cos(2 * np.pi * (hours - 14) / 24.0)
        wspd = np.hypot(u, v)
        wdir = (np.degrees(np.arctan2(-u, -v)) + 360.0) % 360.0           # meteorological: the direction the wind blows FROM
        pres = (1010.0 - 3.5 * gauss(doy, 195, 60) + 2.5 * gauss(doy, 10, 50) + 1.1 * np.sin(2 * np.pi * (hours - 10) / 12.0) + _ar1(rng, n, 0.99, 1.4))
        sst = sst_clim(lat, lon, hours / 24.0) + 0.3 * np.sin(2 * np.pi * (hours - 9) / 24.0) + _ar1(rng, n, 0.995, 0.08)
        airt = sst - 0.9 - 0.6 * gauss(doy, 195, 60) + 0.7 * np.sin(2 * np.pi * (hours - 9) / 24.0) + _ar1(rng, n, 0.98, 0.35)
        rh = np.clip(70.0 + 12.0 * gauss(doy, 195, 60) - 6.0 * gauss(doy, 10, 50) + _ar1(rng, n, 0.985, 4.0), 35, 99)
        swell = np.clip(0.8 + 1.4 * gauss(doy, 195, 60) + _ar1(rng, n, 0.995, 0.25), 0.2, None)
        hs = np.sqrt((0.0125 * wspd ** 2) ** 2 + swell ** 2)
        tp = np.clip(6.5 + 0.18 * wspd + 3.0 * gauss(doy, 195, 60) + _ar1(rng, n, 0.99, 0.6), 4.0, 16.0)
        mwdir = (0.6 * wdir + 0.4 * (215.0 if sea == "arabian" else 190.0) + _ar1(rng, n, 0.99, 10.0)) % 360.0
        cur_dir = (120.0 + 60.0 * np.sin(2 * np.pi * doy / YEAR) + _ar1(rng, n, 0.99, 25.0)) % 360.0
        cur_speed = np.clip(20.0 + 14.0 * gauss(doy, 195, 60) + 8.0 * np.abs(np.sin(2 * np.pi * hours / 12.42)) + _ar1(rng, n, 0.99, 6.0), 1.0, None)
        for when, strength in CYCLONES:                                  # rare cyclone events, at most one per basin per year
            if bid not in strength:
                continue
            x = (hours - (pd.Timestamp(when) - START) / pd.Timedelta(hours=1)) / 7.0
            shape, a = np.exp(-x ** 2), strength[bid]
            wspd = wspd + 24.0 * a * shape
            pres = pres - 42.0 * a * np.exp(-(x / 1.3) ** 2)
            hs = hs + 5.5 * a * np.exp(-((x - 0.6) / 1.4) ** 2)
            tp = tp + 3.0 * a * shape
            sst = sst - 1.2 * a * np.exp(-((x - 1.0) / 2.5) ** 2)
        cols = {"wspd_ms": wspd.round(2), "wdir_deg": wdir.round(0), "airtemp_C": airt.round(2), "rh_pct": rh.round(1), "slp_hPa": pres.round(1),
                "hs_m": np.clip(hs, 0.1, None).round(2), "tp_s": tp.round(1), "mwdir_deg": mwdir.round(0), "sst_C": sst.round(2),
                "cur_speed_cm_s": cur_speed.round(1), "cur_dir_deg": cur_dir.round(0)}
        df = pd.DataFrame({"time_utc": times.strftime("%Y-%m-%dT%H:%M:%SZ"), "lat": lat, "lon": lon, **cols})
        # sensor dropouts: one column for days (the fill value), a few times a year
        for c in ("rh_pct", "cur_speed_cm_s", "cur_dir_deg", "sst_C"):
            for _ in range(int(rng.integers(1, 3))):
                a = int(rng.integers(100, n - 400))
                df.loc[a:a + int(rng.integers(12, 100)), c] = FILL_CSV
        # outages: no rows at all; one buoy goes silent for three weeks
        keep = np.ones(n, bool)
        for _ in range(int(rng.integers(8, 16))):
            a = int(rng.integers(50, n - 100))
            keep[a:a + int(rng.integers(3, 48))] = False
        if bid == "BB01":
            a = int((pd.Timestamp("2025-02-03") - START) / pd.Timedelta(hours=1))
            keep[a:a + 21 * 24] = False
        df = df[keep].reset_index(drop=True)
        file = f"buoy_met_wave_synthetic_{bid}_2024_2025.csv"
        _buoy_faults(file, df, bid, rng)
        path = out / "buoy_met_wave" / file
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as fh:
            fh.write(f"# {SYNTH_NOTE}\n# synthetic: true\n# platform: SYN-BUOY-{bid}\n# fill_value: -999\n")
            fh.write("# units: wspd_ms=m s-1, wdir_deg=degree, airtemp_C=degC, rh_pct=%, slp_hPa=hPa, hs_m=m, tp_s=s, "
                     "mwdir_deg=degree, sst_C=degC, cur_speed_cm_s=cm s-1, cur_dir_deg=degree\n")
            df.to_csv(fh, index=False, lineterminator="\n")
        paths.append(path)
        total += len(df)
    return paths, total


def _buoy_faults(file, df, bid, rng):
    plan = {"AS01": dict(spk=4, rng=1, land=1, bad=0, dup=1, jump=0, stuck=1, drift=0), "AS02": dict(spk=3, rng=1, land=0, bad=1, dup=0, jump=1, stuck=0, drift=1),
            "AS03": dict(spk=2, rng=1, land=0, bad=0, dup=0, jump=0, stuck=0, drift=0), "BB01": dict(spk=3, rng=1, land=0, bad=0, dup=1, jump=0, stuck=1, drift=0),
            "BB02": dict(spk=2, rng=0, land=0, bad=0, dup=0, jump=0, stuck=0, drift=0)}[bid]
    used: set[int] = set()

    def pick_row(lo=300):
        for _ in range(2000):
            i = int(rng.integers(lo, len(df) - 300))
            if all(abs(i - j) > 6 for j in used) and (df.loc[i - 2:i + 2, "wspd_ms"] != FILL_CSV).all() and (df.loc[i - 2:i + 2, ["rh_pct", "sst_C"]] != FILL_CSV).all().all():
                used.add(i)
                return i
        raise RuntimeError("no buoy row")

    thr = {"wspd_ms": 10.0, "airtemp_C": 4.0, "rh_pct": 25.0, "slp_hPa": 5.0, "hs_m": 2.0, "sst_C": 1.5}
    cols = list(thr)
    for k in range(plan["spk"]):
        i = pick_row()
        c = cols[(k + len(bid)) % len(cols)]
        mag = thr[c] * float(rng.choice([1.4, 2.0, 3.0]))
        orig = float(df.at[i, c]); df.at[i, c] = round(orig + mag, 2) if c != "rh_pct" else round(min(orig + mag, 140.0), 1)
        log(file, "csv_data_row", i, c, "", "spike", orig, df.at[i, c], "spike", "just_above", notes=f"{mag / thr[c]:.2f} x {thr[c]}")
    for _ in range(plan["rng"]):
        i = pick_row()
        orig = float(df.at[i, "wspd_ms"]); df.at[i, "wspd_ms"] = 65.0
        log(file, "csv_data_row", i, "wspd_ms", "", "out_of_range", orig, 65.0, "global_range", "just_above_global", notes="wind speed valid_max exceeded")
    for _ in range(plan["land"]):
        i = pick_row()
        olat, olon = float(df.at[i, "lat"]), float(df.at[i, "lon"])
        df.at[i, "lat"], df.at[i, "lon"] = 25.2, 68.5
        log(file, "csv_data_row", i, "lat/lon", "", "position_on_land", f"{olat},{olon}", "25.200,68.500", "position_on_land", "n/a", notes="Sindh, Pakistan")
    for _ in range(plan["bad"]):
        i = pick_row()
        olat = float(df.at[i, "lat"]); df.at[i, "lat"] = 90.5
        log(file, "csv_data_row", i, "lat/lon", "", "position_invalid", olat, 90.5, "position_range", "just_above_global", notes="latitude beyond 90 by 0.5")
    for _ in range(plan["dup"]):
        i = pick_row()
        o = df.at[i, "time_utc"]; df.at[i, "time_utc"] = df.at[i - 1, "time_utc"]
        log(file, "csv_data_row", i, "time_utc", "", "timestamp_duplicate", o, df.at[i, "time_utc"], "timestamp_monotonic", "n/a")
    for _ in range(plan["jump"]):
        i = pick_row()
        o = df.at[i, "time_utc"]
        df.at[i, "time_utc"] = (pd.Timestamp(o) + pd.Timedelta(hours=400)).strftime("%Y-%m-%dT%H:%M:%SZ")
        log(file, "csv_data_row", i, "time_utc", "", "timestamp_jump_forward", o, df.at[i, "time_utc"], "timestamp_monotonic", "n/a", notes="+400 h")
    for _ in range(plan["stuck"]):
        i = pick_row()
        v = float(df.at[i, "rh_pct"])
        df.loc[i:i + 40, "rh_pct"] = v
        used.update(range(i, i + 41))
        log(file, "csv_data_row", i, "rh_pct", "", "stuck_value", v, v, "none", "n/a", expect="not_covered", notes="relative humidity stuck for 41 hours")
    for _ in range(plan["drift"]):
        a = len(df) // 2
        ramp = np.linspace(0, 1.6, len(df) - a)
        ok = df.loc[a:, "sst_C"] != FILL_CSV
        df.loc[a:, "sst_C"] = np.where(ok, (df.loc[a:, "sst_C"] + ramp).round(2), FILL_CSV)
        log(file, "csv_data_row", a, "sst_C", "", "sensor_drift", "", "+1.6 degC by the end", "none", "n/a", expect="not_covered", notes="slow warm drift of the sea temperature sensor")


# ------------------------------------------------------------ 5. Bathymetry ----
def make_bathymetry(out: Path, rng):
    lat = np.round(np.arange(LAT_MIN, LAT_MAX + 1e-6, 0.1), 3)
    lon = np.round(np.arange(LON_MIN, LON_MAX + 1e-6, 0.1), 3)
    la, lo = np.meshgrid(lat, lon, indexing="ij")
    land = globe.is_land(la, lo)
    km = 11.1
    d_sea = ndimage.distance_transform_edt(~land) * km          # one 0.1 deg cell is about 11 km
    d_land = ndimage.distance_transform_edt(land) * km
    relief = ndimage.gaussian_filter(rng.normal(size=la.shape), 5)
    relief = relief / relief.std()
    depth = 20 + 4200 * (1 - np.exp(-d_sea / 380.0)) + 150 * relief
    shelf = 1 - sigmoid((d_sea - 60.0) / 20.0)                                      # a shelf of about 60 km, then a slope
    depth = depth * (1 - 0.35 * shelf) + 60 * shelf
    for lon0, lat0, lat1, h in ((73.0, 0.0, 20.0, 1700.0), (90.0, -5.0, 10.0, 1800.0), (62.0, 3.0, 12.0, 2300.0)):       # Chagos-Laccadive, Ninetyeast and Carlsberg-style ridges
        ridge = np.exp(-(((lo - lon0) / 1.0) ** 2)) * ((la >= lat0) & (la <= lat1))
        depth = depth - h * ridge * (1 - np.exp(-d_sea / 200.0))
    elev = np.where(land, 10 + 1.2 * d_land + 40 * relief, -np.clip(depth, 5, None))
    ds = xr.Dataset(
        {"elevation": (("lat", "lon"), elev.astype("int16"), {"long_name": "Elevation relative to sea level", "standard_name": "height_above_mean_sea_level",
                                                              "units": "m", "comment": "negative = below sea level"})},
        coords={"lat": ("lat", lat, {"units": "degrees_north", "standard_name": "latitude"}),
                "lon": ("lon", lon, {"units": "degrees_east", "standard_name": "longitude"})},
        attrs={"title": "Synthetic bathymetry, 0.1 deg, northern Indian Ocean", "Conventions": "CF-1.8", "comment": SYNTH_NOTE, "is_synthetic": "true",
               "source": "make_synthetic_data.py --profile realistic"})
    path = out / "bathymetry" / "bathymetry_synthetic_0p1deg_indian_ocean.nc"
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.to_netcdf(path, encoding={"elevation": {"zlib": True, "complevel": 4}})
    return path, elev.shape


def write_key(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["error_id", "file", "record_kind", "record_index", "level_index", "cast_id", "depth_m", "variable", "error_type", "original_value", "injected_value",
            "expected_primary_test", "notes", "magnitude_band", "expect"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(KEY)


def run(out: Path, key_path: Path):
    rng = np.random.default_rng(SEED)
    KEY.clear()
    results = []
    for fn in (make_argo, make_ship_ctd, make_sst, make_buoy, make_bathymetry):
        res = fn(out, rng)
        paths = res[0] if isinstance(res[0], list) else [res[0]]
        for p in paths:
            results.append((p, res[1:]))
            print(f"wrote {p}  ({p.stat().st_size / 1024:.0f} KiB)")
    write_key(key_path)
    import hashlib
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(out).as_posix()}" for p, _ in sorted(results)]
    (out / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")
    (out / "README.txt").write_text(f"{SYNTH_NOTE}\nThis directory is the 'realistic' profile of components/01_data_sources/make_synthetic_data.py (seed {SEED}).\n"
                                    "Regenerate: python3 components/01_data_sources/make_synthetic_data.py --profile realistic\n"
                                    "Check:      sha256sum -c MANIFEST.sha256\nThe ground truth of the deliberate faults is in data/blind_errors/realistic/answer_key.csv.\n")
    (key_path.parent / "MANIFEST.sha256").write_text(f"{hashlib.sha256(key_path.read_bytes()).hexdigest()}  {key_path.name}\n")
    print(f"wrote {key_path}  ({len(KEY)} deliberate faults)")
    return results

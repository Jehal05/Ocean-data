"""Query parameters -> a checked Filter. Nothing from the user reaches SQL, a file path, an S3 key or a shell:
numbers and times are parsed by strict patterns, names are compared with lists the server already holds, text is only
ever a SQL parameter. Anything else is refused with 422 (or 413 when a limit is exceeded)."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

ALLOWED_PARAMS = {"time_from", "time_to", "bbox", "depth_min", "depth_max", "variable", "platform", "quality", "page", "page_size",
                  "format", "version", "allow_stale"}
NUM_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
INT_RE = re.compile(r"^\d{1,9}$")
TIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?Z?)?$")
NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,80}$")
PLATFORM_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
GOOD_FLAGS = (1, 2)


class Refused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _show(v: str) -> str:
    return repr(v[:40]) + ("..." if len(v) > 40 else "")


def number(text: str, name: str, lo: float, hi: float) -> float:
    if not NUM_RE.match(text):
        raise Refused(422, f"{name} must be a plain number, got {_show(text)}")
    x = float(text)
    if not math.isfinite(x) or x < lo or x > hi:
        raise Refused(422, f"{name} must be between {lo:g} and {hi:g}")
    return x


def integer(text: str, name: str, lo: int, hi: int) -> int:
    if not INT_RE.match(text):
        raise Refused(422, f"{name} must be a whole number, got {_show(text)}")
    x = int(text)
    if x < lo or x > hi:
        raise Refused(422, f"{name} must be between {lo} and {hi}")
    return x


def parse_time(text: str, name: str, *, end: bool) -> np.datetime64:
    """Date only: from = start of that day, to = end of that day. Date and time: that instant (to is inclusive)."""
    m = TIME_RE.match(text)
    if not m:
        raise Refused(422, f"{name} must look like 2025-01-31 or 2025-01-31T06:30:00Z, got {_show(text)}")
    y, mo, d, hh, mi, ss = m.groups()
    if not 1900 <= int(y) <= 2200:
        raise Refused(422, f"{name}: the year must be between 1900 and 2200")
    try:
        t = np.datetime64(f"{y}-{mo}-{d}T{hh or '00'}:{mi or '00'}:{ss or '00'}", "ns")
    except ValueError:
        raise Refused(422, f"{name} is not a real date or time")
    if hh is None and end:
        t = t + np.timedelta64(1, "D") - np.timedelta64(1, "ns")
    return t


def norm_lon(x):
    return ((x + 180.0) % 360.0) - 180.0


@dataclass(frozen=True)
class Bbox:
    west: float
    south: float
    east: float
    north: float
    full_lon: bool = False

    @property
    def wraps(self) -> bool:
        return not self.full_lon and self.west > self.east

    @property
    def width(self) -> float:
        if self.full_lon:
            return 360.0
        return self.east - self.west if self.west <= self.east else self.east + 360.0 - self.west

    @property
    def area(self) -> float:
        return self.width * (self.north - self.south)

    def lon_mask(self, lon: np.ndarray) -> np.ndarray:
        lon = norm_lon(np.asarray(lon, dtype="float64"))
        if self.full_lon:
            return np.isfinite(lon)
        if self.west <= self.east:
            return (lon >= self.west) & (lon <= self.east)
        return (lon >= self.west) | (lon <= self.east)

    def mask(self, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
        lat = np.asarray(lat, dtype="float64")
        return np.isfinite(lat) & (lat >= self.south) & (lat <= self.north) & self.lon_mask(lon)


def parse_bbox(text: str) -> Bbox:
    parts = text.split(",")
    if len(parts) != 4:
        raise Refused(422, "bbox must be west,south,east,north (four numbers)")
    w, s, e, n = (number(p.strip(), f"bbox[{i}]", -360 if i % 2 == 0 else -90, 360 if i % 2 == 0 else 90) for i, p in enumerate(parts))
    if s > n:
        raise Refused(422, "bbox: south must not be greater than north")
    if e - w >= 360:
        return Bbox(-180.0, s, 180.0, n, full_lon=True)
    east = 180.0 if e == 180.0 else norm_lon(e)          # 180 stays 180 so that a box ending at 180 does not wrap
    return Bbox(norm_lon(w), s, east, n)


@dataclass(frozen=True)
class Filter:
    time_from: np.datetime64 | None = None
    time_to: np.datetime64 | None = None
    bbox: Bbox | None = None
    depth_min: float | None = None
    depth_max: float | None = None
    variables: tuple[str, ...] | None = None
    platforms: tuple[str, ...] | None = None
    quality: str = "good"
    extra: dict = field(default_factory=dict, compare=False)

    def echo(self) -> dict:
        """What was applied, as plain values (for the response and the query log)."""
        out: dict = {"quality": self.quality}
        if self.time_from is not None:
            out["time_from"] = str(self.time_from)
        if self.time_to is not None:
            out["time_to"] = str(self.time_to)
        if self.bbox is not None:
            b = self.bbox
            out["bbox"] = [b.west, b.south, b.east, b.north]
        for k, v in (("depth_min", self.depth_min), ("depth_max", self.depth_max)):
            if v is not None:
                out[k] = v
        if self.variables:
            out["variable"] = list(self.variables)
        if self.platforms:
            out["platform"] = list(self.platforms)
        return out


def read_params(query_params) -> dict[str, str]:
    """Only known names, each at most once, no control characters, short."""
    out: dict[str, str] = {}
    for k in set(query_params.keys()):
        values = query_params.getlist(k)
        if k not in ALLOWED_PARAMS:
            raise Refused(422, f"unknown parameter {_show(k)}")
        if len(values) != 1:
            raise Refused(422, f"parameter {k} was given more than once")
        v = values[0]
        if len(v) > 400 or any(ord(c) < 32 or ord(c) == 127 for c in v):
            raise Refused(422, f"parameter {k} has an invalid value")
        out[k] = v
    return out


def parse_filter(params: dict[str, str], *, variables: list[str], has_time: bool, has_depth: bool, has_platform: bool,
                 is_product: bool) -> Filter:
    f: dict = {}
    if "time_from" in params or "time_to" in params:
        if not has_time:
            raise Refused(422, "this collection has no time axis")
        if "time_from" in params:
            f["time_from"] = parse_time(params["time_from"], "time_from", end=False)
        if "time_to" in params:
            f["time_to"] = parse_time(params["time_to"], "time_to", end=True)
        if f.get("time_from") is not None and f.get("time_to") is not None and f["time_from"] > f["time_to"]:
            raise Refused(422, "time_from must not be later than time_to")
    if "bbox" in params:
        f["bbox"] = parse_bbox(params["bbox"])
    for k in ("depth_min", "depth_max"):
        if k in params:
            if not has_depth:
                raise Refused(422, "this collection has no depth axis")
            f[k] = number(params[k], k, 0, 12000)
    if f.get("depth_min") is not None and f.get("depth_max") is not None and f["depth_min"] > f["depth_max"]:
        raise Refused(422, "depth_min must not be greater than depth_max")
    if "variable" in params:
        names = [x.strip() for x in params["variable"].split(",") if x.strip()]
        if not names or len(names) > 40:
            raise Refused(422, "variable must list 1 to 40 names")
        for n in names:
            if not NAME_RE.match(n) or n not in variables:
                raise Refused(422, f"unknown variable {_show(n)}; this collection has: {', '.join(variables)}")
        f["variables"] = tuple(dict.fromkeys(names))
    if "platform" in params:
        if not has_platform:
            raise Refused(422, "this collection has no platform axis")
        plats = [x.strip() for x in params["platform"].split(",") if x.strip()]
        if not plats or len(plats) > 50 or not all(PLATFORM_RE.match(p) for p in plats):
            raise Refused(422, "platform must list 1 to 50 identifiers made of letters, digits, _ . -")
        f["platforms"] = tuple(dict.fromkeys(plats))
    q = params.get("quality", "good")
    if q not in ("good", "all"):
        raise Refused(422, "quality must be 'good' or 'all'")
    if is_product and "quality" in params:
        raise Refused(422, "products hold only good values; the quality parameter is not available")
    f["quality"] = "good" if is_product else q
    return Filter(**f)

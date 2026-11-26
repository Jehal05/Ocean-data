"""Phase 5 browser tests: the real access API on the stand-in database and moto (tests/dash_stack.py), driven by headless Chrome.

Expected values come from the hand-made datasets in tests/access_helpers.py (tiny_profiles, tiny_series) or are computed here
from the raw synthetic files. SKIPPED, not passed, when Playwright, Chrome, PostgreSQL 16 or moto_server is missing."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import db_stub
from review_helpers import sql

ROOT = Path(__file__).resolve().parents[2]
PORT = 18081
BASE = f"http://127.0.0.1:{PORT}"
CHROME = os.getenv("SCATS_CHROME") or shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
HOSTILE_TITLE = '<img src=x onerror="window.__pwned=1"> =HYPERLINK("http://evil.example","x")'
FORMULA = "=cmd|' /C calc'!A0"

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                              # pragma: no cover
    sync_playwright = None

pytestmark = pytest.mark.skipif(not (sync_playwright and CHROME and db_stub.available() and shutil.which("moto_server")),
                                reason="needs Playwright, Chrome, PostgreSQL 16 binaries and moto_server")


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    out = tmp_path_factory.mktemp("stack") / "stack.out"
    fh = open(out, "w")
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests/dash_stack.py"), "--tiny", "--port", str(PORT)], stdout=fh, stderr=subprocess.STDOUT, cwd=ROOT)
    try:
        for _ in range(240):
            if "READY" in out.read_text() or proc.poll() is not None:
                break
            time.sleep(1.5)
        assert "READY" in out.read_text(), out.read_text()[-2000:]
        sql("UPDATE dataset SET title = %s WHERE dataset_key = 'hostile'", (HOSTILE_TITLE,), fetch=False)
        yield BASE
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        fh.close()


@pytest.fixture(scope="module")
def browser(stack):
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox", "--disable-background-networking", "--disable-sync", "--no-first-run"])
        yield b
        b.close()


@pytest.fixture(autouse=True)
def tidy(stack):
    for name, value in db_stub.API_LIMIT_DEFAULTS.items():
        sql("UPDATE api_limit SET value = %s WHERE name = %s", (value, name), fetch=False)
    sql("UPDATE api_limit SET value = 6000 WHERE name = 'rate_per_minute'", fetch=False)
    sql("UPDATE derived_product SET stale_at = NULL, stale_reason = NULL", fetch=False)
    sql("DELETE FROM login_attempt", fetch=False)
    yield


class Pg:
    """A page with its console, failed requests and every URL it asked for recorded."""

    def __init__(self, browser, accept_downloads=True):
        self.ctx = browser.new_context(viewport={"width": 1100, "height": 900}, accept_downloads=accept_downloads)
        self.page = self.ctx.new_page()
        self.console, self.urls = [], []
        self.page.on("console", lambda m: self.console.append((m.type, m.text)))
        self.page.on("pageerror", lambda e: self.console.append(("pageerror", str(e))))
        self.page.on("request", lambda r: self.urls.append(r.url))

    def login(self, user="viewer", password=None):
        p = self.page
        p.goto(BASE + "/app")
        p.wait_for_selector("#username")
        p.fill("#username", user)
        p.fill("#password", password or f"{user}123")
        p.click("button[type=submit]")
        p.wait_for_selector("#nav a")
        return self

    def go(self, view, wait):
        self.page.goto(f"{BASE}/app#/{view}")
        self.page.wait_for_selector(wait)
        return self

    def close(self):
        self.ctx.close()


@pytest.fixture
def pg(browser):
    p = Pg(browser)
    yield p
    p.close()


# Only these mean "the page was replaced while we looked": look again. Every other error is a real error and ends the test at once.
BETWEEN_PAGES = ("Execution context was destroyed", "Cannot find context with specified id", "Inspected target navigated or closed")


def until(page, js, timeout=20.0):
    """Wait until a JavaScript expression is true. (Playwright's wait_for_function uses eval, which the page's own CSP forbids.)
    A navigation in the middle is retried; any other error (a TypeError in the expression or the page) fails at once with its message."""
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            if page.evaluate(f"() => Boolean({js})"):
                return
        except Exception as e:  # noqa: BLE001 - classified just below
            if not any(m in str(e) for m in BETWEEN_PAGES):
                raise AssertionError(f"the page raised an error while waiting for: {js}\n{e}") from e
            last = e
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for: {js}" + (f" (last error: {last})" if last else ""))


def table_rows(page, selector):
    return page.eval_on_selector_all(f"{selector} tbody tr", "rows => rows.map(r => [...r.cells].map(c => c.textContent))")


# ---------------------------------------------------------------- login and session --
def test_login_page_warns_about_default_passwords_and_a_wrong_password_is_refused(pg):
    p = pg.page
    p.goto(BASE + "/app")
    p.wait_for_selector("#username")
    assert "published development passwords" in p.inner_text("#default-passwords")
    p.fill("#username", "viewer")
    p.fill("#password", "wrong-password")
    p.click("button[type=submit]")
    until(p, "document.querySelector('.login ~ .error')?.textContent.length > 0")
    assert p.inner_text(".login ~ .error").strip() != ""
    assert p.locator("#nav a").count() == 0                                # still not logged in
    assert pg.ctx.cookies() == []                                          # no session cookie was set


def test_login_page_shows_no_password_warning_when_the_server_says_null_or_false(pg):
    p = pg.page
    for body in ('{"default_passwords": null}', '{"default_passwords": false}'):
        p.route("**/health", lambda route, _request, b=body: route.fulfill(status=200, content_type="application/json", body=b))
        p.goto(BASE + "/app")
        p.wait_for_selector("#username")
        assert p.locator("#default-passwords").count() == 0, body
        assert "development passwords" not in p.inner_text("main")
        p.unroute("**/health")


def test_the_session_cookie_is_httponly_samesite_strict_and_not_readable_by_script(pg):
    pg.login()
    cookies = pg.ctx.cookies()
    assert len(cookies) == 1 and cookies[0]["httpOnly"] is True and cookies[0]["sameSite"] == "Strict"
    token = cookies[0]["value"]
    assert token not in pg.page.evaluate("document.cookie")
    assert token not in pg.page.evaluate("JSON.stringify([localStorage, sessionStorage])")      # never stored where script can read it
    assert not any(token in u for u in pg.urls)                                                  # never in a URL
    assert all(token not in t for _, t in pg.console)                                            # never logged


def test_logout_needs_the_csrf_header_and_really_ends_the_session(pg):
    pg.login()
    p = pg.page
    no_header = p.evaluate("fetch('/auth/logout', {method: 'POST', credentials: 'same-origin'}).then(r => r.status)")
    assert no_header == 403                                                # a cookie session without X-Requested-With cannot change anything
    assert p.evaluate("fetch('/auth/me', {credentials: 'same-origin'}).then(r => r.status)") == 200
    p.click("#logout")
    p.wait_for_selector("#username")
    assert p.url.endswith("#/login")
    assert p.evaluate("fetch('/auth/me', {credentials: 'same-origin'}).then(r => r.status)") == 401
    assert [c for c in pg.ctx.cookies() if c["value"]] == []


def test_five_wrong_passwords_lock_the_account_and_the_ui_says_so(pg):
    p = pg.page
    p.goto(BASE + "/app")
    p.wait_for_selector("#username")
    for _ in range(6):
        p.fill("#username", "locktest")
        p.fill("#password", "nope")
        p.click("button[type=submit]")
        p.wait_for_timeout(250)
    until(p, "document.querySelector('.login ~ .error')?.textContent.includes('429')")
    text = p.inner_text(".login ~ .error")
    assert "Too many requests" in text and "too many failed attempts" in text


def test_an_expired_session_sends_the_user_back_to_the_login_page(pg):
    pg.login()
    sql("UPDATE auth_session SET expires_at = now() - interval '1 minute'", fetch=False)
    pg.page.goto(f"{BASE}/app#/series")
    pg.page.wait_for_selector("#username")
    assert pg.page.url.endswith("#/login")
    # the other tests log in again with fresh sessions; nothing is left to restore


# ---------------------------------------------------------------------------------- map --
def test_map_draws_one_footprint_per_published_dataset_and_none_for_unpublished(pg):
    pg.login()
    p = pg.page
    p.wait_for_selector("#map-count")
    assert p.inner_text("#map-count") == "8 datasets shown"               # argo, ctd, buoy, sst, bathy, hostile, stale_target, antimeridian; "unpub" is in review
    assert p.locator("svg.chart g.fp").count() == 9                       # the antimeridian grid is drawn in two pieces
    keys = [r[0] for r in table_rows(p, "table.list")]
    assert sorted(keys) == ["antimeridian", "argo_synthetic_arabian_sea_2025q1", "bathymetry_synthetic_0p1deg", "buoy_met_wave_synthetic_2025q1",
                            "ctd_casts_synthetic_arabian_sea_2025", "hostile", "sst_synthetic_0p25deg_2025q1", "stale_target"]
    assert "unpub" not in keys and "rejected" not in keys
    p.fill("#platform_type", "argo_float")
    p.click("button[type=submit]")
    until(p, "document.getElementById('map-count')?.textContent.startsWith('2 ')")
    assert p.locator("svg.chart g.fp").count() == 2
    assert sorted(r[0] for r in table_rows(p, "table.list")) == ["argo_synthetic_arabian_sea_2025q1", "stale_target"]


def test_a_footprint_and_a_box_that_cross_180_degrees_are_each_drawn_in_two_pieces(pg):
    pg.login()
    p = pg.page
    p.wait_for_selector("#map-count")
    pieces = p.locator("svg.chart g.fp[aria-label^='antimeridian:'] rect.fprect")
    assert pieces.count() == 2                                            # the catalogue says west edge 178, east edge -178: [178,180] and [-180,-178]
    xs = sorted(float(pieces.nth(i).get_attribute("x")) for i in range(2))
    widths = [float(pieces.nth(i).get_attribute("width")) for i in range(2)]
    assert xs[1] - xs[0] > 100 and all(w >= 6 for w in widths)            # one piece at each end of the longitude axis, not one rectangle across the world
    p.fill("#bbox", "60,0,-170,30")                                       # west > east: a box across 180 (it also meets the Arabian Sea data, so the map is drawn)
    p.click("button[type=submit]")
    until(p, "document.querySelectorAll('svg.chart rect.bbox').length > 0")
    assert p.locator("svg.chart rect.bbox").count() == 2                  # [60,180] and [-180,-170]
    xs = sorted(float(p.locator("svg.chart rect.bbox").nth(i).get_attribute("x")) for i in range(2))
    assert xs[0] < xs[1] and p.locator("svg.chart rect.bbox").nth(0).get_attribute("width") != p.locator("svg.chart rect.bbox").nth(1).get_attribute("width")
    p.fill("#bbox", "60,0,70,30")                                         # an ordinary box: one rectangle
    p.click("button[type=submit]")
    until(p, "document.querySelectorAll('svg.chart rect.bbox').length === 1")


def test_an_older_answer_arriving_late_never_overwrites_the_newer_one(pg):
    pg.login()
    p = pg.page
    p.wait_for_selector("#map-count")
    held = []
    p.route("**/datasets?*", lambda route: held.append(route) if not held else route.continue_())      # the first new request is held back
    p.click("button[type=submit]")                                                                   # request 1: no filter (8 datasets), held
    while not held:
        p.wait_for_timeout(50)
    p.fill("#platform_type", "argo_float")
    p.click("button[type=submit]")                                                                   # request 2: argo only (2 datasets), answered at once
    until(p, "document.getElementById('map-count')?.textContent.startsWith('2 ')")
    held[0].continue_()                                                                              # now the old answer (7) arrives
    p.wait_for_timeout(800)
    assert p.inner_text("#map-count") == "2 datasets shown"
    assert p.locator("svg.chart g.fp").count() == 2
    p.unroute("**/datasets?*")


def test_map_box_filter_drawn_and_a_bad_box_is_refused_in_words(pg):
    pg.login()
    p = pg.page
    p.wait_for_selector("#map-count")
    p.fill("#bbox", "69,14,70,16")                                       # touches the buoy point (68,15)? no: 68 < 69
    p.click("button[type=submit]")
    until(p, "document.getElementById('map-count')?.textContent.includes('shown')")
    keys = sorted(r[0] for r in table_rows(p, "table.list"))
    assert "buoy_met_wave_synthetic_2025q1" not in keys and "hostile" not in keys                   # longitude 68 is outside 69..70
    assert "argo_synthetic_arabian_sea_2025q1" in keys and "ctd_casts_synthetic_arabian_sea_2025" in keys
    assert p.locator("svg.chart rect.bbox").count() == 1
    p.fill("#bbox", "1,2,3")
    p.click("button[type=submit]")
    p.wait_for_selector(".messages .error")
    assert "west,south,east,north" in p.inner_text(".messages .error")


def test_map_footer_says_when_the_data_is_synthetic(pg):
    pg.login()
    pg.page.wait_for_selector("#map-count")
    assert "synthetic" in pg.page.inner_text("#synthetic")


# ------------------------------------------------------------------------------- profile --
def pick_profile(pg, key="stale_target"):
    p = pg.go("profile", "#dataset").page
    p.select_option("#dataset", key)
    p.click("button[type=submit]")
    p.wait_for_selector("#profile")
    return p


def test_profile_shows_the_hand_computed_points_and_hides_flagged_values(pg):
    pg.login()
    p = pick_profile(pg)
    p.wait_for_selector("svg.panel")
    # profiles A and B pass the position check; C has position flag 4 and is not listed under good quality
    until(p, "document.querySelector('.pick .count')?.textContent === '2 profiles listed'")
    labels = p.eval_on_selector_all("#profile option", "o => o.map(x => x.textContent)")
    assert labels[0].startswith("A #0") and labels[1].startswith("B #1") and len(labels) == 2
    assert "A #0" in p.inner_text("h3")
    p.click("main .tableblock button")
    rows = table_rows(p, ".tableblock table")
    # profile A: depth 1, 2, 6, 7, 100 with temperature 10, 12, 20, 22, 5 and flags 1, 2, 1, 3, 1; the flag 3 value is hidden under "good"
    assert [(r[0], r[1]) for r in rows] == [("1", "10"), ("2", "12"), ("6", "20"), ("7", "no value"), ("100", "5")]
    assert [r[2] for r in rows] == ["1 good", "2 probably good", "1 good", "3 probably bad", "1 good"]
    assert p.locator("svg.panel .mk").count() == 4                          # no marker for the hidden value
    assert p.locator("svg.panel path.line").count() == 2                    # the line has a gap where the value is missing, it is not drawn to zero
    shapes = p.eval_on_selector_all("svg.panel .mk", "m => m.map(x => x.tagName)")
    assert shapes == ["circle", "polygon", "circle", "circle"]              # flag 1 circle, flag 2 diamond


def test_profile_with_all_values_shows_the_flag_by_shape_and_label(pg):
    pg.login()
    p = pick_profile(pg)
    p.select_option("#quality", "all")
    until(p, "document.querySelectorAll('svg.panel .mk').length === 5")
    assert p.locator("svg.panel path.line").count() == 1                    # all five values present: one unbroken line
    tags = p.eval_on_selector_all("svg.panel .mk", "m => m.map(x => x.tagName + ':' + x.getAttribute('class'))")
    assert tags[3].startswith("polygon") and "flag3" in tags[3]             # flag 3 is a triangle (a polygon), flag 2 a diamond (also a polygon)
    p.click("main .tableblock button")
    rows = table_rows(p, ".tableblock table")
    assert [r[1] for r in rows] == ["10", "12", "20", "22", "5"] and rows[3][2] == "3 probably bad"
    assert p.locator("svg.legend text").count() == 5                        # the legend names every shape


def test_profile_of_the_real_argo_file_has_one_point_per_good_level(pg):
    pg.login()
    p = pg.go("profile", "#dataset").page
    p.wait_for_selector("svg.panel")
    p.click("main .tableblock button")
    rows = table_rows(p, ".tableblock table")
    depths = [float(r[0]) for r in rows]
    assert depths == sorted(depths) and depths[0] < 10                      # shallowest first
    n_temp = sum(1 for r in rows if r[3] != "no value")
    assert p.locator("svg.panel").nth(1).locator(".mk").count() == n_temp   # the second panel is temperature (alphabetical: salinity, temperature)


# --------------------------------------------------------------------------------- series --
def pick_series(pg, key, variable="wind_speed"):
    p = pg.go("series", "#product").page
    p.select_option("#product", key)
    p.select_option("#variable", variable)
    p.click("button[type=submit]")
    p.wait_for_selector("svg.chart path.line")
    return p


def test_series_band_line_gap_and_counts_match_the_hand_computed_hours(pg):
    pg.login()
    p = pick_series(pg, "series_hourly__hostile")
    # hour 0: 2 and 4 (the 100 has flag 3: out) -> mean 3, min 2, max 4, n 2; hour 1: 6 (the record with a bad time flag is out); hour 2: nothing; hour 3: 8
    p.click("main .tableblock button")
    rows = table_rows(p, ".tableblock table")
    got = [(r[0][11:13], r[1], r[2], r[3], r[4]) for r in rows]
    assert got == [("00", "3", "2", "4", "2"), ("01", "6", "6", "6", "1"), ("02", "no value", "no value", "no value", "0"), ("03", "8", "8", "8", "1")]
    assert p.locator("svg.chart path.line").count() == 2                    # hours 0-1 and hour 3: the empty hour is a gap, not a zero
    assert p.locator("svg.chart path.band").count() == 2
    bars = p.locator("svg.counts rect.bar")
    assert bars.count() == 4                                                 # one bar per bin ...
    heights = [float(bars.nth(i).get_attribute("height")) for i in range(4)]
    assert [h > 0 for h in heights] == [True, True, False, True]             # ... and the empty hour's bar has no height
    assert "Band: minimum to maximum" in p.inner_text(".hint")


def test_a_direction_variable_has_a_circular_mean_and_no_band(pg):
    pg.login()
    p = pick_series(pg, "series_hourly__hostile", "wind_direction")
    p.click("main .tableblock button")
    rows = table_rows(p, ".tableblock table")
    hour0 = rows[0]
    assert abs(float(hour0[1]) % 360) < 1e-6 or abs(float(hour0[1]) - 360) < 1e-6      # 350 and 10 degrees: the mean is 0, not 180
    assert p.locator("svg.chart path.band").count() == 0                    # directions have no min and max band


def test_series_of_the_real_buoy_has_a_band_and_a_mean_that_is_inside_it(pg):
    pg.login()
    p = pg.go("series", "#product").page
    p.select_option("#product", "series_hourly__buoy_met_wave_synthetic_2025q1")
    p.select_option("#variable", "air_pressure")
    p.click("button[type=submit]")
    p.wait_for_selector("svg.chart path.band")
    p.click("main .tableblock button")
    for r in table_rows(p, ".tableblock table"):
        if r[1] != "no value":
            assert float(r[2]) <= float(r[1]) <= float(r[3])                # min <= mean <= max in every bin


# ------------------------------------------------------------------------------------ sst --
def test_sst_shows_a_cell_for_every_grid_point_and_the_cell_values_are_the_products(pg):
    pg.login()
    p = pg.go("sst?key=sst_monthly_mean__sst_synthetic_0p25deg_2025q1", "#month").page
    p.wait_for_selector("svg.chart rect.cell")
    assert p.locator("svg.chart rect.cell").count() == 169                   # the box 60,8,63,11 at 0.25 degrees: 13 x 13
    p.click("main .tableblock button")
    rows = table_rows(p, ".tableblock table")
    assert len(rows) == 169
    # independent check: the January mean of the cell at 8.0 N, 60.0 E from the raw file, good flags only
    import numpy as np
    import xarray as xr
    raw = xr.open_dataset(ROOT / "data/raw/sst_grid/sst_synthetic_0p25deg_2025Q1.nc")             # packed kelvin; the parser converts to degC
    jan = raw.sel(time=slice("2025-01-01", "2025-01-31"))
    cell = jan["analysed_sst"].sel(lat=8.0, lon=60.0) - 273.15
    assert int(cell.notnull().sum()) >= 15
    first = [r for r in rows if r[0] == "8" and r[1] == "60"][0]
    assert abs(float(first[2]) - float(np.nanmean(cell.values))) < 0.01, (first, float(np.nanmean(cell.values)))
    assert p.locator("svg.chart.bar").count() == 1                           # the colour scale


def test_sst_too_large_a_box_shows_the_api_refusal_not_a_blank_chart(pg):
    pg.login()
    sql("UPDATE api_limit SET value = 100 WHERE name = 'max_rows_subset'", fetch=False)
    p = pg.go("sst?key=sst_monthly_mean__sst_synthetic_0p25deg_2025q1", "#month").page
    p.fill("#bbox", "60,8,75,24")
    p.click("button[type=submit]")
    p.wait_for_selector(".messages .error")
    text = p.inner_text(".messages .error")
    assert text.startswith("Too much data requested (HTTP 413)") and "max_rows_subset = 100" in text
    assert p.locator("svg.chart rect.cell").count() == 0


def _count_subset_requests(pg, key):
    return [u for u in pg.urls if f"/products/{key}/subset" in u]


def test_sst_stops_at_the_row_cap_says_so_and_asks_for_no_more_pages(pg):
    key = "sst_monthly_mean__sst_synthetic_0p25deg_2025q1"
    pg.login()
    p = pg.go("sst?key=" + key, "#month").page
    first = p.evaluate(f"fetch('/products/{key}/subset?bbox=60,8,75,24&time_from=2025-01-01&time_to=2025-01-31&page_size=1', {{credentials: 'same-origin'}}).then(r => r.json())")
    assert "n_rows_selected" in first, first
    total = first["n_rows_selected"]
    limits = {l['name']: l['value'] for l in p.evaluate("fetch('/limits', {credentials: 'same-origin'}).then(r => r.json())")['limits']}
    assert 2000 < total < limits["max_rows_subset"], (total, limits)                  # over the drawing cap, under the API's own limit
    page_size = min(200, limits["page_size_max"])
    before = len(_count_subset_requests(pg, key))
    p.fill("#bbox", "60,8,75,24")
    p.click("button[type=submit]")
    p.wait_for_selector("svg.chart rect.cell")
    drawn = p.locator("svg.chart rect.cell").count()
    notice = [t for t in p.locator(".result p.notice").all_inner_texts() if t.startswith("Only the first")]
    assert notice == [f"Only the first {drawn} of {total} cells are drawn. Use a smaller box."], notice
    assert p.locator("svg.chart.bar rect.plot").count() == 1                              # the colour scale has a frame, so its pale end is seen
    assert drawn == -(-2000 // page_size) * page_size and drawn < total               # whole pages up to the cap: 2000 with a page of 200
    assert len(_count_subset_requests(pg, key)) - before == -(-2000 // page_size)      # exactly the pages needed to reach the cap, none after it


def test_series_stops_at_the_row_cap_says_so_and_asks_for_no_more_pages(pg):
    key = "series_hourly__buoy_met_wave_synthetic_2025q1"
    pg.login()
    p = pg.go("series", "#product").page
    p.select_option("#product", key)
    p.select_option("#variable", "air_pressure")
    p.fill("#time_from", "2025-01-01")
    p.fill("#time_to", "2025-04-01")
    limits = {l['name']: l['value'] for l in p.evaluate("fetch('/limits', {credentials: 'same-origin'}).then(r => r.json())")['limits']}
    first = p.evaluate(f"fetch('/products/{key}/subset?variable=air_pressure_mean&time_from=2025-01-01&time_to=2025-04-01&page_size=1', {{credentials: 'same-origin'}}).then(r => r.json())")
    total = first["n_rows_selected"]
    assert 2000 < total < limits["max_rows_subset"], (total, limits)
    before = len(_count_subset_requests(pg, key))
    p.click("button[type=submit]")
    p.wait_for_selector("svg.chart path.line")
    notice = [t for t in p.locator(".result p.notice").all_inner_texts() if t.startswith("Only the first")]
    assert len(notice) == 1
    n = int(notice[0].split()[3])
    assert notice[0] == f"Only the first {n} of {total} rows are drawn. Narrow the time range." and n < total
    assert p.locator("svg.counts rect.bar").count() == n                                 # one bar per bin drawn
    assert len(_count_subset_requests(pg, key)) - before == -(-2000 // min(200, limits["page_size_max"]))


# ---------------------------------------------------------------------- explore, notices --
def explore(pg, kind, key, **fields):
    p = pg.go("explore", "#key").page
    if kind != "dataset":
        p.select_option("#type", kind)
    until(p, f"[...document.querySelectorAll('#key option')].some(o => o.value === {key!r})")
    p.select_option("#key", key)
    for k, v in fields.items():
        if k == "quality":
            p.select_option("#quality", v)
        else:
            p.fill(f"#{k}", v)
    return p


def test_a_truncated_page_says_so_and_offers_the_next_page(pg):
    pg.login()
    p = explore(pg, "dataset", "argo_synthetic_arabian_sea_2025q1")
    p.click("button[type=submit]")
    p.wait_for_selector("#explore-count")
    assert p.inner_text("#explore-count") == "4800 rows selected, 50 shown (page 1)"
    assert "page 1 of 96 (4800 selected)" in p.inner_text(".messages .notice")
    assert p.locator("button:has-text('Next page (2)')").count() == 1
    p.click("button:has-text('Next page (2)')")
    until(p, "document.getElementById('explore-count')?.textContent.endsWith('(page 2)')")
    assert "page 2 of 96" in p.inner_text(".messages .notice")


def test_a_selection_that_fits_in_one_page_has_no_truncation_notice(pg):
    pg.login()
    p = explore(pg, "dataset", "hostile", quality="all")
    p.click("button[type=submit]")
    p.wait_for_selector("#explore-count")
    assert p.inner_text("#explore-count") == "6 rows selected, 6 shown (page 1)"
    assert p.locator(".messages .notice").count() == 0
    assert p.locator("button:has-text('Next page')").count() == 0


def test_a_stale_product_is_refused_with_the_reason_and_shown_with_a_notice_when_asked(pg):
    sql("UPDATE derived_product SET stale_at = now(), stale_reason = 'its source dataset was re-reviewed' WHERE product_key = 'binned_profile__stale_target'", fetch=False)
    pg.login()
    p = explore(pg, "product", "binned_profile__stale_target")
    assert "binned_profile__stale_target (stale)" in p.inner_text("#key")
    p.click("button[type=submit]")
    p.wait_for_selector(".messages .error")
    assert "Refused: the data changed or is stale (HTTP 409)" in p.inner_text(".messages .error")
    assert "its source dataset was re-reviewed" in p.inner_text(".messages .error")
    p.check("#allow_stale")
    p.click("button[type=submit]")
    p.wait_for_selector(".messages .notice")
    assert "This product is stale: its source dataset was re-reviewed" in p.inner_text(".messages .notice")
    assert p.locator("#explore-count").count() == 1


def test_api_refusals_are_shown_with_the_status_and_the_apis_own_words(pg):
    pg.login()
    sql("UPDATE api_limit SET value = 100 WHERE name = 'max_rows_subset'", fetch=False)
    p = explore(pg, "dataset", "argo_synthetic_arabian_sea_2025q1")
    p.click("button[type=submit]")
    p.wait_for_selector(".messages .error")
    assert p.inner_text(".messages .error") == "Too much data requested (HTTP 413): the selection has 4800 rows (limit max_rows_subset = 100) (narrow the filters, or use /download)"
    sql("UPDATE api_limit SET value = 5000 WHERE name = 'max_rows_subset'", fetch=False)
    p.fill("#time_from", "2025-03-01")
    p.fill("#time_to", "2025-02-01")
    p.click("button[type=submit]")
    until(p, "document.querySelector('.messages .error')?.textContent.includes('422')")
    assert "time_from must not be later than time_to" in p.inner_text(".messages .error")
    p.fill("#depth_max", "abc")
    p.click("button[type=submit]")
    until(p, "document.querySelector('.messages .error')?.textContent.includes('depth_max must be a number')")


def test_the_rate_limit_message_is_shown(pg):
    pg.login()
    p = explore(pg, "dataset", "hostile", quality="all")
    sql("UPDATE api_limit SET value = 3 WHERE name = 'rate_per_minute' AND role_name = 'viewer'", fetch=False)
    for _ in range(4):
        p.click("button[type=submit]")
        p.wait_for_timeout(400)
    until(p, "document.querySelector('.messages .error')?.textContent.includes('429')")
    assert p.inner_text(".messages .error").startswith("Too many requests, wait a minute (HTTP 429)")


def test_a_viewer_is_refused_an_admin_route_with_a_403_and_the_reason(pg):
    pg.login()
    r = pg.page.evaluate("fetch('/health/audit', {credentials: 'same-origin'}).then(async r => [r.status, (await r.json()).detail])")
    assert r == [403, "the admin role is required"]
    sql("UPDATE api_limit SET value = 5000 WHERE name = 'max_rows_subset'", fetch=False)


def test_the_limits_in_force_are_listed_for_the_user(pg):
    pg.login()
    p = pg.go("explore", "details.limits").page
    until(p, "document.querySelector('details.limits dl')")
    p.click("details.limits summary")
    text = p.inner_text("details.limits")
    assert "max_rows_subset" in text and "5000" in text and "rate_per_minute" in text


# ------------------------------------------------------------------------ hostile strings --
def test_hostile_titles_and_platform_ids_are_text_and_run_nothing(pg):
    pg.login()
    p = pg.page
    p.wait_for_selector("#map-count")
    titles = [r[1] for r in table_rows(p, "table.list")]
    assert HOSTILE_TITLE in titles                                           # shown literally, as text
    assert p.locator("img").count() == 0 and p.evaluate("window.__pwned") is None
    assert p.locator("svg.chart g.fp title", has_text="hostile").count() == 1
    p = explore(pg, "dataset", "hostile", quality="all")
    p.click("button[type=submit]")
    p.wait_for_selector("#explore-count")
    cells = [r[0] for r in table_rows(p, ".tableblock table")]
    assert cells.count(FORMULA) == 6                                         # the formula platform id is plain text in the table
    assert p.evaluate("window.__pwned") is None
    assert p.locator("img, script:not([src])").count() == 0


def test_a_hostile_search_text_is_sent_as_a_value_and_never_changes_the_query(pg):
    pg.login()
    p = pg.page
    p.wait_for_selector("#map-count")
    p.fill("#q", "x' OR '1'='1")
    p.click("button[type=submit]")
    until(p, "document.getElementById('map-count')?.textContent.startsWith('0 ')")
    assert p.inner_text("#map-count") == "0 datasets shown"
    p.fill("#q", "&platform_type=argo_float&page_size=1")                    # looks like extra parameters; it is only text
    p.click("button[type=submit]")
    until(p, "document.getElementById('map-count')?.textContent.includes('shown')")
    assert p.inner_text("#map-count") == "0 datasets shown"


# ------------------------------------------------------------------------------- download --
def test_download_gives_the_server_made_file_name_and_neutralises_formulas(pg, tmp_path):
    pg.login()
    p = explore(pg, "dataset", "hostile", quality="all")
    p.click("button[type=submit]")
    p.wait_for_selector("button.dl")
    with p.expect_download() as d:
        p.click("button.dl[data-format=csv]")
    dl = d.value
    server = pg.ctx.request.get(f"{BASE}/datasets/hostile/download", params={"format": "csv", "quality": "all"})
    header = server.headers["content-disposition"]
    assert dl.suggested_filename in header and re.fullmatch(r"[A-Za-z0-9._-]+\.csv", dl.suggested_filename)
    assert "hostile" in dl.suggested_filename
    f = tmp_path / "out.csv"
    dl.save_as(f)
    text = f.read_text()
    assert "'" + FORMULA in text and f",{FORMULA}," not in text.replace("'" + FORMULA, "")   # every formula cell starts with an apostrophe
    assert "Saved " in p.inner_text("#saved") and "chosen by the server" in p.inner_text("#saved")
    with p.expect_download() as d2:
        p.click("button.dl[data-format=netcdf]")
    assert d2.value.suggested_filename.endswith(".nc")


def test_a_download_that_is_refused_is_shown_as_text_and_nothing_is_saved(pg):
    pg.login()
    p = explore(pg, "dataset", "argo_synthetic_arabian_sea_2025q1")
    p.click("button[type=submit]")
    p.wait_for_selector("button.dl")
    sql("UPDATE api_limit SET value = 100 WHERE name = 'max_rows_download'", fetch=False)
    downloads = []
    p.on("download", lambda d: downloads.append(d))
    p.click("button.dl[data-format=csv]")
    p.wait_for_selector(".messages .error")
    assert "Too much data requested (HTTP 413)" in p.inner_text(".messages .error") and "max_rows_download = 100" in p.inner_text(".messages .error")
    p.wait_for_timeout(500)
    assert downloads == []


# ---------------------------------------------------------- CSP, offline, accessibility --
def test_csp_is_enforced_on_the_pages_and_no_view_causes_a_violation(pg):
    pg.login()
    for view, wait in (("map", "#map-count"), ("profile", "svg.panel"), ("series", "#product"), ("sst", "svg.chart rect.cell"), ("explore", "#key")):
        pg.go(view, wait)
    bad = [t for _, t in pg.console if "Content Security Policy" in t or "Refused to" in t]
    assert bad == []
    r = pg.ctx.request.get(BASE + "/app")
    csp = r.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp and "connect-src 'self'" in csp
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    # control: the policy really blocks an injected inline script (so an empty violation list means something)
    p = pg.page
    p.evaluate("() => { const s = document.createElement('script'); s.textContent = 'window.__injected = 1'; document.body.append(s); }")
    assert p.evaluate("window.__injected") is None
    assert any("Content Security Policy" in t or "Refused to" in t for _, t in pg.console)


def test_the_dashboard_asks_for_nothing_outside_its_own_origin(pg):
    pg.login()
    for view, wait in (("map", "#map-count"), ("profile", "svg.panel"), ("series", "svg.chart"), ("sst", "svg.chart"), ("explore", "#key")):
        pg.go(view, wait)
    outside = [u for u in pg.urls if not (u.startswith(BASE) or u.startswith("data:") or u.startswith("blob:"))]
    assert outside == []
    assert {u.split("?")[0] for u in pg.urls if u.endswith(".js") or u.endswith(".css")} >= {f"{BASE}/app/{n}" for n in ("app.js", "views.js", "logic.js", "api.js", "svg.js", "style.css")}


def test_keyboard_and_screen_reader_basics(pg):
    pg.login()
    p = pg.page
    p.goto(BASE + "/app#/map")
    p.reload()
    p.wait_for_selector("#map-count")
    p.keyboard.press("Tab")
    assert p.evaluate("document.activeElement.className") == "skip"        # the first stop is the skip link
    p.keyboard.press("Tab")
    assert p.evaluate("document.activeElement.getAttribute('data-view')") == "map"
    pg.go("profile", "svg.panel")
    assert p.evaluate("document.activeElement.id") == "heading"            # focus moves to the page heading on a route change
    unlabeled = p.evaluate("[...document.querySelectorAll('input:not([type=checkbox]), select')].filter(e => !e.closest('label')).length")
    assert unlabeled == 0
    for svg in p.query_selector_all("svg.chart"):
        assert svg.get_attribute("role") == "img" and svg.get_attribute("aria-label")
    btn = p.locator("main .tableblock button")
    assert btn.get_attribute("aria-expanded") == "false" and p.locator(".tableblock table").is_hidden()
    btn.press("Enter")                                                      # the table alternative works from the keyboard
    assert btn.get_attribute("aria-expanded") == "true" and p.locator(".tableblock table").is_visible()
    assert p.evaluate("document.documentElement.lang") == "en"


def test_every_view_is_reachable_from_the_nav_and_the_current_one_is_marked(pg):
    pg.login()
    p = pg.page
    for view in ("map", "profile", "series", "sst", "explore"):
        p.click(f"nav a[data-view={view}]")
        until(p, f"document.querySelector('nav a[data-view={view}]')?.getAttribute('aria-current') === 'page'")
        assert p.locator("nav a[aria-current=page]").count() == 1


def test_the_files_the_page_loads_are_served_without_caching_and_unknown_files_are_not_served(pg):
    r = pg.ctx.request.get(BASE + "/app/app.js")
    assert r.status == 200 and "text/javascript" in r.headers["content-type"] and r.headers["cache-control"] == "no-store"
    for name in ("package.json", "../logic.js", "secret.txt", "%2e%2e/%2e%2e/etc/passwd"):
        assert pg.ctx.request.get(f"{BASE}/app/{name}").status in (404, 403)
    assert pg.ctx.request.get(BASE + "/", max_redirects=0).status == 307


AXE = ROOT / "tests/tools/node_modules/axe-core/axe.min.js"      # npm install --prefix tests/tools  (a test-time tool; never served, never committed)


@pytest.mark.skipif(not AXE.is_file(), reason="axe-core is not installed: run `npm install --prefix tests/tools`")
def test_axe_core_finds_no_wcag_a_or_aa_violation_in_any_view(pg):
    pg.login()
    p = pg.page
    src = AXE.read_text()
    tags = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
    found = {}
    views = [("map", "#map-count"), ("profile", "#profile"), ("series", "svg.chart path.line, #product"), ("sst", "#month"), ("explore", "#key")]
    for view, wait in views:
        pg.go(view, wait)
        if view == "profile":
            p.wait_for_selector("svg.panel")
        if view == "series":
            p.click("button[type=submit]")
            p.wait_for_selector("svg.chart path.line")
        if view == "sst":
            p.wait_for_selector("svg.chart rect.cell")
        p.evaluate(src)                                            # Runtime.evaluate is not blocked by the page's CSP; nothing is added to the served files
        res = p.evaluate("axe.run(document, {runOnly: {type: 'tag', values: %s}}).then(r => ({n: r.violations.length, v: r.violations.map(x => ({id: x.id, impact: x.impact, nodes: x.nodes.slice(0, 3).map(n => n.target.join(' '))})), passes: r.passes.length}))" % tags)
        assert res["passes"] > 10, (view, res)                     # axe really ran rules on this page
        if res["v"]:
            found[view] = res["v"]
    pg2 = Pg(p.context.browser)
    try:
        pg2.page.goto(BASE + "/app")
        pg2.page.wait_for_selector("#username")
        pg2.page.evaluate(src)
        res = pg2.page.evaluate("axe.run(document, {runOnly: {type: 'tag', values: %s}}).then(r => r.violations.map(x => ({id: x.id, impact: x.impact})))" % tags)
        if res:
            found["login"] = res
    finally:
        pg2.close()
    assert found == {}, found


# ---------------------------------------------------------------------------------------- the helper itself --
class _FakePage:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), 0

    def evaluate(self, _js):
        self.calls += 1
        o = self.outcomes.pop(0) if self.outcomes else True
        if isinstance(o, Exception):
            raise o
        return o


def test_until_retries_a_navigation_but_nothing_else():
    nav = Exception("Page.evaluate: Execution context was destroyed, most likely because of a navigation")
    page = _FakePage([nav, nav, False, True])
    until(page, "x", timeout=5)
    assert page.calls == 4                                                  # two navigations retried, one False, then true
    boom = Exception("Page.evaluate: TypeError: Cannot read properties of null (reading 'textContent')")
    page = _FakePage([boom, True])
    with pytest.raises(AssertionError, match="TypeError: Cannot read properties of null"):
        until(page, "document.getElementById('nope').textContent", timeout=5)
    assert page.calls == 1                                                  # it did not look again
    page = _FakePage([nav] * 1000)
    with pytest.raises(AssertionError, match=r"timed out waiting for: x \(last error: .*Execution context was destroyed"):
        until(page, "x", timeout=0.5)


def test_until_fails_at_once_on_a_real_page_that_throws(browser):
    pgx = Pg(browser)
    try:
        pgx.page.set_content("<p id=a>x</p>")
        t0 = time.monotonic()
        with pytest.raises(AssertionError, match="TypeError"):
            until(pgx.page, "document.getElementById('missing').textContent === 'x'", timeout=15)
        assert time.monotonic() - t0 < 5                                    # at once, not after the 15 s timeout
    finally:
        pgx.close()

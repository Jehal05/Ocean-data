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
    assert p.locator(".legend .chip").count() == 6                          # the legend names every shape (the six flags, each a chip with its shape and words)


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
    assert p.locator(".hint", has_text="Band: minimum to maximum").count() == 1


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
    assert p.evaluate("document.activeElement.id") == "theme"               # then the top bar (theme, sign out), then the sidebar
    p.keyboard.press("Tab")
    assert p.evaluate("document.activeElement.id") == "logout"
    p.keyboard.press("Tab")
    assert p.evaluate("document.activeElement.getAttribute('data-view')") == "map"
    pg.go("profile", "svg.panel")
    assert p.evaluate("document.activeElement.id") == "heading"            # focus moves to the page heading on a route change
    unlabeled = p.evaluate("[...document.querySelectorAll('input:not([type=checkbox]), select')].filter(e => !e.closest('label')).length")
    assert unlabeled == 0
    for svg in p.query_selector_all("svg.chart"):
        # a chart you can only look at is an image; a chart you can move through with the arrow keys is a focusable group. Both have a name.
        assert svg.get_attribute("aria-label") and (svg.get_attribute("role") == "img" or (svg.get_attribute("role") == "group" and svg.get_attribute("tabindex") == "0"))
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


# ================================================================== Chart room (UI redesign) ==
VIEWS5 = (("map", "#map-count"), ("profile", "svg.panel"), ("series", "svg.chart path.line"), ("sst?key=sst_monthly_mean__sst_synthetic_0p25deg_2025q1", "svg.chart rect.cell"), ("explore", ".panel-filters"))
SST_KEY = "sst_monthly_mean__sst_synthetic_0p25deg_2025q1"


def set_theme(p, want):
    for _ in range(4):
        if p.get_attribute("#theme", "data-theme") == want:
            return
        p.click("#theme")
    raise AssertionError(f"could not reach the theme {want}")


def is_dark(p):
    return p.evaluate("document.documentElement.classList.contains('dark')")


def stub_clipboard(p, ok=True):
    p.evaluate("""(ok) => { window.__copied = null;
        Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText: (t) => ok ? (window.__copied = t, Promise.resolve()) : Promise.reject(new Error('denied'))}}); }""", ok)


# ---- 3. layout: sidebar and top bar ----
def test_the_sidebar_has_five_icon_links_with_one_current_and_the_top_bar_has_user_role_theme_and_sign_out(pg):
    pg.login()
    p = pg.page
    p.wait_for_selector("#map-count")
    links = p.locator("nav#nav[aria-label=Main] a[data-view]")
    assert links.count() == 5
    assert p.eval_on_selector_all("#nav a[data-view]", "a => a.map(x => [x.querySelectorAll('svg').length, x.textContent.trim()])") == [[1, "Map"], [1, "Profile"], [1, "Series"], [1, "SST"], [1, "Explore"]]
    assert p.locator("#nav a[aria-current=page]").count() == 1 and p.get_attribute("#nav a[aria-current=page]", "data-view") == "map"
    assert p.inner_text(".topbar .who") == "viewer · viewer" or p.inner_text(".topbar .who").startswith("viewer")
    assert p.locator(".topbar #logout").inner_text() == "Sign out" and p.locator(".topbar #theme").count() == 1
    assert p.locator("body.anon").count() == 0


def test_the_sidebar_is_not_shown_to_someone_who_is_not_signed_in(pg):
    p = pg.page
    p.goto(BASE + "/app")
    p.wait_for_selector("#username")
    assert p.locator("body.anon").count() == 1 and not p.is_visible("#nav")
    assert p.is_visible("#theme")                                                   # the theme choice is available on the login page too


# ---- 4. provenance strip ----
def test_every_figure_has_a_provenance_strip_with_versions_a_short_checksum_and_flag_chips(pg):
    pg.login()
    for view, wait in VIEWS5[1:4]:
        p = pg.go(view, wait).page
        strip = p.locator(".provenance").first
        strip.wait_for()
        assert p.locator(".provenance").count() == 1
        for field in ("data_version", "product_version", "qc_config_sha256", "flags_used"):
            assert strip.locator(f"[data-field={field}]").count() == 1, (view, field)
        assert strip.locator("[data-field=data_version]").inner_text() not in ("", "unknown")
        short = strip.locator("[data-field=qc_config_sha256]")
        full = short.get_attribute("title")
        assert re.fullmatch(r"[0-9a-f]{64}", full) and short.inner_text() == full[:12] + "…"     # 12 characters shown, the whole value in the title
        assert strip.locator("[data-field=flags_used] .chip").count() >= 1


def test_the_copy_buttons_copy_the_checksum_and_a_one_line_caption_and_say_so(pg):
    pg.login()
    p = pick_profile(pg)
    p.wait_for_selector(".provenance")
    stub_clipboard(p)
    full = p.get_attribute(".provenance [data-field=qc_config_sha256]", "title")
    p.click(".provenance .copy-sha")
    until(p, "document.querySelector('.prov-copied')?.textContent === 'Copied'")
    assert p.evaluate("window.__copied") == full and p.get_attribute(".prov-copied", "role") == "status"
    p.click(".provenance .copy-caption")
    caption = p.evaluate("window.__copied")
    assert "\n" not in caption and "stale_target" in caption and full in caption and "flags used:" in caption
    assert p.inner_text(".provenance [data-field=data_version]") in caption
    assert ("synthetic" in caption) == (p.inner_text("#synthetic").strip() != "")      # the synthetic notice is in the caption exactly when it is in the footer


def test_when_the_clipboard_is_refused_the_text_is_shown_selected_in_a_visible_field(pg):
    pg.login()
    p = pick_profile(pg)
    p.wait_for_selector(".provenance")
    stub_clipboard(p, ok=False)
    p.click(".provenance .copy-caption")
    p.wait_for_selector(".provenance input.copyline")
    assert p.is_visible(".copyline")
    sel = p.evaluate("(() => { const e = document.querySelector('.copyline'); return [document.activeElement === e, e.selectionStart, e.selectionEnd, e.value.length]; })()")
    assert sel[0] and sel[1] == 0 and sel[2] == sel[3] > 40                           # focused, and the whole text is selected


# ---- 5. flag chips ----
def test_flag_chips_carry_the_marker_shape_and_the_words_in_the_legend_the_table_and_the_strip(pg):
    pg.login()
    p = pick_profile(pg)
    p.wait_for_selector("svg.panel .mk")
    p.select_option("#quality", "all")
    until(p, "document.querySelectorAll('.tableblock tbody tr').length > 0 && document.querySelector('.tableblock td .chip[data-flag=\"3\"]')")
    shape = "c => { const e = c.querySelector('svg > *'); return e.tagName + ':' + (e.getAttribute('class') || '') + ':' + ((e.getAttribute('points') || '').split(' ').length || 0); }"
    legend = {c.get_attribute("data-flag"): c for c in p.locator(".legend .chip").all()}
    assert sorted(legend) == ["0", "1", "2", "3", "4", "9"]
    assert [legend[k].inner_text().strip() for k in sorted(legend)] == ["0 not checked", "1 good", "2 probably good", "3 probably bad", "4 bad", "9 missing"]
    tags = {k: legend[k].evaluate(shape) for k in legend}
    assert tags["0"].startswith("rect") and tags["1"].startswith("circle") and tags["3"].endswith(":3") and tags["2"].endswith(":4") and tags["4"].startswith("path") and tags["9"].startswith("circle")
    cell = p.locator(".tableblock td .chip[data-flag='3']").first
    assert cell.inner_text().strip() == "3 probably bad" and cell.evaluate(shape) == tags["3"]       # the table chip has the legend chip's shape
    marker = p.locator("svg.panel polygon.mk.flag3").first
    assert len(marker.get_attribute("points").split(" ")) == 3                                       # and the chart marker is the same triangle
    assert p.locator(".provenance [data-field=flags_used] .chip").count() >= 1


def test_an_unknown_flag_is_named_as_unknown_never_shown_as_good(pg):
    pg.login()
    p = pg.page

    def tamper(route, request):
        r = route.fetch()
        body = r.json()
        for row in body.get("rows", []):
            if "temperature_qc" in row:
                row["temperature_qc"] = 7
        route.fulfill(response=r, json=body)

    p.route("**/datasets/stale_target/subset*", tamper)
    p.goto(BASE + "/app#/profile")
    p.select_option("#dataset", "stale_target")
    p.click("button[type=submit]")
    p.wait_for_selector("svg.panel .mk")
    p.select_option("#quality", "all")
    until(p, "document.querySelector('.tableblock td .chip[data-flag=\"7\"]')")
    assert p.locator(".tableblock td .chip[data-flag='7']").first.inner_text().strip() == "unknown flag 7"
    p.unroute("**/datasets/stale_target/subset*")


# ---- 6. map ----
def test_the_map_has_an_ocean_a_degree_graticule_and_a_legend_by_type(pg):
    pg.login()
    p = pg.page
    p.wait_for_selector("#map-count")
    assert p.get_attribute("svg.chart rect.mapbg", "fill") == "url(#ocean)" and p.locator("#ocean").count() == 1
    labels = p.eval_on_selector_all("svg.chart .glabel", "t => t.map(x => x.textContent)")
    assert labels and all(re.fullmatch(r"(0|[0-9]+)°[EWNS]?", t) for t in labels)
    nums = {int(re.match(r"\d+", t).group(0)) for t in labels}
    assert all(n % 30 == 0 for n in nums)                                              # every 30 degrees
    kinds = p.locator(".legend .chip").all_inner_texts()
    assert [k.strip() for k in kinds] == ["profile (solid)", "time series (hatched)", "grid (dotted)"]
    assert p.locator("pattern#fphatch").count() == 1 and p.locator("pattern#fpdots").count() == 1
    fill = lambda sel: p.eval_on_selector(sel, "e => getComputedStyle(e).fill")           # noqa: E731
    assert "fphatch" in fill("svg.swatch.type-timeseries rect") and "fpdots" in fill("svg.swatch.type-grid rect")      # the legend uses the same patterns as the footprints
    assert "fpdots" in fill("g.fp.type-grid rect.fprect")


def test_a_footprint_card_opens_on_hover_and_on_focus_enter_pins_it_and_escape_closes_it(pg):
    pg.login()
    p = pg.page
    p.wait_for_selector("#map-count")
    assert p.locator(".mapwrap .card").is_hidden()
    p.locator("g.fp[aria-label^='antimeridian:']").first.hover(force=True)               # mouse
    p.wait_for_selector(".mapwrap .card:not([hidden])")
    card = p.inner_text(".mapwrap .card")
    assert "antimeridian" in card and "Explore" in card and "Platform" in card and "Time" in card and "Variables" in card
    assert p.get_attribute(".card a:has-text('Explore')", "href") == "#/explore?type=dataset&key=antimeridian"
    p.mouse.move(2, 2)
    until(p, "document.querySelector('.mapwrap .card').hidden")                           # leaving closes a card that was not pinned
    p.locator("g.fp[aria-label^='argo_synthetic_arabian_sea_2025q1:']").first.focus()      # keyboard
    p.wait_for_selector(".mapwrap .card:not([hidden])")
    assert "Open profile" in p.inner_text(".card") and p.get_attribute(".card a:has-text('Open profile')", "href").startswith("#/profile?key=argo_synthetic")
    p.keyboard.press("Enter")
    assert p.locator("g.fp.pinned").count() == 1 and p.evaluate("!!document.activeElement.closest('.card')")      # pinned, and focus moved into the card
    p.keyboard.press("Escape")
    assert p.locator(".mapwrap .card").is_hidden() and p.locator("g.fp.pinned").count() == 0
    assert p.evaluate("document.activeElement.getAttribute('aria-label')").startswith("argo_synthetic_arabian_sea_2025q1:")   # focus is back on the footprint


def test_a_hostile_title_in_the_footprint_card_is_text(pg):
    pg.login()
    p = pg.page
    p.wait_for_selector("#map-count")
    p.locator("g.fp[aria-label^='hostile:']").first.focus()
    p.wait_for_selector(".mapwrap .card:not([hidden])")
    assert p.inner_text(".card h3") == HOSTILE_TITLE and p.locator(".card img").count() == 0
    assert p.evaluate("window.__pwned") is None


# ---- 7. profile ----
def test_temperature_and_salinity_panels_are_side_by_side_and_stack_on_a_narrow_screen(pg):
    pg.login()
    p = pick_profile(pg, "argo_synthetic_arabian_sea_2025q1")
    p.wait_for_selector("svg.panel")
    assert p.locator("svg.panel").count() == 2
    a, b = (p.locator("svg.panel").nth(i).bounding_box() for i in (0, 1))
    assert abs(a["y"] - b["y"]) < 2 and b["x"] > a["x"] + a["width"] - 1                 # side by side
    p.set_viewport_size({"width": 600, "height": 900})
    p.wait_for_timeout(200)
    a, b = (p.locator("svg.panel").nth(i).bounding_box() for i in (0, 1))
    assert b["y"] > a["y"] + a["height"] - 1 and abs(a["x"] - b["x"]) < 2                # stacked below 700 px


def test_the_profile_crosshair_readout_and_table_row_follow_the_pointer_and_the_arrow_keys(pg):
    pg.login()
    p = pick_profile(pg)
    p.wait_for_selector("svg.panel .mk")
    p.click("main .tableblock button")
    assert p.locator("svg.panel line.crosshair:not([hidden])").count() == 0 and p.locator("tr.hl").count() == 0
    mk = p.locator("svg.panel").first.locator(".mk").nth(1).bounding_box()
    box = p.locator("svg.panel").first.locator("rect.hit").bounding_box()
    p.mouse.move(box["x"] + box["width"] / 2, mk["y"] + mk["height"] / 2)
    until(p, "document.querySelectorAll('svg.panel line.crosshair:not([hidden])').length >= 2")
    text = p.inner_text("#readout")
    assert re.match(r"temperature [\d.]+ .* at [\d.]+ m, flag \d [a-z ]+", text) and p.locator("#readout .chip").count() == 1
    assert p.locator("tr.hl").count() == 1
    depth = p.inner_text("tr.hl td:first-child")
    assert f"at {depth} m" in text                                                       # the highlighted row is the point read out
    first = p.inner_text("#readout")
    p.locator("svg.panel").first.focus()                                                 # keyboard
    p.keyboard.press("ArrowDown")
    until(p, f"document.getElementById('readout').textContent !== {first!r}")
    assert p.locator("tr.hl").count() == 1
    p.keyboard.press("Escape")
    until(p, "document.querySelectorAll('tr.hl').length === 0")
    assert p.locator("svg.panel line.crosshair:not([hidden])").count() == 0


# ---- 8. series ----
BUOY = "series_hourly__buoy_met_wave_synthetic_2025q1"


def test_dragging_a_brush_on_the_series_sets_from_and_to_and_asks_again(pg):
    pg.login()
    p = pick_series(pg, BUOY, "air_pressure")
    p.wait_for_selector("svg.chart rect.brushhit")
    f0, t0 = p.input_value("#time_from"), p.input_value("#time_to")
    n0 = len(_count_subset_requests(pg, BUOY))
    box = p.locator("svg.chart rect.brushhit").bounding_box()
    y = box["y"] + box["height"] / 2
    p.mouse.move(box["x"] + box["width"] * 0.25, y)
    p.mouse.down()
    p.mouse.move(box["x"] + box["width"] * 0.5, y, steps=5)
    assert p.locator("svg.chart rect.brush:not([hidden])").count() == 1                  # the brush is drawn while dragging
    p.mouse.up()
    until(p, f"document.getElementById('time_from').value !== {f0!r}")
    f1, t1 = p.input_value("#time_from"), p.input_value("#time_to")
    assert f0 < f1 < t1 <= t0 and t1 < t0                                                # a narrower range inside the old one
    until(p, "document.querySelector('svg.chart path.line') && !document.querySelector('.skeleton')")
    assert len(_count_subset_requests(pg, BUOY)) > n0                                    # the same API call, made again
    until(p, f"document.querySelector('#range_from') && document.querySelector('output[for=range_from]').textContent >= {f1!r}")


def test_two_range_inputs_are_the_keyboard_route_to_the_same_zoom(pg):
    pg.login()
    p = pick_series(pg, BUOY, "air_pressure")
    p.wait_for_selector("#range_from")
    f0 = p.input_value("#time_from")
    n0 = len(_count_subset_requests(pg, BUOY))
    p.focus("#range_from")
    for _ in range(3):
        p.keyboard.press("ArrowRight")
    shown = p.inner_text("output[for=range_from]")
    assert p.input_value("#time_from") == shown and shown > f0                           # From follows the slider
    assert len(_count_subset_requests(pg, BUOY)) == n0                                   # nothing is asked until the range is applied
    p.focus("#range_to")
    p.keyboard.press("ArrowLeft")
    assert p.input_value("#time_to") == p.inner_text("output[for=range_to]")
    p.focus("#range_apply")
    p.keyboard.press("Enter")
    until(p, f"document.getElementById('time_from').value === {shown!r} && document.querySelector('svg.chart path.line')")
    assert len(_count_subset_requests(pg, BUOY)) > n0
    until(p, "document.activeElement.id === 'range_apply'")                              # focus is not lost when the chart is drawn again


def test_a_bin_with_no_good_values_is_a_hatched_strip_labelled_in_words(pg):
    pg.login()
    p = pick_series(pg, "series_hourly__hostile")
    gap = p.locator("svg.chart rect.gap")
    assert gap.count() == 1                                                              # hour 2 only
    assert p.locator("pattern#gaphatch").count() == 1 and "gaphatch" in gap.evaluate("e => getComputedStyle(e).fill")
    assert gap.locator("title").text_content() == "no good values"
    assert p.locator(".hint", has_text="hatched strip marked").count() == 1
    assert p.locator("svg.chart path.line").count() == 2                                 # and the line is broken there, not drawn through zero


# ---- 9. SST ----
def test_the_month_slider_and_the_select_agree_and_the_slider_asks_for_that_month(pg):
    pg.login()
    p = pg.go(f"sst?key={SST_KEY}", "#month_slider").page
    p.wait_for_selector("svg.chart rect.cell")
    n = p.locator("#month option").count()
    assert n >= 2 and p.get_attribute("#month_slider", "max") == str(n - 1)
    assert p.locator("label[for=month_slider]").inner_text() == "Month" and p.inner_text("#month_text") == p.input_value("#month")
    first = p.input_value("#month")
    p.focus("#month_slider")
    p.keyboard.press("ArrowRight")
    until(p, f"document.getElementById('month').value !== {first!r} && document.getElementById('month_text').textContent === document.getElementById('month').value")
    until(p, "document.querySelector('svg.chart rect.cell') && !document.querySelector('.skeleton')")
    assert p.locator("svg.chart rect.cell").count() > 0


def test_play_steps_one_month_at_a_time_and_stops_at_the_last_month(pg):
    pg.login()
    p = pg.go(f"sst?key={SST_KEY}", "#play").page
    p.wait_for_selector("svg.chart rect.cell")
    months = p.eval_on_selector_all("#month option", "o => o.map(x => x.value)")
    assert len(months) >= 2 and p.input_value("#month") == months[0]
    p.click("#play")
    assert p.inner_text("#play") == "Pause"
    seen = []
    end = time.monotonic() + 12
    while time.monotonic() < end and p.inner_text("#play") == "Pause":
        v = p.input_value("#month")
        if not seen or seen[-1] != v:
            seen.append(v)
        p.wait_for_timeout(100)
    assert p.inner_text("#play") == "Play"                                               # stopped by itself
    assert seen == months                                                                # it stepped one month at a time, never skipping one, to the last
    until(p, f"document.getElementById('month_text').textContent === {months[-1]!r}")
    p.wait_for_timeout(1300)
    assert p.input_value("#month") == months[-1]                                         # and stays on the last


def test_a_late_answer_for_an_earlier_month_never_overwrites_the_newer_one(pg):
    pg.login()
    p = pg.go(f"sst?key={SST_KEY}", "#month_slider").page
    p.wait_for_selector("svg.chart rect.cell")
    months = p.eval_on_selector_all("#month option", "o => o.map(x => x.value)")
    held = []
    p.route(f"**/products/{SST_KEY}/subset*", lambda route, request: held.append(route) if not held else route.continue_())
    p.focus("#month_slider")
    p.keyboard.press("ArrowRight")                                                       # month 2: held back
    while not held:
        p.wait_for_timeout(50)
    p.keyboard.press("ArrowRight") if len(months) > 2 else p.keyboard.press("ArrowLeft")      # month 3 (or back to 1): answered at once
    want = months[2] if len(months) > 2 else months[0]
    until(p, f"document.getElementById('month_text').textContent === {want!r} && document.querySelector('svg.chart rect.cell')")
    first = lambda: p.locator("svg.chart rect.cell title").first.text_content()               # noqa: E731  (a cell's own value: it differs from month to month)
    now = first()
    held[0].continue_()
    p.wait_for_timeout(1000)
    assert p.input_value("#month") == want and first() == now                                 # the old month's answer did not replace the newer drawing
    p.unroute(f"**/products/{SST_KEY}/subset*")


def test_play_is_disabled_with_a_visible_reason_when_the_system_asks_for_less_motion(browser):
    ctx = browser.new_context(viewport={"width": 1100, "height": 900}, reduced_motion="reduce")
    p = ctx.new_page()
    try:
        p.goto(BASE + "/app")
        p.fill("#username", "viewer")
        p.fill("#password", "viewer123")
        p.click("button[type=submit]")
        p.wait_for_selector("#nav a")
        p.goto(f"{BASE}/app#/sst?key={SST_KEY}")
        p.wait_for_selector("#play")
        assert p.is_disabled("#play") and p.is_visible("#play-note") and "less motion" in p.inner_text("#play-note")
        assert p.locator("#month_slider").is_enabled()                                   # the slider still works
    finally:
        ctx.close()


def test_a_cell_readout_gives_latitude_longitude_value_with_unit_and_observation_count(pg):
    pg.login()
    p = pg.go(f"sst?key={SST_KEY}", "svg.chart rect.cell").page
    p.wait_for_selector("#cell-readout")
    p.locator("svg.chart rect.cell").nth(5).hover(force=True)
    until(p, "document.querySelector('rect.cell.sel')")
    text = p.inner_text("#cell-readout")
    assert re.fullmatch(r"latitude [\d.]+, longitude [\d.]+: [\d.]+ \S+, \d+ observations?", text), text
    i = int(p.get_attribute("rect.cell.sel", "data-i"))
    p.click("main .tableblock button")
    row = table_rows(p, ".tableblock table")[i]
    assert text.startswith(f"latitude {row[0]}, longitude {row[1]}") and f": {row[2]} " in text and text.endswith(f", {row[3]} observations")   # the same numbers as the table
    p.locator("svg.chart").first.focus()                                                 # keyboard
    p.keyboard.press("ArrowRight")
    until(p, f"document.querySelector('rect.cell.sel').getAttribute('data-i') !== '{i}'")
    assert p.locator("rect.cell.sel").count() == 1
    ends = p.eval_on_selector_all("svg.chart.bar text", "t => t.map(x => x.textContent)")
    assert p.locator("svg.chart.bar").count() == 1 and ends[0].startswith("min ") and ends[1].startswith("max ")


# ---- 10. explore ----
def test_explore_has_four_steps_and_download_cards_with_the_file_type_the_rows_and_the_limit(pg):
    pg.login()
    p = explore(pg, "dataset", "argo_synthetic_arabian_sea_2025q1")
    assert p.locator(".steps > li.step").count() == 3
    assert p.locator("text=Step 1: Choose").count() == 1 and p.locator("text=Step 2: Filter").count() == 1 and p.locator("text=Step 3: Preview").count() == 1
    assert p.locator(".dlcard").count() == 0                                             # step 4 appears with the preview
    p.click("button[type=submit]")
    p.wait_for_selector(".dlcard")
    assert p.locator("text=Step 4: Download").count() == 1 and p.locator(".dlcard").count() == 2
    limit = pg.ctx.request.get(f"{BASE}/limits").json()
    max_dl = {x["name"]: x["value"] for x in limit["limits"]}["max_rows_download"]
    selected = int(re.match(r"(\d+) rows selected", p.inner_text("#explore-count")).group(1))
    assert [c.strip() for c in p.locator(".dlcard .ftype").all_inner_texts()] == ["CSV", "NetCDF"]
    assert p.locator(".dlcard .dlrows").all_inner_texts() == [f"{selected} rows selected"] * 2
    assert p.locator(".dlcard .dllimit").all_inner_texts() == [f"Rows limit for a download: {max_dl}"] * 2
    assert "formula" in p.inner_text(".dlcard:has(.ftype:text-is('CSV'))")
    assert p.locator("button.dl[data-format=csv]").count() == 1 and p.locator("button.dl[data-format=netcdf]").count() == 1


def test_the_explore_status_line_says_which_page_of_how_many(pg):
    pg.login()
    p = explore(pg, "dataset", "argo_synthetic_arabian_sea_2025q1", quality="all")
    p.click("button[type=submit]")
    p.wait_for_selector("#explore-status")
    assert p.get_attribute("#explore-status", "role") == "status"
    m = re.fullmatch(r"Page 1 of (\d+) \((\d+) rows selected\)", p.inner_text("#explore-status"))
    assert m and int(m.group(1)) == -(-int(m.group(2)) // 50)


# ---- 11. feedback ----
def test_a_skeleton_stands_in_while_a_request_is_open_and_is_gone_after_the_answer(pg):
    pg.login()
    p = pg.page
    p.wait_for_selector("#map-count")
    held = []
    p.route("**/datasets?*", lambda route, request: held.append(route))
    p.click("button[type=submit]")
    while not held:
        p.wait_for_timeout(50)
    assert p.locator(".result .skeleton").count() >= 1 and p.locator("#map-count").count() == 0 and p.locator(".skeleton-box [role=status]").inner_text() == "Loading"
    held[0].continue_()
    until(p, "document.getElementById('map-count') && !document.querySelector('.skeleton')")
    p.unroute("**/datasets?*")


def test_links_in_the_map_table_are_at_least_24_px_square(pg):
    pg.login()
    p = pg.page
    p.wait_for_selector("table.list td a")
    small = p.eval_on_selector_all("table.list td a", "a => a.map(e => e.getBoundingClientRect()).filter(r => r.width < 24 || r.height < 24).length")
    assert small == 0                                                                           # WCAG 2.2 target size (2.5.8)


def test_an_empty_result_says_so_and_offers_a_next_step(pg):
    pg.login()
    p = pg.page
    p.wait_for_selector("#map-count")
    p.fill("#q", "no-such-dataset-anywhere")
    p.click("button[type=submit]")
    p.wait_for_selector(".empty")
    assert "No datasets match" in p.inner_text(".empty") and "Widen" in p.inner_text(".empty")
    p.click(".empty button")                                                              # the next step works: clear the filters
    until(p, "document.getElementById('map-count')?.textContent === '8 datasets shown'")
    p = explore(pg, "dataset", "hostile", quality="all", time_from="2030-01-01")
    p.click("button[type=submit]")
    p.wait_for_selector(".empty")
    assert "No rows match" in p.inner_text(".empty") and p.locator(".dlcard").count() == 0


# ---- 12. theme ----
def test_the_theme_button_cycles_system_light_dark_and_the_choice_survives_a_reload(pg):
    pg.login()
    p = pg.page
    p.wait_for_selector("#map-count")
    assert p.get_attribute("#theme", "data-theme") == "system" and not is_dark(p)
    p.click("#theme")
    assert p.get_attribute("#theme", "data-theme") == "light" and not is_dark(p)
    p.click("#theme")
    assert p.get_attribute("#theme", "data-theme") == "dark" and is_dark(p)
    assert p.evaluate("localStorage.getItem('scats-theme')") == "dark"
    p.reload()
    p.wait_for_selector("#map-count")
    assert is_dark(p) and p.get_attribute("#theme", "data-theme") == "dark"            # remembered
    assert p.evaluate("getComputedStyle(document.body).backgroundColor") != "rgb(247, 244, 238)"
    p.click("#theme")
    assert p.get_attribute("#theme", "data-theme") == "system" and p.evaluate("localStorage.getItem('scats-theme')") == "system"
    assert p.evaluate("document.documentElement.getAttribute('style')") is None        # set with a class, never an inline style


def test_system_follows_the_operating_system_and_an_explicit_choice_does_not(pg):
    pg.login()
    p = pg.page
    p.wait_for_selector("#map-count")
    p.emulate_media(color_scheme="dark")
    until(p, "document.documentElement.classList.contains('dark')")
    p.emulate_media(color_scheme="light")
    until(p, "!document.documentElement.classList.contains('dark')")
    p.click("#theme")                                                                      # light
    p.emulate_media(color_scheme="dark")
    p.wait_for_timeout(300)
    assert not is_dark(p)


def test_the_page_works_when_local_storage_throws(browser):
    ctx = browser.new_context(viewport={"width": 1100, "height": 900})
    ctx.add_init_script("""const boom = () => { throw new DOMException('blocked', 'SecurityError'); };
        Storage.prototype.getItem = boom; Storage.prototype.setItem = boom;""")
    p = ctx.new_page()
    errors = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    try:
        p.goto(BASE + "/app")
        p.fill("#username", "viewer")
        p.fill("#password", "viewer123")
        p.click("button[type=submit]")
        p.wait_for_selector("#map-count")
        p.click("#theme")
        p.click("#theme")
        assert is_dark(p) and p.get_attribute("#theme", "data-theme") == "dark"          # the choice works for this visit
        assert errors == []
    finally:
        ctx.close()


# ---- 13. responsive, no errors, axe ----
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_no_view_scrolls_sideways_at_360_px_and_the_bottom_bar_is_reachable(browser, stack, theme):
    pg = Pg(browser)
    try:
        pg.page.set_viewport_size({"width": 360, "height": 740})
        pg.login()
        p = pg.page
        set_theme(p, theme)
        for view, wait in VIEWS5:
            pg.go(view, wait)
            until(p, "!document.querySelector('.skeleton')")
            over = p.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
            assert over[0] <= over[1], (view, theme, over)
            nav = p.locator("#nav").bounding_box()
            assert nav["y"] + nav["height"] >= 739 and nav["width"] >= 359             # the sidebar is a bottom bar on a narrow screen
            assert p.locator("#nav a").first.locator(".navlabel").is_visible()
    finally:
        pg.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_no_view_raises_a_page_error_or_a_csp_violation_in_either_theme(browser, stack, theme):
    pg = Pg(browser)
    try:
        pg.login()
        set_theme(pg.page, theme)
        for view, wait in VIEWS5:
            pg.go(view, wait)
            pg.page.wait_for_timeout(200)
        pg.page.hover("#nav a[data-view=map]")
        bad = [(k, t) for k, t in pg.console if k == "pageerror" or "Content Security Policy" in t or "Refused to" in t]
        assert bad == []
        assert pg.page.evaluate("document.querySelectorAll('[style]').length") == 0       # nothing carries an inline style
    finally:
        pg.close()


@pytest.mark.skipif(not AXE.is_file(), reason="axe-core is not installed: run `npm install --prefix tests/tools`")
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width", [360, 1280])
def test_axe_finds_no_a_or_aa_violation_in_any_view_in_either_theme_at_360_and_1280_px(browser, stack, theme, width):
    pg = Pg(browser)
    try:
        p = pg.page
        p.set_viewport_size({"width": width, "height": 900})
        pg.login()
        set_theme(p, theme)
        src = AXE.read_text()
        tags = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
        found = {}
        for view, wait in VIEWS5:
            pg.go(view, wait)
            until(p, "!document.querySelector('.skeleton')")
            p.evaluate("document.querySelector('details.panel-filters')?.setAttribute('open', '')")      # closed on a phone: open it so its fields are checked too
            if view == "explore":
                until(p, "document.querySelectorAll('#key option').length > 0")
            p.mouse.move(0, 0)
            p.evaluate(src)
            res = p.evaluate("axe.run(document, {runOnly: {type: 'tag', values: %s}}).then(r => ({passes: r.passes.length, v: r.violations.map(x => ({id: x.id, impact: x.impact, nodes: x.nodes.slice(0, 3).map(n => n.target.join(' '))}))}))" % tags)
            assert res["passes"] > 10, (view, res)
            if res["v"]:
                found[view] = res["v"]
        assert found == {}, (theme, width, found)
    finally:
        pg.close()

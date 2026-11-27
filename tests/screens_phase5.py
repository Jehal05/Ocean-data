"""Regenerate docs/screens/*.png: every view, light and dark, at 1280 px and 360 px, from the synthetic fixture data.
Starts the same stack as the browser tests (tests/dash_stack.py). SKIPPED (exit 2) when Playwright, Chrome or PostgreSQL 16 is missing."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import db_stub  # noqa: E402
from review_helpers import sql  # noqa: E402

PORT = 18092
BASE = f"http://127.0.0.1:{PORT}"
OUT = ROOT / "docs/screens"
CHROME = os.getenv("SCATS_CHROME") or shutil.which("google-chrome") or shutil.which("chromium")
SST = "sst_monthly_mean__sst_synthetic_0p25deg_2025q1"


def prepare(p, view):
    """Put each view in a state that shows what it is for."""
    p.goto(f"{BASE}/app#/{view}" + (f"?key={SST}" if view == "sst" else ""))
    wait = {"map": "#map-count", "profile": "svg.panel", "series": "#product", "sst": "svg.chart rect.cell", "explore": "#key"}[view]
    p.wait_for_selector(wait, state="attached")
    p.evaluate("document.querySelector('details.panel-filters')?.setAttribute('open', '')")       # a phone starts with it closed; the script needs the fields
    if view == "map":
        p.locator("g.fp[aria-label^='argo_synthetic']").first.focus()
        p.wait_for_selector(".mapwrap .card:not([hidden])")
    if view == "profile":
        p.select_option("#quality", "all")
        p.wait_for_timeout(800)
    if view == "series":
        p.select_option("#product", "series_hourly__buoy_met_wave_synthetic_2025q1")
        p.select_option("#variable", "air_pressure")
        p.click("button[type=submit]")
        p.wait_for_selector("svg.chart path.line")
    if view == "sst":
        p.locator("svg.chart rect.cell").nth(40).hover(force=True)
    if view == "explore":
        p.select_option("#key", "argo_synthetic_arabian_sea_2025q1")
        p.click("button[type=submit]")
        p.wait_for_selector(".dlcard")
    if p.viewport_size["width"] < 760:
        p.evaluate("document.querySelector('details.panel-filters')?.removeAttribute('open')")      # and the picture shows what a phone user sees first
    p.evaluate("window.scrollTo(0, 0)")
    p.wait_for_timeout(400)


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP: Playwright is not installed")
        return 2
    if not (CHROME and db_stub.available()):
        print("SKIP: no Chrome or no PostgreSQL 16 binaries")
        return 2
    out = ROOT / "reports/screens_stack.out"
    out.parent.mkdir(exist_ok=True)
    fh = open(out, "w")
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests/dash_stack.py"), "--port", str(PORT)], stdout=fh, stderr=subprocess.STDOUT, cwd=ROOT)
    try:
        for _ in range(240):
            if "READY" in out.read_text() or proc.poll() is not None:
                break
            time.sleep(1.5)
        if "READY" not in out.read_text():
            print("the stack did not start:", out.read_text()[-800:])
            return 1
        sql("UPDATE api_limit SET value = 6000 WHERE name = 'rate_per_minute'", fetch=False)       # twenty pictures in a minute would meet the default rate limit
        OUT.mkdir(parents=True, exist_ok=True)
        for old in OUT.glob("*.png"):
            old.unlink()
        with sync_playwright() as pw:
            b = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
            for width, height in ((1280, 860), (360, 740)):
                for theme in ("light", "dark"):
                    ctx = b.new_context(viewport={"width": width, "height": height}, color_scheme=theme, reduced_motion="reduce")
                    p = ctx.new_page()
                    p.goto(BASE + "/app")
                    p.fill("#username", "viewer")
                    p.fill("#password", "viewer123")
                    p.click("button[type=submit]")
                    p.wait_for_selector("#nav a")
                    if p.get_attribute("#theme", "data-theme") != "system":
                        raise SystemExit("the theme should start as 'system'")
                    for view in ("map", "profile", "series", "sst", "explore"):
                        prepare(p, view)
                        name = f"{view}-{theme}-{width}.png"
                        p.screenshot(path=str(OUT / name), full_page=(width == 360))
                        print("wrote", name, flush=True)
                    ctx.close()
            b.close()
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        fh.close()


if __name__ == "__main__":
    sys.exit(main())

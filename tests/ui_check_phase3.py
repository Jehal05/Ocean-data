"""Drive the real web UI in headless Chrome: login -> start review -> change a flag -> approve (publishes) -> audit page.
Runs the review API in-process against the stand-in PostgreSQL and moto. Prints what was seen. Exit 0 ok, 1 failed, 2 skipped."""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in ("libs", "components/02_parser_api", "components/03_qc_framework", "components/04_expert_review_ui", "components/05_data_store", "tests"):
    sys.path.insert(0, str(ROOT / p))
import db_stub  # noqa: E402

CHROME = "/usr/bin/google-chrome"


def main():
    if not db_stub.available() or not os.path.exists(CHROME):
        print("SKIP: no PostgreSQL stand-in or no Chrome")
        return 2
    import uvicorn
    from moto import mock_aws
    from playwright.sync_api import sync_playwright
    import review_helpers as h
    db_stub.start(fresh=True)
    seen = []
    try:
        db_stub.reset_data()
        stg = Path(tempfile.mkdtemp())
        mp = type("MP", (), {"setenv": staticmethod(lambda k, v: os.environ.__setitem__(k, v))})
        h.set_env(mp, stg)
        with mock_aws():
            h.make_bucket()
            h.ingest(h.BUOY, stg)
            from review_api.main import app
            srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=58002, log_level="warning"))
            threading.Thread(target=srv.run, daemon=True).start()
            time.sleep(2)
            base = "http://127.0.0.1:58002/"
            with sync_playwright() as pw:
                b = pw.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
                pg = b.new_page()
                errs = []
                pg.on("pageerror", lambda e: errs.append(str(e)))
                pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
                pg.goto(base)
                pg.get_by_label("Username").fill("reviewer")
                pg.get_by_label("Password").fill("reviewer123")
                pg.get_by_role("button", name="Log in").click()
                pg.wait_for_selector("text=" + h.BUOY_KEY)
                seen.append("login ok; dataset list shows " + h.BUOY_KEY)
                pg.goto(base + f"#/d/{h.BUOY_KEY}")
                pg.get_by_role("button", name="Start review").click()
                pg.wait_for_selector("text=Record decision")
                seen.append("review started; decision form visible")
                pg.goto(base + f"#/d/{h.BUOY_KEY}/flags?flag=4")
                pg.wait_for_selector("input[type=checkbox]")
                pg.locator("input[type=checkbox]").first.check()
                pg.locator("textarea").fill("checked against the raw file <b>x</b>")
                pg.get_by_role("button", name="Change flag").click()
                time.sleep(1.5)
                seen.append("flag change submitted; page text: " + pg.inner_text("main")[:160].replace("\n", " | "))
                pg.goto(base + f"#/d/{h.BUOY_KEY}")
                pg.wait_for_selector("text=Record decision")
                pg.get_by_label("Comment (required)").fill("approved after review")
                pg.get_by_label("I accept", exact=False).check()
                pg.get_by_role("button", name="Record decision").click()
                time.sleep(4)
                st = h.sql("SELECT status FROM dataset WHERE dataset_key=%s", (h.BUOY_KEY,))[0][0]
                seen.append(f"after approving in the UI the dataset status is {st}")
                pg.goto(base + f"#/d/{h.BUOY_KEY}/audit")
                time.sleep(1.5)
                txt = pg.inner_text("main")
                seen.append("audit page shows: " + ", ".join(e for e in ("review.start", "flag.change", "review.decision", "dataset.publish") if e in txt))
                seen.append("literal '<b>x</b>' shown as text, no bold element injected: " + str("<b>x</b>" in txt and pg.locator("main b").count() == 0))
                seen.append(f"browser console/page errors: {errs or 'none'}")
                b.close()
    finally:
        db_stub.stop()
    print("\n".join(seen))
    return 0 if ("published" in " ".join(seen) or "approved" in " ".join(seen)) else 1


if __name__ == "__main__":
    sys.exit(main())

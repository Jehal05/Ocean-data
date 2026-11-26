"""Static rules for the dashboard files: nothing from outside, nothing that turns text into markup or code."""
from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "components/08_visualization_dashboard/dashboard/static"
SERVED = ("index.html", "app.js", "api.js", "logic.js", "svg.js", "views.js", "style.css")


def text(name):
    return (STATIC / name).read_text()


def test_every_served_file_exists_and_nothing_else_is_in_the_directory_but_package_json():
    present = {p.name for p in STATIC.iterdir()}
    assert present == set(SERVED) | {"package.json"}, present      # a vendored library would show up here, and there must be none


def test_no_file_names_a_host_so_nothing_can_load_from_a_cdn():
    for name in SERVED:
        urls = re.findall(r"""(?:https?:)?//[A-Za-z0-9.-]+\.[A-Za-z]{2,}""", text(name))
        urls = [u for u in urls if u != "http://www.w3.org"]            # the SVG namespace is a name, never fetched
        assert urls == [], (name, urls)


def test_no_inner_html_outer_html_insert_adjacent_html_document_write_or_eval():
    bad = re.compile(r"\b(innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\s*\(|new Function|setTimeout\s*\(\s*['\"`]|srcdoc)")
    for name in ("app.js", "api.js", "logic.js", "svg.js", "views.js"):
        assert bad.search(text(name)) is None, (name, bad.search(text(name)))


def test_the_page_has_no_inline_script_style_or_event_handler_attribute():
    html = text("index.html")
    assert re.search(r"<script(?![^>]*\bsrc=)", html) is None
    assert "<style" not in html
    assert re.search(r"\son[a-z]+\s*=", html) is None and "style=" not in html
    assert re.findall(r'<script[^>]*src="([^"]+)"', html) == ["/app/app.js"]


def test_the_scripts_set_no_style_attribute_as_text():
    for name in ("app.js", "views.js", "svg.js"):
        assert not re.search(r"setAttribute\(\s*['\"]style['\"]", text(name)), name
        assert ".style." not in text(name) and ".style =" not in text(name), name


def test_state_changing_calls_send_the_csrf_header():
    api = text("api.js")
    assert '"X-Requested-With": "scats-ui"' in api
    assert "credentials" in api

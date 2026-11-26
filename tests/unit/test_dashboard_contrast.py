"""WCAG 2.x contrast of the dashboard's colours, computed from style.css itself (nothing hard-coded here but the pairs to check).
Normal text needs 4.5:1; chart marks, lines and focus outlines need 3:1 (WCAG 1.4.11, 2.4.11). A pair is (what is drawn, what it is drawn on)."""
from __future__ import annotations

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[2] / "components/08_visualization_dashboard/dashboard/static/style.css").read_text()
RULES = {}
for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)):
    for one in sel.split(","):
        decls = RULES.setdefault(one.strip(), {})
        for d in body.split(";"):
            if ":" in d:
                k, v = d.split(":", 1)
                decls[k.strip()] = v.strip()
ROOT_VARS = {k: v for k, v in RULES[":root"].items() if k.startswith("--")}


def value(selector, prop):
    v = RULES[selector][prop]
    m = re.fullmatch(r"var\((--[\w-]+)\)", v)
    return ROOT_VARS[m.group(1)] if m else v


def rgba(text):
    text = text.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{3})", text)
    if m:
        text = "#" + "".join(c * 2 for c in m.group(1))
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", text)
    if m:
        h = m.group(1)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (1.0,)
    m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?\s*\)", text)
    assert m, f"cannot read the colour {text!r}"
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), float(m.group(4) or 1)


def over(fg, bg, alpha=None):
    r, g, b, a = rgba(fg)
    a = a if alpha is None else alpha
    br, bgc, bb, _ = rgba(bg)
    return tuple(round(a * c + (1 - a) * x) for c, x in zip((r, g, b), (br, bgc, bb), strict=True))


def luminance(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


PAGE = value("body", "background")                                    # the colour behind everything that has none of its own
WHITE = value("button", "background")
HEADER = value("header", "background")
NOTE = value(".warning", "background")

TEXT = [   # (name, text colour, background)
    ("body text", value("body", "color"), PAGE),
    ("footer text", value("footer", "color"), PAGE),
    ("synthetic notice in the footer", value("#synthetic", "color"), PAGE),
    ("navigation links on the header", value("nav a", "color"), HEADER),
    ("buttons", value("button", "color"), WHITE),
    ("buttons on hover", value("button", "color"), value("button:hover", "background")),
    ("form fields", value(".field input", "color"), value(".field input", "background")),
    ("error text", value(".error", "color"), PAGE),
    ("warning text", value(".warning", "color"), value(".warning", "background")),
    ("notice text", value(".notice", "color"), value(".notice", "background")),
    ("hint and count text", value(".hint", "color"), PAGE),
    ("axis tick labels", value(".tick", "fill"), PAGE),
    ("axis titles", value(".label", "fill"), PAGE),
]


def marks():
    band_fill = over(value(".band", "fill"), PAGE, alpha=float(RULES[".band"]["fill-opacity"]))
    return [   # (name, mark colour, what it is drawn on)
        ("mean line on the page", value(".line", "stroke"), PAGE),
        ("mean line inside the band", value(".line", "stroke"), "#%02x%02x%02x" % band_fill),
        ("band outline (its minimum and maximum edges)", value(".band", "stroke"), PAGE),
        ("count bars", value(".bar", "fill"), PAGE),
        ("good marker", value(".mk", "fill"), PAGE),
        ("probably-good marker (flag 2)", value(".mk.flag2", "fill"), PAGE),
        ("probably-bad marker (flag 3)", value(".mk.flag3", "fill"), PAGE),
        ("bad marker outline (flag 4)", value(".mk.flag4", "stroke"), PAGE),
        ("ring and cross markers", value(".mk.ring", "stroke"), PAGE),
        ("footprint outline", value(".fprect", "stroke"), PAGE),
        ("map box outline", value(".bbox", "stroke"), PAGE),
        ("empty-cell outline", value(".cell.empty", "stroke"), PAGE),
        ("plot frame", value(".plot", "stroke"), PAGE),
        ("keyboard focus outline", re.search(r"#[0-9a-fA-F]{6}", RULES["a:focus-visible"]["outline"]).group(0), PAGE),
    ]


def test_the_contrast_formula_is_the_wcag_one():
    assert round(ratio((0, 0, 0), (255, 255, 255)), 2) == 21.0
    assert round(ratio((119, 119, 119), (255, 255, 255)), 2) == 4.48          # #777 on white: the well-known 4.48:1, just under AA
    assert ratio((10, 20, 30), (10, 20, 30)) == 1.0


def test_normal_text_has_at_least_4_5_to_1():
    bad = {n: round(ratio(rgba(fg), rgba(bg)), 2) for n, fg, bg in TEXT if ratio(rgba(fg), rgba(bg)) < 4.5}
    assert bad == {}, bad


def test_chart_marks_and_the_focus_outline_have_at_least_3_to_1():
    bad = {n: round(ratio(rgba(c), rgba(bg)), 2) for n, c, bg in marks() if ratio(rgba(c), rgba(bg)) < 3.0}
    assert bad == {}, bad


def test_the_check_can_fail_the_colours_it_replaced_were_below_the_limit():
    assert ratio(rgba("#e08a00"), rgba(PAGE)) < 3.0                         # the focus outline before the fix: 2.62:1
    old_band = over(value(".band", "fill"), PAGE, alpha=float(RULES[".band"]["fill-opacity"]))
    assert ratio(old_band, rgba(PAGE)) < 3.0                                # the band with no outline before the fix: 1.38:1
    assert value(".band", "stroke") != "none"

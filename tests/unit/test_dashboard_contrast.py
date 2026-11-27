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
THEMES = ("light", "dark")
ROOT_VARS = {k: v for k, v in RULES[":root"].items() if k.startswith("--")}
VARS = {"light": ROOT_VARS, "dark": {**ROOT_VARS, **{k: v for k, v in RULES[":root.dark"].items() if k.startswith("--")}}}


def value(selector, prop, theme="light"):
    v = RULES[selector][prop]
    m = re.fullmatch(r"var\((--[\w-]+)\)", v)
    return VARS[theme][m.group(1)] if m else v


def token(name, theme):
    return VARS[theme][name]


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


def text_pairs(th):
    page, surface, nav = value("body", "background", th), token("--surface", th), value(".topbar", "background", th)
    v = lambda sel, prop: value(sel, prop, th)      # noqa: E731
    ocean = (token("--ocean-1", th), token("--ocean-2", th))
    return [   # (name, text colour, background)
        ("body text", v("body", "color"), page),
        ("footer text", v("footer", "color"), page),
        ("synthetic notice in the footer", v("#synthetic", "color"), page),
        ("brand and tools on the top bar", v(".topbar", "color"), nav),
        ("top bar buttons", v(".topbar button", "color"), nav),
        ("top bar buttons on hover", v(".topbar button", "color"), v(".topbar button:hover", "background")),
        ("sidebar links", v(".sidebar a", "color"), v(".sidebar", "background")),
        ("current sidebar link", v(".sidebar a", "color"), v('.sidebar a[aria-current="page"]', "background")),
        ("sidebar links on hover", v(".sidebar a", "color"), v(".sidebar a:hover", "background")),
        ("links", v("a", "color"), page),
        ("links on a card or panel", v("a", "color"), surface),
        ("buttons", v("button", "color"), v("button", "background")),
        ("buttons on hover", v("button", "color"), v("button:hover", "background")),
        ("primary buttons", v("button[type=\"submit\"]", "color"), v("button[type=\"submit\"]", "background")),
        ("primary buttons on hover", v("button[type=\"submit\"]", "color"), v("button[type=\"submit\"]:hover", "background")),
        ("form fields", v(".field input", "color"), v(".field input", "background")),
        ("field labels on the filter panel", v(".field", "color"), v(".panel-filters", "background")),
        ("check labels on the filter panel", v(".check", "color"), v(".panel-filters", "background")),
        ("error text", v(".error", "color"), page),
        ("warning text", v(".warning", "color"), v(".warning", "background")),
        ("notice text", v(".notice", "color"), v(".notice", "background")),
        ("hint and count text", v(".hint", "color"), page),
        ("status line", v(".status", "color"), page),
        ("provenance labels", v(".prov-label", "color"), page),
        ("flag chips", v(".chip", "color"), v(".chip", "background")),
        ("readout", v(".readout", "color") if "color" in RULES[".readout"] else v("body", "color"), v(".readout", "background")),
        ("card text", v("body", "color"), v(".card", "background")),
        ("table header", v("body", "color"), v("th", "background")),
        ("highlighted table row", v("body", "color"), v("tr.hl td", "background")),
        ("table cells", v("body", "color"), v("table", "background")),
        ("empty state", v("body", "color"), v(".empty", "background")),
        ("axis tick labels", v(".tick", "fill"), page),
        ("axis titles", v(".label", "fill"), page),
        ("graticule labels on the ocean (light end)", v(".glabel", "fill"), ocean[0]),
        ("graticule labels on the ocean (dark end)", v(".glabel", "fill"), ocean[1]),
        ("gap label", v(".gaplabel", "fill"), page),
    ]


def marks(th):
    page = value("body", "background", th)
    ocean = (token("--ocean-1", th), token("--ocean-2", th))
    v = lambda sel, prop: value(sel, prop, th)      # noqa: E731
    band_fill = over(v(".band", "fill"), page, alpha=float(RULES[".band"]["fill-opacity"]))
    focus = token(re.search(r"var\((--[\w-]+)\)", RULES["a:focus-visible"]["outline"]).group(1), th)
    return [   # (name, mark colour, what it is drawn on)
        ("mean line on the page", v(".line", "stroke"), page),
        ("mean line inside the band", v(".line", "stroke"), "#%02x%02x%02x" % band_fill),
        ("band outline (its minimum and maximum edges)", v(".band", "stroke"), page),
        ("count bars", v(".bar", "fill"), page),
        ("good marker", v(".mk", "fill"), page),
        ("probably-good marker (flag 2)", v(".mk.flag2", "fill"), page),
        ("probably-bad marker (flag 3)", v(".mk.flag3", "fill"), page),
        ("bad marker outline (flag 4)", v(".mk.flag4", "stroke"), page),
        ("ring and cross markers", v(".mk.ring", "stroke"), page),
        ("selected marker outline", v(".mk.on", "stroke"), page),
        ("footprint outline on the ocean (light end)", v(".fprect", "stroke"), ocean[0]),
        ("footprint outline on the ocean (dark end)", v(".fprect", "stroke"), ocean[1]),
        ("selected footprint outline", v(".fp:focus-visible .fprect", "stroke"), ocean[1]),
        ("map box outline on the ocean", v(".bbox", "stroke"), ocean[1]),
        ("empty-cell outline", v(".cell.empty", "stroke"), page),
        ("selected cell outline", v(".cell.sel", "stroke"), page),
        ("plot frame", v(".plot", "stroke"), page),
        ("crosshair", v(".crosshair", "stroke"), page),
        ("brush outline", v(".brush", "stroke"), page),
        ("hatched gap outline", v(".gap", "stroke"), page),
        ("hatch lines", v(".hatch-line", "stroke"), page),
        ("keyboard focus outline", focus, page),
        ("keyboard focus outline on the top bar", focus, v(".topbar", "background")),
        ("keyboard focus outline on the sidebar", focus, v(".sidebar", "background")),
        ("keyboard focus outline on a panel", focus, token("--surface", th)),
    ]


def test_the_contrast_formula_is_the_wcag_one():
    assert round(ratio((0, 0, 0), (255, 255, 255)), 2) == 21.0
    assert round(ratio((119, 119, 119), (255, 255, 255)), 2) == 4.48          # #777 on white: the well-known 4.48:1, just under AA
    assert ratio((10, 20, 30), (10, 20, 30)) == 1.0


import pytest  # noqa: E402


@pytest.mark.parametrize("theme", THEMES)
def test_normal_text_has_at_least_4_5_to_1(theme):
    bad = {n: round(ratio(rgba(fg), rgba(bg)), 2) for n, fg, bg in text_pairs(theme) if ratio(rgba(fg), rgba(bg)) < 4.5}
    assert bad == {}, bad


@pytest.mark.parametrize("theme", THEMES)
def test_chart_marks_and_the_focus_outline_have_at_least_3_to_1(theme):
    bad = {n: round(ratio(rgba(c), rgba(bg)), 2) for n, c, bg in marks(theme) if ratio(rgba(c), rgba(bg)) < 3.0}
    assert bad == {}, bad


def test_the_dark_theme_overrides_every_colour_token_of_the_light_one():
    """A token missing from the dark block would silently keep its light value on a dark page."""
    colour_tokens = {k for k, v in ROOT_VARS.items() if re.search(r"#|rgba?\(", v)}
    assert colour_tokens - set(RULES[":root.dark"]) == set(), colour_tokens - set(RULES[":root.dark"])


def test_the_check_can_fail_the_colours_it_replaced_were_below_the_limit():
    assert ratio(rgba("#e08a00"), rgba(value("body", "background"))) < 3.0                         # the focus outline before the fix: 2.62:1
    old_band = over(value(".band", "fill"), value("body", "background"), alpha=float(RULES[".band"]["fill-opacity"]))
    assert ratio(old_band, rgba(value("body", "background"))) < 3.0                                # the band with no outline before the fix: 1.38:1
    assert value(".band", "stroke") != "none"


LOGIC = (Path(__file__).resolve().parents[2] / "components/08_visualization_dashboard/dashboard/static/logic.js").read_text()
STOPS = [tuple(int(n) for n in m.split(",")) for m in re.findall(r"\[(\d+, \d+, \d+)\]", re.search(r"const STOPS = (.*?);", LOGIC).group(1))]


@pytest.mark.parametrize("theme", THEMES)
def test_every_end_of_the_sst_colour_scale_is_seen_against_the_page(theme):
    """The pale stops of the scale are below 3:1 on their own on a light page (viridis yellow is 1.23:1) and the dark ones on a dark page.
    Each cell therefore has an outline that is at least 3:1, and the colour bar has a frame that is. The numbers are also in the table."""
    page = value("body", "background", theme)
    assert len(STOPS) == 5
    assert min(ratio(c, rgba(page)) for c in STOPS) < 3.0                                  # the problem this answers is real: fill alone is not enough
    outline = value(".cell", "stroke", theme)
    assert outline != "none" and ratio(rgba(outline), rgba(page)) >= 3.0, outline
    assert float(RULES[".cell"]["stroke-width"]) > 0
    assert ratio(rgba(value(".plot", "stroke", theme)), rgba(page)) >= 3.0

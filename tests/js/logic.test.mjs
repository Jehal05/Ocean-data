// Unit tests for the pure dashboard logic. Every expected value below was worked out by hand.
import assert from "node:assert/strict";
import test from "node:test";

import {
  bandRuns, baseVariables, boxParam, columnsOf, citation, colour, dispositionName, errorText, extent, flagInfo, fmt, lonSpans, monthRange, months, niceAxis,
  notices, oneProfile, parseBox, plusSeconds, profileList, query, runs, timeAxis, utc,
} from "../../components/08_visualization_dashboard/dashboard/static/logic.js";

test("flags: shape and label for every flag, unknown is never good", () => {
  assert.deepEqual(flagInfo(1), { code: 1, label: "good", shape: "circle" });
  assert.equal(flagInfo(2).shape, "diamond");
  assert.equal(flagInfo(3).shape, "triangle");
  assert.equal(flagInfo(4).shape, "cross");
  assert.equal(flagInfo(9).label, "missing");
  assert.equal(flagInfo(7).label, "unknown flag 7");
  assert.notEqual(flagInfo(7).label, "good");
  assert.equal(flagInfo(null).label, "no flag");
  assert.equal(flagInfo(undefined).code, null);
  const shapes = [1, 2, 3, 4].map((f) => flagInfo(f).shape);
  assert.equal(new Set(shapes).size, 4, "the shapes differ, so colour is not the only signal");
});

test("a missing value is said to be missing, never shown as zero", () => {
  assert.equal(fmt(null), "no value");
  assert.equal(fmt(undefined), "no value");
  assert.equal(fmt(NaN), "no value");
  assert.equal(fmt(0), "0");
  assert.equal(fmt(26.864316940307617, 3), "26.864");
  assert.equal(fmt(1539.935, 3), "1539.935");
  assert.equal(fmt(0.00001234, 3), "1.23e-5");
});

test("nice axis: 1, 2, 5 steps that cover the data", () => {
  assert.deepEqual(niceAxis(0, 100, 5).ticks, [0, 20, 40, 60, 80, 100]);
  const a = niceAxis(26.1, 27.4, 5);                       // raw step 0.26 -> 0.5
  assert.equal(a.step, 0.5);
  assert.deepEqual(a.ticks, [26, 26.5, 27, 27.5]);
  assert.equal(a.min, 26); assert.equal(a.max, 27.5);
  const d = niceAxis(0, 1, 5);                              // raw 0.2 -> exactly 0.2
  assert.deepEqual(d.ticks, [0, 0.2, 0.4, 0.6, 0.8, 1]);
  const flat = niceAxis(5, 5, 5);                           // one value: a range is made around it
  assert.ok(flat.min < 5 && flat.max > 5);
  assert.equal(niceAxis(null, 3), null);
  const neg = niceAxis(-3.2, 7.9, 4);                       // raw 2.775 -> 5
  assert.deepEqual(neg.ticks, [-5, 0, 5, 10]);
});

test("gaps: a missing value ends a run, nothing is joined across it", () => {
  assert.deepEqual(runs([0, 1, 2, 3, 4], [10, 11, null, 13, 14]), [[[0, 10], [1, 11]], [[3, 13], [4, 14]]]);
  assert.deepEqual(runs([0, 1, 2], [null, NaN, undefined]), []);
  assert.deepEqual(runs([0, 1], [5, 6]), [[[0, 5], [1, 6]]]);
  assert.deepEqual(runs([0, 1, 2], [0, null, 0]).length, 2, "zero is a value, null is a gap");
});

test("band runs need both ends", () => {
  assert.deepEqual(bandRuns([0, 1, 2], [1, 1, 1], [2, null, 3]), [[[0, 1, 2]], [[2, 1, 3]]]);
});

test("extent ignores missing values", () => {
  assert.deepEqual(extent([3, null, 1, NaN, 2]), [1, 3]);
  assert.equal(extent([null]), null);
});

test("longitude wrap at 180 degrees", () => {
  assert.deepEqual(lonSpans(60, 75), [[60, 75]]);
  assert.deepEqual(lonSpans(170, -170), [[170, 180], [-180, -170]]);
  assert.deepEqual(lonSpans(170, 190), [[170, 180], [-180, -170]]);
  assert.deepEqual(lonSpans(-10, 10), [[-10, 10]]);
  assert.deepEqual(lonSpans(190, 200), [[-170, -160]]);          // both edges wrapped into -180..180
  assert.deepEqual(lonSpans(540, 550), [[-180, -170]]);
});

test("box parsing: plain numbers, west > east means across 180", () => {
  assert.deepEqual(parseBox("60,5,80,25"), { west: 60, south: 5, east: 80, north: 25, wraps: false });
  const w = parseBox("170,-10,-170,10");
  assert.equal(w.wraps, true); assert.equal(w.west, 170); assert.equal(w.east, -170);
  assert.equal(parseBox("170,-10,190,10").east, -170);
  assert.equal(parseBox("-180,0,180,10").east, 180);
  assert.match(parseBox("1,2,3").error, /west,south,east,north/);
  assert.match(parseBox("a,b,c,d").error, /plain numbers/);
  assert.match(parseBox("1,5,2,3").error, /south must/);
  assert.match(parseBox("1,-95,2,3").error, /south must/);
  assert.match(parseBox("1,2,2000,3").error, /between -540 and 540/);
  assert.equal(parseBox("1e3,2,3,4").error !== undefined, true);
  assert.equal(boxParam(parseBox("60,5,80,25")), "60,5,80,25");
});

test("query string: sorted, empty values left out, no injection of extra parameters", () => {
  assert.equal(query({ b: "2", a: "1", c: "", d: null, e: undefined }), "?a=1&b=2");
  assert.equal(query({}), "");
  assert.equal(query({ q: "a&b=c" }), "?q=a%26b%3Dc");
  assert.equal(query({ q: " x " }), "?q=x");
  assert.equal(query({ n: 0 }), "?n=0");
});

test("error text keeps the API's own words and names the status", () => {
  assert.match(errorText(413, "the selection has 9000 rows (limit max_rows_subset = 5000)"), /^Too much data requested \(HTTP 413\): the selection has 9000 rows/);
  assert.match(errorText(429, "slow down"), /^Too many requests, wait a minute \(HTTP 429\): slow down/);
  assert.match(errorText(403, "the admin role is required"), /^Not allowed \(HTTP 403\): the admin role/);
  assert.equal(errorText(504, ""), "The request ran out of time (HTTP 504)");
  assert.equal(errorText(418, ""), "Request failed (418) (HTTP 418)");
  assert.match(errorText(502, "x"), /nothing was returned/);
});

test("file name only from the server's header, never invented", () => {
  assert.equal(dispositionName('attachment; filename="argo_subset_20250101.csv"'), "argo_subset_20250101.csv");
  assert.equal(dispositionName("attachment"), null);
  assert.equal(dispositionName('attachment; filename="../../etc/passwd"'), null);
  assert.equal(dispositionName(null), null);
  assert.equal(dispositionName('attachment; filename="a b.csv"'), null);
});

test("citation fields are always filled in words", () => {
  const c = citation({ data_version: "abc", product_version: null, qc_config_sha256: null, flags_used: [1, 2] });
  assert.deepEqual(c, { data_version: "abc", product_version: "none (a dataset)", qc_config_sha256: "not recorded", flags_used: "1, 2" });
  assert.equal(citation({ product_version: 3 }).product_version, "3");
  assert.equal(citation({ flags_used: "set by the quality parameter" }).flags_used, "set by the quality parameter");
});

test("notices: truncated and stale are said plainly", () => {
  const t = notices({ truncated: true, page: 1, page_size: 50, n_rows_selected: 120 });
  assert.equal(t.length, 1);
  assert.match(t[0], /page 1 of 3 \(120 selected\)/);
  assert.deepEqual(notices({ truncated: false, stale: false }), []);
  const s = notices({ stale: true, stale_reason: "its source dataset was re-reviewed" });
  assert.match(s[0], /stale: its source dataset was re-reviewed/);
  assert.match(notices({ truncated: true, page: 2, page_size: 100, total: 250 })[0], /page 2 of 3 \(250 selected\)/);
  assert.deepEqual(notices(null), []);
});

test("profiles: one entry per platform and record, in time order; rows of one profile by depth", () => {
  const rows = [
    { platform_id: "B", source_record: 0, time: "2025-01-02T00:00:00Z", latitude: 1, longitude: 2, depth: 5 },
    { platform_id: "A", source_record: 1, time: "2025-01-01T00:00:00Z", latitude: 3, longitude: 4, depth: 20 },
    { platform_id: "A", source_record: 1, time: "2025-01-01T00:00:00Z", latitude: 3, longitude: 4, depth: 10 },
    { platform_id: "A", source_record: 0, time: "2025-01-01T00:00:00Z", latitude: 3, longitude: 4, depth: 7 },
  ];
  const l = profileList(rows);
  assert.equal(l.length, 3);
  assert.deepEqual(l.map((p) => `${p.platform_id}${p.source_record}`), ["A0", "A1", "B0"]);
  assert.deepEqual(oneProfile(rows, { platform_id: "A", source_record: 1 }).map((r) => r.depth), [10, 20]);
});

test("colour scale: ends, middle, no value gives no colour", () => {
  assert.equal(colour(0, 0, 10), "rgb(68,1,84)");
  assert.equal(colour(10, 0, 10), "rgb(253,231,37)");
  assert.equal(colour(5, 0, 10), "rgb(33,145,140)");
  assert.equal(colour(-5, 0, 10), "rgb(68,1,84)");
  assert.equal(colour(99, 0, 10), "rgb(253,231,37)");
  assert.equal(colour(null, 0, 10), null);
  assert.equal(colour(NaN, 0, 10), null);
  assert.equal(colour(3, 3, 3), "rgb(33,145,140)");
});

test("months and ranges", () => {
  assert.deepEqual(monthRange("2025-02"), ["2025-02-01", "2025-02-28"]);
  assert.deepEqual(monthRange("2024-02"), ["2024-02-01", "2024-02-29"]);
  assert.deepEqual(monthRange("2025-12"), ["2025-12-01", "2025-12-31"]);
  assert.equal(monthRange("2025-13"), null);
  assert.equal(monthRange("x"), null);
  assert.deepEqual(months("2025-01-01T00:00:00Z", "2025-03-21T00:00:00Z"), ["2025-01", "2025-02", "2025-03"]);
  assert.deepEqual(months("2024-11-15", "2025-02-01"), ["2024-11", "2024-12", "2025-01", "2025-02"]);
  assert.deepEqual(months(null, null), []);
});

test("times are always said in UTC, whatever offset the API sent", () => {
  assert.equal(utc("2025-01-01T11:21:10+05:30"), "2025-01-01T05:51:10Z");
  assert.equal(utc("2025-01-01T05:51:10Z"), "2025-01-01T05:51:10Z");
  assert.equal(utc(null), "no value");
  assert.equal(utc("not a time"), "not a time");
});

test("one profile is asked for with a one second window", () => {
  assert.equal(plusSeconds("2025-01-01T05:51:10Z", 1), "2025-01-01T05:51:11Z");
  assert.equal(plusSeconds("2025-01-01T23:59:59Z", 1), "2025-01-02T00:00:00Z");
  assert.equal(plusSeconds("nonsense", 1), null);
});

test("series variables: base names without the statistic suffix, each once, in order", () => {
  assert.deepEqual(baseVariables(["wind_speed_count", "wind_speed_max", "wind_speed_mean", "wind_speed_min", "wind_direction_count", "wind_direction_mean"]), ["wind_speed", "wind_direction"]);
  assert.deepEqual(baseVariables(["temperature"]), ["temperature"]);
  assert.deepEqual(baseVariables(null), []);
  assert.deepEqual(baseVariables(["mean_flow_mean"]), ["mean_flow"]);
});

test("the columns of a base variable, only those the product has, in a fixed order", () => {
  const all = ["wind_direction_count", "wind_direction_mean", "wind_speed_count", "wind_speed_max", "wind_speed_mean", "wind_speed_min"];
  assert.deepEqual(columnsOf(all, "wind_speed"), ["wind_speed_mean", "wind_speed_min", "wind_speed_max", "wind_speed_count"]);
  assert.deepEqual(columnsOf(all, "wind_direction"), ["wind_direction_mean", "wind_direction_count"]);
  assert.deepEqual(columnsOf(all, "nothing"), []);
  assert.deepEqual(columnsOf(null, "x"), []);
});

test("time axis: ticks on whole days inside the data, no more than about n", () => {
  const D = 86400000;
  const a = timeAxis(Date.UTC(2025, 0, 1), Date.UTC(2025, 0, 11), 6);           // 10 days: 1 day gives 10 ticks (too many), 2 days gives 5
  assert.equal(a.step, 2 * D);
  assert.deepEqual(a.ticks, [2, 4, 6, 8, 10].map((d) => Date.UTC(2025, 0, d)));  // 2 January 2025 is epoch day 20090, even
  const hours = timeAxis(Date.UTC(2025, 0, 1, 0), Date.UTC(2025, 0, 1, 12), 6);   // 12 hours: 3 hour steps give 4 intervals
  assert.equal(hours.step, 3 * 3600000);
  assert.deepEqual(hours.ticks.map((t) => new Date(t).toISOString().slice(11, 13)), ["00", "03", "06", "09", "12"]);
  assert.equal(timeAxis(null, 5), null);
  const same = timeAxis(1000, 1000);
  assert.ok(same.max > same.min);
  assert.ok(a.ticks.every((t) => t >= a.min && t <= a.max));
});

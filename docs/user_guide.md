# Dashboard user guide

Open `/app` on the access API. Sign in with the account you were given. Use the sidebar: **Map, Profile, Series, SST, Explore** (icons only on a narrow window; a bar at the bottom on a phone). The top bar shows who you are, a theme button (follow the system, light, dark) and **Sign out**. Filters are in a panel on the left of each view; on a phone the panel starts closed. Everything works with the keyboard; each chart has a "Show the numbers as a table" button.

**Reading any view.** The strip under each chart gives *data_version*, *product_version*, the *QC config checksum* (first 12 characters; hover for all of it; **Copy** copies it) and the *flags used* as chips (a chip has the same shape as its marker in the chart, plus its words). **Copy figure caption** copies one line for a report: title, key, versions, checksum, flags, and a note when the data is synthetic. If the browser refuses to copy, the text appears selected in a box: press Ctrl+C. Quote these when you cite a number. Yellow notices tell you when only part of the selection is shown (a truncated page) or when a product is *stale* (its source data was re-reviewed; rebuild it). If the data is synthetic the footer says so.

**Map.** Each rectangle is the footprint of one published dataset (solid = profile, hatched = time series, dotted = grid; labels every 30 degrees). Point at a footprint or Tab to it for a card; Enter keeps the card open and moves into it (Open profile / series / SST and Explore), Escape closes it. Filters: text, platform type, variable, box (`west,south,east,north`, longitudes may cross 180), time range.

**Profile.** Pick a profile dataset and a profile; the chart shows variables against depth. "Good" shows only flags 1 and 2; "all" shows every value, with a shape and a label for its flag (not checked, probably bad, bad, missing). Missing values are gaps, never zeros. Point at a chart, or focus it and use the up and down arrow keys: a crosshair, a readout (value, depth, flag chip) and the matching table row follow. Temperature and salinity are side by side (stacked on a narrow window).

**Series.** Buoy hourly or daily products: mean line, min-max band, and the number of values in each bin. A bin with no good values is a hatched strip labelled "no good values" and has count 0; the line has a gap there. To zoom, drag across the chart, or use the two range sliders and press **Apply range**: From and To are filled in and the same request is made again. Wind and wave direction show a circular mean only.

**SST.** The monthly mean grid for a box; choose the month with the slider (or the Month box) or press **Play** (one month a second, stops at the last; off when your system asks for less motion). Point at a cell, or focus the map and use the arrow keys, for its latitude, longitude, value and number of observations. The colour bar shows min and max. Blank cells have no value. Large boxes are refused by the API, and the refusal is shown.

**Explore.** Four steps: 1 Choose, 2 Filter, 3 Preview, 4 Download. The status line says "Page 1 of N (M rows selected)"; each download card shows the file type, the rows selected and the rows limit. The server chooses the file name. Cells that begin like a spreadsheet formula are prefixed with `'`.

**While it loads.** Grey placeholders stand in until the answer arrives. An empty result says so and suggests what to change.

**Messages.** Errors show the HTTP status and the API's own words, for example "Too much data requested (HTTP 413)". Too many requests: wait a minute. Session ended: sign in again.

**For the operator.** Behind TLS, set `AUTH_COOKIE_SECURE=true` so the session cookie is only sent over HTTPS (it is off by default so a plain-HTTP demo works), and set HSTS at the reverse proxy; this service does not. The login page shows the default-password warning only when it is opened on the server itself: the API tells only a caller that connects from loopback and carries no forwarding header (`X-Forwarded-*`, `Forwarded`, `X-Real-IP`). Keep your proxy's forwarding headers switched on.

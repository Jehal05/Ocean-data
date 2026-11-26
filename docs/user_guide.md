# Dashboard user guide

Open `/app` on the access API. Sign in with the account you were given. Use the menu: **Map, Profile, Series, SST, Explore**. Everything works with the keyboard; each chart has a "Show the numbers as a table" button.

**Reading any view.** The box under the chart gives *data version*, *product version*, *QC config checksum* and *flags used*: quote these when you cite a number. Yellow notices tell you when only part of the selection is shown (a truncated page) or when a product is *stale* (its source data was re-reviewed; rebuild it). If the data is synthetic the footer says so.

**Map.** Each rectangle is the footprint of one published dataset. Filters: text, platform type, variable, box (`west,south,east,north`, longitudes may cross 180), time range.

**Profile.** Pick a profile dataset and a profile; the chart shows variables against depth. "Good" shows only flags 1 and 2; "all" shows every value, with a shape and a label for its flag (not checked, probably bad, bad, missing). Missing values are gaps, never zeros.

**Series.** Buoy hourly or daily products: mean line, min-max band, and the number of values in each bin. A bin with no good values is a gap and has count 0. Wind and wave direction show a circular mean only.

**SST.** The monthly mean grid for a box; choose the month. Blank cells have no value. Large boxes are refused by the API, and the refusal is shown.

**Explore.** Choose a dataset or product, set filters, preview the first page, and download CSV or NetCDF. The server chooses the file name. Cells that begin like a spreadsheet formula are prefixed with `'`.

**Messages.** Errors show the HTTP status and the API's own words, for example "Too much data requested (HTTP 413)". Too many requests: wait a minute. Session ended: sign in again.

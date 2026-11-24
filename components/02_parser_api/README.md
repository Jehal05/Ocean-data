# 02 Data Parser API

**Phase 2.** Not built yet.

Will provide: one parser plugin per format (NetCDF, CSV, raw-style), metadata extraction and validation, unit normalisation, variable mapping to the standard names in `docs/data_model.md`, conversion into the common data model, and an initial QC flag.

Input: files in the raw zone (`data/raw`). Output: standardised in-memory datasets passed to the QC framework.

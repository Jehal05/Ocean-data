# 05 Scientific Data Store

**Phase 3, built (Zarr only).** `scats_store/` writes approved data to Zarr format 2 (Blosc/zstd, lossless) in `standardised/`, verifies it against the source, and can read it back and check it against the catalogue. Writes go to a temp prefix first; the manifest is written last. Kerchunk/VirtualiZarr is deferred. Layout and failure behaviour: `docs/phase3_design.md`. Tested with moto, not SeaweedFS.

"""Limits of the Phase 2 QC and ingest that users must be told about (shown by qc-summary and the review service)."""

LIMITS = [
    "The spike test cannot see the first and last record of a profile or series; they get range tests only.",
    "timestamp_monotonic cannot tell which record is wrong when two consecutive records are wrong or a pair is swapped: it flags both as ambiguous. A forward jump on the last record is invisible.",
    "density_inversion flags both levels of the offending pair, so a single bad value can earn soft flag 3 on its neighbours.",
    "When units_assumed_from_mapping is true, the file did not declare its units and the YAML mapping was trusted.",
    "Regional ranges and the buoy spike thresholds are engineering judgement; the Argo-derived thresholds were recalled from memory. All are open questions for NPOL.",
]

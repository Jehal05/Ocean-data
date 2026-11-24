# Phase 2 mutation check (original error set)

Baseline: recall 24/24, primary test fired 24/24

| Test removed | Recall without it | Primary fired without it | In answer key? | Verdict |
|---|---|---|---|---|
| missing_value | 24/24 | 19/24 | yes | score drops |
| format_consistency | 24/24 | 24/24 | no | not exercised by the answer key (unit tests only) |
| global_range | 24/24 | 19/24 | yes | score drops |
| regional_range | 24/24 | 24/24 | no | not exercised by the answer key (unit tests only) |
| spike | 22/24 | 19/24 | yes | score drops |
| position_on_land | 22/24 | 22/24 | yes | score drops |
| position_range | 23/24 | 23/24 | yes | score drops |
| timestamp_valid | 24/24 | 24/24 | no | not exercised by the answer key (unit tests only) |
| timestamp_monotonic | 20/24 | 20/24 | yes | score drops |
| density_inversion | 23/24 | 22/24 | yes | score drops |

Recall unchanged when removed (another test flags the same cells): missing_value, format_consistency, global_range, regional_range, timestamp_valid

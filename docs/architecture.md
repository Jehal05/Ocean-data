# SCATS POC: Architecture (Phase 1)

Ocean Environment and Acoustic Intelligence System for Geo-spatial Analysis and
Target-Based Modelling. Proof of concept for GeM bid GEM/2026/B/8266583.

Status: DRAFT for review. Built from the Scope of Work supplied with the bid.

## 1. Component diagram and data flow

```mermaid
flowchart LR
    subgraph SRC["1. Data Sources"]
        A1[Argo / CTD profiles]
        A2[Moored buoys: wind, wave, current]
        A3[Satellite: SST, SLA, currents]
        A4[Numerical model outputs]
        A5[Bathymetry, sediment]
    end

    P["2. Data Parser API<br/>read, validate, normalise units,<br/>map names, initial flag"]
    QC["3. QC Framework<br/>automated tests, flag management"]
    RV["4. Expert Review Interface<br/>approve / reject, change flags,<br/>comment, audit trail"]

    subgraph STORE["5. Scientific Data Store (Zarr / Kerchunk / VirtualiZarr)"]
        Z1[(Raw zone)]
        Z2[(Standardised zone)]
        Z3[(Derived product zone)]
    end
    CAT[(Catalogue, flags, users, audit log<br/>PostgreSQL + PostGIS)]

    PIPE["6. Derived Products Pipeline<br/>scheduled + on demand"]
    API["7. Data Access API"]
    DASH["8. Visualisation Dashboard<br/>profile, section, map, explore"]

    SRC -->|files as received| Z1
    Z1 --> P --> QC
    QC <-->|results / overrides| RV
    QC -->|passed + approved| Z2
    Z2 --> PIPE -->|publish products| Z3
    Z2 --> API
    Z3 --> API
    API --> DASH
    DASH -. direct read .-> STORE
    P --> CAT
    QC --> CAT
    RV --> CAT
    API --> CAT
```

Reading the diagram:

1. Source files land untouched in the **raw zone**.
2. The **parser** converts each source into the common data model and assigns an initial flag.
3. The **QC framework** runs automated tests and stores a flag for every observation and variable, naming the test that set it.
4. The **review interface** lets a scientist inspect the results, override flags and approve or reject a dataset. Every action is written to the audit log, and the loop with QC is two-way.
5. Approved data moves to the **standardised zone**, which is the source of truth.
6. The **products pipeline** reads the standardised zone and writes products to the **derived zone**.
7. The **Data Access API** serves observations, metadata and products to people and other systems.
8. The **dashboard** is the user-facing layer on top of the API and the store.

## 2. Technology choices

| Concern | Choice | Reason |
|---|---|---|
| Language | Python 3.12 | Standard for ocean science; xarray, gsw and netCDF libraries are mature. |
| API framework | FastAPI | Automatic OpenAPI docs, which the scope asks for in programmatic access. |
| Array data | xarray | Labelled N-dimensional data maps directly onto CF/NetCDF models. |
| Storage format | Zarr | Cloud-native chunked arrays named in the scope of work. |
| Reference files | Kerchunk, VirtualiZarr | Read existing NetCDF/HDF files as Zarr without copying them. Named in the scope. |
| Seawater physics | gsw (TEOS-10) | Accepted standard for density, sound speed and heat content. |
| Object storage | SeaweedFS with S3 gateway | Open source, runs on a laptop or on-premise. MinIO was dropped because its free community images are no longer published. All code uses only the S3 API, so the store can be swapped by editing one compose service. |
| Catalogue and audit | PostgreSQL 16 + PostGIS | Spatial queries on observations, relational integrity, trigger-enforced append-only audit log. |
| Packaging | Docker Compose | One command to start, and runs on laptop, cloud or on-premise as the scope requires. |
| Standards | CF Conventions, GOOS recommendations, OGC | Required by the scope of work (see `data_model.md`). |
| Frontend (Phase 5) | React, MapLibre, Plotly or ECharts | Open-source, no licence cost, handles maps and profile plots. |

## 3. Scope: what is in the POC

| Area | In POC | Future-ready only |
|---|---|---|
| Ingestion of 3 formats (NetCDF, CSV, raw-style) | Yes (Phase 2) | More instrument formats |
| Automated QC and flag history | Yes (Phase 2) | Instrument-specific tests for NPOL sensors |
| Expert review with audit trail | Yes (Phase 3) | Multi-step approval workflows |
| Three-zone Zarr store | Yes (Phase 3) | Tiered or archival storage |
| Derived products | Yes (Phase 4): binned profiles, hourly/daily series, monthly SST. T-S, sections, MLD, OHC, sound speed, climatologies are not built | Full acoustic propagation modelling |
| Data Access API | Yes (Phase 4): discovery, subset, download, limits, query log | OGC web services (WMS/WCS/SensorThings) |
| Dashboard (profile, section, map, explore) | Yes (Phase 5) | Collaborative features |
| Machine learning, forecasting, advanced analytics | No | Extension points only |
| Secure sharing with external stakeholders | Roles only | Federated login, fine-grained sharing |

"Future-ready" means the design leaves room (for example, the derived zone and the products pipeline can host a model output) but nothing is built.

## 4. Storage zones

| Zone | Bucket | Contents | Rule |
|---|---|---|---|
| Raw | `raw` | Original files exactly as received | Never modified. Versioning on. Checksums in `MANIFEST.sha256`. |
| Standardised | `standardised` | Common-model datasets that passed QC and review | Written only after approval. Source of truth. |
| Derived | `derived` | Products generated from the standardised zone | Reproducible from the standardised zone. |

## 5. Where flags live (decision taken in Phase 2)

QC flags are kept in two places that must agree:

* **Staging dataset** (a NetCDF file per dataset until Phase 3 moves it to Zarr) holds the full flag array for every cell.
* **PostgreSQL** (`qc_flag`) holds one row per cell whose flag is 2, 3, 4, 5, 8 or 9, naming the test that set it, plus `observation.overall_flag`. Cells that are good (1) get no row. Grids get one summary row per time slice and variable for flags 3 and 4, because a per-cell table would be huge and land-masked cells are structural.
* `scats_ingest.verify_flag_consistency()` recomputes the expected rows from the staging file and reports every difference. It runs after every ingest. Details and reasoning: `docs/phase2_design.md` section 3.

Phase 3 decisions: a flag only changes by adding a row; a person's row beats any automatic row (`qc_flag_current`); when a QC re-run no longer flags a cell a "cleared" automatic row is added, and a person's decision still wins. Zarr layout: see `docs/phase3_design.md`.

## 5a. Phase 4: products and access (as built)

```
standardised zone (published datasets only) --read_store--> 06 products pipeline --write_zarr_safe--> derived zone  derived/<kind>/<dataset_key>/v<N>.zarr
        catalogue: dataset, dataset_store ----------------------------------------> derived_product (lineage, versions, stale)
        views catalogue_dataset / catalogue_product ---> 07 access API (role scats_access) --lazy read--> standardised / derived
                                                                         \--> query_log (same transaction as the catalogue read)
```
* The pipeline reads published datasets only, uses flags 1 and 2 only, interpolates nothing, and records a lineage (source manifest, review version, QC config version and checksum, code and config checksums, flags used). A product is stale when its source leaves `published` or changes (trigger plus view).
* The API has one policy table (method, path) to role; an unlisted route is 403. Limits per role are rows in `api_limit` with hard maxima. Two database roles: `scats_products` (build) and `scats_access` (read views, write `query_log`).
* Stand-ins used for testing: PostgreSQL 16 without PostGIS (bbox search is plain latitude/longitude comparison; PostGIS would use `ST_Intersects`) and moto for S3. Docker, SeaweedFS and PostGIS are not verified. Details: `docs/phase4_design.md`.

## 5b. Phase 5: dashboard (as built)

```
browser --GET /app, /app/{file} (public, fixed list, page CSP)--> 07 access API --> static files of 08 (no build)
browser --fetch same origin, HttpOnly cookie + X-Requested-With--> the Phase 4 endpoints (datasets, products, subset, download, limits, health)
```
* One origin, so no CORS. No third-party JavaScript or CDN. Every API string is inserted as text. CSP: `default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'`.
* Additive API changes only: `/app` routes, `/` redirect, `default_passwords` boolean in `/health`, three extra response headers. No existing behaviour changed.
* Not drawn: coastline, tiles, zoom, sections, T-S diagrams (see `docs/phase5_design.md`, section 8).

## 6. What Phase 1 delivered from this design

Compose stack (database, object storage, API skeleton), the database schema with a limited API role, the three buckets, the data model specification, sample data and an error-injected test set. Components 2 to 8 exist as empty folders with a README stating what comes in which phase.

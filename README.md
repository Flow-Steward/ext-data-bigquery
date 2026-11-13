# BigQuery workflow extension

Bring BigQuery table data into Flow Steward for reporting, record lookup and workflow-driven data changes. Read-only Datasets support table and column discovery, saved SQL with named parameters, validation, previews, execution and output-schema inference. Separate workflow actions run one GoogleSQL SELECT or one INSERT, UPDATE, DELETE or MERGE, returning job identity and available row-count and processing metadata. Use query results in later workflow steps, or write calculated and collected data back to native BigQuery tables.

The connection selects one Google Cloud project, location and allowed datasets. Queries use ordinary native tables with fully qualified identifiers. Set a per-query bytes-billed ceiling to control each query's maximum billing exposure; this is not an account-wide spending limit. Authenticate using a matching service-account JSON key or a short-lived access token.

Dataset SQL reads one table with explicit columns, filters, ordering and a limit, without joins, functions or subqueries. The separate workflow query action accepts a broader supported GoogleSQL subset. Reads return at most 100 rows. Views, external tables, DDL, scripts and load/export jobs are outside this connector. Parameters support scalar strings, booleans and numbers.

## Workflow SQL reference

`flowsteward.bigquery` provides two host-v2 actions through a minimal, patched Google MCP Toolbox v1.17.0 (`45ed205cc8682ed066c82e0b65bcd0e8fc3becce`) binary:

- **Read rows (SELECT)**: one GoogleSQL SELECT, up to 100 rows, with job metadata and an explicit `truncated` flag.
- **Write rows (INSERT, UPDATE, DELETE, MERGE)**: one INSERT, UPDATE, DELETE or MERGE. Requires a durable external-effect claim and is suppressed before I/O in test mode.

There are no triggers, DDL, scripts, sessions, load/export jobs, external URL inputs, remote functions, stored routines, views or external tables. Only ordinary native tables in the connection's Google project and explicit dataset allowlist are supported. Physical table targets must use fully qualified backticks: `project.dataset.table`. Google dry-run metadata independently validates the statement type, referenced datasets and ordinary table metadata (including every explicit SQL table identifier independently of provider expansion) before the execution job is submitted. WITH/CTE statements and comma joins are unsupported; use explicit JOIN syntax. Fewer than 50 explicit table references are accepted; native dry-run metadata also fails closed at 50 references. Supported built-in SQL functions are intentionally conservative; see `commands.py`.

## Connection

Set `project`, `location` (for example `US`), and `allowed_datasets` (1–32 dataset IDs in that project). `maximum_bytes_billed` defaults to 10 MiB and must be positive (maximum 1 TB). This is a per-query billing ceiling, not a total account budget. `request_timeout_seconds` defaults to 20, maximum 30. The host entrypoint timeout is 40 seconds. Returned JSON is bounded to 480 KiB; large results fail instead of automatically fetching/replaying queries.

Choose exactly one secret:

- `service_account_json`: a service-account JSON document whose `project_id` matches the configured project. It is written into a managed temporary file with mode 0600 and supplied through `GOOGLE_APPLICATION_CREDENTIALS`; caller file paths and other ADC forms are not accepted. OAuth token exchange uses the fixed `https://oauth2.googleapis.com/token` endpoint.
- `access_token`: a short-lived Google OAuth bearer token. A minimal native patch passes a managed subprocess environment token into the upstream source's existing static-token client helper. The token is never placed in argv, SQL, or the Toolbox configuration. Token refresh is the caller's responsibility.

The subprocess receives a minimal environment without inherited proxies, credential paths, endpoint overrides or TLS overrides. Google SDK endpoints are fixed and TLS certificate verification stays enabled. Grant only the required BigQuery job and dataset read/write permissions to the Google identity. Dataset allowlists constrain this extension; IAM remains the actual Google authorization boundary.

## Parameters and results

Each action accepts `connection_ref`, `statement`, and optional `parameters`: an array of `{ "name": "value", "type": "STRING", "value": "hello" }`. Use `@value` in SQL. Types are STRING, BOOL, FLOAT64 and INT64; integers must fit the safe JSON range ±9007199254740991, floats must be finite, NULL/arrays/structs are not supported as parameters. Values use native Google query parameter binding; they are not interpolated into SQL. Comments, semicolons, template syntax and positional parameters are rejected conservatively, including when found inside literals.

Results include `rows`, `job_id`, `project`, `location`, `statement_type`, `total_rows`, `truncated`, and (when supplied by Google) `affected_rows`, `bytes_processed`, `bytes_billed`. MERGE row counts sum the native DML mutation statistics and are omitted when unavailable. BigQuery NUMERIC/BIGNUMERIC results use the upstream decimal-string normalizer; BYTES are base64. No returned page is automatically replayed. Use an explicitly new bounded query for further reading.

Write job IDs are deterministic hashes of account, project, connection, effect claim, SQL and parameters. The native client sets `JobID` without a random suffix. No application retry is performed. An error or timeout after dispatch returns `retryable: false`, `external_effect_status: timeout_unknown`, and the deterministic job identity. A BigQuery job may continue after the local process exits. Inspect the job in Google Cloud by project/location/job ID before deciding whether a manual retry is safe. A duplicate job-ID response is not treated as a fresh successful write. Stable job identity reduces duplicate submission risk; it is not an assertion that arbitrary manual replays are exactly-once.

## Build and verification

`./scripts/build_toolbox.sh` uses a digest-pinned Go 1.27.4 Docker image with 1 CPU/2 GiB. It builds only the BigQuery source and SQL tool imports for Linux AMD64 and ARM64, retaining upstream Apache licensing and patch provenance. The builder uses shared batch caches owned and cleaned by the batch coordinator. Each release ZIP contains exactly one target ELF, its checksum/provenance, Python source, public contracts, tests and the reproducible patch. Build with `TOOLBOX_SOURCE` pointing to an unmodified local checkout at the pinned commit. Package each target with `python3 scripts/package_extension.py --runtime-target linux-amd64` (or linux-arm64).

Offline tests cover host contracts, parameter guards, test-mode suppression, stable job identity, uncertainty and managed auth configuration; upstream BigQuery source, SQL tool and common utility unit suites plus added dry-run policy tests run in the native builder. Real Google Cloud integration is intentionally opt-in and skipped without credentials. No live BigQuery operation has been verified in this release environment; successful compilation and mock contracts do not imply cloud authentication, permission or service availability.

References: [Google parameterized queries](https://cloud.google.com/bigquery/docs/parameterized-queries), [jobs.insert](https://cloud.google.com/bigquery/docs/reference/rest/v2/jobs/insert), [jobs.get](https://cloud.google.com/bigquery/docs/reference/rest/v2/jobs/get). API shapes were checked against those official references and pinned upstream source.

## Datasets and Data Query (1.1.3)

BigQuery is also a read-only SQL dataset connector. Use the same project,
location, allowed datasets, billing ceiling, timeout and Google credential fields
as its existing project connection. Saved SQL uses `:named` scalar parameters
with JSON-schema `string`, `integer`, `number` or `boolean` declarations; values
are sent separately as native typed BigQuery parameters. Null/array/object
parameters are currently unsupported. The existing workflow query/write actions
continue to use their original `@name` typed parameter list.

The saved SQL dialect intentionally accepts one fully qualified backticked native
table, explicit ordinary column projections (optional `AS` aliases), comparison
predicates, `AND`/`OR`/`NOT`, `IS NULL`, bounded `IN` lists, `ORDER BY` and positive
integer `LIMIT`. Column names are ordinary non-keyword identifiers; table aliases,
functions, casts, joins, subqueries, unions, wildcard projections, comments and
scripts are refused. Every projected, filtered and sorted column must be allowed
by the dataset's column scope. Connection project/dataset policy and the native
read-only dry-run/table checks apply independently.

Dataset operations are `test_connection`, `introspect_schema`, `validate_query`,
`preview_query`, `execute_template` and `infer_output_schema`. Validation executes
a bounded preview after local checks. Reads return at most 100 rows and 460 KiB,
respect smaller row/byte budgets, and share the connection request deadline
(maximum 30 seconds). Native query billing limits still apply. Empty reads retain
a matching saved output schema. Schema inference always describes the current
projection, including aliases; empty results use the selected Dataset snapshot
for source column types, or expose projected names with unknown types.

Schema refresh uses the official native table-list and table-metadata tools over
only `allowed_datasets`, with a cumulative deadline and byte budget. It refuses
catalogs exceeding 100 inspected tables or 1000 columns, and excludes external
tables and views. When refreshing a selected Dataset, it fetches only that
Dataset's selected tables and returns only its permitted columns. The native listing asks for at most 101 entries and rejects an
overflow instead of publishing an incomplete snapshot. Google IAM permissions
are unchanged; existing credentials must already allow table metadata reads.
Cloud-free tests exercise the host envelope, MCP subprocess bridge and native
Google HTTP transport fixtures. They do not establish live Google Cloud access.

This release requires host contract 1.5.0 or later for its artifact-free service declarations.

## Automated CI and releases

Pushes to `main` and pull requests run the shared extension build workflow. The checked-in `.github/release-config.json` selects both Linux targets, the repository's package script and test inputs. Missing native payloads and published-only inventory are restored from checksum-pinned release archives; tracked source files are preserved. The source packager retains its provenance checks.

After the versioned release commit is on `main`, publish by pushing its version tag:

```sh
git tag v1.1.9
git push origin v1.1.9
```

The release workflow runs the shared checks, builds the target archives and publishes the GitHub release and checksums. Do not move an existing tag. To rerun a release for an existing tag, dispatch the same workflow:

```sh
gh workflow run release.yml --repo Flow-Steward/ext-data-bigquery --ref main -f tag=v1.1.9
```

The manifest, descriptor (when present), package version and tag must identify the same release. Release publication uses the workflow token; no additional repository secret is required by these callers.

### Standalone CI coverage

The release recipe lists the standalone test files and individual test nodes explicitly, including package, source and provenance contracts. The shared runner clears repository pytest addopts so packaged contract checks are not silently deselected. This scope does not claim live provider or database verification.

The following integration coverage requires a separate environment and is not part of standalone CI:

- `tests/test_live.py`: Opt-in live provider/server test requires external credentials or a disposable service fixture.
- `tests/test_package.py::test_packaged_sql_dataset_query_dispatches_through_actual_core`: Executes a Core validator/dispatcher with PYTHONPATH=/app and imports Core modules.

## Workflow action setup and examples

See [Workflow actions](docs/workflow-actions.md) for choosing Dataset reads versus explicit writes, connection requirements and copyable field examples.

CI builds both Linux architectures from the pinned upstream checkout; it does not restore older release binaries. `scripts/build_toolbox.sh` clones the pinned tag automatically, or accepts a clean exact-commit checkout through `TOOLBOX_SOURCE` / `FS_TOOLBOX_SOURCE_DIR`. The build generates `toolbox-provenance.json`, `SHA256SUMS` and selected compiler metadata, which are included in release archives.

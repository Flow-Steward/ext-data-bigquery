# BigQuery workflow actions

Configure project `demo-project`, the matching location and allowed_datasets `["ops"]`. Use exactly one credential in connection secrets: service_account_json or a short-lived access_token. Fully qualified native table names must belong to that project and allowed dataset; views and external tables are outside this connector. Query execution respects maximum_bytes_billed.

## Read rows

Choose **BigQuery: Read rows with a SELECT query**, select the project connection and enter:

```sql
SELECT order_id, status FROM `demo-project.ops.orders` WHERE status = @status LIMIT 10
```

Set **Named typed parameters (JSON array)** to:

```json
[{"name":"status","type":"STRING","value":"approved"}]
```

This reads a bounded result for the next workflow step. Inspect result.truncated before assuming all matching rows were returned; result.rows holds up to 100 rows.

## Write workflow results

Choose **BigQuery: Insert, update, delete or merge rows**, select the connection and enter:

```sql
UPDATE `demo-project.ops.orders` SET status = @status WHERE order_id = @order_id
```

Supply these parameters, binding values from previous steps as needed:

```json
[{"name":"status","type":"STRING","value":"approved"},{"name":"order_id","type":"INT64","value":1042}]
```

One INSERT, UPDATE, DELETE or MERGE is supported. Use the same fully qualified backticked table names and connection dataset allowlist as for reads.
Do not interpolate values into SQL, submit multi-statement scripts or use DDL.
Test mode suppresses writes. A transport failure can leave a write outcome uncertain;
inspect the returned job ID and BigQuery job status before manually repeating it.

Parameters are an array of typed name/type/value objects, not a plain name/value object. Use @name in SQL and name without @ in the parameter object.

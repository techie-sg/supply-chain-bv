# Dispatch tools

The assistant can call two read-only Python tools through the configured `GroqService`. They run in the application process using the existing `Tool` contract. MCP integration is deferred.

`domain/tools.py` defines validated inputs and the JSON schemas sent to Groq. `service/tools.py` exposes the tools, assembles results, and calculates metrics. All database reads live in `queries/tools.py` and use transactional sessions with the configured connection and statement timeouts.

## Live dispatch status

```text
get_live_dispatch_status(store_id)
```

Use this for the current queue, waiting orders, rider availability, working hours and breaks, batching candidates, and snapshot conditions. The tool description identifies the current manager's store.

The result contains:

| Field | Meaning |
| --- | --- |
| `store_id`, `scenario_key` | Store and loaded scenario |
| `as_of` | Original snapshot time, ISO 8601 in Asia/Kolkata |
| `data_age_sec`, `stale`, `stale_after_sec` | Snapshot age and the five-minute freshness threshold |
| `conditions.is_raining` | Weather condition from the loaded scenario definition |
| `queue.open_orders` | Count of orders that are neither delivered nor cancelled |
| `queue.packed_waiting`, `counts_by_status` | Packed orders waiting for a rider and open-order counts by status |
| `queue.oldest_order_age_sec` | Age of the oldest open order at the snapshot time |
| `queue.oldest_packed_waiting_age_sec` | Age of the oldest packed order waiting for a rider |
| `queue.oldest_frozen_packed_age_sec` | Age of the oldest frozen-item order waiting for a rider |
| `queue.orders`, `orders_truncated` | Up to 50 open orders, oldest first; counts cover the full queue |
| `riders` | IDs, names, status, zone, employment type, shift hours, break age, deliveries, and return ETA |
| `zones` | Zone names, distances, and dry/rain ride times |
| `summary` | Available riders, riders returning within ten minutes, and packed orders per available rider |

Each returned order includes its ID, status, zone, item count, frozen-item flag, assigned rider, placement time, and age. Ages are measured against `as_of`; loading a snapshot does not start a simulator. A stale snapshot is returned with its true timestamp, and the answer must mention that it may be out of date.

When there are no available riders, `pending_per_available_rider` is `null` and the summary explains why. Working-hours and break rules come from retrieved policy; the tool supplies rider facts.

Example call:

```json
{"store_id": "DS-BLR-014"}
```

## Historical delivery metrics

```text
get_delivery_metrics(store_id, date, start_hour, end_hour)
```

Use this for past performance or to compare periods. Call it once per period.

| Input | Constraint |
| --- | --- |
| `store_id` | Nonempty store identifier |
| `date` | Valid calendar date in `YYYY-MM-DD`, interpreted in Asia/Kolkata |
| `start_hour` | Integer from 0 to 23, inclusive |
| `end_hour` | Integer from 1 to 24, exclusive, greater than `start_hour` |

For 8 to 10pm, use `start_hour=20` and `end_hour=22`. The system prompt supplies today's date in Asia/Kolkata so the model can resolve relative dates.

The result contains the requested period, returned and missing hours, hourly rows, and a computed period summary. Hourly rows include orders, ten-minute SLA percentage, average pick-pack, rider-wait and ride minutes, riders online, and the rain flag.

Summary calculations:

- Order counts are summed.
- SLA percentage and stage times are weighted by each hour's order count. They are `null` when the returned rows have no orders.
- Average riders online is the mean across returned hours.
- Orders per rider online divides total orders by the sum of hourly riders online. It is `null` when that sum is zero.
- Rain hours count returned rows whose rain flag is true.

Partial coverage succeeds with `hours_missing` populated. The summary covers only returned hours, and the answer must mention the gap. Historical metrics are supplied aggregates; they are not recalculated from the current open-order snapshot.

Example call:

```json
{"store_id": "DS-BLR-014", "date": "2026-10-08", "start_hour": 20, "end_hour": 22}
```

## Results and errors

The service returns a data dictionary, serialized as JSON in the tool message sent to Groq. Validation rejects missing, malformed, or extra arguments before any database read. Errors use this shape:

```json
{
  "error": {
    "code": "NO_SNAPSHOT",
    "message": "No live dispatch snapshot is loaded, so no live figures are available. Do not estimate queue or rider numbers.",
    "details": {}
  }
}
```

| Code | Meaning and recovery |
| --- | --- |
| `INVALID_INPUT` | Correct the arguments using the tool schema and retry |
| `INVALID_PERIOD` | Correct the date or hour range |
| `NO_SNAPSHOT` | Load a scenario before requesting live figures |
| `UNKNOWN_STORE` | Use an identifier from `known_store_ids` |
| `NO_METRICS_FOR_PERIOD` | Consult the available dates and hours; do not invent missing metrics |
| `DATA_UNAVAILABLE` | Retry once, then explain that the required data cannot be reached |

Database failures return a safe error message. Internal exceptions are logged, and connection details are never included in tool results.

## Chat and trace

All chat requests use the same RAG retrieval and configured Groq service. Tools are available without a preliminary read of the scenario tables. A policy-only answer can be generated without fetching operational rows.

The expandable **Agent trace** records executed dispatch and setting tools, their arguments, the data's `as_of` time and stale flag, errors, and the manager's customized settings. It is saved on the assistant message for display and is never sent back to the model as conversation history.

Tool calls are logged with their arguments, duration, and error status. They inherit the chat request ID through the existing logging context. Operational tools perform no writes. Setting proposals continue to require the manager's confirmation.

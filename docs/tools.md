# DispatchDesk Tool Specification

| | |
| --- | --- |
| **Spec version** | 1.0 |
| **Status** | Draft |
| **Protocol** | Model Context Protocol (MCP), tools capability |
| **MCP server** | `dispatchdesk-ops` (`backend/mcp_server/server.py`) |
| **Implementation** | `backend/service/tools.py` |
| **Contracts** | `backend/domain/tools.py` (Pydantic models; source of each tool's `inputSchema` and `outputSchema`) |

This document is the contract for the operational tools that the DispatchDesk agent can call. It is written for three readers:

- **Engineers** implementing or changing a tool.
- **Agent and prompt owners** deciding when the model should call a tool.
- **Reviewers** checking that tool behavior supports the product guardrails.

The `description` text in each tool section is passed to the model verbatim. Changing it changes agent behavior, so treat description edits as code changes: review them and re-run the agent evaluation suite.

---

## 1. Tool catalog

| Name | Title | Purpose | Side effects | Data freshness |
| --- | --- | --- | --- | --- |
| `get_live_dispatch_status` | Live dispatch status | Current order queue, rider fleet, and zone ride times for one store | None (read-only) | Live snapshot, labeled with `as_of` |
| `get_delivery_metrics` | Delivery metrics | Hourly delivery-stage performance for one store, date, and hour range | None (read-only) | Historical aggregates |

Both tools are read-only by design. Every action that changes the world, such as assigning a rider, batching orders, activating an incentive, or messaging a customer, is out of scope for this server. The agent may only *propose* such actions, and the manager executes them separately.

---

## 2. Conventions

### 2.1 Naming

- Tool names use `verb_object` in snake_case (`get_live_dispatch_status`).
- Parameters are unambiguous and carry their type or unit in the name (`store_id`, `start_hour`, not `store` or `start`).
- Output fields carry their unit as a suffix: `_sec`, `_min`, `_hours`, `_km`, `_pct`.
- Riders are returned with both `rider_id` and `name`. The model refers to people by name in answers, and the ID is used for follow-up actions.

### 2.2 Result format

Tools follow the MCP tool-result format.

**Success.** The result's `structuredContent` contains the data object, which conforms to the tool's `outputSchema`. The same JSON is also serialized into a single `text` content block, for clients that do not read structured content.

```json
{
  "content": [{ "type": "text", "text": "{...same JSON as structuredContent...}" }],
  "structuredContent": { },
  "isError": false
}
```

**Tool execution error.** Business and data errors are returned as a normal result with `isError: true`, so the model can read the error and recover. The `text` block contains a JSON error object:

```json
{
  "content": [{
    "type": "text",
    "text": "{\"error\": {\"code\": \"UNKNOWN_STORE\", \"message\": \"...\", \"details\": {}}}"
  }],
  "isError": true
}
```

| Error field | Type | Description |
| --- | --- | --- |
| `code` | string | Stable, machine-readable code from the tool's error table. |
| `message` | string | Plain-language explanation. States what went wrong **and what to do instead**, so the model can self-correct or tell the manager. |
| `details` | object | Code-specific data that helps recovery, such as valid store IDs or available dates. |

**Protocol errors.** An unknown tool name or arguments that fail JSON Schema validation are rejected by the MCP layer as standard JSON-RPC errors. They never reach the tool implementation.

Error messages never include stack traces, SQL, connection strings, or credentials.

### 2.3 Time

- All timestamps are ISO 8601 with an explicit offset, in `Asia/Kolkata`: `2026-10-05T20:14:00+05:30`.
- Dates are `YYYY-MM-DD` in `Asia/Kolkata`.
- Hour ranges are half-open: `start_hour` is inclusive and `end_hour` is exclusive.

### 2.4 Live-data freshness

Live data is a snapshot with an `as_of` timestamp. The snapshot does not advance on its own.

- **Order ages** (`age_sec`, `oldest_order_age_sec`) are computed relative to `as_of`, not to the wall clock, so they describe the queue as it was when the snapshot was taken.
- **`data_age_sec`** is the wall-clock time between `as_of` and the tool call. Consumers use it to label staleness: "Live data as of 20:14 (3 min ago)."
- Any answer that states a live figure must also state `as_of`.

### 2.5 Computation responsibility

Tools return computed figures: counts, ratios, and order-weighted averages. The model quotes these values rather than doing arithmetic itself. This reduces the risk of invented or miscalculated numbers in answers.

### 2.6 Operational requirements

| Requirement | Value |
| --- | --- |
| Call timeout (client side) | 5 s |
| Live status caching | None, or a short TTL only. Every result must carry its true `as_of`. |
| Metrics caching | Allowed; historical rows do not change within a scenario. |
| Input validation | JSON Schema at the MCP layer, then Pydantic validation in the implementation. |
| Audit logging | Every call is logged with request ID, tool name, arguments, duration, and `isError`. Result payloads are not logged in full. |
| Access | Local stdio server, started by the agent process. No network listener. |

---

## 3. `get_live_dispatch_status`

### 3.1 Definition

| Property | Value |
| --- | --- |
| `name` | `get_live_dispatch_status` |
| `title` | Live dispatch status |
| `annotations` | `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: false` |

**`description` (passed to the model verbatim):**

> Get the live order queue, rider statuses, and zone ride times for one dark store. Use this for any question about what is happening right now: backlog, which orders are waiting, which riders are free or returning, batching candidates, ETAs, or a specific rider's hours on shift and time since last break. Returns figures as of the snapshot time in `as_of`; always quote that time when stating live numbers. Do not use this for past performance; use `get_delivery_metrics` instead.

### 3.2 When to use

| Use when the manager asks… | Do not use when… |
| --- | --- |
| "What's going wrong right now?" | The question is about a past shift or a comparison between periods. Use `get_delivery_metrics`. |
| "Can I batch these packed orders?" | The question is purely about policy ("What's the max shift length?"). Use playbook retrieval. |
| "Is Farhan due a break?" / "Keep him on till close?" | |
| "What ETA should I show customers?" | |

### 3.3 Input

```json
{
  "type": "object",
  "properties": {
    "store_id": {
      "type": "string",
      "minLength": 1,
      "description": "Dark store identifier, as shown for the current scenario (for example, the store in the scenario banner)."
    }
  },
  "required": ["store_id"],
  "additionalProperties": false
}
```

### 3.4 Output

`outputSchema` is generated from the `LiveDispatchStatus` model in `domain/tools.py`. The table below is the normative field reference.

| Field | Type | Description |
| --- | --- | --- |
| `store_id` | string | Echo of the input. |
| `scenario_key` | string | Loaded scenario (`normal`, `backlog`, `rain`, …). |
| `as_of` | timestamp | When the snapshot was taken. |
| `data_age_sec` | integer | Seconds between `as_of` and this call. |
| `conditions.is_raining` | boolean \| null | Whether the current scenario declares rain. `null` if not available. |
| `queue.open_orders` | integer | Orders not yet delivered. |
| `queue.packed_waiting` | integer | Orders with status `packed_waiting_rider`. |
| `queue.oldest_order_age_sec` | integer \| null | Age of the oldest open order, relative to `as_of`. `null` if the queue is empty. |
| `queue.orders_truncated` | boolean | `true` if more than 50 open orders exist and only the 50 oldest are listed. |
| `queue.orders[]` | array | Open orders, oldest first, at most 50. |
| `queue.orders[].order_id` | string | |
| `queue.orders[].status` | string | Order status as defined by the order model (for example, `packed_waiting_rider`). |
| `queue.orders[].zone_id` | string | Delivery zone. Join to `zones[]` for ride times. |
| `queue.orders[].item_count` | integer | |
| `queue.orders[].has_frozen_items` | boolean | Cold-chain flag. Batching rules restrict orders with frozen items. |
| `queue.orders[].assigned_rider_id` | string \| null | |
| `queue.orders[].placed_at` | timestamp | |
| `queue.orders[].age_sec` | integer | `as_of − placed_at`. |
| `riders[]` | array | Every rider on the roster. |
| `riders[].rider_id` | string | |
| `riders[].name` | string | |
| `riders[].status` | string | Rider status as defined by the rider model (for example, `available`). |
| `riders[].current_zone` | string \| null | |
| `riders[].employment_type` | string | |
| `riders[].hours_on_shift` | number | Hours worked so far this shift. |
| `riders[].minutes_since_last_break` | integer | |
| `riders[].deliveries_today` | integer | |
| `riders[].eta_back_min` | integer \| null | Minutes until a rider out on delivery returns. `null` if at the store. |
| `zones[]` | array | Every zone the store serves. |
| `zones[].zone_id` | string | |
| `zones[].zone_name` | string | |
| `zones[].distance_from_store_km` | number | |
| `zones[].avg_ride_min_dry` | number | Average ride time in dry conditions. |
| `zones[].avg_ride_min_rain` | number | Average ride time in rain. |
| `summary.available_riders` | integer | Riders with status `available`. |
| `summary.riders_returning_within_10_min` | integer | Riders with `eta_back_min` ≤ 10. |
| `summary.pending_per_available_rider` | number \| null | `packed_waiting ÷ available_riders`, 2 dp. `null` if no rider is available. |
| `summary.note` | string \| null | Plain-language note for edge cases (for example, "No riders are currently available."). |

The tool reports rider facts only. It does not evaluate them against working-hours limits. Maximum shift length and mandatory break rules come from the rider safety and working-hours policy, which the agent retrieves from the playbook.

### 3.5 Errors

| Code | Condition | `details` | Expected agent behavior |
| --- | --- | --- | --- |
| `NO_SNAPSHOT` | No live snapshot is loaded. | `{}` | Tell the manager live data is unavailable. Do not state any live figures. |
| `UNKNOWN_STORE` | `store_id` does not match the loaded snapshot. | `{ "requested", "known_store_ids" }` | Retry once with a store from `known_store_ids`, or ask the manager. |
| `DATA_UNAVAILABLE` | Database error or timeout. | `{ "retryable": true }` | Retry once. If it fails again, tell the manager live data cannot be reached. |

### 3.6 Examples

Values in all examples are illustrative.

#### Backlog: six packed orders, two available riders

Arguments:

```json
{ "store_id": "KOR-01" }
```

`structuredContent` (orders truncated to the two oldest, zones omitted, for brevity):

```json
{
  "store_id": "KOR-01",
  "scenario_key": "backlog",
  "as_of": "2026-10-05T20:10:00+05:30",
  "data_age_sec": 140,
  "conditions": { "is_raining": false },
  "queue": {
    "open_orders": 6,
    "packed_waiting": 6,
    "oldest_order_age_sec": 540,
    "orders_truncated": false,
    "orders": [
      {
        "order_id": "O-2001",
        "status": "packed_waiting_rider",
        "zone_id": "Z3",
        "item_count": 5,
        "has_frozen_items": false,
        "assigned_rider_id": null,
        "placed_at": "2026-10-05T20:01:00+05:30",
        "age_sec": 540
      },
      {
        "order_id": "O-2002",
        "status": "packed_waiting_rider",
        "zone_id": "Z1",
        "item_count": 2,
        "has_frozen_items": true,
        "assigned_rider_id": null,
        "placed_at": "2026-10-05T20:03:00+05:30",
        "age_sec": 420
      }
    ]
  },
  "riders": [
    {
      "rider_id": "R-03",
      "name": "Farhan",
      "status": "available",
      "current_zone": null,
      "employment_type": "employed",
      "hours_on_shift": 8.5,
      "minutes_since_last_break": 150,
      "deliveries_today": 24,
      "eta_back_min": null
    },
    {
      "rider_id": "R-04",
      "name": "Deepak",
      "status": "available",
      "current_zone": null,
      "employment_type": "gig",
      "hours_on_shift": 2.0,
      "minutes_since_last_break": 60,
      "deliveries_today": 6,
      "eta_back_min": null
    },
    {
      "rider_id": "R-05",
      "name": "Manoj",
      "status": "on_delivery",
      "current_zone": "Z2",
      "employment_type": "gig",
      "hours_on_shift": 4.0,
      "minutes_since_last_break": 35,
      "deliveries_today": 11,
      "eta_back_min": 6
    }
  ],
  "zones": [],
  "summary": {
    "available_riders": 2,
    "riders_returning_within_10_min": 1,
    "pending_per_available_rider": 3.0,
    "note": null
  }
}
```

#### Rain: packed orders with batching candidates

Arguments:

```json
{ "store_id": "KOR-01" }
```

`structuredContent` (orders truncated to three, riders to one):

```json
{
  "store_id": "KOR-01",
  "scenario_key": "rain",
  "as_of": "2026-10-05T20:40:00+05:30",
  "data_age_sec": 60,
  "conditions": { "is_raining": true },
  "queue": {
    "open_orders": 5,
    "packed_waiting": 5,
    "oldest_order_age_sec": 480,
    "orders_truncated": false,
    "orders": [
      {
        "order_id": "O-3001",
        "status": "packed_waiting_rider",
        "zone_id": "Z1",
        "item_count": 3,
        "has_frozen_items": false,
        "assigned_rider_id": null,
        "placed_at": "2026-10-05T20:32:00+05:30",
        "age_sec": 480
      },
      {
        "order_id": "O-3002",
        "status": "packed_waiting_rider",
        "zone_id": "Z1",
        "item_count": 2,
        "has_frozen_items": false,
        "assigned_rider_id": null,
        "placed_at": "2026-10-05T20:34:00+05:30",
        "age_sec": 360
      },
      {
        "order_id": "O-3003",
        "status": "packed_waiting_rider",
        "zone_id": "Z1",
        "item_count": 4,
        "has_frozen_items": true,
        "assigned_rider_id": null,
        "placed_at": "2026-10-05T20:36:00+05:30",
        "age_sec": 240
      }
    ]
  },
  "riders": [
    {
      "rider_id": "R-06",
      "name": "Ajay",
      "status": "available",
      "current_zone": null,
      "employment_type": "employed",
      "hours_on_shift": 5.0,
      "minutes_since_last_break": 70,
      "deliveries_today": 14,
      "eta_back_min": null
    }
  ],
  "zones": [
    {
      "zone_id": "Z1",
      "zone_name": "Koramangala 5th Block",
      "distance_from_store_km": 1.2,
      "avg_ride_min_dry": 5.0,
      "avg_ride_min_rain": 7.5
    }
  ],
  "summary": {
    "available_riders": 2,
    "riders_returning_within_10_min": 1,
    "pending_per_available_rider": 2.5,
    "note": null
  }
}
```

#### Normal: dry conditions, four available riders

Arguments:

```json
{ "store_id": "KOR-01" }
```

`structuredContent` (summary only, for brevity):

```json
{
  "store_id": "KOR-01",
  "scenario_key": "normal",
  "as_of": "2026-10-05T19:30:00+05:30",
  "data_age_sec": 95,
  "conditions": { "is_raining": false },
  "summary": {
    "available_riders": 4,
    "riders_returning_within_10_min": 0,
    "pending_per_available_rider": 0.25,
    "note": null
  }
}
```

#### Error: unknown store

Arguments:

```json
{ "store_id": "HSR-99" }
```

Result (`isError: true`), text payload:

```json
{
  "error": {
    "code": "UNKNOWN_STORE",
    "message": "No live dispatch data for store 'HSR-99'. The loaded snapshot is for store 'KOR-01'. Retry with store_id 'KOR-01'.",
    "details": { "requested": "HSR-99", "known_store_ids": ["KOR-01"] }
  }
}
```

#### Error: no snapshot loaded

Result (`isError: true`), text payload:

```json
{
  "error": {
    "code": "NO_SNAPSHOT",
    "message": "No live dispatch snapshot is loaded, so no live figures are available. Do not estimate queue or rider numbers.",
    "details": {}
  }
}
```

---

## 4. `get_delivery_metrics`

### 4.1 Definition

| Property | Value |
| --- | --- |
| `name` | `get_delivery_metrics` |
| `title` | Delivery metrics |
| `annotations` | `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: false` |

**`description` (passed to the model verbatim):**

> Get historical hourly delivery metrics for one dark store on one date and hour range: orders, 10-minute SLA %, average pick-pack, rider-wait and ride minutes, riders online, and rain flag, plus a pre-computed period summary. Use this to explain past performance or compare periods; call it once per period. The hour range is start-inclusive, end-exclusive: 8 to 10pm is start_hour=20, end_hour=22. Quote the summary figures rather than recalculating them. Do not use this for the current queue; use `get_live_dispatch_status` instead.

### 4.2 When to use

| Use when the manager asks… | Do not use when… |
| --- | --- |
| "Why did SLA fall last night 8–10pm?" | The question is about orders or riders right now. Use `get_live_dispatch_status`. |
| "Compare last night with the night before." (two calls) | A period crosses midnight. Make two calls, one per date. |
| "How did rain affect rider wait yesterday?" | |

The agent resolves relative dates ("last night", "the night before") itself, using today's date in Asia/Kolkata from its system prompt. If no data exists for the chosen date, the error lists the available dates.

### 4.3 Input

```json
{
  "type": "object",
  "properties": {
    "store_id": {
      "type": "string",
      "minLength": 1,
      "description": "Dark store identifier."
    },
    "date": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "Calendar date in Asia/Kolkata, YYYY-MM-DD."
    },
    "start_hour": {
      "type": "integer",
      "minimum": 0,
      "maximum": 23,
      "description": "First hour of the period, inclusive, 24-hour clock."
    },
    "end_hour": {
      "type": "integer",
      "minimum": 1,
      "maximum": 24,
      "description": "End of the period, exclusive, 24-hour clock. Must be greater than start_hour. 8 to 10pm is start_hour=20, end_hour=22."
    }
  },
  "required": ["store_id", "date", "start_hour", "end_hour"],
  "additionalProperties": false
}
```

### 4.4 Output

`outputSchema` is generated from the `DeliveryMetrics` model in `domain/tools.py`. The table below is the normative field reference.

| Field | Type | Description |
| --- | --- | --- |
| `store_id` | string | Echo of the input. |
| `period.date` | date | Echo of the input. |
| `period.start_hour` | integer | Echo of the input. |
| `period.end_hour` | integer | Echo of the input. |
| `period.hours_requested` | integer | `end_hour − start_hour`. |
| `period.hours_returned` | integer | Hours with data. |
| `period.hours_missing` | integer[] | Requested hours with no data. Empty if complete. |
| `hourly[]` | array | One entry per hour with data, ascending. |
| `hourly[].hour` | integer | Start of the hour (20 means 20:00–20:59). |
| `hourly[].orders` | integer | |
| `hourly[].sla_10min_pct` | number | Share of orders delivered within 10 minutes. |
| `hourly[].avg_pick_pack_min` | number | |
| `hourly[].avg_rider_wait_min` | number | |
| `hourly[].avg_ride_min` | number | |
| `hourly[].riders_online` | integer | |
| `hourly[].rain_flag` | boolean | |
| `period_summary.total_orders` | integer | Sum of `orders`. |
| `period_summary.sla_10min_pct` | number \| null | Order-weighted mean, 1 dp. |
| `period_summary.avg_pick_pack_min` | number \| null | Order-weighted mean, 1 dp. |
| `period_summary.avg_rider_wait_min` | number \| null | Order-weighted mean, 1 dp. |
| `period_summary.avg_ride_min` | number \| null | Order-weighted mean, 1 dp. |
| `period_summary.avg_riders_online` | number | Simple mean across returned hours, 1 dp. |
| `period_summary.orders_per_rider_online` | number \| null | `total_orders ÷ sum(riders_online)`, 2 dp. `null` if no riders were online. |
| `period_summary.rain_hours` | integer | Hours with `rain_flag` true. |
| `source` | string | Always `"hourly_metrics (historical aggregates)"`. |

**Order-weighted mean:** `Σ(metric_h × orders_h) ÷ Σ(orders_h)`. Weighted fields are `null` when `total_orders` is 0.

**Partial coverage** is not an error. If some requested hours have no data, the result succeeds with `hours_missing` populated, and the summary covers only the returned hours. The agent must mention the gap.

### 4.5 Errors

| Code | Condition | `details` | Expected agent behavior |
| --- | --- | --- | --- |
| `INVALID_PERIOD` | `end_hour` ≤ `start_hour`, or the date is not a real calendar date. | `{ "reason" }` | Fix the arguments using the message and retry. |
| `UNKNOWN_STORE` | No metrics exist for this `store_id`. | `{ "requested", "known_store_ids" }` | Retry with a known store, or ask the manager. |
| `NO_METRICS_FOR_PERIOD` | Valid period, but no data in it. | `{ "available_dates", "available_hours_on_date" }` | Retry with an available date if the intent is clear; otherwise tell the manager which dates exist. |
| `DATA_UNAVAILABLE` | Database error or timeout. | `{ "retryable": true }` | Retry once, then tell the manager metrics cannot be reached. |

### 4.6 Examples

Values in all examples are illustrative.

#### Dry evening

Arguments:

```json
{ "store_id": "KOR-01", "date": "2026-10-03", "start_hour": 20, "end_hour": 22 }
```

`structuredContent`:

```json
{
  "store_id": "KOR-01",
  "period": {
    "date": "2026-10-03",
    "start_hour": 20,
    "end_hour": 22,
    "hours_requested": 2,
    "hours_returned": 2,
    "hours_missing": []
  },
  "hourly": [
    {
      "hour": 20, "orders": 62, "sla_10min_pct": 81.0,
      "avg_pick_pack_min": 2.6, "avg_rider_wait_min": 1.8, "avg_ride_min": 5.4,
      "riders_online": 18, "rain_flag": false
    },
    {
      "hour": 21, "orders": 55, "sla_10min_pct": 84.0,
      "avg_pick_pack_min": 2.5, "avg_rider_wait_min": 1.5, "avg_ride_min": 5.2,
      "riders_online": 17, "rain_flag": false
    }
  ],
  "period_summary": {
    "total_orders": 117,
    "sla_10min_pct": 82.4,
    "avg_pick_pack_min": 2.6,
    "avg_rider_wait_min": 1.7,
    "avg_ride_min": 5.3,
    "avg_riders_online": 17.5,
    "orders_per_rider_online": 3.34,
    "rain_hours": 0
  },
  "source": "hourly_metrics (historical aggregates)"
}
```

#### Rain-affected evening

When this result is compared with the dry evening above, rider wait shows the largest increase (1.7 to 6.3 min), alongside higher volume and fewer riders online.

Arguments:

```json
{ "store_id": "KOR-01", "date": "2026-10-04", "start_hour": 20, "end_hour": 22 }
```

`structuredContent`:

```json
{
  "store_id": "KOR-01",
  "period": {
    "date": "2026-10-04",
    "start_hour": 20,
    "end_hour": 22,
    "hours_requested": 2,
    "hours_returned": 2,
    "hours_missing": []
  },
  "hourly": [
    {
      "hour": 20, "orders": 78, "sla_10min_pct": 47.0,
      "avg_pick_pack_min": 2.8, "avg_rider_wait_min": 5.9, "avg_ride_min": 7.6,
      "riders_online": 14, "rain_flag": true
    },
    {
      "hour": 21, "orders": 71, "sla_10min_pct": 41.0,
      "avg_pick_pack_min": 2.9, "avg_rider_wait_min": 6.8, "avg_ride_min": 7.9,
      "riders_online": 13, "rain_flag": true
    }
  ],
  "period_summary": {
    "total_orders": 149,
    "sla_10min_pct": 44.1,
    "avg_pick_pack_min": 2.8,
    "avg_rider_wait_min": 6.3,
    "avg_ride_min": 7.7,
    "avg_riders_online": 13.5,
    "orders_per_rider_online": 5.52,
    "rain_hours": 2
  },
  "source": "hourly_metrics (historical aggregates)"
}
```

#### Error: invalid period

Arguments:

```json
{ "store_id": "KOR-01", "date": "2026-10-04", "start_hour": 22, "end_hour": 20 }
```

Result (`isError: true`), text payload:

```json
{
  "error": {
    "code": "INVALID_PERIOD",
    "message": "end_hour must be greater than start_hour. For 8 to 10pm use start_hour=20, end_hour=22.",
    "details": { "reason": "end_hour_not_after_start_hour" }
  }
}
```

#### Error: no data for the period

Arguments:

```json
{ "store_id": "KOR-01", "date": "2026-09-01", "start_hour": 20, "end_hour": 22 }
```

Result (`isError: true`), text payload:

```json
{
  "error": {
    "code": "NO_METRICS_FOR_PERIOD",
    "message": "No hourly metrics for store 'KOR-01' on 2026-09-01. Data exists for 2026-10-03 and 2026-10-04.",
    "details": {
      "available_dates": ["2026-10-03", "2026-10-04"],
      "available_hours_on_date": []
    }
  }
}
```

#### Error: unknown store

Arguments:

```json
{ "store_id": "HSR-99", "date": "2026-10-04", "start_hour": 20, "end_hour": 22 }
```

Result (`isError: true`), text payload:

```json
{
  "error": {
    "code": "UNKNOWN_STORE",
    "message": "No delivery metrics for store 'HSR-99'. Known store: 'KOR-01'.",
    "details": { "requested": "HSR-99", "known_store_ids": ["KOR-01"] }
  }
}
```

---

## 5. Changelog

| Version | Change |
| --- | --- |
| 1.0 | Initial specification of `get_live_dispatch_status` and `get_delivery_metrics`. |

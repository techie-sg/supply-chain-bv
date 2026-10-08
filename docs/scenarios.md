# Synthetic scenario starting states

Each scenario has its own YAML file in
[backend/service/scenario_data](../backend/service/scenario_data). The
[loader](../backend/service/scenarios.py) validates the selected file and turns it into database rows. The
[Gradio workspace](../backend/ui/gradio_app.py) calls the loader directly.
They seed `orders`, `riders`, `hourly_metrics`, and `zones`. The document and
document chunk tables are unaffected. The original workbook remains in
`docs/initial/` as source material; the app does not read it.

| Key | Starting state |
|---|---|
| `normal` | Six orders and four available riders, dry conditions |
| `backlog` | Six packed orders waiting and two available riders, dry conditions |
| `rain` | Rain, packed orders, and batching candidates |

The [settings module](../backend/config.py) reads `DATABASE_URL` (or `DB_URL`)
from the process environment or `backend/.env`. Copy
[`backend/.env.example`](../backend/.env.example) to `backend/.env` and set your
local connection details. The `.env` file is ignored by Git. Migrations are run manually from your local
machine; app startup and deployment do not run them. With a database URL your
machine can reach already set in your shell, run:

```bash
cd backend
uv sync --locked
uv run alembic current
uv run python cli.py migrate
uv run alembic current
```

`current` shows the database revision before and after the upgrade. The
migration changes only the database selected by that URL.

Start the Gradio workspace with `uv run python cli.py app` from `backend/`.
The scenario dropdown lists each YAML file by its title. **Current situation**
shows the loaded scenario's name and description. Selecting another scenario
previews its data without changing the current situation. Click **Load scenario** to replace the operational
data. The workspace shows the loaded scenario and store. A successful
load starts a new conversation; a failed load keeps the current one.

The **Demo tools** view shows searchable, read-only tables for orders,
riders, hourly metrics, and zones. The current scenario's rows come from PostgreSQL;
other scenarios preview their YAML starting data. Preview dates are generated
relative to preview time; saved dates retain their original load time.
All displayed timestamps are converted to IST and omit the timezone suffix.
Choosing another scenario
only changes the preview; it does not replace the saved operational rows.
Click **Refresh** beside the **Store** badge to return to the current scenario and
read its latest saved rows, including edits made since loading. Refresh does not
reset any rows or clear chat. An empty or unavailable database shows no current
rows rather than substituting YAML data.
There is no Excel export or separate HTTP API.

The **Current scenario** indicator identifies the saved operational dataset.
On a successful load, the exact snapshot is shown in Demo tools. Chat uses the
question, the stored messages of the latest conversation, and retrieved
playbook passages; scenario rows are not supplied to it. Clear chat or loading
a scenario starts a new conversation; earlier ones stay stored.
The snapshot's `as_of` timestamp records
when it was loaded. Refresh reads saved changes on demand; it does not advance
the simulation. Reload the scenario to establish a fresh starting point.

The loader validates the whole file before changing the database. It
then deletes the current operational rows and inserts the chosen starting
state in one transaction. If insertion fails, the previous state remains. Each
load uses the current Asia/Kolkata time for `as_of`; order placement is that time
minus each order's configured `age_sec`. Each hourly record uses `day_offset`
to set its date relative to the load date. Zones, riders, orders, and hourly
history can differ completely between files. Load again whenever a fresh
starting state is needed. Add a new `.yaml` file to make a new scenario
available in the dropdown on app restart; no Python case branch is needed.

YAML keeps these larger hand-edited records readable. The loader uses safe
parsing and checks required fields, duplicate identifiers, and references
before resetting operational data.

The load callback takes only the selected scenario key. The rain
case includes route estimates as declared synthetic inputs. Those estimates are
not verified map results.

The historical hourly rows are supplied aggregates because the original source
has no underlying completed-order events. `age_sec` and total delivery minutes
are calculated when needed, rather than stored. Rain is represented by the
hourly flag and zone dry/rain averages; there is no separate weather interval
table in this six-table schema.

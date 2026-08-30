# IEEE-AESS-x-ODC-Internship-Program---SkyPulse
This project, part of the IEEE AESS Mentorship Program and accomplished in partnership with ODC aims to integrate a sensor map that gives us insights into our ground station and cube sat.

## How to Run the Project

The project is split into two separate applications:

- `backend/` provides the FastAPI API.
- `frontend/` provides the Vite/TanStack web application.

Run them in two terminals so the frontend can talk to the backend during development.

### Prerequisites

- Python 3.10+ for the backend.
- Node.js 20+ and Bun or npm for the frontend.
- A local CouchDB for telemetry, and a MongoDB connection string for the Atlas mirror and for user accounts. Copy `backend/.env.example` to `backend/.env` and fill both in.
- Node-RED and a Mosquitto broker on `localhost:1883` to feed telemetry in. See [Telemetry Ingest](#4-telemetry-ingest).
- Optional: a [Groq](https://console.groq.com) API key for the written chart readings and the copilot. Without one those panels say so and the rest of the dashboard is unaffected. See [AI Interpretations](#9-ai-interpretations).

### 1. Run the Backend

1. Open a terminal and go to the backend folder:

	```bash
	cd backend
	```

2. Create and activate a virtual environment:

	```bash
	python -m venv .venv
	.venv\Scripts\activate
	```

3. Install the backend dependencies:

	```bash
	pip install -r requirements.txt
	```

4. Start the FastAPI server:

	```bash
	uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
	```

5. Verify the backend is running:

	- API base URL: `http://127.0.0.1:8000`
	- Swagger docs: `http://127.0.0.1:8000/docs`

	The backend starts whether or not CouchDB is up. CouchDB connects on first
	use, so with the service stopped the telemetry endpoints answer `503` and
	everything else — `/docs`, `/api/auth`, the Atlas mirror — keeps working.
	Start the service and the next request connects on its own; no restart.
	`GET /api/storage` reports the state of both stores and of the MQTT
	subscriber.

### 2. Run the Frontend

1. Open a second terminal and go to the frontend folder:

	```bash
	cd frontend
	```

2. Install the frontend dependencies with your preferred package manager:

	```bash
	bun install
	```

	If you do not use Bun, `npm install` also works.

3. Copy `frontend/.env.example` to `frontend/.env`. The frontend holds no auth
	secrets — it only needs to know where the backend is:

	- `VITE_TELEMETRY_API_BASE_URL` — leave empty to use the `/api` dev proxy.
	- `TELEMETRY_API_BASE_URL` — the same backend as reached from the SSR server.
	- `LOVABLE_API_KEY` — only for the AI copilot.

4. Start the frontend development server:

	```bash
	bun run dev
	```

	Or, with npm:

	```bash
	npm run dev
	```

5. Open the app in your browser at:

	- `http://localhost:5173`

### 3. How the Two Parts Connect

- The frontend development server proxies requests sent to `/api` to the backend at `http://127.0.0.1:8000`.
- Keep both servers running while developing so the dashboard can load telemetry data correctly.
- Going through that proxy also keeps the session cookies same-origin, so the browser sends them back automatically.

### 4. Telemetry Ingest

Telemetry reaches the backend two ways, and both run the same pipeline in
`backend/app/services/ingest_service.py` — node registry, model validation,
denoising, CouchDB, Atlas mirror. Keeping that sequence in one place is what
stops the two transports from drifting apart.

| Transport | Path | Used by |
| --- | --- | --- |
| MQTT | Node-RED → Mosquitto `localhost:1883` → `backend/app/mqtt.py` | the live node flow |
| HTTP | `POST /api/telemetry` | curl, direct sensor pushes, the frontend |

MQTT is the primary path. The backend subscribes to `skypulse/telemetry` as a
durable client — fixed client id, `clean_session=False`, QoS 1 — so packets
published while it restarts are queued by the broker and delivered on reconnect
rather than lost.

Setup:

1. Start Mosquitto and confirm the Node-RED flow is reaching it:

   ```bash
   mosquitto_sub -h localhost -t skypulse/telemetry -v
   ```

2. No `.env` change is needed for a local setup — the defaults in
   `backend/.env.example` already point at that broker and topic. Override
   `MQTT_HOST`, `MQTT_PORT`, `MQTT_TOPIC` or `MQTT_USERNAME` / `MQTT_PASSWORD`
   for anything else, or set `MQTT_ENABLED=false` to run on HTTP ingest alone.
3. Restart the backend. On connect it prints:

   ```
   MQTT ingest: subscribed to skypulse/telemetry (qos 1)
   ```

Notes:

- Every stored packet logs the transport it arrived on, as
  `Received [mqtt]: ...` or `Received [http]: ...`.
- `GET /api/storage` reports the subscriber under `mqtt`: `connected`, the
  `received` / `stored` / `rejected` / `dropped` counters, and the queue depth.
  A rising `received` means the broker is delivering. A non-zero `dropped`
  means the stores are not keeping up with the nodes.
- Like CouchDB, the broker is reported rather than required. The backend starts
  whether or not Mosquitto is up, retries in the background with backoff, and
  re-subscribes on every reconnect — so a broker restart costs no subscription
  and no backend restart.
- **Do not leave the Node-RED HTTP request node wired alongside the MQTT
  publish node.** Nothing deduplicates, so every packet sent both ways is
  stored twice, in CouchDB and in Atlas.
- `MQTT_CLIENT_ID` must be unique per backend instance. Two instances sharing
  one durable session keep kicking each other off the broker. To run more than
  one consumer, use a Mosquitto shared subscription
  (`$share/skypulse/skypulse/telemetry`) rather than varying the id.

### 5. Authentication

Identity lives in **your own Supabase project**. Supabase Auth (`auth.users`)
hashes the passwords, mints the tokens and sends the confirmation and reset
emails; the `public.profiles` table holds the role, status and console
preferences on top.

Everything that talks to it lives in `backend/app/authentification/`. The
frontend has no auth logic and no Supabase client of its own: it posts the
sign-in form to `/api/auth/login` and renders whatever `/api/auth/me` returns.
The backend keeps the Supabase session in **httpOnly cookies**, so no token is
ever readable from the page.

Setup:

1. Run `backend/app/authentification/schema.sql` in the Supabase SQL editor
   (Dashboard → SQL Editor → Run). It is idempotent and safe to re-run.
2. Fill `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` and `SUPABASE_SECRET_KEY` in
   `backend/.env` — from Dashboard → Project Settings → API keys. The secret
   key must never reach the frontend.
3. Add `http://localhost:5173/dashboard` and `http://localhost:5173/auth` under
   Authentication → URL Configuration → Redirect URLs.
4. Verify the link: `cd backend && venv\Scripts\python.exe check_supabase.py`

Notes:

- Browse the endpoints at `http://127.0.0.1:8000/docs` under the **auth** tag.
- The first account to register becomes the admin; the `on_auth_user_created`
  trigger decides that.
- Roles are `user` < `operator` < `admin`, read from `profiles` on every
  request — never from the token, whose `role` claim is the Postgres role.

### 6. RF Fault Detection

The trained model in `backend/app/time series model/` scores the ground
station's RF link and the 3D view acts on the result: an overheating receiver
lights a flashing red beacon beside the mast, and wind pushes the antenna off
aim while streaks blow across the scene.

**What the model is.** `cubesat_multiclass_model.pkl` is a
`RandomForestClassifier` (300 trees) over a flattened **20-step window of 7
features**, in this exact order:

```
snr, antenna_pointing_error_deg, ber, signal_lock,
wind_speed_mps, frequency_offset_hz, sdr_temperature_c
```

20 x 7 = **140 columns**, standardised by `cubesat_multiclass_scaler.pkl`, with
`cubesat_label_encoder.pkl` naming the three classes:

| Class | Dashboard label | What the scene does |
| --- | --- | --- |
| `NORMAL` | `normal` | nothing — the station sits still |
| `SDR_OVERHEATING` | `overheating` | red beacon flashes beside the mast |
| `ANTENNA_WIND` | `wind` | dish is pushed off aim, streaks blow through |

A window is labelled by its **last** row, so a prediction is a nowcast of the
current state, not a summary of the last twenty readings. Rebuilt from
`cubesat_rf_fault_dataset.csv` that convention reproduces **98.9%** accuracy over
all 1981 windows, and 1981 x 0.8 = 1584 matches the scaler's `n_samples_seen_`
exactly — which is how you can tell the serving code flattens the window the
same way training did.

**What the model does not do** is say *when*. That one number is added in
`detector.forecast()`: a least-squares trend across the window, extrapolated to
the threshold where the impairment actually costs the link (60 °C for the SDR,
half the -3 dB beamwidth for pointing error). When nothing is trending there is
no ETA and the panel says so rather than inventing one.

The `drivers` list is explanation, not decision — the raw readings behind the
call, with their per-second trends, so an alert can be checked rather than
trusted.

**Running it.** Nothing extra to start — the backend runs the simulator itself:

```bash
cd backend
venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Open **Dashboard -> Ground station**. The simulator cycles through quiet link ->
SDR heating -> wind gust -> recovery, so the beacon and the wind both appear
within a couple of minutes.

To drive it from outside the process instead — the path a real receiver or a
Node-RED flow would use:

```bash
cd backend
venv\Scripts\python.exe simulate_rf_stream.py
```

That POSTs readings to `/api/rf/telemetry` and prints each prediction. **Live
readings win:** while the script is posting, the built-in simulator mutes
itself, and the sliding window is cleared on the switch so it never straddles
two sources.

**Endpoints** (all under the `rf-fault` tag at `/docs`):

| Endpoint | Purpose |
| --- | --- |
| `GET /api/rf/stream` | Server-sent events, one prediction per reading. This is what the 3D view reads. |
| `GET /api/rf/status` | Latest prediction, the model's class list, and pipeline health — start here when nothing is moving. |
| `POST /api/rf/telemetry` | Ingest one live reading, get its prediction back. |
| `POST /api/rf/predict` | Same, named for testing the model by hand. |
| `GET /api/rf/history` | The last few minutes of predictions. |
| `POST /api/rf/simulator/pause` \| `/resume` | Freeze the simulated feed for a demo. |

A reading must carry all seven feature fields or it is rejected with a 422
naming the missing ones. The first 19 readings only fill the window —
`window_filled` is false and no probabilities are returned until the 20th.

**Reading a prediction:**

| Field | Meaning |
| --- | --- |
| `label` | the winning class, as the dashboard names it |
| `class_probabilities` | the full split across all three classes |
| `confidence` | the model's probability for `label` |
| `fault_probability` | `1 - P(NORMAL)` — the single number `RF_THRESHOLD` is applied to |
| `margin` | how far the winner beat the runner-up; under 0.2 the panel flags a close call |
| `eta_seconds`, `eta_basis` | the forecast, and the threshold it counts down to |
| `severity` | `alert` past the threshold, `watch` past `RF_WATCH_PROBABILITY`, else `nominal` |
| `scenario` | simulator ground truth only. Null on live telemetry, and never a prediction |

Notes:

- The pickles were written by **scikit-learn 1.6.1**, which is pinned in
  `requirements.txt`. A different minor version loads with a warning; a major
  bump is not guaranteed to load at all.
- Retraining with a **new class** does not break anything: unknown classes land
  on the generic `degraded` label and still raise an alert. Give one its own
  colour and 3D behaviour by adding it to `CLASS_LABELS` in `detector.py` and
  `RF_LABEL_STYLE` in `frontend/src/lib/rf-fault.ts`.
- Changing the window size means retraining. The backend refuses to load a model
  whose feature count disagrees with `RF_WINDOW_SIZE` and tells you which value
  to set.
- Set `RF_SIM_ENABLED=false` once real telemetry is attached — the ingest
  endpoints keep working either way.
- The model never sees CouchDB or Atlas. RF predictions are live state, not
  stored telemetry, which is why they arrive over SSE rather than a `/history`
  poll.

### 7. Sensor History

Every sensor page has a **Stored history** panel under the live chart, for
browsing what is already on disk rather than what is arriving now.

The denoised database currently holds ~820,000 documents spanning about a
month, so a range cannot be enumerated — a month of temperature alone is over
100,000 readings. The history endpoint **aggregates** instead: each point on the
curve is a bucket carrying min, max, average and how many readings it merges.

**How it stays fast.** A CouchDB map/reduce view (`_design/history` on the
denoised database) emits every reading keyed
`[sensor, year, month, day, hour, minute]` and reduces with `_stats`. CouchDB
keeps min/max/sum/count for each minute in the b-tree and rolls them up to
hours or days by changing `group_level` alone:

| Query | Time |
| --- | --- |
| 31 days, per day | ~23 ms |
| A busy day, per minute (507 buckets over 30k readings) | ~170 ms |
| Full range, all five sensors | ~70 ms |

Pulling the same ranges and averaging them in Python took 1–3 seconds, and
would get worse every day the nodes run.

The view is created automatically by `ensure_indexes()`. **The first query
builds the index**, which over a database this size takes a few minutes and
outlives the HTTP request that triggered it — so the endpoint answers `503`
with "the history index is still building" until it is ready, and the panel
says so rather than showing an empty chart. It is built once; the design doc is
only rewritten when its map actually changes, so restarting the backend does
not trigger a rebuild.

```
GET /api/telemetry/{sensor_id}/history?start=…&end=…&granularity=auto
```

`granularity` is `auto`, `minute`, `hour`, `day`, `month` or `year`. `auto`
picks a bucket size from the span; an explicit one that would produce more than
1500 buckets is coarsened, and `granularity_adjusted` tells the UI to say so.
Both bounds are optional — omit them for everything stored.

The response also carries `extent`: the sensor's full stored range and reading
count, whatever range was asked for. That is what lets the panel say
*"104,413 readings on record, 29 Jul → 30 Aug"* and bound its date picker to
data that exists.

Notes:

- The chart draws a **min–max band under the average line**. Plotting the mean
  alone would erase the spikes — a minute that touched 41 °C and one flat at
  24 °C can share a mean, and on a fault dashboard that is the whole point.
- The nodes run in bursts, so an empty window is far more often the wrong dates
  than missing data. The empty state offers a **Jump to the latest data** button.
- **CSV export** downloads the buckets on screen, with the raw columns included.
- Only the denoised database has the view. The raw database is untouched.

### 8. Seeing the Denoising

Both charts on a sensor page draw the **denoised** signal and reveal the **raw**
one on hover, as a dashed amber trace. The tooltip then lists the denoised
value, the raw value, and the shift between them.

Nothing extra is stored for this: a denoised document already keeps the original
reading in `value` and the filtered one in `filtered_value`, so both series come
out of one document. The history view emits each reading twice, under `sensor`
and `sensor__raw` — `_stats` reduces one number at a time, so two series means
two keys.

**Motion is the exception.** Its denoiser rewrites `accel_x/y/z` in place and
leaves `magnitude` alone, so `magnitude` *is* the raw reading and the filtered
magnitude has to be recomputed from the filtered axes. Reading `magnitude` for
both would draw the same line twice.

What each filter actually does, measured over 400 recent samples:

| Sensor | Filter | Mean \|raw − denoised\| |
| --- | --- | --- |
| pressure | Kalman | **0.356 hPa** (max 5.6) |
| acoustic | Butterworth low-pass, 0.18 Hz | 0.008 dB |
| motion | Butterworth low-pass, 0.15 Hz per axis | 0.0006 m/s² |
| humidity | **none** — `filtered_value = value` | 0 |
| temperature | median(5) | **0 — see below** |

> **Known issue: the temperature filter is a no-op.**
> `apply_denoising()` only ever takes the newest point, `[-1]`, and at the last
> index `denoise_temperature_points()` clamps its window out of bounds — the
> 5-sample window becomes `[v(n-3), v(n-2), v(n), v(n), v(n)]`, three copies of
> the current value, whose median is always that value. A 99 °C spike into a
> 20 °C series passes through unfiltered. The other three filters are causal
> (Kalman, Butterworth) and are unaffected. Fixing it means making the window
> trailing rather than centred-and-clamped; it would only affect new readings.

The UI is honest about this rather than drawing two identical curves: when the
backend finds the two series identical across a range it reports
`raw.identical`, and the caption reads *"Median filter — but it left this range
unchanged."*

### 9. AI Interpretations

Short written readings of each chart, from **Groq** running
`openai/gpt-oss-120b`. Three of them, each answering a different question:

| Endpoint | Answers | Re-reads when |
| --- | --- | --- |
| `GET /api/ai/rf` | what the RF link is doing and why | the fault label, severity or countdown band changes |
| `GET /api/ai/sensor/{id}/live` | what the live trace is doing now | you press Refresh |
| `GET /api/ai/sensor/{id}/history` | what the range on screen shows | you change the date range or bucket size |

**Nothing is polled.** A note is written when a page opens, when the situation
it describes actually changes, or when you press Refresh — never on a timer.
The RF stream produces a prediction every 2 seconds and the live chart redraws
every second; writing a paragraph about each would be thousands of completions
an hour, all saying the same thing.

Between requests a **shared server-side cache** decides whether the question is
new. Each note carries a *signature* — the fault label and severity, the shape
of a live trace, the exact history query — and the model is only asked again
when that signature changes, bracketed by a per-kind `min_interval` and
`max_age`. Measured: ten consecutive requests cost one completion and nine cache
hits. The cache is shared, so ten operators watching the same fault cost one
completion, not ten.

`?force=true` skips the cache, and is what the Refresh button sends — otherwise
it would hand back the sentence already on screen and look broken. A 1-second
floor swallows an accidental double-click and nothing more.

**Setup.** Put a Groq API key in `backend/.env`:

```dotenv
groq_model=gsk_...
```

The name is what the working `.env` already used; `GROQ_API_KEY` is accepted as
an alias. Despite the name the value is a key, not a model — set
`GROQ_MODEL_NAME` to change the model. Without a key the panels quietly say the
reading is unavailable and everything else works. `GET /api/ai/status` reports
whether it loaded, and how many completions it has spent.

### 10. The Copilot

The chat bubble on every dashboard page is an assistant that answers questions
about the platform **and takes you to the answer**.

One call returns three things: prose for the chat, a `target` to navigate to,
and a `guide` typed out on the page beside that target once you arrive.

**The model never writes a URL.** It picks an `id` from a fixed table in
`backend/app/ai/platform_map.py`, and the backend resolves it into a route, a
parameter and a DOM anchor. A hallucinated id resolves to nothing and you get
the answer without a jump — a wrong sentence is recoverable, a navigation to a
route that does not exist is a broken app.

Eleven destinations are registered: the overview grid, sidebar, live chart, raw
overlay, history panel, date filter, CSV export, 3D station, RF alert panel, the
fault animations, and site health.

**Adding a destination** means two things and nothing else: a row in
`TARGETS`, and a matching `data-tour="…"` attribute on the component. If they
disagree the copilot navigates and nothing lights up, silently — so it is worth
checking they still line up:

```bash
python - <<'PY'
import sys, re, pathlib
sys.path.insert(0, "backend")
from app.ai.platform_map import TARGETS
found = set()
for f in pathlib.Path("frontend/src").rglob("*.tsx"):
    found |= set(re.findall(r'data-tour="([a-z0-9-]+)"', f.read_text(encoding="utf-8")))
missing = [t.id for t in TARGETS if t.anchor not in found]
print("targets:", len(TARGETS), "| missing anchors:", missing or "none")
PY
```

**The arrival is choreographed** rather than instant, because the point is to
show you where a thing lives, not just to mark it. The page scrolls to the
target, a pointer flies out of the chat and clicks it, everything else dims, and
the explanation types itself out alongside.

Two things about that were harder than they look:

- The router runs with `scrollRestoration: true`, which resets the page to the
  top **after** the new route renders. Scrolling the moment the element mounts
  means the router undoes it a frame later and nothing appears to happen — so
  the scroll waits for the router to settle, then verifies the element is
  actually on screen and retries if something moved the page.
- The caption is placed by **scoring** every side for how much of the target it
  would cover, not by taking the first that fits. When none of them fit, the
  first-fit version still clamps back into the viewport, and where it lands is
  on top of the thing it is describing.

The chat panel **fades to 28% when the pointer is elsewhere** so it stops
covering the dashboard, stays solid while hovered, focused, or for seven seconds
after a reply lands, **slides aside** when a highlight would be underneath it,
and can be **dragged by its header** (double-click the header to reset it).

### Optional Checks

- Backend health check: open `http://127.0.0.1:8000/docs`.
- Ingest health check: `curl http://127.0.0.1:8000/api/storage`. Poll it twice —
  rising CouchDB and Atlas counts mean data is landing, and the `mqtt` block
  tells you whether it is arriving over the broker.
- Broker check, independent of the backend:
  `mosquitto_sub -h localhost -t skypulse/telemetry -v`.
- Frontend build check: run `bun run build` or `npm run build` inside `frontend/`.
- RF detector check: `curl http://127.0.0.1:8000/api/rf/status`. `detector_available`
  false means the model did not load, and `detector_error` says why.
- AI check: `curl http://127.0.0.1:8000/api/ai/status`. `available` false means
  no Groq key was found, and `error` says so.
- History index check: `curl "http://127.0.0.1:8000/api/telemetry/temperature/history?granularity=day"`.
  A `503` means the CouchDB view is still building — wait and retry.

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

### Optional Checks

- Backend health check: open `http://127.0.0.1:8000/docs`.
- Ingest health check: `curl http://127.0.0.1:8000/api/storage`. Poll it twice —
  rising CouchDB and Atlas counts mean data is landing, and the `mqtt` block
  tells you whether it is arriving over the broker.
- Broker check, independent of the backend:
  `mosquitto_sub -h localhost -t skypulse/telemetry -v`.
- Frontend build check: run `bun run build` or `npm run build` inside `frontend/`.

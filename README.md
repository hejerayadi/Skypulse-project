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
- Supabase environment variables for the frontend if you want authentication and database access to work locally.

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

3. Make sure the required Supabase variables are available to the frontend. The app expects:

	- `VITE_SUPABASE_URL`
	- `VITE_SUPABASE_PUBLISHABLE_KEY`

	If you are running the server-side Supabase helpers locally, you may also need:

	- `SUPABASE_URL`
	- `SUPABASE_PUBLISHABLE_KEY`
	- `SUPABASE_SERVICE_ROLE_KEY`

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

### Optional Checks

- Backend health check: open `http://127.0.0.1:8000/docs`.
- Frontend build check: run `bun run build` or `npm run build` inside `frontend/`.

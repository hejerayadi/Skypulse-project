from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import mongo, mqtt
from app.authentification import service as auth_service
from app.authentification.router import router as auth_router
from app.ai import client as ai_client
from app.database import CouchDBUnavailable, is_connected
from app.rf_fault import stream as rf_stream
from app.routers.ai import router as ai_router
from app.routers.rf_fault import router as rf_fault_router
from app.routers.telemetry import router as telemetry_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Connecting and indexing here rather than at import keeps a slow or
    # unreachable Atlas from blocking module load.
    mongo.start()
    # Accounts live in Supabase, not in either telemetry store, so this only
    # checks the link and reports what is missing rather than opening anything.
    auth_service.start()

    # Reported, not required. CouchDB connects on first use, so a backend that
    # starts before it is a working backend — the telemetry routes answer 503
    # until it comes up and then resume without a restart.
    if is_connected():
        print("Connected to CouchDB.")
    else:
        print(
            "CouchDB is not reachable — telemetry endpoints will answer 503 "
            "until it is. Everything else, including /api/auth, still works."
        )

    # Node-RED publishes telemetry to Mosquitto now. Started last so the
    # stores are already up when the first packet is delivered, and started
    # without blocking: a broker that is down keeps retrying in the background
    # while /api/telemetry carries on serving HTTP ingest.
    mqtt.start()

    # RF fault detection. Started inside the lifespan rather than at import so
    # it can capture this event loop for the SSE fan-out, and so a model that
    # fails to load costs a warning rather than the whole app.
    rf_stream.start()

    # Reported, not required. A missing Groq key costs the interpretation
    # panels and nothing else, so it is logged here rather than raised.
    ai_client.load()

    yield

    rf_stream.stop()

    # Stop consuming before the stores close, so no packet is pulled off the
    # broker that we are no longer able to write.
    mqtt.stop()
    # Give the writer thread a moment to flush what ingest already queued.
    mongo.stop()


app = FastAPI(
    title="SkyPulse Backend",
    version="1.0",
    lifespan=lifespan
)

# Allow the frontend dev server to call this API directly during local development.
# allow_credentials matters for auth: without it the browser refuses to send the
# session cookies back on a cross-origin call, and every request looks signed out.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.exception_handler(CouchDBUnavailable)
async def couchdb_unavailable(request: Request, exc: CouchDBUnavailable):
    """
    One place to turn "CouchDB is down" into a 503, so no endpoint has to catch
    it and none of them can accidentally report it as a 500.
    """

    return JSONResponse(status_code=503, content={"detail": str(exc)})


app.include_router(
    auth_router,
    prefix="/api"
)

app.include_router(
    telemetry_router,
    prefix="/api"
)

app.include_router(
    rf_fault_router,
    prefix="/api"
)

app.include_router(
    ai_router,
    prefix="/api"
)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers.telemetry import router as telemetry_router

app = FastAPI(
    title="SkyPulse Backend",
    version="1.0"
)

# Allow the frontend dev server to call this API directly during local development.
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

app.include_router(
    telemetry_router,
    prefix="/api"
)
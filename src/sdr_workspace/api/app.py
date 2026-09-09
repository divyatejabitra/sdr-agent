from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from sdr_workspace.api.routes import internal, public, router, webhooks
from sdr_workspace.config import get_settings

settings = get_settings()
app = FastAPI(title=settings.app_name)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def correlation(request: Request, call_next):
    cid = request.headers.get("X-Correlation-Id", "")
    response = await call_next(request)
    if cid:
        response.headers["X-Correlation-Id"] = cid
    response.headers["X-Delivery-Mode"] = settings.delivery_mode
    return response




app.include_router(public)
app.include_router(webhooks)
app.include_router(internal)
app.include_router(router)

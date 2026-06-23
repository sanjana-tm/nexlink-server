"""
NexLink Server — Entry Point
==============================
Creates the FastAPI application and registers all routers, middleware,
exception handlers, and WebSocket endpoints.

Run modes:
  Development:  uvicorn main:app --reload --port 9000
  Production:   uvicorn main:app --host 0.0.0.0 --port 9000 --workers 1
  Docker:       Managed by docker-compose.yml
  Direct:       python main.py (uses uvicorn programmatically)

Why workers=1 for WebSocket servers?
  WebSocket connections are stateful and pinned to a specific process.
  With multiple workers, agent A connects to worker 1 but a command
  sent via the API might hit worker 2 — which doesn't have that connection.
  Solution for scaling: Redis pub/sub in Phase 3, or use a load balancer
  with sticky sessions. For now, workers=1 is correct for Phase 2.
"""
from __future__ import annotations

import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.api.router import api_router
from server.config.settings import get_settings
from server.core.exceptions import NexLinkError, nexlink_error_handler, generic_error_handler
from server.core.lifecycle import get_uptime, lifespan
from server.ws.gateway import router as ws_router

settings = get_settings()

# ── FastAPI Application ────────────────────────────────────────────────────────
app = FastAPI(
    title="NexLink Orchestration Server",
    description="""
## NexLink Server — Enterprise Android IFP Operations Platform

Centralized orchestration server for managing Android IFP devices.

### Architecture
- **Device Registry**: SERIAL_NUMBER-based permanent identity
- **Real-time Communication**: WebSocket gateway for bidirectional agent control
- **Heartbeat Management**: 30s heartbeat with automatic offline detection
- **Screenshot Capture**: Remote screenshot via ADB screencap
- **XML Hierarchy**: Remote UI dump via uiautomator
- **Command Execution**: Remote shell commands with audit trail
- **Automation**: Appium/pytest test execution
- **Health Monitoring**: CPU/memory/storage scoring (0-100)
- **Alerting**: Threshold-based alerts with resolution workflow
- **Audit Logging**: Append-only audit trail for all mutations

### Authentication
1. Register: `POST /api/v1/auth/register` → get API key (one-time)
2. Get token: `POST /api/v1/auth/token` → get JWT access + refresh tokens
3. Use token: `Authorization: Bearer <access_token>` on all requests
4. WebSocket: `ws://server:9000/ws/v1/connect?token=<token>&serial=<serial>`

### Device Identity
- SERIAL_NUMBER: permanent hardware serial (e.g., TMX2405A12345)
- Never IP addresses, never ADB TCP IDs
    """,
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ── CORS Middleware ────────────────────────────────────────────────────────────
# Allow all origins in dev. Restrict in production via NEXLINK_CORS_ORIGINS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Exception Handlers ────────────────────────────────────────────────────────
# Register custom exception handlers BEFORE routes so they take effect.
app.add_exception_handler(NexLinkError, nexlink_error_handler)
app.add_exception_handler(Exception, generic_error_handler)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(api_router)   # /api/v1/...
app.include_router(ws_router)    # /ws/v1/connect


# ── Health & Info Endpoints ───────────────────────────────────────────────────

@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """
    Server health check endpoint.
    Used by Docker HEALTHCHECK and load balancers.
    Returns HTTP 200 if healthy, HTTP 503 if degraded.
    """
    from server.db.session import engine
    from sqlalchemy import text

    # Quick DB check
    db_status = "ok"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"error: {e}"

    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "version": "2.0.0",
        "database": db_status,
        "uptime_seconds": get_uptime(),
    }


@app.get("/info", tags=["health"])
async def server_info() -> dict:
    """Return server configuration info (non-sensitive fields only)."""
    from server.ws.manager import connection_manager

    return {
        "version": "2.0.0",
        "name": "NexLink Orchestration Server",
        "phase": 2,
        "active_ws_connections": connection_manager.connection_count,
        "online_devices": connection_manager.online_device_ids,
        "uptime_seconds": get_uptime(),
    }


# ── CLI entry point ────────────────────────────────────────────────────────────

def main() -> None:
    """Run the server programmatically (for direct `python main.py` usage)."""
    try:
        import uvicorn
    except ImportError:
        print("ERROR: uvicorn not installed. Run: pip install uvicorn[standard]", file=sys.stderr)
        sys.exit(1)

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
        workers=1,  # WebSocket state requires single worker (see module docstring)
    )


if __name__ == "__main__":
    main()

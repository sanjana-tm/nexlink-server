"""
NexLink Server — APK Distribution API
======================================
GET  /api/v1/apk/version       — current APK info (size, version string)
GET  /api/v1/apk/download      — serve the APK binary (no auth required)
POST /api/v1/apk/upload        — upload a new APK (admin key required)
POST /api/v1/apk/push/{serial} — send apk.update WS command to a device (admin key required)

The APK is stored at APK_DIR/nexlink-agent.apk (default: ./apk/).
On Render.com the disk is ephemeral; re-upload after each deploy or mount a
persistent disk.  The push endpoint constructs the download URL from the
incoming request so it works both locally and behind a reverse proxy.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse

from server.api.deps import require_admin
from server.ws.manager import connection_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/apk", tags=["apk"])

APK_DIR = Path(os.getenv("APK_DIR", "./apk"))
APK_PATH = APK_DIR / "nexlink-agent.apk"
VERSION_PATH = APK_DIR / "version.txt"


def _apk_version() -> str:
    if VERSION_PATH.exists():
        return VERSION_PATH.read_text().strip()
    if APK_PATH.exists():
        return f"unknown ({APK_PATH.stat().st_size} bytes)"
    return "none"


@router.get("/version", summary="Get current APK version info")
async def get_apk_version() -> dict:
    return {
        "available": APK_PATH.exists(),
        "version": _apk_version(),
        "size_bytes": APK_PATH.stat().st_size if APK_PATH.exists() else 0,
    }


@router.get("/download", summary="Download the NexLink agent APK")
async def download_apk() -> FileResponse:
    if not APK_PATH.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No APK uploaded yet. POST to /api/v1/apk/upload first.",
        )
    return FileResponse(
        path=str(APK_PATH),
        media_type="application/vnd.android.package-archive",
        filename="nexlink-agent.apk",
    )


@router.post("/upload", summary="Upload a new APK (admin only)")
async def upload_apk(
    file: UploadFile = File(...),
    version: str = "latest",
    _: None = Depends(require_admin),
) -> dict:
    APK_DIR.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    APK_PATH.write_bytes(content)
    VERSION_PATH.write_text(version)
    logger.info("APK uploaded: %d bytes, version=%s", len(content), version)
    return {"status": "uploaded", "size_bytes": len(content), "version": version}


@router.post("/push/{serial}", summary="Push OTA update to a connected device (admin only)")
async def push_apk_to_device(
    serial: str,
    request: Request,
    _: None = Depends(require_admin),
) -> dict:
    if not APK_PATH.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No APK uploaded yet. POST to /api/v1/apk/upload first.",
        )
    if not connection_manager.is_connected(serial):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Device {serial} is not connected",
        )

    base_url = str(request.base_url).rstrip("/")
    apk_url = f"{base_url}/api/v1/apk/download"

    await connection_manager.send(serial, {
        "type": "apk.update",
        "payload": {"url": apk_url, "version": _apk_version()},
    })

    logger.info("apk.update sent to device %s (url=%s)", serial[:12], apk_url)
    return {"status": "update_sent", "serial": serial, "url": apk_url, "version": _apk_version()}

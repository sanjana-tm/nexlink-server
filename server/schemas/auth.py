"""
NexLink Server — Auth Pydantic Schemas
========================================
Request/response schemas for authentication endpoints.

Auth Flow:
  1. Agent calls POST /api/v1/auth/register with serial_number + device info
     -> Server creates Device + ApiKey
     -> Returns api_key (ONE TIME ONLY)

  2. Agent calls POST /api/v1/auth/token with serial_number + api_key
     -> Server verifies sha256(api_key) against DB
     -> Returns access_token (JWT) + refresh_token (JWT)

  3. Agent includes access_token in every subsequent request:
     Authorization: Bearer <access_token>

  4. When access_token expires, agent calls POST /api/v1/auth/refresh
     with refresh_token -> gets new access_token
"""
from __future__ import annotations

from pydantic import BaseModel


class RegisterRequest(BaseModel):
    """Payload sent by agent when registering for the first time."""

    serial_number: str
    model: str | None = None
    manufacturer: str | None = None
    android_version: str | None = None


class RegisterResponse(BaseModel):
    """Registration success response."""

    serial_number: str
    api_key: str
    message: str = "Device registered successfully"


class TokenRequest(BaseModel):
    """Request a JWT access token using an API key."""

    serial_number: str
    api_key: str


class TokenResponse(BaseModel):
    """JWT token pair returned after successful authentication."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    """Request a new access token using a refresh token."""

    refresh_token: str


class RefreshResponse(BaseModel):
    """New access token issued from a valid refresh token."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int

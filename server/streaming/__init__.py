"""
NexLink Server — Streaming Signaling Layer (Phase 11)
=======================================================
Server-side management of remote streaming sessions.

The server acts as a signaling relay — it does NOT process
video/audio frames. Frames flow directly between the agent
and the viewer's browser via WebRTC or WebSocket.

Server responsibilities:
  1. Session lifecycle (create, authorize, track, end)
  2. Permission enforcement (who can view which device?)
  3. Signaling relay (SDP offers/answers, ICE candidates)
  4. MJPEG frame relay (fallback when WebRTC unavailable)
"""
from __future__ import annotations

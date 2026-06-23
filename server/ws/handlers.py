"""
NexLink Server — WebSocket Message Handlers
============================================
Dispatches incoming WebSocket messages to the correct handler function.

Message Protocol:
  All messages are JSON objects with this structure:
  {
    "type": "<event_type>",        -- required, dot-separated
    "session_id": "<UUID>",        -- optional, echoed back in responses
    "device_id": "<UUID>",         -- set by server, not trusted from client
    "payload": { ... },            -- event-specific data
    "ts": "<ISO8601 UTC>"          -- agent-side timestamp
  }

Server-to-Agent Message Types:
  ping               → agent should reply with pong
  command            → execute a command (Phase 3)
  config_update      → update agent configuration (Phase 4)
  broadcast          → server announcement

Agent-to-Server Message Types:
  pong               → response to server ping
  heartbeat          → system health data (can also use REST /heartbeat)
  telemetry          → arbitrary telemetry data
  command_ack        → agent received command
  command_result     → agent completed command
  reconnect_attempt  → agent reporting its reconnect state

Unknown types are logged and ignored (forward compatibility).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from server.services.event_bus import event_bus
from server.ws.manager import ConnectionManager

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MessageDispatcher:
    """
    Routes incoming WebSocket messages to handler methods.

    Usage:
        dispatcher = MessageDispatcher(connection_manager, device_id, session_id)
        await dispatcher.dispatch(message_dict)
    """

    def __init__(
        self,
        manager: ConnectionManager,
        device_id: str,
        session_id: str,
    ) -> None:
        self._manager = manager
        self._device_id = device_id
        self._session_id = session_id

    async def dispatch(self, message: dict[str, Any]) -> None:
        """
        Route a message to its handler based on 'type' field.
        Unknown types are logged and ignored for forward compatibility.
        """
        msg_type = message.get("type", "unknown")

        handlers = {
            "pong":             self._handle_pong,
            "heartbeat":        self._handle_ws_heartbeat,
            "telemetry":        self._handle_telemetry,
            "command_ack":      self._handle_command_ack,
            "command_result":   self._handle_command_result,
            "reconnect_attempt": self._handle_reconnect_attempt,
        }

        handler = handlers.get(msg_type)
        if handler is None:
            logger.debug(
                "Unknown WebSocket message type '%s' from device %s — ignoring",
                msg_type, self._device_id,
            )
            return

        try:
            await handler(message)
        except Exception as e:
            logger.error(
                "Handler error for message type '%s' from device %s: %s",
                msg_type, self._device_id, e, exc_info=True,
            )
            # Send error back to agent
            await self._manager.send(self._device_id, {
                "type": "error",
                "payload": {
                    "error": "HANDLER_ERROR",
                    "detail": f"Server error processing '{msg_type}'",
                },
                "ts": _now_iso(),
            })

    # ── Handler methods ────────────────────────────────────────────────────────

    async def _handle_pong(self, message: dict) -> None:
        """Agent replied to our ping. Log latency if timestamp available."""
        payload = message.get("payload", {})
        ping_ts = payload.get("ping_ts")
        if ping_ts:
            try:
                sent = datetime.fromisoformat(ping_ts)
                latency_ms = (datetime.now(timezone.utc) - sent).total_seconds() * 1000
                logger.debug(
                    "Pong from %s: latency=%.1fms", self._device_id, latency_ms
                )
            except Exception:
                pass

        await event_bus.publish(
            "ws.pong",
            payload={"device_id": self._device_id},
            source_device_id=self._device_id,
            session_id=self._session_id,
        )

    async def _handle_ws_heartbeat(self, message: dict) -> None:
        """
        Heartbeat sent over WebSocket (vs REST POST /heartbeat).
        Publish to event bus — heartbeat_manager subscriber processes it.
        """
        payload = message.get("payload", {})
        await event_bus.publish(
            "heartbeat.received",
            payload={**payload, "device_id": self._device_id},
            source_device_id=self._device_id,
            session_id=self._session_id,
        )

    async def _handle_telemetry(self, message: dict) -> None:
        """Arbitrary telemetry data from agent — route to event bus."""
        payload = message.get("payload", {})
        logger.debug("Telemetry from %s: %s keys", self._device_id, len(payload))
        await event_bus.publish(
            "agent.telemetry",
            payload={**payload, "device_id": self._device_id},
            source_device_id=self._device_id,
            session_id=self._session_id,
        )

    async def _handle_command_ack(self, message: dict) -> None:
        """Agent acknowledged receiving a command."""
        payload = message.get("payload", {})
        command_id = payload.get("command_id")
        logger.info(
            "Command ACK: device=%s command_id=%s", self._device_id, command_id
        )
        await event_bus.publish(
            "command.ack",
            payload={**payload, "device_id": self._device_id},
            source_device_id=self._device_id,
            session_id=self._session_id,
        )

    async def _handle_command_result(self, message: dict) -> None:
        """Agent completed a command and is reporting the result."""
        payload = message.get("payload", {})
        command_id = payload.get("command_id")
        success = payload.get("success", False)
        logger.info(
            "Command result: device=%s command_id=%s success=%s",
            self._device_id, command_id, success,
        )
        await event_bus.publish(
            "command.completed" if success else "command.failed",
            payload={**payload, "device_id": self._device_id},
            source_device_id=self._device_id,
            session_id=self._session_id,
        )

    async def _handle_reconnect_attempt(self, message: dict) -> None:
        """Agent reporting its own reconnect attempt metadata."""
        payload = message.get("payload", {})
        logger.debug("Reconnect attempt report from %s: %s", self._device_id, payload)
        await event_bus.publish(
            "device.reconnect_attempt",
            payload={**payload, "device_id": self._device_id},
            source_device_id=self._device_id,
            session_id=self._session_id,
        )


# ── Server-to-Agent message builders ─────────────────────────────────────────

def build_ping_message(session_id: str) -> dict:
    """Build a ping message to send to an agent."""
    return {
        "type": "ping",
        "session_id": session_id,
        "payload": {"ping_ts": _now_iso()},
        "ts": _now_iso(),
    }


def build_command_message(
    command_id: str,
    command_type: str,
    params: dict,
    session_id: str,
) -> dict:
    """Build a command message to dispatch to an agent."""
    return {
        "type": "command",
        "session_id": session_id,
        "payload": {
            "command_id": command_id,
            "command_type": command_type,
            "params": params,
            "issued_at": _now_iso(),
        },
        "ts": _now_iso(),
    }


def build_connected_message(device_id: str, session_id: str) -> dict:
    """Welcome message sent immediately after WebSocket connection established."""
    return {
        "type": "connected",
        "device_id": device_id,
        "session_id": session_id,
        "payload": {
            "server_version": "2.0.0",
            "server_ts": _now_iso(),
        },
        "ts": _now_iso(),
    }

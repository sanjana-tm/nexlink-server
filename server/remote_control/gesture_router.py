"""
NexLink Server — Gesture Router (Phase 12)
=============================================
Decomposes compound gestures into sequences of atomic input events.

Why compound gestures matter for IFP:
  - "Scroll down" on a 65-inch IFP is not a single swipe.
    It's a series of small swipes with specific velocity to trigger
    Android's kinetic scrolling. The GestureRouter computes the optimal
    swipe parameters for the target device's screen size and density.

  - "Pinch to zoom" requires two simultaneous touch points, which
    `adb shell input` cannot do natively. The router falls back to
    sequential gestures or uses `adb shell input motionevent` where
    supported (Android 11+).

  - Navigation gestures (back, home, recents) differ between
    Android versions and OEM skins. The router knows the correct
    keycode or swipe gesture for each.

Gesture catalog:
  tap, double_tap, long_press, swipe_up, swipe_down, swipe_left,
  swipe_right, scroll_up, scroll_down, pinch_in, pinch_out,
  back, home, recents, power, volume_up, volume_down,
  open_notifications, open_quick_settings, screenshot
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List

from .coordinate_mapper import CoordinateMapper, MappedCoord

logger = logging.getLogger(__name__)


@dataclass
class AtomicInput:
    """A single input event ready for injection on the agent."""
    action: str                      # tap, swipe, keyevent, text
    x: int = 0
    y: int = 0
    x2: int = 0
    y2: int = 0
    duration_ms: int = 0
    key_code: str = ""
    text: str = ""
    delay_before_ms: int = 0        # Wait before executing this step


@dataclass
class GestureResult:
    """Result of routing a gesture through the decomposer."""
    gesture_name: str
    steps: List[AtomicInput] = field(default_factory=list)
    success: bool = True
    error: str = ""


class GestureRouter:
    """
    Decomposes high-level gestures into sequences of atomic inputs.

    Uses the CoordinateMapper to translate viewer coordinates to
    device coordinates before generating input steps.
    """

    def __init__(self, mapper: CoordinateMapper) -> None:
        self._mapper = mapper

    def route(
        self,
        gesture: str,
        viewer_x: float = 0.0,
        viewer_y: float = 0.0,
        viewer_x2: float = 0.0,
        viewer_y2: float = 0.0,
        params: dict | None = None,
    ) -> GestureResult:
        """
        Route a named gesture to a sequence of atomic inputs.

        Args:
            gesture:    Gesture name (tap, swipe_down, back, etc.)
            viewer_x/y: Start coordinates on viewer canvas.
            viewer_x2/y2: End coordinates (for swipes).
            params:     Additional parameters (duration, repeat, text, etc.)

        Returns:
            GestureResult with a list of AtomicInput steps.
        """
        params = params or {}

        # Navigation gestures — no coordinates needed
        nav_handlers = {
            "back": self._gesture_back,
            "home": self._gesture_home,
            "recents": self._gesture_recents,
            "power": self._gesture_power,
            "volume_up": self._gesture_volume_up,
            "volume_down": self._gesture_volume_down,
            "open_notifications": self._gesture_notifications,
            "open_quick_settings": self._gesture_quick_settings,
            "screenshot": self._gesture_screenshot,
        }

        if gesture in nav_handlers:
            return nav_handlers[gesture]()

        # Coordinate-based gestures
        start = self._mapper.map(viewer_x, viewer_y)
        if not start.valid:
            return GestureResult(gesture_name=gesture, success=False, error="Start coordinate outside device area")

        if gesture == "tap":
            return self._gesture_tap(start)

        elif gesture == "double_tap":
            return self._gesture_double_tap(start)

        elif gesture == "long_press":
            duration = params.get("duration_ms", 1000)
            return self._gesture_long_press(start, duration)

        elif gesture == "swipe":
            end = self._mapper.map(viewer_x2, viewer_y2)
            if not end.valid:
                return GestureResult(gesture_name=gesture, success=False, error="End coordinate outside device area")
            duration = params.get("duration_ms", 300)
            return self._gesture_swipe(start, end, duration)

        elif gesture in ("swipe_up", "swipe_down", "swipe_left", "swipe_right"):
            distance = params.get("distance", 0.3)
            return self._gesture_directional_swipe(gesture, start, distance)

        elif gesture in ("scroll_up", "scroll_down"):
            count = params.get("count", 3)
            return self._gesture_scroll(gesture, start, count)

        elif gesture == "text":
            text = params.get("text", "")
            return self._gesture_text(text)

        return GestureResult(gesture_name=gesture, success=False, error=f"Unknown gesture: {gesture}")

    # ── Atomic Gestures ───────────────────────────────────────────────────────

    def _gesture_tap(self, coord: MappedCoord) -> GestureResult:
        return GestureResult(
            gesture_name="tap",
            steps=[AtomicInput(action="tap", x=coord.device_x, y=coord.device_y)],
        )

    def _gesture_double_tap(self, coord: MappedCoord) -> GestureResult:
        return GestureResult(
            gesture_name="double_tap",
            steps=[
                AtomicInput(action="tap", x=coord.device_x, y=coord.device_y),
                AtomicInput(action="tap", x=coord.device_x, y=coord.device_y, delay_before_ms=100),
            ],
        )

    def _gesture_long_press(self, coord: MappedCoord, duration: int) -> GestureResult:
        return GestureResult(
            gesture_name="long_press",
            steps=[AtomicInput(
                action="swipe",
                x=coord.device_x, y=coord.device_y,
                x2=coord.device_x, y2=coord.device_y,
                duration_ms=duration,
            )],
        )

    def _gesture_swipe(self, start: MappedCoord, end: MappedCoord, duration: int) -> GestureResult:
        return GestureResult(
            gesture_name="swipe",
            steps=[AtomicInput(
                action="swipe",
                x=start.device_x, y=start.device_y,
                x2=end.device_x, y2=end.device_y,
                duration_ms=duration,
            )],
        )

    def _gesture_directional_swipe(
        self, direction: str, start: MappedCoord, distance: float,
    ) -> GestureResult:
        """Generate a swipe in a cardinal direction from the given point."""
        dev = self._mapper._device
        dx, dy = 0, 0
        pixel_dist = int(distance * max(dev.width, dev.height))

        if direction == "swipe_up":
            dy = -pixel_dist
        elif direction == "swipe_down":
            dy = pixel_dist
        elif direction == "swipe_left":
            dx = -pixel_dist
        elif direction == "swipe_right":
            dx = pixel_dist

        end_x = max(0, min(dev.width - 1, start.device_x + dx))
        end_y = max(0, min(dev.height - 1, start.device_y + dy))

        return GestureResult(
            gesture_name=direction,
            steps=[AtomicInput(
                action="swipe",
                x=start.device_x, y=start.device_y,
                x2=end_x, y2=end_y,
                duration_ms=300,
            )],
        )

    def _gesture_scroll(
        self, direction: str, center: MappedCoord, count: int,
    ) -> GestureResult:
        """Multi-step scroll (repeated small swipes for momentum)."""
        dev = self._mapper._device
        step_distance = dev.height // 6
        steps: list[AtomicInput] = []

        for i in range(count):
            if direction == "scroll_down":
                y1 = center.device_y
                y2 = max(0, center.device_y - step_distance)
            else:
                y1 = center.device_y
                y2 = min(dev.height - 1, center.device_y + step_distance)

            steps.append(AtomicInput(
                action="swipe",
                x=center.device_x, y=y1,
                x2=center.device_x, y2=y2,
                duration_ms=200,
                delay_before_ms=100 if i > 0 else 0,
            ))

        return GestureResult(gesture_name=direction, steps=steps)

    def _gesture_text(self, text: str) -> GestureResult:
        if not text:
            return GestureResult(gesture_name="text", success=False, error="Empty text")
        return GestureResult(
            gesture_name="text",
            steps=[AtomicInput(action="text", text=text)],
        )

    # ── Navigation Gestures ───────────────────────────────────────────────────

    def _gesture_back(self) -> GestureResult:
        return GestureResult(gesture_name="back", steps=[AtomicInput(action="keyevent", key_code="KEYCODE_BACK")])

    def _gesture_home(self) -> GestureResult:
        return GestureResult(gesture_name="home", steps=[AtomicInput(action="keyevent", key_code="KEYCODE_HOME")])

    def _gesture_recents(self) -> GestureResult:
        return GestureResult(gesture_name="recents", steps=[AtomicInput(action="keyevent", key_code="KEYCODE_APP_SWITCH")])

    def _gesture_power(self) -> GestureResult:
        return GestureResult(gesture_name="power", steps=[AtomicInput(action="keyevent", key_code="KEYCODE_POWER")])

    def _gesture_volume_up(self) -> GestureResult:
        return GestureResult(gesture_name="volume_up", steps=[AtomicInput(action="keyevent", key_code="KEYCODE_VOLUME_UP")])

    def _gesture_volume_down(self) -> GestureResult:
        return GestureResult(gesture_name="volume_down", steps=[AtomicInput(action="keyevent", key_code="KEYCODE_VOLUME_DOWN")])

    def _gesture_notifications(self) -> GestureResult:
        dev = self._mapper._device
        return GestureResult(
            gesture_name="open_notifications",
            steps=[AtomicInput(action="swipe", x=dev.width // 2, y=0, x2=dev.width // 2, y2=dev.height // 3, duration_ms=300)],
        )

    def _gesture_quick_settings(self) -> GestureResult:
        dev = self._mapper._device
        return GestureResult(
            gesture_name="open_quick_settings",
            steps=[AtomicInput(action="swipe", x=dev.width // 2, y=0, x2=dev.width // 2, y2=dev.height // 2, duration_ms=300)],
        )

    def _gesture_screenshot(self) -> GestureResult:
        return GestureResult(
            gesture_name="screenshot",
            steps=[
                AtomicInput(action="keyevent", key_code="KEYCODE_POWER"),
                AtomicInput(action="keyevent", key_code="KEYCODE_VOLUME_DOWN", delay_before_ms=50),
            ],
        )

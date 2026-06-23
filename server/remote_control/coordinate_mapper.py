"""
NexLink Server — Coordinate Mapper (Phase 12)
================================================
Translates coordinates between the viewer's browser canvas and
the target device's physical screen.

Why coordinate mapping is harder than it looks:
  1. Aspect ratio mismatch:
     Viewer canvas = 800x450 (16:9). IFP panel = 3840x2160 (16:9).
     Simple case — just scale. But...
     Viewer canvas = 800x600 (4:3). IFP panel = 3840x2160 (16:9).
     The IFP image is letterboxed in the viewer — black bars on top/bottom.
     A click on the black bar shouldn't map to the IFP screen.

  2. Rotation:
     Android IFPs can be portrait or landscape. The viewer always
     sees the correct orientation in the stream, but the underlying
     coordinate system may differ from what the viewer sees.

  3. Device-specific offsets:
     Status bar, navigation bar, and notch areas affect the touchable
     area. Coordinates must account for these dead zones.

The mapper uses a projection model:
  viewer_coord → normalized (0-1, accounting for letterbox) → device_pixel
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class DisplayInfo:
    """Physical display dimensions of the target device."""
    width: int                      # Physical width in pixels
    height: int                     # Physical height in pixels
    density: int = 0                # DPI (0 = unknown)
    rotation: int = 0              # 0, 90, 180, 270 degrees
    status_bar_height: int = 0     # Top offset for status bar
    nav_bar_height: int = 0        # Bottom offset for navigation bar


@dataclass
class ViewportInfo:
    """Viewer's canvas dimensions in the browser."""
    width: int                      # Canvas width in CSS pixels
    height: int                     # Canvas height in CSS pixels


@dataclass
class MappedCoord:
    """Result of coordinate mapping."""
    device_x: int                   # Pixel X on the device
    device_y: int                   # Pixel Y on the device
    valid: bool = True              # False if click was outside device area (letterbox)
    normalized_x: float = 0.0      # 0.0-1.0 in device coordinate space
    normalized_y: float = 0.0


class CoordinateMapper:
    """
    Maps viewer coordinates to device coordinates.

    Handles aspect ratio mismatch (letterboxing) and device rotation.

    Usage:
        mapper = CoordinateMapper(
            device=DisplayInfo(width=3840, height=2160),
            viewport=ViewportInfo(width=800, height=450),
        )
        result = mapper.map(viewer_x=400, viewer_y=225)
        # result.device_x = 1920, result.device_y = 1080
    """

    def __init__(
        self,
        device: DisplayInfo,
        viewport: ViewportInfo,
    ) -> None:
        self._device = device
        self._viewport = viewport
        self._compute_projection()

    def update_device(self, device: DisplayInfo) -> None:
        """Update device dimensions (e.g., after rotation)."""
        self._device = device
        self._compute_projection()

    def update_viewport(self, viewport: ViewportInfo) -> None:
        """Update viewer dimensions (e.g., after browser resize)."""
        self._viewport = viewport
        self._compute_projection()

    def map(self, viewer_x: float, viewer_y: float) -> MappedCoord:
        """
        Map a viewer coordinate to a device coordinate.

        Args:
            viewer_x: X position on the viewer canvas (pixels).
            viewer_y: Y position on the viewer canvas (pixels).

        Returns:
            MappedCoord with device pixel coordinates and validity.
        """
        # Account for letterboxing — subtract the offset, scale to content area
        content_x = viewer_x - self._offset_x
        content_y = viewer_y - self._offset_y

        # Check if click is inside the content area (not on letterbox bars)
        if (content_x < 0 or content_x > self._content_width or
                content_y < 0 or content_y > self._content_height):
            return MappedCoord(device_x=0, device_y=0, valid=False)

        # Normalize to 0.0-1.0 within the content area
        norm_x = content_x / self._content_width if self._content_width > 0 else 0
        norm_y = content_y / self._content_height if self._content_height > 0 else 0

        # Clamp to valid range
        norm_x = max(0.0, min(1.0, norm_x))
        norm_y = max(0.0, min(1.0, norm_y))

        # Apply rotation transform
        rot_x, rot_y = self._apply_rotation(norm_x, norm_y)

        # Scale to device pixel coordinates
        device_x = int(rot_x * self._device.width)
        device_y = int(rot_y * self._device.height)

        # Offset for status bar
        device_y += self._device.status_bar_height

        # Clamp to device bounds
        device_x = max(0, min(self._device.width - 1, device_x))
        device_y = max(0, min(self._device.height - 1, device_y))

        return MappedCoord(
            device_x=device_x,
            device_y=device_y,
            valid=True,
            normalized_x=norm_x,
            normalized_y=norm_y,
        )

    def map_swipe(
        self,
        x1: float, y1: float,
        x2: float, y2: float,
    ) -> Tuple[MappedCoord, MappedCoord]:
        """Map a swipe gesture (start and end points)."""
        return self.map(x1, y1), self.map(x2, y2)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _compute_projection(self) -> None:
        """
        Compute the letterbox offset and content area dimensions.

        The device image is fit into the viewer canvas while preserving
        aspect ratio. This creates black bars (letterbox/pillarbox) when
        aspect ratios don't match.
        """
        vw, vh = self._viewport.width, self._viewport.height
        dw, dh = self._device.width, self._device.height

        if vw <= 0 or vh <= 0 or dw <= 0 or dh <= 0:
            self._offset_x = 0.0
            self._offset_y = 0.0
            self._content_width = float(vw)
            self._content_height = float(vh)
            return

        viewer_aspect = vw / vh
        device_aspect = dw / dh

        if device_aspect > viewer_aspect:
            # Device is wider — pillarbox (bars on top/bottom)
            self._content_width = float(vw)
            self._content_height = vw / device_aspect
            self._offset_x = 0.0
            self._offset_y = (vh - self._content_height) / 2
        else:
            # Device is taller — letterbox (bars on left/right)
            self._content_height = float(vh)
            self._content_width = vh * device_aspect
            self._offset_x = (vw - self._content_width) / 2
            self._offset_y = 0.0

    def _apply_rotation(self, x: float, y: float) -> Tuple[float, float]:
        """Apply device rotation transform to normalized coordinates."""
        rotation = self._device.rotation
        if rotation == 0:
            return x, y
        elif rotation == 90:
            return y, 1.0 - x
        elif rotation == 180:
            return 1.0 - x, 1.0 - y
        elif rotation == 270:
            return 1.0 - y, x
        return x, y

    def to_dict(self) -> dict:
        return {
            "device": {"width": self._device.width, "height": self._device.height, "rotation": self._device.rotation},
            "viewport": {"width": self._viewport.width, "height": self._viewport.height},
            "offset": {"x": self._offset_x, "y": self._offset_y},
            "content": {"width": self._content_width, "height": self._content_height},
        }

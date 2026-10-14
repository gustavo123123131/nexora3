"""Visual-only mobile Chromium window sizing.

The site's layout viewport and device pixel ratio are provided by the existing
ModeConfig / Playwright context; they are NEVER changed here. Physical window
and compositor presentation are independent of profiles, network and sessions.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import subprocess
from typing import Optional


@dataclass(frozen=True)
class MobileWindow:
    css_width: int
    css_height: int
    width: int           # Intended visible content width on the desktop
    height: int          # Intended visible content height on the desktop
    scale: float         # Uniform visual scale, not the emulated device DPR
    outer_width: int
    outer_height: int
    left: int
    top: int


def work_area() -> tuple[int, int, int, int]:
    """Best-effort current desktop usable rect; Linux Xvfb and Windows supported."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            area = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(
                0x0030, 0, ctypes.byref(area), 0
            ):
                w = area.right - area.left
                h = area.bottom - area.top
                if w >= 400 and h >= 400:
                    return (area.left, area.top, w, h)
        except (OSError, AttributeError, ValueError):
            pass
    elif os.environ.get("DISPLAY"):
        try:
            proc = subprocess.run(
                ["xrandr", "--current"], capture_output=True, text=True,
                timeout=2, check=False,
            )
            for line in proc.stdout.splitlines():
                match = re.search(r"(\d+)x(\d+)\s+[\d.]+\*", line)
                if match:
                    w, h = map(int, match.groups())
                    if w >= 400 and h >= 400:
                        return (0, 0, w, h)
            match = re.search(r"current\s+(\d+)\s*x\s*(\d+)", proc.stdout)
            if match:
                return (0, 0, int(match.group(1)), int(match.group(2)))
        except (OSError, subprocess.TimeoutExpired):
            pass
    return (0, 0, 1366, 768)


def mobile_window(viewport: dict, area: Optional[tuple[int, int, int, int]] = None) -> MobileWindow:
    """Fit a physical *view* into desktop work area without altering CSS viewport.

    Browser chrome/decorations occupy space above the page. Preserve the whole
    mobile presentation on smaller monitors by uniform compositor scaling.
    """
    x, y, screen_w, screen_h = area if area is not None else work_area()
    css_w = int(viewport["width"])
    css_h = int(viewport["height"])
    if css_w < 240 or css_h < 320:
        raise ValueError("Viewport mobile inválido.")
    usable_w = max(300, screen_w - 64)
    usable_h = max(320, screen_h - 130)
    scale = min(1.0, usable_w / css_w, usable_h / css_h)
    content_w = max(1, round(css_w * scale))
    content_h = max(1, round(css_h * scale))
    # Chrome sets platform-specific minimum window width; setContentsSize
    # requests the exact content dimensions independently of that minimum.
    outer_w = content_w + 26
    outer_h = content_h + 104
    left = x + max(0, (screen_w - outer_w) // 2)
    top = y + max(0, (screen_h - outer_h) // 2)
    return MobileWindow(
        css_w, css_h, content_w, content_h, scale,
        outer_w, outer_h, left, top,
    )


def mobile_window_args(viewport: dict, area=None) -> list[str]:
    """Chromium *window-only* flags; no zoom/deviceScaleFactor overrides."""
    p = mobile_window(viewport, area)
    return [
        f"--window-position={p.left},{p.top}",
        f"--window-size={p.outer_width},{p.outer_height}",
    ]


def fit_mobile_content(context, page, viewport: dict, dpr: float, area=None) -> MobileWindow:
    """Best-effort native window sizing using the public Chrome DevTools API.

    The viewport stays at the selected logical Android/iPhone dimensions.
    'scale' is a compositor presentation factor; DPR remains the real
    emulated device DPR. Unsupported CDP versions leave the original
    Playwright mobile context working rather than failing to launch.
    """
    plan = mobile_window(viewport, area)
    session = context.new_cdp_session(page)
    try:
        target = session.send("Browser.getWindowForTarget")
        window_id = target.get("windowId")
        if window_id is not None:
            try:
                session.send("Browser.setContentsSize", {
                    "windowId": window_id,
                    "width": plan.width,
                    "height": plan.height,
                })
                session.send("Browser.setWindowBounds", {
                    "windowId": window_id,
                    "bounds": {"left": plan.left, "top": plan.top},
                })
            except Exception:
                # Browser.setContentsSize is experimental on older Chromiums.
                session.send("Browser.setWindowBounds", {
                    "windowId": window_id,
                    "bounds": {
                        "left": plan.left, "top": plan.top,
                        "width": plan.outer_width, "height": plan.outer_height,
                    },
                })
        if plan.scale < 0.999:
            session.send("Emulation.setDeviceMetricsOverride", {
                "width": plan.css_width,
                "height": plan.css_height,
                "deviceScaleFactor": float(dpr),
                "mobile": True,
                "scale": plan.scale,
            })
    finally:
        session.detach()
    return plan

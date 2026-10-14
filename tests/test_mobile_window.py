"""Mobile presentation regression tests; no external site or user profiles.

The Android/iPhone CSS viewport must stay constant when the actual Chromium
window is resized/fitted onto desktops of different sizes.
"""
import math
from urllib.parse import quote

from Settings.mobile_window import mobile_window, mobile_window_args, fit_mobile_content


def test_mobile_presentation_dimensions_and_independent_dpr():
    mobile = {"width": 412, "height": 915}
    desktop = mobile_window(mobile, (0, 0, 1920, 1200))
    laptop = mobile_window(mobile, (0, 0, 1366, 768))
    small = mobile_window({"width": 390, "height": 844}, (0, 0, 1024, 720))
    assert desktop.css_width == laptop.css_width == 412
    assert desktop.css_height == laptop.css_height == 915
    assert desktop.scale == 1
    assert laptop.scale < 1
    assert laptop.height < 915
    assert math.isclose(laptop.width / laptop.height, 412 / 915, rel_tol=.003)
    assert small.outer_height <= 720 and small.outer_width <= 1024
    assert laptop.outer_height <= 768 and laptop.outer_width <= 1366
    assert mobile_window(mobile, (0, 0, 1366, 768), idx=3).left >= laptop.left
    assert len(mobile_window_args(mobile, (0, 0, 1366, 768), idx=1)) == 2


def test_headful_mobile_viewport_and_window_layout():
    """Chromium with Xvfb, same API as the Windows headed Chromium."""
    from playwright.sync_api import sync_playwright
    cases = [
        ("android", {"width": 412, "height": 915}, 2.75),
        ("iphone", {"width": 390, "height": 844}, 3.0),
    ]
    with sync_playwright() as pw:
        for _, viewport, dpr in cases:
            context = pw.chromium.launch_persistent_context(
                user_data_dir="", headless=False, args=mobile_window_args(viewport, area=(0,0,1024,768)),
                viewport=viewport, is_mobile=True, has_touch=True, device_scale_factor=dpr,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                html = (
                    '<meta name="viewport" content="width=device-width, initial-scale=1">'
                    '<style>html,body{margin:0;overflow-x:hidden}'
                    '#screen{height:150vh;width:100%;background:olive}</style>'
                    '<div id="screen"></div>'
                )
                page.goto("data:text/html," + quote(html), wait_until="domcontentloaded")
                before = page.evaluate("""() => ({
                    w: innerWidth, h: innerHeight, dpr: devicePixelRatio,
                    scroll: document.documentElement.scrollWidth,
                })""")
                assert before["w"] == viewport["width"]
                assert before["h"] == viewport["height"]
                assert abs(before["dpr"] - dpr) < 0.01
                plan, cdp = fit_mobile_content(
                    context, page, viewport, dpr,
                    area=(0, 0, 1024, 768),
                )
                after = page.evaluate("""() => ({
                    w: innerWidth, h: innerHeight, dpr: devicePixelRatio,
                    scroll: document.documentElement.scrollWidth,
                })""")
                assert after["w"] == before["w"]
                assert after["h"] == before["h"]
                assert abs(after["dpr"] - dpr) < .01
                assert after["scroll"] <= viewport["width"]
                assert plan.scale < 1.0
                assert plan.outer_height <= 768
                page.evaluate("scrollTo(0, document.body.scrollHeight)")
                assert page.evaluate("scrollY") > 0
                # The window fit is applied *before* real sites navigate.
                # A later page change must not reset the selected mobile viewport.
                page.goto("data:text/html," + quote(html), wait_until="domcontentloaded")
                after_navigation = page.evaluate("""() => ({
                    w: innerWidth, h: innerHeight, dpr: devicePixelRatio,
                    scroll: document.documentElement.scrollWidth,
                })""")
                assert after_navigation["w"] == viewport["width"]
                assert after_navigation["h"] == viewport["height"]
                assert abs(after_navigation["dpr"] - dpr) < .01
                assert after_navigation["scroll"] <= viewport["width"]
                bounds = cdp.send("Browser.getWindowForTarget")["bounds"]
                assert bounds["width"] <= 1024
                assert bounds["height"] <= 768 + 120  # compositor/platform decorations
            finally:
                context.close()

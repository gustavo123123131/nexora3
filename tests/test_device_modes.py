"""Safe regression tests for browser device modes and session isolation.

No account creation, proxy, bank or external site calls are performed.
"""
from pathlib import Path
import pytest
from Settings.device_modes import (
    MODE_LABELS, mode_label, resolve_mode, profile_directory, profile_mode
)
from Settings.Criar_Mae.settings.browser import make_config as config_mae
from Settings.Criar_Filha.settings.browser import make_config as config_filha


@pytest.mark.parametrize("make_config", [config_mae, config_filha])
def test_device_modes_without_crossing_profiles(make_config, tmp_path):
    for mode in ("desktop", "android", "iphone"):
        cfg = make_config(mode, 1)
        assert cfg.mode == mode
        if mode == "desktop":
            assert cfg.is_mobile is False
            assert cfg.has_touch is False
            assert "Android" not in cfg.user_agent
            assert "iPhone" not in cfg.user_agent
        else:
            assert cfg.is_mobile is True
            assert cfg.has_touch is True
            assert cfg.viewport["width"] <= 430
            if mode == "android":
                assert "Android" in cfg.user_agent
                assert "iPhone" not in cfg.user_agent
            else:
                assert "iPhone" in cfg.user_agent
                assert "Android" not in cfg.user_agent

    cfg1 = make_config("mixed", 1)
    cfg2 = make_config("mixed", 2)
    cfg3 = make_config("mixed", 3)
    assert [cfg1.mode, cfg2.mode, cfg3.mode] == ["android", "iphone", "android"]
    assert profile_directory(tmp_path, "mixed", 1) == profile_directory(tmp_path, "android", 1)
    assert profile_directory(tmp_path, "mixed", 2) == profile_directory(tmp_path, "iphone", 2)

    desktop = profile_directory(tmp_path, "desktop", 1)
    android = profile_directory(tmp_path, "android", 1)
    iphone = profile_directory(tmp_path, "iphone", 1)
    assert desktop == tmp_path / "profile_1"
    assert android == tmp_path / "android" / "profile_1"
    assert iphone == tmp_path / "iphone" / "profile_1"
    assert len({desktop, android, iphone}) == 3

    # Keep the original desktop directory contents untouched.
    desktop.mkdir()
    (desktop / "existing.txt").write_text("session")
    for directory in (android, iphone):
        directory.mkdir(parents=True)
        (directory / "marker.txt").write_text(directory.parent.name)
    assert (desktop / "existing.txt").read_text() == "session"
    assert (android / "marker.txt").read_text() == "android"
    assert (iphone / "marker.txt").read_text() == "iphone"


def test_unavailable_app_and_legacy_modes():
    assert mode_label("desktop") == MODE_LABELS["desktop"]
    assert mode_label("celular") == MODE_LABELS["mixed"]
    assert mode_label("app") == MODE_LABELS["app"]
    assert resolve_mode("celular") == "mixed"
    for bad in ("app", "Aplicativo Android (indisponível)"):
        with pytest.raises(ValueError, match="indisponível"):
            resolve_mode(bad)
        with pytest.raises(ValueError, match="indisponível"):
            config_mae(bad, 1)


@pytest.mark.parametrize("make_config", [config_mae, config_filha])
def test_chromium_device_launch_if_browser_installed(make_config, tmp_path):
    """Exercise real Chromium with data: URL only (no external transactions)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        for mode, idx in (("desktop", 1), ("android", 1), ("iphone", 1), ("mixed", 2)):
            cfg = make_config(mode, idx)
            profile_dir = profile_directory(tmp_path, mode, idx)
            profile_dir.parent.mkdir(parents=True, exist_ok=True)
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=True,
                user_agent=cfg.user_agent,
                viewport=cfg.viewport,
                device_scale_factor=cfg.device_scale_factor,
                is_mobile=cfg.is_mobile,
                has_touch=cfg.has_touch,
            )
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto("data:text/html,<title>local test</title>")
                result = page.evaluate("() => ({ ua: navigator.userAgent, touch: navigator.maxTouchPoints })")
                assert result["ua"] == cfg.user_agent
                if cfg.has_touch:
                    assert result["touch"] > 0
                assert page.title() == "local test"
            finally:
                ctx.close()

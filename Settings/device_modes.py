"""Visible device modes, stable IDs and backwards-compatible mode selection.

These modes emulate browser viewports through Playwright Chromium; they do not
run native iOS Safari or a native Android application.
"""
MODE_LABELS = {
    "android": "Mobile — Somente Android",
    "iphone": "Mobile — Somente iPhone",
    "mixed": "Mobile — Android e iPhone (alternados por instância)",
    "app": "Aplicativo Android (indisponível)",
    "desktop": "Computador",
}

OLD_MODES = {"celular": "mixed"}
NATIVE_ANDROID_APP_ERROR = (
    "Aplicativo Android indisponível: este projeto utiliza Chromium/Playwright, "
    "não possui emulador nem execução de aplicativo Android nativo."
)


def mode_label(mode: str) -> str:
    """Display the stored legacy mode without changing existing settings."""
    value = str(mode or "desktop").strip().lower()
    return MODE_LABELS.get(OLD_MODES.get(value, value), MODE_LABELS["desktop"])


def resolve_mode(value: str) -> str:
    """Return a stable mode ID, rejecting unsupported app emulation."""
    raw = str(value or "").strip()
    inverse = {label: key for key, label in MODE_LABELS.items()}
    name = inverse.get(raw, raw.lower())
    name = OLD_MODES.get(name, name)
    if name == "app":
        raise ValueError(NATIVE_ANDROID_APP_ERROR)
    if name not in ("desktop", "android", "iphone", "mixed"):
        raise ValueError("Modo de dispositivo inválido: " + raw)
    return name


def profile_mode(mode: str, idx: int) -> str:
    """Stable, predictable Android/iPhone allocation for a mixed group."""
    mode = resolve_mode(mode)
    if mode == "mixed":
        return "android" if int(idx) % 2 else "iphone"
    return mode


def profile_directory(base, mode: str, idx: int):
    """Desktop legacy profiles keep their paths; new mobile modes never share data."""
    selected = resolve_mode(mode)
    name = profile_mode(selected, idx)
    if name == "desktop":
        return base / f"profile_{idx}"
    # A combined choice has its own sessions, separate from the single mode.
    if selected == "mixed":
        return base / "mixed" / name / f"profile_{idx}"
    return base / name / f"profile_{idx}"

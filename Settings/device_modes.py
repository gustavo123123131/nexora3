"""Visible device modes, stable IDs and backwards-compatible mode selection.

These modes emulate browser viewports through Playwright Chromium; they do not
run native iOS Safari or a native Android application.
"""
MODE_LABELS = {
    "android": "Mobile — Somente Android",
    "iphone": "Mobile — Somente iPhone",
    "mixed": "Mobile — Android e iPhone",
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


def account_profile_directory(base, origin: str, account_id: int):
    """Private Chromium user-data directory, keyed by account, not window idx.

    Old profile_1/profile_2 paths are intentionally not reused because they
    contain browsing data from unrelated historic launches.
    """
    if origin not in ("mae", "filha") or type(account_id) is not int or account_id < 1:
        raise ValueError("Identificador de conta inválido.")
    return base / "accounts" / f"account_{account_id}"


def stored_profile_directory(base, profile: dict):
    """Resolve a persisted Chromium profile without assigning a new path.

    For current accounts require the exact historical account_N location.
    Legacy main-branch paths must stay beneath their original __profiles root.
    """
    from pathlib import Path
    root = Path(base).resolve()
    origin = profile["origin"]
    if origin not in ("mae", "filha"):
        raise ValueError("Origem da conta inválida.")
    stored = profile.get("profilePath")
    if not isinstance(stored, str) or not stored.strip():
        raise ValueError("Diretório original não foi registrado.")
    path = Path(stored)
    if not path.is_absolute():
        raise ValueError("Caminho do perfil deve ser absoluto.")
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("O perfil está fora do diretório reservado.")
    if path.is_symlink():
        raise ValueError("O perfil não pode ser um link simbólico.")
    if not profile.get("legacySource"):
        expected = account_profile_directory(base, origin, int(profile["id"])).resolve()
        if expected != resolved:
            raise ValueError("O caminho persistido não pertence a esta conta.")
    return path

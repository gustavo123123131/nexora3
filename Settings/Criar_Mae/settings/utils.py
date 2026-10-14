import json
import random
import re
from pathlib import Path

# --- Caminhos e Constantes ---
# BASE_DIR agora é a pasta "settings" onde este arquivo está
BASE_DIR = Path(__file__).resolve().parent

# PROFILES_DIR: Vamos usar .parent para voltar uma pasta e salvar na raiz do projeto
# Assim a pasta 'settings' não fica cheia de lixo de cache
PROFILES_DIR = BASE_DIR.parent / "__profiles"

# SETTINGS_PATH continua dentro da pasta settings
SETTINGS_PATH = BASE_DIR / "settings.json"

# Os arquivos de configuração estão na mesma pasta (settings), então BASE_DIR funciona
DESKTOP_UA_PATH = BASE_DIR / "__desktop_userAgent.json"
MOBILE_UA_PATH  = BASE_DIR / "__mobile_userAgent.json"
DESKTOP_GPU_PATH = BASE_DIR / "__desktop_vendors.json"
MOBILE_GPU_PATH  = BASE_DIR / "__mobile_vendors.json"


# --- Funções de JSON ---
def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_json(path: Path, data: dict):
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# --- Funções de Settings ---
def load_settings() -> dict:
    return load_json(SETTINGS_PATH)

def write_settings(data: dict):
    save_json(SETTINGS_PATH, data)


# --- Random Pickers ---
def pick_desktop_ua() -> str:
    data = load_json(DESKTOP_UA_PATH)
    default = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    return random.choice(data.get("userAgents", [default]))

def pick_mobile_ua() -> str:
    data = load_json(MOBILE_UA_PATH)
    default = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    return random.choice(data.get("userAgents", [default]))

def pick_desktop_gpu() -> tuple[str, str]:
    data = load_json(DESKTOP_GPU_PATH)
    items = data.get("GPUTiers", {}).get("Desktop", [])
    if not items:
        return "Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1050 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)"
    item = random.choice(items)
    return item["vendor"], item["renderer"]

def pick_mobile_gpu() -> tuple[str, str]:
    """
    Picker antigo (mantido por compatibilidade).
    ATENÇÃO: isso pode gerar Frankenstein se usado junto com UA aleatório.
    Prefira usar pick_android_profile() quando quiser coerência por modelo.
    """
    data = load_json(MOBILE_GPU_PATH)
    items = data.get("GPUTiers", {}).get("Android", [])
    if not items:
        return "Qualcomm", "Adreno (TM) 640"
    item = random.choice(items)
    return item["vendor"], item["renderer"]


# =====================================================
# NOVO: helpers para evitar "Frankenstein" no Android
# =====================================================

def extract_android_model_from_ua(ua: str) -> str:
    """
    Extrai o modelo do aparelho a partir de UAs Android do tipo:
    Mozilla/5.0 (Linux; Android 14; Pixel 6) ...
    Retorna: "Pixel 6"
    """
    m = re.search(r"Android [^;]+;\s*([^;\)]+)", ua)
    return m.group(1).strip() if m else ""

def pick_android_ua(max_tries: int = 50) -> str:
    """
    Garante UA Android (se cair em iPhone, tenta de novo).
    """
    for _ in range(max_tries):
        ua = pick_mobile_ua()
        if "Android" in ua:
            return ua
    # fallback (se o arquivo tiver pouco Android por algum motivo)
    return pick_mobile_ua()

def pick_android_gpu_by_model(model_name: str) -> tuple[str, str]:
    """
    Busca GPU coerente pelo modelo (usando __mobile_vendors.json -> GPUTiers.Android).
    Se não achar match, faz fallback para um Android aleatório (ainda plausível).
    """
    data = load_json(MOBILE_GPU_PATH)
    items = data.get("GPUTiers", {}).get("Android", [])
    if not items:
        return "Qualcomm", "Adreno (TM) 640"

    m = (model_name or "").lower()

    # match por "contains"
    for it in items:
        it_model = (it.get("model") or "").lower()
        if it_model and it_model in m:
            return it.get("vendor", "Qualcomm"), it.get("renderer", "Adreno (TM) 640")

    # fallback coerente (não casado, mas realista)
    it = random.choice(items)
    return it.get("vendor", "Qualcomm"), it.get("renderer", "Adreno (TM) 640")

def pick_android_profile() -> tuple[str, str, str]:
    """
    Retorna um perfil Android coerente:
    (ua_android, webgl_vendor, webgl_renderer)
    """
    ua = pick_android_ua()
    model = extract_android_model_from_ua(ua)
    v, r = pick_android_gpu_by_model(model)
    return ua, v, r
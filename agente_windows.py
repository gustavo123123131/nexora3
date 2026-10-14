"""Run the original Lixo CPA Python automation on this Windows computer.

The web page in a private GitHub Codespace can access it through the user's
own browser. No remote connections to the computer are accepted: this server
only binds 127.0.0.1. The exact panel origin and a per-launch key are required.
"""
import argparse
import os
import secrets
from urllib.parse import urlsplit


def validate_panel_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or not hostname.endswith(".app.github.dev")
        or not hostname.split(".")[0].endswith("-8765")
        or not hostname.startswith(tuple("abcdefghijklmnopqrstuvwxyz0123456789"))
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Cole o endereco HTTPS da porta 8765 do GitHub Codespaces, "
            "por exemplo https://NOME-8765.app.github.dev"
        )
    return f"https://{hostname}"


def main():
    parser = argparse.ArgumentParser(description="Agente Lixo CPA: Chromium no Windows")
    parser.add_argument("--painel", help="URL HTTPS da porta 8765 do Codespaces")
    parser.add_argument("--porta", type=int, default=8767)
    args = parser.parse_args()

    print("Lixo CPA - agente local do Chromium no Windows")
    print("A pagina web continuará no Codespaces, mas o Playwright rodará aqui.")
    raw = args.painel or input("Cole o link HTTPS da porta 8765 do Codespaces: ")
    try:
        origin = validate_panel_url(raw)
    except (ValueError, AttributeError) as exc:
        parser.error(str(exc))

    key = secrets.token_urlsafe(24)
    os.environ["LIXOCPA_AGENT_ORIGIN"] = origin
    os.environ["LIXOCPA_AGENT_KEY"] = key

    # All original automation modules are imported by servidor.py on demand.
    # Do not import original Tk widgets or run a duplicate browser driver here.
    from servidor import app
    import uvicorn

    print("\n" + "=" * 64)
    print("AGENTE PRONTO - deixe esta janela aberta enquanto usar o painel.")
    print("Cole o codigo de pareamento no painel do GitHub Codespaces:")
    print(f"\n{key}\n")
    print(f"Endereco de emergencia: http://127.0.0.1:{args.porta}")
    print("O Chromium abrira normalmente na area de trabalho do Windows.")
    print("=" * 64 + "\n", flush=True)
    # HTTP polling happens several times per second. Show only warnings/errors,
    # not a line for each GET/POST. Pairing details above stay visible.
    uvicorn.run(app, host="127.0.0.1", port=args.porta,
                log_level="warning", access_log=False)


if __name__ == "__main__":
    main()

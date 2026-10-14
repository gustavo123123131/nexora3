import time

try:
    from .popup import close_all_popups
except Exception:
    from popup import close_all_popups


DEFAULT_GAME_CATEGORY_ID = 3
DEFAULT_PLATFORM_ID = 13


def _log_message(log, message: str):
    if callable(log):
        try:
            log(message)
            return
        except Exception:
            pass
    print(message)


def _build_search_page_url(page, game_category_id: int = DEFAULT_GAME_CATEGORY_ID, platform_id: int = DEFAULT_PLATFORM_ID) -> str:
    current_url = page.url
    base_url = "/".join(current_url.split("/")[:3])
    return f"{base_url}/home/subgame?gameCategoryId={int(game_category_id)}&platformId={int(platform_id)}"


def _find_game_page_with_iframe(page, timeout_ms=15000, extra_tabs_to_check=2):
    context = page.context
    start = time.time()

    while (time.time() - start) * 1000 < timeout_ms:
        try:
            pages = list(context.pages)

            candidate_pages = []
            if pages:
                candidate_pages.append(pages[0])
                candidate_pages.extend(pages[1:1 + extra_tabs_to_check])

            for p in candidate_pages:
                try:
                    if p.locator("iframe").count() > 0:
                        return p
                except Exception:
                    continue
        except Exception:
            pass

        time.sleep(0.5)

    return None


def search_and_open_game(page, idx, game_name: str, game_id: str, log=None):
    game_name = (game_name or "").strip()
    game_id = str(game_id or "").strip()

    if not game_name:
        raise ValueError("Nome do jogo não informado.")
    if not game_id:
        raise ValueError("ID do jogo não informado.")

    _log_message(log, f"[#{idx}] --- BUSCANDO {game_name.upper()} ({game_id}) ---")

    search_page_url = _build_search_page_url(page)

    while True:
        try:
            _log_message(log, f"[#{idx}] Navegando para a busca do jogo...")
            page.goto(search_page_url, wait_until="domcontentloaded")

            close_all_popups(page, idx)

            sel_input = 'input.ui-input__input[placeholder="Pesquisar"]'
            page.wait_for_selector(sel_input, state="visible", timeout=10000)
            time.sleep(1)
            page.fill(sel_input, "")
            page.fill(sel_input, game_name)

            page.wait_for_selector("div.poster-image", timeout=10000)
            time.sleep(2)

            game_selector = f'div.poster-image[style*="{game_id}"]'
            game_card = page.locator(game_selector).first

            if game_card.count() == 0:
                _log_message(log, f"[#{idx}] ❌ Jogo {game_name} ({game_id}) não encontrado. Recarregando...")
                page.reload()
                continue

            _log_message(log, f"[#{idx}] 🎯 Jogo encontrado: {game_name} ({game_id}).")
            time.sleep(2)
            close_all_popups(page, idx)

            game_card.scroll_into_view_if_needed()

            try:
                game_card.click(timeout=5000)
            except Exception:
                _log_message(log, f"[#{idx}] Clique normal falhou. Tentando via JS...")
                game_card.evaluate("node => node.click()")

            try:
                _log_message(log, f"[#{idx}] ⏳ Verificando carregamento do jogo nas guias abertas...")

                game_page = _find_game_page_with_iframe(page, timeout_ms=15000, extra_tabs_to_check=2)

                if not game_page:
                    raise Exception("Nenhuma das guias monitoradas carregou iframe do jogo.")

                if game_page != page:
                    _log_message(log, f"[#{idx}] ✅ Jogo abriu em nova guia.")
                else:
                    _log_message(log, f"[#{idx}] ✅ Jogo abriu na guia principal.")

                time.sleep(3)
                return game_page

            except Exception:
                _log_message(log, f"[#{idx}] ⚠️ Clique executado mas o jogo não abriu. Recarregando...")
                page.reload()
                continue

        except Exception as e:
            _log_message(log, f"[#{idx}] ⚠️ Erro geral ao abrir jogo: {e}")
            time.sleep(3)
            page.reload()
            continue


def search_and_open_fortune_snake(page, idx, log=None):
    _log_message(log, f"[#{idx}] ℹ️ Fortune Snake desativado.")
    return
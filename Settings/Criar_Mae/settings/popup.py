import time
from playwright.sync_api import Page

from .utils import load_settings

AREA_PIX_SCOPE = "mae"


def _scope_key(name: str) -> str:
    return f"{AREA_PIX_SCOPE}_{name}"


def popup_auto_close_enabled() -> bool:
    try:
        st = load_settings() or {}
        return bool(st.get(_scope_key("auto_close_popups"), False))
    except Exception:
        return False


def _remove_notice_user_tip(page: Page) -> bool:
    """
    Fecha banner: div.notic-user-tip-warp
    Sempre tenta Cancel; se falhar, remove via JS.
    """
    wrap_sel = "div.notic-user-tip-warp"
    cancel_sel = "div.notic-user-tip-warp span.web-push-tip-cancel"

    try:
        wrap = page.locator(wrap_sel).first
        if wrap.count() <= 0 or not wrap.is_visible():
            return False
    except Exception:
        return False

    try:
        btn = page.locator(cancel_sel).first
        if btn.count() > 0 and btn.is_visible():
            try:
                btn.scroll_into_view_if_needed(timeout=1200)
            except Exception:
                pass

            try:
                btn.click(force=True, timeout=2000)
            except Exception:
                try:
                    btn.evaluate("node => node.click()")
                except Exception:
                    pass

            time.sleep(0.3)
            return True
    except Exception:
        pass

    try:
        page.evaluate(
            """() => {
                document.querySelectorAll('div.notic-user-tip-warp').forEach(el => el.remove());
            }"""
        )
        time.sleep(0.2)
        return True
    except Exception:
        return False


def _number_keyboard_visible(page: Page) -> bool:
    selectors = [
        ".ui-number-keyboard:visible",
        ".ui-number-keyboard__body:visible",
    ]

    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return True
        except Exception:
            pass

    return False


def _real_popup_visible(page: Page) -> bool:
    selectors = [
        "div.notic-user-tip-warp:visible",
        "div.ui-dialog:visible",
        "div.ui-popup.ui-popup--center:visible",
        "div.red-pocket:visible",
        "div[class*='_wrap_']:has(div.red-pocket):visible",
        "div[class*='_wrap_']:has-text('Bônus de login'):visible",
        "div[class*='_wrap_']:has-text('Abrir bolsa'):visible",
        "div[class*='_wrap_']:has-text('bolsa da sorte'):visible",
        "div[class*='_close_'] span.close-style-type2.not-red-pocket:visible",
        "span.close-style-type2.not-red-pocket:visible",
        "div[class*='_close_'] span.close-style-type2:visible",
        "span.close-style-type2:visible",
        "i.ui-dialog-close-box__icon:visible",
        ".ui-dialog-close-box:visible",
        "button.ui-dialog__confirm:visible",
    ]

    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return True
        except Exception:
            pass

    return False


def close_all_popups(page: Page, idx: int):
    print(f"[#{idx}] Verificando popups bloqueadores...")

    close_selectors = [
        # X novo
        "div[class*='_close_'] span.close-style-type2.not-red-pocket:visible",
        "span.close-style-type2.not-red-pocket:visible",
        "div[class*='_close_'] span.close-style-type2:visible",
        "span.close-style-type2:visible",

        # fallbacks de X
        "div[class*='_close_'] span:visible",
        "i.ui-dialog-close-box__icon:visible",
        ".ui-dialog-close-box:visible",
    ]

    confirm_selectors = [
        "button.ui-dialog__confirm:visible",
        "button.ui-dialog__confirm:has(span.ui-button__text:text-is('Confirmar')):visible",
        "button:has(span.ui-button__text:text-is('Confirmar')):visible",
        "button:has-text('Confirmar'):visible",
    ]

    for _ in range(12):
        found_popup = False
        clicked_close = False

        # 1) banner web-push
        try:
            if _remove_notice_user_tip(page):
                found_popup = True
        except Exception:
            pass

        # 2) tenta fechar no X
        for sel in close_selectors:
            try:
                locs = page.locator(sel)
                cnt = locs.count()
                if cnt <= 0:
                    continue

                for i in range(min(cnt, 8)):
                    btn = locs.nth(i)

                    try:
                        if not btn.is_visible():
                            continue
                    except Exception:
                        continue

                    try:
                        btn.scroll_into_view_if_needed(timeout=1200)
                    except Exception:
                        pass

                    try:
                        btn.click(force=True, timeout=2000)
                        time.sleep(0.2)
                        found_popup = True
                        clicked_close = True
                        break
                    except Exception:
                        try:
                            btn.evaluate("node => node.click()")
                            time.sleep(0.2)
                            found_popup = True
                            clicked_close = True
                            break
                        except Exception:
                            pass

                if clicked_close:
                    break
            except Exception:
                pass

        # 3) se fechou no X, tenta Confirmar logo em seguida
        if clicked_close:
            for _wait in range(6):
                clicked_confirm = False

                for sel in confirm_selectors:
                    try:
                        locs = page.locator(sel)
                        cnt = locs.count()
                        if cnt <= 0:
                            continue

                        for i in range(min(cnt, 6)):
                            btn = locs.nth(i)

                            try:
                                if not btn.is_visible():
                                    continue
                            except Exception:
                                continue

                            try:
                                btn.scroll_into_view_if_needed(timeout=1200)
                            except Exception:
                                pass

                            try:
                                btn.click(force=True, timeout=2000)
                                time.sleep(0.2)
                                found_popup = True
                                clicked_confirm = True
                                break
                            except Exception:
                                try:
                                    btn.evaluate("node => node.click()")
                                    time.sleep(0.2)
                                    found_popup = True
                                    clicked_confirm = True
                                    break
                                except Exception:
                                    pass

                        if clicked_confirm:
                            break
                    except Exception:
                        pass

                if clicked_confirm:
                    break

                time.sleep(0.25)

        # 4) se o confirmar aparecer sozinho
        if not clicked_close and _real_popup_visible(page):
            for sel in confirm_selectors:
                try:
                    locs = page.locator(sel)
                    cnt = locs.count()
                    if cnt <= 0:
                        continue

                    clicked_confirm = False

                    for i in range(min(cnt, 6)):
                        btn = locs.nth(i)

                        try:
                            if not btn.is_visible():
                                continue
                        except Exception:
                            continue

                        try:
                            btn.scroll_into_view_if_needed(timeout=1200)
                        except Exception:
                            pass

                        try:
                            btn.click(force=True, timeout=2000)
                            time.sleep(0.2)
                            found_popup = True
                            clicked_confirm = True
                            break
                        except Exception:
                            try:
                                btn.evaluate("node => node.click()")
                                time.sleep(0.2)
                                found_popup = True
                                clicked_confirm = True
                                break
                            except Exception:
                                pass

                    if clicked_confirm:
                        break
                except Exception:
                    pass

        # 5) overlay
        try:
            if (
                not _number_keyboard_visible(page)
                and _real_popup_visible(page)
                and page.is_visible(".ui-overlay")
            ):
                try:
                    page.mouse.click(10, 10)
                    time.sleep(0.4)
                    found_popup = True
                except Exception:
                    pass
        except Exception:
            pass

        if not found_popup:
            break

    print(f"[#{idx}] Tela limpa.")


def sweep_popups_if_enabled(page: Page, idx: int) -> bool:
    if not popup_auto_close_enabled():
        return False

    try:
        close_all_popups(page, idx)
        return True
    except Exception:
        return False
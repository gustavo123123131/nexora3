"""Real Chromium / real Playwright request-event integration test.

ALL traffic is intercepted by Playwright routes and served from test memory.
No packets are sent to 5gbet or the CDN. This verifies browser event timing,
HTTPS apex-to-www redirects, and the actual x-object-id header observation.
It does NOT verify production authentication or prove the external site works.
"""
import json

import pytest
from playwright.sync_api import sync_playwright

from Settings.platform_identity import (
    PlatformIdentityObserver, FiveGBetIdentityContract,
    provider_identity_contract,
)

ROOT = "https://5gbet.com/"
WWW = "https://www.5gbet.com/"
API = "https://cdnapiif.ppa5g.net/hall/api/message/list/all?siteCode=296"


def _html(uid):
    # This script imitates a first-paint request without opening a Profile.
    # JavaScript sends only the test UID, not a cookie or token.
    raw = json.dumps({"uid": uid, "browserLanguage": "en", "init": {}})
    return (
        "<!doctype html><html><head><meta charset='utf-8'></head>"
        "<body><p>Test-only registration site</p><script>"
        "window.apiDone=false;"
        "document.addEventListener('DOMContentLoaded', async () => {"
        "try { await fetch(" + json.dumps(API) + ", {"
        "'headers':{'X-Object-ID':" + json.dumps(raw) + "}}); }"
        "catch (_e) {} finally { window.apiDone=true; }"
        "});</script></body></html>"
    )


def _route(route, uid):
    req = route.request
    url = req.url
    if url == ROOT:
        route.fulfill(status=302, headers={"location": WWW})
    elif url == WWW:
        route.fulfill(status=200, content_type="text/html", body=_html(uid))
    elif url.startswith("https://cdnapiif.ppa5g.net/"):
        headers = {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "x-object-id",
        }
        if req.method == "OPTIONS":
            route.fulfill(status=204, headers=headers)
        elif req.method == "GET" and url.startswith(API):
            # Error from the API is NOT proof of authentication.
            route.fulfill(
                status=200, headers={**headers, "Content-Type": "application/json"},
                body='{"err_code":41000}',
            )
        else:
            route.abort()
    else:
        route.abort()


def _profile(number):
    return {"id": number, "accountId": "local-" + str(number),
            "runId": "run-" + str(number), "username": "person" + str(number)}


def test_real_chromium_catches_initial_navigation_header_and_diagnoses_rejection():
    logs = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=True)
        context.route("**/*", lambda route: _route(route, 82193475))
        page = context.new_page()
        obs = PlatformIdentityObserver(
            page, logs.append,
            _profile(79),
            expected_origin=ROOT,  # No www, followed by real redirect.
        )
        try:
            obs.start()  # BEFORE page.goto
            assert isinstance(obs.contract, FiveGBetIdentityContract)
            assert obs._request_route_seen is False
            page.goto(ROOT, wait_until="domcontentloaded")
            page.wait_for_function("window.apiDone===true", timeout=9000)
            obs.after_registration_click()
            assert page.url == WWW
            assert obs._request_route_seen
            assert obs._header_seen_uid == "82193475"
            assert obs.identify(wait_seconds=0) is None
        finally:
            obs.close()
            browser.close()
    joined = "\n".join(logs)
    assert "Adaptador 5gbet ativo" in joined
    assert "Origem após navegação: oficial" in joined
    assert "Requisição permitida da API observada" in joined
    assert "x-object-id.uid observado" in joined
    assert "Resposta da API contém erro" in joined
    assert "ID observado, não verificado" in joined
    assert "82193475" not in joined
    assert "41000" not in joined


def test_real_chromium_separate_contexts_never_share_request_headers():
    logs_a, logs_b = [], []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        contexts = [
            browser.new_context(ignore_https_errors=True),
            browser.new_context(ignore_https_errors=True),
        ]
        for ctx, uid in zip(contexts, (9342156, 9342157)):
            ctx.route("**/*", lambda route, uid=uid: _route(route, uid))
        pages = [ctx.new_page() for ctx in contexts]
        obs = [
            PlatformIdentityObserver(pages[0], logs_a.append, _profile(79),
                                     expected_origin=ROOT),
            PlatformIdentityObserver(pages[1], logs_b.append, _profile(80),
                                     expected_origin=ROOT),
        ]
        try:
            for watcher in obs:
                watcher.start()
            for page in pages:
                page.goto(ROOT, wait_until="domcontentloaded")
                page.wait_for_function("window.apiDone===true", timeout=9000)
            for watcher in obs:
                watcher.after_registration_click()
                assert watcher.identify(wait_seconds=0) is None
            assert obs[0]._header_seen_uid == "9342156"
            assert obs[1]._header_seen_uid == "9342157"
            assert not obs[0]._header_conflict and not obs[1]._header_conflict
        finally:
            for watcher in obs:
                watcher.close()
            browser.close()

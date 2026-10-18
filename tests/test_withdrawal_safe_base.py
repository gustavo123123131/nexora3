"""Read-only withdrawal integration tests: never place a financial order."""
from types import SimpleNamespace
from uuid import uuid4

from nexora_accounts.withdrawal_operations import WithdrawalStore


def test_withdrawal_preflight_is_persisted_and_isolated(tmp_path):
    run = str(uuid4())
    ids = {1: str(uuid4()), 2: str(uuid4())}
    profiles = SimpleNamespace(public_detail=lambda i: {
        "accountId": ids[i], "lastSessionId": run,
    })
    store = WithdrawalStore(tmp_path / "withdrawals.json", profiles=profiles)
    first = store.reserve(profile_id=1, account_id=ids[1], run_id=run)
    assert first["reused"] is False
    assert first["operation"]["status"] == "queued"
    assert store.reserve(profile_id=1, account_id=ids[1], run_id=run)["reused"]
    other = store.reserve(profile_id=2, account_id=ids[2], run_id=run)
    assert other["reused"] is False
    finished = store.finish(first["operation"]["operationId"],
        profile_id=1, account_id=ids[1], run_id=run,
        result={"stage": "contract",
                "message": "O valor realmente sacável e os campos de envio ainda não foram validados no domínio real.",
                "trace": [{"stage": "withdrawInfoV2",
                           "event": "api_accepted", "httpStatus": 200,
                           "providerCode": 1,
                           "secret": "do-not-persist"}]})
    assert finished["status"] == "blocked"
    assert finished["submissionSent"] is False
    assert "do-not-persist" not in (tmp_path / "withdrawals.json").read_text()
    assert len(store.list(2)["operations"]) == 1
    assert len(store.list(1)["operations"]) == 1
    again = WithdrawalStore(tmp_path / "withdrawals.json", profiles=profiles)
    assert again.list(1)["operations"][0]["status"] == "blocked"
    assert again.reserve(profile_id=1, account_id=ids[1], run_id=run)["reused"] is False


def test_read_only_browser_never_requests_balance_or_sends_cash():
    from playwright.sync_api import sync_playwright
    from nexora_accounts.withdrawal_browser import inspect_withdrawal_preflight
    page_url = "https://www.5gbet.com/"
    sdk_url = page_url + "commonChunk.mock.js"
    sdk = """
      window.requests = [];
      export const n = {request:async ({url}) => {
        window.requests.push(url);
        return {status:200,data:{code:1,data:{
          hasWithdrawPasswd:true,
          accounts:[{id:'remote-destination'}]
        }}};
      }};
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            def mocked_route(route):
                if route.request.url == page_url:
                    route.fulfill(status=200, content_type="text/html",
                                  body="<script type='module' src='/commonChunk.mock.js'></script>")
                elif route.request.url == sdk_url:
                    route.fulfill(status=200, content_type="text/javascript",
                                  body=sdk)
                else:
                    route.abort()
            context.route("**/*", mocked_route)
            page = context.new_page()
            page.goto(page_url, wait_until="domcontentloaded")
            result = inspect_withdrawal_preflight(page)
            assert result["stage"] == "contract", result
            assert page.evaluate("window.requests") == [
                "/api/finance/certify/withdrawInfoV2"
            ]
        finally:
            browser.close()

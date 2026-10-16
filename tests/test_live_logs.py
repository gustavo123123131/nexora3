"""Global live logs merge Mãe and Filha events without exposing credentials."""
from fastapi.testclient import TestClient

import servidor
from webui import live_logs


def test_live_log_feed_incremental_order_and_redaction():
    with live_logs._LOCK:
        live_logs._EVENTS.clear()
        live_logs._SEQUENCE = 0
    live_logs.publish("Mãe", "[#1] Browser abriu\n[#1] Proxy pronta")
    live_logs.publish("Filha", "[#2] Navegador pronto")
    first = live_logs.read(after=0, limit=2)
    assert len(first["events"]) == 2
    assert [row["source"] for row in first["events"]] == ["Mãe", "Mãe"]
    assert first["hasMore"] is True
    second = live_logs.read(after=first["cursor"])
    assert [row["source"] for row in second["events"]] == ["Filha"]
    assert second["hasMore"] is False
    assert live_logs.read(after=second["cursor"])["events"] == []

    live_logs.publish("Mãe",
        "senha=abc123 token:secreto https://user:pass@example.test/profile CPF 52998224725")
    redacted = live_logs.read(after=second["cursor"])["events"][0]["text"]
    for secret in ("abc123", "secreto", "user:pass", "52998224725"):
        assert secret not in redacted
    assert "[oculto]" in redacted
    assert "[numero-oculto]" in redacted


def test_authenticated_local_log_endpoint_and_browser_source():
    with TestClient(servidor.app) as client:
        state = client.get("/api/state")
        assert state.status_code == 200
        before = live_logs.read()["cursor"]
        # Real existing GUI logger must feed the new panel.
        root = servidor.init_original_ui()
        todo = [root]
        tabs = []
        while todo:
            item = todo.pop()
            if type(item).__name__ in ("MaeTab", "FilhaTab"):
                tabs.append(item)
            todo.extend(getattr(item, "children", ()))
        assert len(tabs) == 2
        for tab in tabs:
            tab._log("[Teste] mensagem global")
        reply = client.get("/api/live-logs", params={"after": before})
        assert reply.status_code == 200
        events = reply.json()["events"]
        sources = {e["source"] for e in events if e["text"] == "[Teste] mensagem global"}
        assert sources == {"Mãe", "Filha"}
        # Original widgets still contain messages for desktop compatibility,
        # while web state snapshots omit the duplicated full log history.
        assert all("[Teste] mensagem global" in tab.log_text._text for tab in tabs)
        tree = client.get("/api/state").json()["tree"]
        def walk(node):
            yield node
            for child in node.get("children", []):
                yield from walk(child)
        assert len([node for node in walk(tree) if node["type"] == "textbox"]) >= 2
        assert all(node["options"].get("value") == "" for node in walk(tree)
                   if node["type"] == "textbox")
        assert client.get("/api/live-logs",
                          headers={"Host": "unauthorized.example"}).status_code == 403

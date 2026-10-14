"""Smoke tests. They deliberately do not launch account/bank automations."""
import time
from fastapi.testclient import TestClient
import servidor


def walk(node):
    yield node
    for ch in node.get('children', []):
        yield from walk(ch)


def test_localhost_ui_and_events():
    with TestClient(servidor.app) as client:
        for route in ['/', '/static/app.js', '/static/style.css']:
            assert client.get(route).status_code == 200
        data = client.get('/api/state').json()
        assert len(data['server_version']) > 8
        assert len(data['csrf_token']) > 32
        headers = {'X-LixoCPA-CSRF': data['csrf_token']}
        tabs = data['tree']['children'][0]['children']
        assert [t['options']['_tab_label'] for t in tabs] == ['Criar Mãe', 'Criar Filha', 'Área Pix']
        assert len(list(walk(data['tree']))) >= 150
        assert client.get('/api/state', headers={'Host': 'attacker.example'}).status_code == 403
        assert client.post('/api/event', json={'id': '1', 'event': 'click'},
                           headers={'Origin': 'http://attacker.example'}).status_code == 403
        assert client.post('/api/event', json={'id': '1', 'event': 'click'}).status_code == 403
        assert client.post('/api/event', json={'id': '1', 'event': 'click'},
                           headers={'X-LixoCPA-CSRF': 'incorrect'}).status_code == 403
        assert client.post('/api/dialog', json={'id': 1, 'value': False}).status_code == 403
        assert client.post('/api/dialog', json={'id': -10, 'value': False},
                           headers=headers).status_code == 404
        assert client.post('/api/event', json={'id': '1', 'event': 'delete'},
                           headers=headers).status_code == 400
        assert client.post('/api/event', json={'id': 'not-found', 'event': 'click'},
                           headers=headers).status_code == 404

        # Form inputs continue using the original Python StringVar.
        mae = tabs[0]
        input_url = next(n for n in walk(mae) if n['type'] == 'entry' and
                         n['options'].get('value','').startswith('http'))
        widget = servidor.ctk.get_widget(input_url['id'])
        previous = widget.get()
        try:
            assert client.post('/api/event', json={'id': input_url['id'],
                               'event': 'change', 'value': 'https://example.test'},
                               headers=headers).status_code == 200
            assert widget.get() == 'https://example.test'
            refreshed = client.get('/api/state').json()['tree']
            assert next(n for n in walk(refreshed) if n['id'] == input_url['id'])['options']['value'] == 'https://example.test'
        finally:
            widget.set(previous)

        # Original CTk callback opens the instance selector.
        button = next(n for n in walk(mae) if n['type'] == 'button' and
                      n['options'].get('text') == 'Selecionar instâncias')
        assert client.post('/api/event', json={'id': button['id'], 'event': 'click'},
                           headers=headers).status_code == 200
        modal_id = None
        for _ in range(35):
            all_nodes = list(walk(client.get('/api/state').json()['tree']))
            modals = [n for n in all_nodes if n['type'] == 'modal']
            if modals:
                modal_id = modals[0]['id']
                cancel = next(n for n in walk(modals[0]) if n['type'] == 'button' and
                              n['options'].get('text') == 'Cancelar')
                break
            time.sleep(.025)
        assert modal_id is not None
        assert client.post('/api/event', json={'id': cancel['id'], 'event': 'click'},
                           headers=headers).status_code == 200
        for _ in range(35):
            if not any(n['type'] == 'modal' for n in
                       walk(client.get('/api/state').json()['tree'])):
                break
            time.sleep(.025)
        assert not any(n['type'] == 'modal' for n in
                       walk(client.get('/api/state').json()['tree']))


def test_codespaces_private_forwarded_origin(monkeypatch):
    monkeypatch.setenv('CODESPACES', 'true')
    monkeypatch.setenv('CODESPACE_NAME', 'exemplo-seguro-123')
    monkeypatch.setenv('GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN', 'app.github.dev')
    url = 'https://exemplo-seguro-123-8765.app.github.dev'
    with TestClient(servidor.app, base_url=url) as client:
        assert client.get('/').status_code == 200
        data = client.get('/api/state').json()
        csrf_headers = {'X-LixoCPA-CSRF': data['csrf_token']}
        payload = {'id': 'not-found', 'event': 'click'}

        # Codespaces can rewrite browser Origin and strip/change Referer.
        # The token permits actions only when read through our own UI origin.
        for proxy_headers in [
            {'Origin': url},
            {'Origin': 'http://localhost:8765'},
            {'Origin': 'http://localhost:8765', 'Referer': ''},
            {'Origin': 'https://studious-sniffle-8765.app.github.dev'},
            {},
        ]:
            assert client.post('/api/event', json=payload,
                               headers={**proxy_headers, **csrf_headers}).status_code == 404
            assert client.post('/api/event', json=payload,
                               headers=proxy_headers).status_code == 403

        assert client.post('/api/event', json=payload,
                           headers={'Origin': 'https://attacker.example'}).status_code == 403
        assert client.get('/', headers={'Host': 'other-8765.app.github.dev'}).status_code == 403
        assert client.get('/', headers={'Host': 'exemplo-seguro-8765.app.github.dev'}).status_code == 403
        assert client.get('/', headers={'Host': 'exemplo-seguro-123-8765.preview.app.github.dev'}).status_code == 200

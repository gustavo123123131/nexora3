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
        for route in ['/', '/static/app.js', '/static/style.css', '/static/proxy_view.js', '/static/proxy_view.css', '/static/workspace_theme.css']:
            assert client.get(route).status_code == 200
        data = client.get('/api/state').json()
        assert len(data['server_version']) > 8
        assert len(data['csrf_token']) > 32
        headers = {'X-LixoCPA-CSRF': data['csrf_token']}
        tabs = data['tree']['children'][0]['children']
        assert [t['options']['_tab_label'] for t in tabs] == ['Criar Mãe', 'Criar Filha']
        assert len(list(walk(data['tree']))) >= 110  # Duas abas, sem Área Pix
        # A aba Pix saiu, e controles exclusivos não podem estar visíveis.
        def walk_visible(node):
            if node.get('hidden'):
                return
            yield node
            for child in node.get('children', []):
                yield from walk_visible(child)

        for tab in tabs:
            elements = list(walk(tab))
            modes = [n for n in elements if n['type'] == 'option' and len(n['options'].get('values', [])) == 5]
            assert modes
            assert 'Mobile — Somente Android' in modes[0]['options']['values']
            assert 'Aplicativo Android (indisponível)' in modes[0]['options']['values']
            visible_pix = [n for n in walk_visible(tab)
                           if n['options'].get('text') in
                           ('AutoPIX', 'AutoChave', 'Iniciar saque', 'PIN do saque', 'Valor depósito')]
            assert not visible_pix
            pix_widgets = [n for n in elements if n['options'].get('text') in ('AutoPIX', 'AutoChave', 'Iniciar saque')]
            assert pix_widgets
            # The controls can remain as hidden legacy callbacks, but not be visible.
            for item in pix_widgets:
                if item['options'].get('text') == 'Iniciar saque':
                    assert item['hidden']
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
    """The GitHub-hosted page must not launch cloud Playwright."""
    monkeypatch.setenv('CODESPACES', 'true')
    monkeypatch.setenv('CODESPACE_NAME', 'exemplo-seguro-123')
    monkeypatch.setenv('GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN', 'app.github.dev')
    url = 'https://exemplo-seguro-123-8765.app.github.dev'
    with TestClient(servidor.app, base_url=url) as client:
        assert client.get('/').status_code == 200
        data = client.get('/api/state').json()
        headers = {'X-LixoCPA-CSRF': data['csrf_token']}
        payload = {'id': 'not-found', 'event': 'click'}
        assert client.post('/api/event', json=payload, headers=headers).status_code == 403
        assert client.post('/api/event', json=payload).status_code == 403
        assert client.get('/', headers={'Host': 'other-8765.app.github.dev'}).status_code == 403


def test_windows_agent_paired_cloud_origin(monkeypatch):
    """Authorized HTTPS panel may command the Windows-only localhost agent."""
    origin = 'https://my-private-codespace-8765.app.github.dev'
    monkeypatch.delenv('CODESPACES', raising=False)
    monkeypatch.setenv('LIXOCPA_AGENT_ORIGIN', origin)
    monkeypatch.setenv('LIXOCPA_AGENT_KEY', 'test-only-randomly-generated-value')
    with TestClient(servidor.app, base_url='http://127.0.0.1:8767') as client:
        preflight = client.options('/api/event', headers={
            'Origin': origin,
            'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'x-lixocpa-agent-key,x-lixocpa-csrf',
            'Access-Control-Request-Private-Network': 'true',
        })
        assert preflight.status_code == 204
        assert preflight.headers['access-control-allow-origin'] == origin
        assert preflight.headers['access-control-allow-private-network'] == 'true'

        assert client.get('/api/state', headers={'Origin': origin}).status_code == 403
        wrong = client.get('/api/state', headers={
            'Origin': origin, 'X-LixoCPA-Agent-Key': 'incorrect'
        })
        assert wrong.status_code == 403
        assert wrong.headers['access-control-allow-origin'] == origin

        allowed = {'Origin': origin, 'X-LixoCPA-Agent-Key': 'test-only-randomly-generated-value'}
        response = client.get('/api/state', headers=allowed)
        assert response.status_code == 200
        assert response.headers['access-control-allow-origin'] == origin
        csrf = response.json()['csrf_token']
        payload = {'id': 'not-found', 'event': 'click'}
        assert client.post('/api/event', headers=allowed, json=payload).status_code == 403
        assert client.post('/api/event', headers={**allowed, 'X-LixoCPA-CSRF': csrf},
                           json=payload).status_code == 404

        wrong_origin = {'Origin': 'https://evil-8765.app.github.dev',
                        'X-LixoCPA-Agent-Key': 'test-only-randomly-generated-value'}
        rejected = client.get('/api/state', headers=wrong_origin)
        assert rejected.status_code == 403
        assert 'access-control-allow-origin' not in rejected.headers
        assert client.options('/api/state', headers=wrong_origin).status_code == 403

        # Browser opened directly to local fallback does not need the pairing
        # key, but still must send CSRF on POST.
        assert client.get('/api/state').status_code == 200
        assert client.post('/api/event', headers={'X-LixoCPA-CSRF': csrf},
                           json=payload).status_code == 404

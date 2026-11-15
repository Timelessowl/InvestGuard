import json

import httpx

from app import umami

WEBSITE = '6f1c2a5e-8f0b-4c39-9a51-3d7e2b4c9f10'


def fake_umami():
    state = {'password': 'umami', 'websites': set()}

    def handler(request):
        path, body = request.url.path, json.loads(request.content or b'{}')
        if path == '/api/heartbeat':
            return httpx.Response(200, json={'ok': True})
        if path == '/api/auth/login':
            ok = body == {'username': 'admin', 'password': state['password']}
            return httpx.Response(200, json={'token': 't-' + state['password']}) if ok else httpx.Response(401)
        # Like Umami, a token stops working after the password changes.
        if request.headers.get('authorization') != 'Bearer t-' + state['password']:
            return httpx.Response(401)
        if path == '/api/me/password':
            assert body['currentPassword'] == state['password']
            state['password'] = body['newPassword']
            return httpx.Response(200, json={})
        if path == '/api/websites' and request.method == 'POST':
            state['websites'].add(body['id'])
            return httpx.Response(200, json=body)
        if path.startswith('/api/websites/'):
            # Real Umami: 200 with null body for an unknown id.
            website = path.rsplit('/', 1)[1]
            return httpx.Response(200, content=json.dumps({'id': website} if website in state['websites'] else None))
        return httpx.Response(404)
    return state, httpx.MockTransport(handler)


def test_bootstrap_replaces_default_password_and_is_idempotent():
    state, transport = fake_umami()
    assert umami.bootstrap('http://umami', WEBSITE, 'analytics-pass', transport=transport) == 'created'
    assert state == {'password': 'analytics-pass', 'websites': {WEBSITE}}
    assert umami.bootstrap('http://umami', WEBSITE, 'analytics-pass', transport=transport) == 'exists'


def test_bootstrap_refuses_unknown_password():
    state, transport = fake_umami()
    state['password'] = 'changed-by-hand'
    try:
        umami.bootstrap('http://umami', WEBSITE, 'analytics-pass', transport=transport)
    except RuntimeError as error:
        assert 'UMAMI_ADMIN_PASSWORD' in str(error)
    else:
        raise AssertionError('bootstrap should fail')

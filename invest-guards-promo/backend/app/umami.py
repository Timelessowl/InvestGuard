"""Product analytics in self-hosted Umami: tracker config for the UI and one-shot instance setup."""
import logging
import os
import sys
import time

import httpx

log = logging.getLogger(__name__)
DEFAULT_PASSWORD = 'umami'


def website_id():
    return os.environ.get('UMAMI_WEBSITE_ID', '').strip() or None


def login(http, password):
    response = http.post('/api/auth/login', json={'username': 'admin', 'password': password})
    return response.json()['token'] if response.status_code == 200 else None


def wait_ready(http, timeout):
    deadline = time.monotonic() + timeout
    while True:
        try:
            if http.get('/api/heartbeat').status_code == 200:
                return
        except httpx.TransportError:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError('Umami не отвечает')
        time.sleep(2)


def bootstrap(base_url, website, password, domain='localhost', name='ИнвестСигнал', transport=None, timeout=120):
    """Idempotent: replaces the default admin password and creates the website with a fixed id."""
    with httpx.Client(base_url=base_url, transport=transport, timeout=15, trust_env=False) as http:
        wait_ready(http, timeout)
        token = login(http, password)
        if token is None:
            token = login(http, DEFAULT_PASSWORD)
            if token is None:
                raise RuntimeError('Пароль admin в Umami не совпадает ни с UMAMI_ADMIN_PASSWORD, ни со стандартным')
            http.post('/api/me/password', headers={'Authorization': f'Bearer {token}'},
                      json={'currentPassword': DEFAULT_PASSWORD, 'newPassword': password}).raise_for_status()
            # Tokens are bound to the password hash, so log in again.
            token = login(http, password)
        auth = {'Authorization': f'Bearer {token}'}
        # Umami answers 200 with `null` for a missing website (admins may view any id).
        response = http.get(f'/api/websites/{website}', headers=auth)
        if response.status_code == 200 and response.json():
            return 'exists'
        http.post('/api/websites', headers=auth, json={'id': website, 'name': name, 'domain': domain}).raise_for_status()
        return 'created'


def main():
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    password, website = os.environ.get('UMAMI_ADMIN_PASSWORD', ''), website_id()
    if len(password) < 8 or not website or not os.environ.get('UMAMI_APP_SECRET'):
        sys.exit('Задайте в .env UMAMI_ADMIN_PASSWORD (от 8 символов), UMAMI_APP_SECRET и UMAMI_WEBSITE_ID')
    result = bootstrap(os.environ.get('UMAMI_URL', 'http://umami:3000'), website, password,
                       domain=os.environ.get('UMAMI_DOMAIN', 'localhost'))
    log.info('Umami: сайт %s %s', website, 'уже есть' if result == 'exists' else 'создан')


if __name__ == '__main__':
    main()

import os
import ssl
from pathlib import Path

import certifi
import httpx

USER_AGENT = 'InvestGuard/0.1 (+https://github.com/Timelessowl/InvestGuard)'
# Some regional sites and zakupki.gov.ru use the Russian Ministry of Digital Development CA.
BUNDLED_CA = Path(__file__).resolve().parents[1] / 'certs/russian_trusted_ca.pem'


def ssl_context():
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(BUNDLED_CA)
    for path in filter(None, os.environ.get('EXTRA_CA_FILE', '').split(os.pathsep)):
        context.load_verify_locations(path)
    return context


def http_client(transport=None, timeout=20.0):
    # trust_env=False: connect directly, ignoring system HTTP(S)_PROXY.
    return httpx.Client(transport=transport, verify=ssl_context(), trust_env=False, timeout=timeout,
                        follow_redirects=True, headers={'User-Agent': USER_AGENT})

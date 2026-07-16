"""Superset authentication: guest-token minting for the embedded dashboard.

Only public entry point is get_guest_token(); everything else (login,
embedded-UUID resolution/caching) is an implementation detail of that flow.
"""
import http.cookiejar
import json
import threading
import time
import urllib.error
import urllib.request

from app.config import APPLICATION_JSON, DASHBOARD_TITLE, SUPERSET_DOMAIN, \
    SUPERSET_INTERNAL_URL, read_secret

_embedded_uuid = None
_embedded_lock = threading.Lock()


def _fetch_guest_token():
    """Mint a Superset guest token for the embedded dashboard.

    Logs in as admin, resolves (and caches) the dashboard's embedded UUID,
    creating the embedded registration if superset-init has not run yet.
    """
    global _embedded_uuid
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(method, path, body=None, headers=None):
        req = urllib.request.Request(
            SUPERSET_INTERNAL_URL + path,
            data=json.dumps(body).encode('utf-8') if body is not None else None,
            headers={'Content-Type': APPLICATION_JSON, **(headers or {})},
            method=method,
        )
        with opener.open(req, timeout=20) as resp:
            return json.load(resp)

    login = call('POST', '/api/v1/security/login', {
        'username': read_secret('superset_admin_username'),
        'password': read_secret('superset_admin_password'),
        'provider': 'db',
        'refresh': True,
    })
    auth = {'Authorization': 'Bearer ' + login['access_token']}
    csrf = call('GET', '/api/v1/security/csrf_token/', None, auth)['result']
    write_headers = {**auth, 'X-CSRFToken': csrf, 'Referer': SUPERSET_INTERNAL_URL}

    with _embedded_lock:
        if _embedded_uuid is None:
            dashboards = call('GET', '/api/v1/dashboard/', None, auth)['result']
            dash = next(d for d in dashboards if d['dashboard_title'] == DASHBOARD_TITLE)
            try:
                emb = call('GET', f"/api/v1/dashboard/{dash['id']}/embedded", None, auth)
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
                emb = call('POST', f"/api/v1/dashboard/{dash['id']}/embedded",
                           {'allowed_domains': []}, write_headers)
            _embedded_uuid = emb['result']['uuid']

    guest = call('POST', '/api/v1/security/guest_token/', {
        'user': {'username': 'embedded-guest'},
        'resources': [{'type': 'dashboard', 'id': _embedded_uuid}],
        'rls': [],
    }, write_headers)
    return {'token': guest['token'], 'embeddedUuid': _embedded_uuid,
            'supersetDomain': SUPERSET_DOMAIN}


def get_guest_token():
    """fetch_guest_token(), retrying past Docker's occasional DNS blip."""
    for attempt in range(3):
        try:
            return _fetch_guest_token()
        except urllib.error.URLError:
            if attempt == 2:
                raise
            time.sleep(1)

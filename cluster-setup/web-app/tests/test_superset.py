"""Tests for app.superset.get_guest_token: the login -> csrf -> embedded-uuid
resolution -> guest-token mint flow, with every HTTP call mocked."""
import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.superset as superset


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class FakeOpener:
    """Replays canned JSON bodies keyed by (method, path); raises 404 for
    an 'embedded' GET so the code path that creates the registration runs."""

    def __init__(self, responses, has_embedded_already):
        self.responses = responses
        self.has_embedded_already = has_embedded_already
        self.calls = []

    def open(self, req, timeout=None):
        method = req.get_method()
        path = req.full_url.split('localhost:8088', 1)[-1]
        self.calls.append((method, path))
        if path.startswith('/api/v1/dashboard/') and path.endswith('/embedded') \
                and method == 'GET' and not self.has_embedded_already:
            raise urllib.error.HTTPError(req.full_url, 404, 'not found', None, None)
        body = self.responses.get((method, path))
        if body is None:
            raise AssertionError(f'unexpected call: {method} {path}')
        return _FakeResponse(json.dumps(body).encode('utf-8'))


def _install_fake_opener(monkeypatch_target, has_embedded_already):
    responses = {
        ('POST', '/api/v1/security/login'): {'access_token': 'tok'},
        ('GET', '/api/v1/security/csrf_token/'): {'result': 'csrf123'},
        ('GET', '/api/v1/dashboard/'): {'result': [
            {'id': 7, 'dashboard_title': 'Gdansk Public Transport'},
        ]},
        ('GET', '/api/v1/dashboard/7/embedded'): {'result': {'uuid': 'existing-uuid'}},
        ('POST', '/api/v1/dashboard/7/embedded'): {'result': {'uuid': 'new-uuid'}},
        ('POST', '/api/v1/security/guest_token/'): {'token': 'guest-tok-abc'},
    }
    opener = FakeOpener(responses, has_embedded_already)
    urllib.request.build_opener = lambda *a, **kw: opener
    return opener


class GuestTokenTests(unittest.TestCase):

    def setUp(self):
        self._orig_build_opener = urllib.request.build_opener
        self._orig_read_secret = superset.read_secret
        superset.read_secret = lambda name: 'fake-' + name
        superset._embedded_uuid = None  # reset module cache between tests

    def tearDown(self):
        urllib.request.build_opener = self._orig_build_opener
        superset.read_secret = self._orig_read_secret

    def test_reuses_existing_embedded_registration(self):
        opener = _install_fake_opener(self, has_embedded_already=True)
        result = superset.get_guest_token()
        self.assertEqual(result['token'], 'guest-tok-abc')
        self.assertEqual(result['embeddedUuid'], 'existing-uuid')
        self.assertNotIn(('POST', '/api/v1/dashboard/7/embedded'), opener.calls)

    def test_creates_embedded_registration_when_missing(self):
        _install_fake_opener(self, has_embedded_already=False)
        result = superset.get_guest_token()
        self.assertEqual(result['embeddedUuid'], 'new-uuid')

    def test_embedded_uuid_is_cached_across_calls(self):
        _install_fake_opener(self, has_embedded_already=True)
        superset.get_guest_token()
        self.assertEqual(superset._embedded_uuid, 'existing-uuid')
        # second call must not hit the dashboard-lookup endpoints again
        opener2 = _install_fake_opener(self, has_embedded_already=True)
        superset.get_guest_token()
        self.assertNotIn(('GET', '/api/v1/dashboard/'), opener2.calls)


if __name__ == '__main__':
    unittest.main()

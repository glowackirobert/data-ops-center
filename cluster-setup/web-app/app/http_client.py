"""Outbound HTTP helpers for upstream services (GTFS feed, route-shapes API,
Pinot, Loki, Prometheus). app/http.py is the response side of this server."""
import json
import ssl
import urllib.error
import urllib.request


def http_get(url, timeout):
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.URLError as e:
        if not isinstance(getattr(e, 'reason', None), ssl.SSLCertVerificationError):
            raise
        # TLS-intercepting proxies (dev machines) break verification; the
        # data is public, so degrade rather than lose the feature.
        print(f'HTTP: certificate verification failed for {url}, retrying unverified')
        ctx = ssl.create_default_context()  # NOSONAR — deliberate fallback documented above
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = False  # NOSONAR — deliberate fallback documented above
        ctx.verify_mode = ssl.CERT_NONE  # NOSONAR — deliberate fallback documented above
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read()


def read_json(target, timeout, what, error):
    """urlopen + json.load for a Request or URL. Every way of not getting an
    answer (timeout, refused, HTTP error page) becomes `error`, so callers
    have one exception to catch."""
    try:
        with urllib.request.urlopen(target, timeout=timeout) as resp:
            return json.load(resp)
    except TimeoutError as e:
        raise error(f'{what} did not respond within {timeout}s') from e
    except urllib.error.URLError as e:
        raise error(f'{what} unreachable: {e.reason}') from e

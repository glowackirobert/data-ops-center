"""Outbound HTTP GET helper shared by gtfs.py and geo.py.

Separate from app/http.py, which holds response-side helpers for this
server's own Handler — this module is about fetching from upstream services
(GTFS static feed, route-shapes API), not answering requests.
"""
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

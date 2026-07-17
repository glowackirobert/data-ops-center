"""Small HTTP response helpers shared by the request handler.

Centralizing error responses here means a route handler's unexpected
exception is logged with full detail server-side but never echoed back to
the client verbatim — the previous do-it-all except-clause sent raw
exception text (e.g. file paths) straight into the JSON response body.
"""
import json
import traceback

from app.config import APPLICATION_JSON


def send(handler, code, body, content_type, time_used_ms=None):
    data = body if isinstance(body, bytes) else body.encode('utf-8')
    handler.send_response(code)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(len(data)))
    handler.send_header('Cache-Control', 'no-store')
    # Pinot's own broker-reported query time, exposed as a response header
    # rather than folded into the JSON body so no existing consumer's parsing
    # has to change to pick up a latency badge.
    if time_used_ms is not None:
        handler.send_header('X-Pinot-Time-Ms', str(time_used_ms))
    handler.end_headers()
    handler.wfile.write(data)


def send_json(handler, code, payload, time_used_ms=None):
    send(handler, code, json.dumps(payload), APPLICATION_JSON, time_used_ms=time_used_ms)


def send_error_response(handler, path):
    """Log the full exception server-side; send only a generic message."""
    print(f'ERROR: {path} failed:')
    traceback.print_exc()
    send_json(handler, 500, {'error': 'internal error'})

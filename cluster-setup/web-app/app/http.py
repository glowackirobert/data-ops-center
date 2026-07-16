"""Small HTTP response helpers shared by the request handler.

Centralizing error responses here means a route handler's unexpected
exception is logged with full detail server-side but never echoed back to
the client verbatim — the previous do-it-all except-clause sent raw
exception text (e.g. file paths) straight into the JSON response body.
"""
import json
import traceback

from app.config import APPLICATION_JSON


def send(handler, code, body, content_type):
    data = body if isinstance(body, bytes) else body.encode('utf-8')
    handler.send_response(code)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(len(data)))
    handler.send_header('Cache-Control', 'no-store')
    handler.end_headers()
    handler.wfile.write(data)


def send_json(handler, code, payload):
    send(handler, code, json.dumps(payload), APPLICATION_JSON)


def send_error_response(handler, path):
    """Log the full exception server-side; send only a generic message."""
    print(f'ERROR: {path} failed:')
    traceback.print_exc()
    send_json(handler, 500, {'error': 'internal error'})

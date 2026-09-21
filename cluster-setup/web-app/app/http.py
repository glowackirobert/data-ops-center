"""Small HTTP response helpers shared by the request handler. An unexpected
exception is logged in full server-side and never echoed to the client."""
import json
import traceback

from app.config import APPLICATION_JSON


def send(handler, code, body, content_type, time_used_ms=None, stats=None,
         cache_control='no-store', etag=None):
    data = body if isinstance(body, bytes) else body.encode('utf-8')
    handler.send_response(code)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(len(data)))
    handler.send_header('Cache-Control', cache_control)
    if etag:
        handler.send_header('ETag', etag)
    # Pinot's query time and the scan figures behind it go in headers, not
    # the JSON body, so no consumer's parsing has to change. Header values
    # can't contain newlines, hence the compact separators.
    if time_used_ms is not None:
        handler.send_header('X-Pinot-Time-Ms', str(time_used_ms))
    if stats is not None:
        handler.send_header('X-Pinot-Stats', json.dumps(stats, separators=(',', ':')))
    handler.end_headers()
    handler.wfile.write(data)


def send_json(handler, code, payload, time_used_ms=None, stats=None):
    send(handler, code, json.dumps(payload), APPLICATION_JSON,
         time_used_ms=time_used_ms, stats=stats)


def send_error_response(handler, path):
    print(f'ERROR: {path} failed:')
    traceback.print_exc()
    send_json(handler, 500, {'error': 'internal error'})

"""Thin ASGI adapter around the framework-neutral request core (stdlib only, ~150 lines).

Responsibilities: bounded body read, raw (still percent-encoded) path so encoded separators can
never match a route, header passthrough with duplicates preserved for the core to reject, staged
SSE writing with keep-alive comments and disconnect handling, and a static allow-list loaded at
startup (no filesystem access while serving, so traversal is impossible by construction).
The core's blocking calls run on a small bounded thread pool, never on the event loop.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import policy, wire
from .app import Request
from .policy import PolicyError

MAX_STATIC_FILE = 5 * 1024 * 1024
KEEPALIVE_SECONDS = 10.0
CONTENT_TYPES = {
    '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
    '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon',
    '.woff2': 'font/woff2', '.txt': 'text/plain; charset=utf-8'}
NOT_BUILT = (b'<!doctype html><html lang="en"><meta charset="utf-8"><title>UI not built</title>'
             b'<h1>UI not built</h1><p>The API is running. Build the interface with '
             b'<code>npm --prefix ui ci &amp;&amp; npm --prefix ui run build</code> and restart.</p>'
             b'</html>')


def load_static(root):
    """Map '/relative/path' -> (bytes, content type) for allowed files. Missing dir -> {}."""
    root = Path(root)
    files = {}
    if not root.is_dir():
        return files
    for path in sorted(root.rglob('*')):
        content_type = CONTENT_TYPES.get(path.suffix.lower())
        if (content_type is None or not path.is_file() or path.is_symlink()
                or path.stat().st_size > MAX_STATIC_FILE):
            continue
        files['/' + path.relative_to(root).as_posix()] = (path.read_bytes(), content_type)
    return files


class AsgiApp:
    def __init__(self, app, *, static=None, keepalive=KEEPALIVE_SECONDS, on_shutdown=None,
                 threads=16):
        self.app, self.static = app, static or {}
        self.keepalive, self._on_shutdown = keepalive, on_shutdown
        self._pool = ThreadPoolExecutor(max_workers=threads, thread_name_prefix='web-core')

    async def __call__(self, scope, receive, send):
        kind = scope['type']
        if kind == 'lifespan':
            return await self._lifespan(receive, send)
        if kind != 'http':
            if kind == 'websocket':
                await send({'type': 'websocket.close', 'code': 1008})
            return
        try:
            await self._http(scope, receive, send)
        except OSError:
            return  # client went away mid-write

    async def _lifespan(self, receive, send):
        while True:
            message = await receive()
            if message['type'] == 'lifespan.startup':
                await send({'type': 'lifespan.startup.complete'})
            elif message['type'] == 'lifespan.shutdown':
                self._pool.shutdown(wait=False, cancel_futures=True)
                if self._on_shutdown:
                    self._on_shutdown()
                await send({'type': 'lifespan.shutdown.complete'})
                return

    # -- request ------------------------------------------------------------------------------
    async def _http(self, scope, receive, send):
        body = await self._read_body(receive)
        if body is None:
            return
        raw = scope.get('raw_path') or scope['path'].encode('utf-8')
        path = raw.decode('latin-1')
        headers = [(k.decode('latin-1'), v.decode('latin-1')) for k, v in scope['headers']]
        method = scope['method'].upper()
        client = scope.get('client')
        if method == 'GET' and not (path == '/api' or path.startswith('/api/')):
            return await self._static(path, headers, send)
        request = Request(method, path, scope.get('query_string', b'').decode('latin-1'), headers,
                          body, client[0] if client else 'unknown', scope.get('scheme', 'http'))
        loop = asyncio.get_running_loop()
        outcome = await loop.run_in_executor(self._pool, self.app.begin, request)
        if hasattr(outcome, 'run'):
            return await self._stream(outcome, loop, receive, send)
        await self._send_all(send, outcome.status, outcome.headers, outcome.body)

    @staticmethod
    async def _read_body(receive):
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return None
            if len(body) <= wire.MAX_BODY:
                body += message.get('body', b'')
            if not message.get('more_body'):
                return bytes(body[:wire.MAX_BODY + 1])

    @staticmethod
    async def _send_all(send, status, headers, body):
        out = [(k.encode('latin-1'), v.encode('latin-1')) for k, v in headers]
        out.append((b'content-length', str(len(body)).encode()))
        await send({'type': 'http.response.start', 'status': status, 'headers': out})
        await send({'type': 'http.response.body', 'body': body, 'more_body': False})

    # -- streaming ----------------------------------------------------------------------------
    async def _stream(self, stream, loop, receive, send):
        out = [(k.encode('latin-1'), v.encode('latin-1')) for k, v in stream.headers]
        await send({'type': 'http.response.start', 'status': stream.status, 'headers': out})
        await send({'type': 'http.response.body', 'body': stream.first(), 'more_body': True})
        turn = loop.run_in_executor(self._pool, stream.run)
        gone = asyncio.ensure_future(self._watch(receive))
        try:
            while True:
                done, _ = await asyncio.wait({turn, gone}, timeout=self.keepalive,
                                             return_when=asyncio.FIRST_COMPLETED)
                if gone in done:
                    return  # the turn finishes on the worker; its result is discarded
                if turn in done:
                    break
                await send({'type': 'http.response.body', 'body': b': keep-alive\n\n',
                            'more_body': True})
            for chunk in turn.result():
                await send({'type': 'http.response.body', 'body': chunk, 'more_body': True})
            await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
        finally:
            gone.cancel()

    @staticmethod
    async def _watch(receive):
        while True:
            if (await receive())['type'] == 'http.disconnect':
                return

    # -- static -------------------------------------------------------------------------------
    async def _static(self, path, headers, send):
        host = next((v for k, v in headers if k.lower() == 'host'), None)
        try:
            self.app.policy.check_host(host)
        except PolicyError:
            return await self._send_all(send, 403, [('content-type', 'application/json')]
                                        + policy.response_headers(api=True), b'{"error":"forbidden"}')
        key = '/index.html' if path == '/' else path
        found = self.static.get(key)
        last = path.rsplit('/', 1)[-1]
        if found is None and not self.static:
            found = (NOT_BUILT, CONTENT_TYPES['.html'])
        elif found is None and last and '.' not in last and '/index.html' in self.static:
            found = self.static['/index.html']  # SPA route
        if found is None:
            return await self._send_all(send, 404, [('content-type', 'text/plain; charset=utf-8')]
                                        + policy.response_headers(api=False), b'not found')
        data, content_type = found
        await self._send_all(send, 200, [('content-type', content_type)]
                             + policy.response_headers(api=False), data)

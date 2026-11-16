import importlib.util
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

RELAY = Path(__file__).resolve().parents[1] / 'plugins' / 'workassistant' / 'relay' / 'relay.py'
spec = importlib.util.spec_from_file_location('wa_relay', RELAY)
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)

GOOD = 'wa_goodkey'
APP_TOOLS = [{'name': 'ask', 'description': 'd', 'inputSchema': {'type': 'object'}},
             {'name': 'search', 'description': 'd', 'inputSchema': {'type': 'object'}}]


@pytest.fixture
def app():
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append({'path': self.path, 'headers': dict(self.headers), 'body': body})
            auth = self.headers.get('Authorization')
            if auth not in (f'Bearer {GOOD}', 'Bearer wa_otherkey', 'Bearer wa_secretkey123'):
                return self._send(401, {'error': 'bad token'})
            if 'id' not in body:
                self.send_response(202)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            m = body['method']
            if m == 'tools/list':
                result = {'tools': APP_TOOLS}
            elif m == 'initialize':
                result = {'protocolVersion': body['params']['protocolVersion'], 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'app'}}
            else:
                result = {'content': [{'type': 'text', 'text': 'ok'}], 'isError': False}
            self._send(200, {'jsonrpc': '2.0', 'id': body['id'], 'result': result})

        def _send(self, status, obj):
            data = json.dumps(obj).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = HTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{srv.server_port}', seen
    srv.shutdown()


def write_cfg(path, url, key=GOOD):
    path.write_text(json.dumps({'url': url, 'key': key}))


def call(name, args=None, id=1):
    return {'jsonrpc': '2.0', 'id': id, 'method': 'tools/call', 'params': {'name': name, 'arguments': args or {}}}


def list_req(id=1):
    return {'jsonrpc': '2.0', 'id': id, 'method': 'tools/list'}


def text(reply):
    return reply['result']['content'][0]['text']


def test_without_config_only_connect_is_listed(tmp_path):
    cfg = tmp_path / 'c.json'
    [r] = relay.handle(list_req(), cfg)
    assert [t['name'] for t in r['result']['tools']] == ['connect']
    assert r['result']['tools'][0]['inputSchema']['required'] == ['url', 'key']
    [i] = relay.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'initialize',
                        'params': {'protocolVersion': '2025-06-18'}}, cfg)
    assert i['result']['protocolVersion'] == '2025-06-18'
    assert i['result']['serverInfo']['name'] == 'workassistant-relay'
    assert i['result']['capabilities']['tools']['listChanged'] is True


def test_without_config_other_calls_say_not_connected(tmp_path):
    [r] = relay.handle(call('ask', {'q': 'x'}), tmp_path / 'c.json')
    assert r['result']['isError'] is True
    assert text(r) == ('Not connected: ask the user for their workAssistant URL and key '
                       '(web app → Access tokens) and call connect')


def test_connect_saves_config_only_when_the_app_accepts_the_key(tmp_path, app):
    url, _ = app
    cfg = tmp_path / 'sub' / 'c.json'
    [r] = relay.handle(call('connect', {'url': url, 'key': 'wa_bad'}), cfg)
    assert r['result']['isError'] is True
    assert not cfg.exists()
    out = relay.handle(call('connect', {'url': url + '/', 'key': GOOD}), cfg)
    assert text(out[0]) == 'Connected; 3 tools available'
    assert {'jsonrpc': '2.0', 'method': 'notifications/tools/list_changed'} in out
    assert json.loads(cfg.read_text()) == {'url': url, 'key': GOOD}


def test_forwarding_adds_bearer_and_appends_connect(tmp_path, app):
    url, seen = app
    cfg = tmp_path / 'c.json'
    write_cfg(cfg, url)
    [r] = relay.handle(list_req(7), cfg)
    assert r['id'] == 7
    assert [t['name'] for t in r['result']['tools']] == ['ask', 'search', 'connect']
    assert seen[-1]['path'] == '/mcp'
    assert seen[-1]['headers']['Authorization'] == f'Bearer {GOOD}'
    assert 'text/event-stream' in seen[-1]['headers']['Accept']
    [c] = relay.handle(call('ask', {'q': 'x'}, id=8), cfg)
    assert text(c) == 'ok' and c['id'] == 8
    [i] = relay.handle({'jsonrpc': '2.0', 'id': 9, 'method': 'initialize',
                        'params': {'protocolVersion': '2025-06-18'}}, cfg)
    assert i['result']['capabilities']['tools']['listChanged'] is True


def test_notification_gets_no_reply(tmp_path, app):
    url, seen = app
    cfg = tmp_path / 'c.json'
    note = {'jsonrpc': '2.0', 'method': 'notifications/initialized'}
    assert relay.handle(note, cfg) == []
    write_cfg(cfg, url)
    assert relay.handle(note, cfg) == []
    assert seen[-1]['body'] == note


def test_401_and_network_error_messages(tmp_path, app):
    url, _ = app
    cfg = tmp_path / 'c.json'
    write_cfg(cfg, url, 'wa_revoked')
    [r] = relay.handle(call('ask'), cfg)
    assert r['result']['isError'] is True and text(r) == 'Key rejected; run the connect step again'
    write_cfg(cfg, 'http://127.0.0.1:1')
    [r] = relay.handle(call('ask'), cfg)
    assert r['result']['isError'] is True and 'http://127.0.0.1:1' in text(r)


def test_corrupt_config_is_a_tool_error_not_a_crash(tmp_path):
    cfg = tmp_path / 'c.json'
    cfg.write_text('{not json')
    [r] = relay.handle(call('ask'), cfg)
    assert r['result']['isError'] is True
    assert text(r) == f'Config file unreadable: {cfg}; call connect again'


def test_config_is_reread_each_request(tmp_path, app):
    url, seen = app
    cfg = tmp_path / 'c.json'
    write_cfg(cfg, url, GOOD)
    relay.handle(call('ask'), cfg)
    write_cfg(cfg, url, 'wa_otherkey')
    relay.handle(call('ask'), cfg)
    assert seen[-1]['headers']['Authorization'] == 'Bearer wa_otherkey'


def test_key_never_in_output(tmp_path, app):
    url, _ = app
    cfg = tmp_path / 'c.json'
    key = 'wa_secretkey123'
    outs = relay.handle(call('connect', {'url': url, 'key': key}), cfg)
    outs += relay.handle(call('ask'), cfg) + relay.handle(list_req(), cfg)
    write_cfg(cfg, 'http://127.0.0.1:1', key)
    outs += relay.handle(call('ask'), cfg)
    cfg.write_text('garbage')
    outs += relay.handle(call('ask'), cfg)
    assert len(outs) >= 5 and key not in json.dumps(outs)


def test_failures_still_list_connect_so_the_key_can_be_replaced(tmp_path, app):
    url, _ = app
    cfg = tmp_path / 'c.json'
    cfg.write_text('{not json')
    init = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}}
    [i] = relay.handle(init, cfg)
    assert i['result']['capabilities']['tools']['listChanged'] is True
    [r] = relay.handle(list_req(), cfg)
    assert [t['name'] for t in r['result']['tools']] == ['connect']
    write_cfg(cfg, url, 'wa_revoked')
    [r] = relay.handle(list_req(), cfg)
    assert [t['name'] for t in r['result']['tools']] == ['connect']
    [i] = relay.handle(init, cfg)
    assert 'result' in i
    write_cfg(cfg, 'http://127.0.0.1:1')
    [r] = relay.handle(list_req(), cfg)
    assert [t['name'] for t in r['result']['tools']] == ['connect']


def test_utf8_survives_stdin_as_a_subprocess(tmp_path, app):
    url, seen = app
    cfg = tmp_path / 'c.json'
    write_cfg(cfg, url)
    s = '“hi” — Åsa'
    line = json.dumps(call('ask', {'q': s}), ensure_ascii=False) + '\n'
    r = subprocess.run([sys.executable, str(RELAY)], input=line.encode('utf-8'), capture_output=True, timeout=30,
                       env={**os.environ, 'WORKASSISTANT_CONFIG': str(cfg)})
    assert json.loads(r.stdout)['result']['content'][0]['text'] == 'ok'
    assert seen[-1]['body']['params']['arguments']['q'] == s

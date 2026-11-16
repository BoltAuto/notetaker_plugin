"""Stdio MCP relay: forwards each JSON-RPC message to the workAssistant app's /mcp, adding the bearer key.

Standard library only (Python 3.10+). Stdout carries protocol messages only; diagnostics go to stderr.
The key is never logged or printed.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT = 180
NOT_CONNECTED = ('Not connected: ask the user for their workAssistant URL and key '
                 '(web app → Access tokens) and call connect')
KEY_REJECTED = 'Key rejected; run the connect step again'
CONNECT_TOOL = {
    'name': 'connect',
    'description': ('Connect this plugin to workAssistant. Ask the user for their workAssistant URL and an access '
                    'key (web app → Access tokens), then call this. Can be called again to replace the key.'),
    'inputSchema': {
        'type': 'object',
        'properties': {'url': {'type': 'string', 'description': 'workAssistant base URL'},
                       'key': {'type': 'string', 'description': 'access key'}},
        'required': ['url', 'key'],
    },
}


class RelayError(Exception):
    pass


def default_config_path() -> Path:
    return Path(os.environ.get('WORKASSISTANT_CONFIG') or Path.home() / '.workassistant' / 'config.json')


def log(text: str) -> None:
    print(f'[workassistant-relay] {text}', file=sys.stderr, flush=True)


def load_config(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        cfg = json.loads(path.read_text(encoding='utf-8'))
        url, key = cfg['url'], cfg['key']
        if not isinstance(url, str) or not isinstance(key, str) or not url or not key:
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise RelayError(f'Config file unreadable: {path}; call connect again')
    return {'url': url.rstrip('/'), 'key': key}


def save_config(path: Path, url: str, key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump({'url': url, 'key': key}, f)
    if os.name == 'posix':
        os.chmod(path, 0o600)


def post(url: str, key: str, msg: dict) -> dict | None:
    """POST one message. Returns the parsed JSON body (None for an empty body). Raises RelayError."""
    req = urllib.request.Request(
        f'{url}/mcp', data=json.dumps(msg).encode(), method='POST',
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json',
                 'Accept': 'application/json, text/event-stream'})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise RelayError(KEY_REJECTED)
        try:
            data = json.loads(e.read())
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get('jsonrpc') and ('result' in data or 'error' in data):
            return data
        raise RelayError(f'workAssistant returned HTTP {e.code} for {url}')
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, 'reason', e)
        raise RelayError(f'Could not reach workAssistant at {url}: {type(reason).__name__}')
    if not body.strip():
        return None
    try:
        return json.loads(body)
    except ValueError:
        raise RelayError(f'workAssistant at {url} sent an unreadable reply')


def tool_error(msg: dict, text: str) -> dict:
    return {'jsonrpc': '2.0', 'id': msg['id'], 'result': {'content': [{'type': 'text', 'text': text}], 'isError': True}}


def fail(msg: dict, text: str) -> list[dict]:
    """Report a failure for a request: a tool error for tools/call, else a JSON-RPC error."""
    if 'id' not in msg:
        log(text)
        return []
    if msg.get('method') == 'tools/call':
        return [tool_error(msg, text)]
    return [{'jsonrpc': '2.0', 'id': msg['id'], 'error': {'code': -32000, 'message': text}}]


def connect(msg: dict, config_path: Path) -> list[dict]:
    args = (msg.get('params') or {}).get('arguments') or {}
    url, key = args.get('url'), args.get('key')
    if not isinstance(url, str) or not isinstance(key, str) or not url.strip() or not key.strip():
        return [tool_error(msg, 'connect needs a url and a key')]
    url, key = url.strip().rstrip('/'), key.strip()
    try:
        reply = post(url, key, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        n = len(reply['result']['tools'])
    except RelayError as e:
        return [tool_error(msg, str(e))]
    except (KeyError, TypeError):
        return [tool_error(msg, f'{url} did not answer like a workAssistant MCP endpoint')]
    try:
        save_config(config_path, url, key)
    except OSError as e:
        return [tool_error(msg, f'Could not save config to {config_path}: {type(e).__name__}')]
    text = f'Connected; {n + 1} tools available'  # the app's tools plus connect itself
    return [{'jsonrpc': '2.0', 'id': msg['id'], 'result': {'content': [{'type': 'text', 'text': text}], 'isError': False}},
            {'jsonrpc': '2.0', 'method': 'notifications/tools/list_changed'}]


def answer_locally(msg: dict, problem: str) -> list[dict]:
    """Answer without the app: only `connect` is available. `problem` is the text for any other tool call."""
    method, id_ = msg.get('method'), msg['id']
    if method == 'initialize':
        version = (msg.get('params') or {}).get('protocolVersion', '2025-06-18')
        return [{'jsonrpc': '2.0', 'id': id_, 'result': {
            'protocolVersion': version, 'capabilities': {'tools': {'listChanged': True}},
            'serverInfo': {'name': 'workassistant-relay', 'version': '1'}}}]
    if method == 'tools/list':
        return [{'jsonrpc': '2.0', 'id': id_, 'result': {'tools': [CONNECT_TOOL]}}]
    if method == 'ping':
        return [{'jsonrpc': '2.0', 'id': id_, 'result': {}}]
    return fail(msg, problem)


def handle(msg: dict, config_path: Path) -> list[dict]:
    """Return the messages to write to stdout for one incoming message."""
    if not isinstance(msg, dict):
        return []
    method = msg.get('method')
    is_request = 'id' in msg and method is not None
    if method == 'tools/call' and (msg.get('params') or {}).get('name') == 'connect':
        return connect(msg, config_path)
    problem = None
    try:
        cfg = load_config(config_path)
    except RelayError as e:
        cfg, problem = None, str(e)
    if cfg is None:
        return answer_locally(msg, problem or NOT_CONNECTED) if is_request else (log(problem) or [] if problem else [])
    try:
        reply = post(cfg['url'], cfg['key'], msg)
    except RelayError as e:
        if method in ('initialize', 'tools/list'):
            log(str(e))  # still answer, so connect stays available to fix the problem
            return answer_locally(msg, str(e))
        return fail(msg, str(e))
    if not is_request or reply is None:
        return []
    result = reply.get('result')
    if isinstance(result, dict):
        if method == 'initialize':
            result.setdefault('capabilities', {}).setdefault('tools', {})['listChanged'] = True
        elif method == 'tools/list' and isinstance(result.get('tools'), list):
            result['tools'].append(CONNECT_TOOL)
    return [reply]


def main() -> None:
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    config_path = default_config_path()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            log('ignoring a line that is not JSON')
            continue
        try:
            out = handle(msg, config_path)
        except Exception as e:  # keep the loop alive
            log(f'internal error: {type(e).__name__}')
            out = fail(msg, 'Relay internal error') if isinstance(msg, dict) else []
        for m in out:
            sys.stdout.write(json.dumps(m) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    main()

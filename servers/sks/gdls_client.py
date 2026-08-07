"""GDLS (キミスタ) CDP直叩き クライアント. v2 — 単一WS ・ 並行 event listener."""
from __future__ import annotations
import asyncio
import json
import os
import subprocess
import time
import sys
import urllib.parse
from pathlib import Path

CHROME_PATH = os.environ.get(
    'GDLS_CHROME_PATH',
    r'C:\Program Files\Google\Chrome\Application\chrome.exe',
)
CDP_PORT = int(os.environ.get('GDLS_CDP_PORT', '9222'))
PROFILE_DIR = os.environ.get(
    'GDLS_PROFILE_DIR',
    str(Path.home() / '.tact-mcp' / 'chrome-cdp-profile'),
)
GDLS_LOGIN_URL = 'https://gdls.gakken.jp/teacher/g-method/login'
GDLS_STUDENT_URL = 'https://gdls.gakken.jp/teacher/setting/v3/student'
# 教室別のキミスタ ログインコード。~/.tact-mcp/sks.env 等で GDLS_LOGIN_CODE を設定する。
KIMISTA_CODE = os.environ.get('GDLS_LOGIN_CODE', '')


def _deps_ok():
    try:
        import websockets, requests  # noqa
        return True
    except ImportError:
        return False


def launch_chrome() -> None:
    import requests
    try:
        r = requests.get(f'http://localhost:{CDP_PORT}/json/version', timeout=1.5)
        if r.ok: return
    except Exception:
        pass
    Path(PROFILE_DIR).mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [CHROME_PATH, f'--remote-debugging-port={CDP_PORT}',
         f'--user-data-dir={PROFILE_DIR}',
         '--no-first-run', '--no-default-browser-check',
         GDLS_LOGIN_URL],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        try:
            r = requests.get(f'http://localhost:{CDP_PORT}/json/version', timeout=1)
            if r.ok: return
        except Exception:
            time.sleep(0.5)


class CDPSession:
    """単一WS・並行 event listener・dialog 自動 accept."""

    def __init__(self, ws):
        self.ws = ws
        self._id = 0
        self._responses: dict[int, dict] = {}
        self._events: list[dict] = []
        self._recv_task = None
        self._dialog_auto_accept = False
        self._closed = False

    def id(self) -> int:
        self._id += 1
        return self._id

    async def _reader(self):
        try:
            async for raw in self.ws:
                m = json.loads(raw)
                if 'id' in m:
                    self._responses[m['id']] = m
                elif 'method' in m:
                    self._events.append(m)
                    # dialog auto accept
                    if self._dialog_auto_accept and m['method'] == 'Page.javascriptDialogOpening':
                        rid = self.id()
                        await self.ws.send(json.dumps({
                            'id': rid, 'method': 'Page.handleJavaScriptDialog',
                            'params': {'accept': True}}))
        except Exception:
            self._closed = True

    async def start(self):
        self._recv_task = asyncio.create_task(self._reader())

    async def call(self, method: str, params: dict | None = None, timeout: float = 15.0):
        rid = self.id()
        await self.ws.send(json.dumps({'id': rid, 'method': method, 'params': params or {}}))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if rid in self._responses:
                return self._responses.pop(rid)
            await asyncio.sleep(0.05)
        return {'timeout': True}

    async def call_no_wait(self, method: str, params: dict | None = None):
        rid = self.id()
        await self.ws.send(json.dumps({'id': rid, 'method': method, 'params': params or {}}))
        return rid

    def events_of(self, method: str) -> list[dict]:
        return [e for e in self._events if e.get('method') == method]


def _pick_tab():
    import requests
    tabs = requests.get(f'http://localhost:{CDP_PORT}/json').json()
    for t in tabs:
        u = t.get('url') or ''
        if 'gdls.gakken.jp' in u and 'service-worker' not in u:
            return t
    for t in tabs:
        if (t.get('type') == 'page'):
            return t
    return None


async def delete_student(seitocd: str, name_hint: str = '') -> dict:
    import websockets, requests
    launch_chrome()
    await asyncio.sleep(1.0)
    tab = _pick_tab()
    if not tab:
        return {'ok': False, 'error': 'no gdls tab'}

    async with websockets.connect(tab['webSocketDebuggerUrl'], max_size=50 * 1024 * 1024) as ws:
        s = CDPSession(ws)
        await s.start()
        await s.call('Page.enable')
        await s.call('Runtime.enable')

        # 現URL
        r = await s.call('Runtime.evaluate', {'expression': 'location.href', 'returnByValue': True})
        href = r.get('result', {}).get('result', {}).get('value', '')

        # ログイン
        if '/login' in href or 'g-method/login' in href:
            r = await s.call('Runtime.evaluate', {
                'expression': "(function(){var i=document.querySelector('input'); if(!i) return{no:true}; i.focus(); return{ok:true}})()",
                'returnByValue': True})
            # insertText
            await s.call('Input.insertText', {'text': KIMISTA_CODE})
            await asyncio.sleep(0.3)
            # ログイン button (Vue.js <a>) real mouse click
            r = await s.call('Runtime.evaluate', {
                'expression': "(function(){var a=Array.from(document.querySelectorAll('a,button')).find(x=>x.textContent.trim()==='ログイン'); if(!a) return{no:true}; var r=a.getBoundingClientRect(); return{x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2)}})()",
                'returnByValue': True})
            v = r.get('result', {}).get('result', {}).get('value', {})
            if v.get('x'):
                await s.call('Input.dispatchMouseEvent', {'type': 'mousePressed', 'x': v['x'], 'y': v['y'], 'button': 'left', 'clickCount': 1})
                await s.call('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'x': v['x'], 'y': v['y'], 'button': 'left', 'clickCount': 1})
            # 遷移待ち
            for _ in range(20):
                await asyncio.sleep(0.5)
                r = await s.call('Runtime.evaluate', {'expression': 'location.href', 'returnByValue': True})
                if '/login' not in r.get('result', {}).get('result', {}).get('value', ''):
                    break

        # 生徒管理 に 遷移 (name 検索)
        query = urllib.parse.quote(name_hint or seitocd)
        await s.call('Page.navigate', {'url': f'{GDLS_STUDENT_URL}?name={query}'})
        # 要素 描画待ち
        for _ in range(15):
            await asyncio.sleep(0.5)
            r = await s.call('Runtime.evaluate', {
                'expression': f"document.body.innerText.indexOf({json.dumps(seitocd)})",
                'returnByValue': True})
            if r.get('result', {}).get('result', {}).get('value', -1) >= 0:
                break

        # dialog 自動 accept を ON
        s._dialog_auto_accept = True

        # 対象行 の 削除 button 検出 & 座標
        r = await s.call('Runtime.evaluate', {
            'expression': f'''(function(){{
                var seito = {json.dumps(seitocd)};
                var target = null;
                var all = Array.from(document.querySelectorAll('button, a, div[role="button"]'));
                for (var i=0; i<all.length; i++) {{
                    if (all[i].textContent.trim() !== '削除') continue;
                    var b = all[i];
                    // 同 行 (親 tr/li/div-row) に seitocd が あるか
                    var scope = b.closest('tr, li, div[class*="row"], div[class*="Row"], div[data-v]') || b.parentElement;
                    var checked = false;
                    for (var d=0; d<8; d++) {{
                        if (!scope) break;
                        if ((scope.textContent||'').indexOf(seito) >= 0) {{ checked = true; break; }}
                        scope = scope.parentElement;
                    }}
                    if (checked) {{ target = b; break; }}
                }}
                if (!target) return {{no:true}};
                target.scrollIntoView({{block:'center'}});
                var r = target.getBoundingClientRect();
                return {{x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2), htmlSample:target.outerHTML.slice(0,150)}};
            }})()''',
            'returnByValue': True})
        info = r.get('result', {}).get('result', {}).get('value', {})
        if info.get('no'):
            return {'ok': False, 'error': 'delete button not found', 'detail': info}

        # dispatchEvent で 削除 click を 発火 (native confirm が 出て auto accept される はず)
        # 送信 のみ(応答 は 待たない — dialog block で 返らない ため)
        click_js = f'''(function(){{
            var seito = {json.dumps(seitocd)};
            var target = null;
            var all = Array.from(document.querySelectorAll('button, a, div[role="button"]'));
            for (var i=0; i<all.length; i++) {{
                if (all[i].textContent.trim() !== '削除') continue;
                var b = all[i];
                var scope = b.closest('tr, li, div[class*="row"], div[class*="Row"], div[data-v]') || b.parentElement;
                for (var d=0; d<8; d++) {{
                    if (!scope) break;
                    if ((scope.textContent||'').indexOf(seito) >= 0) {{ target = b; break; }}
                    scope = scope.parentElement;
                }}
                if (target) break;
            }}
            if (!target) return 'no-target';
            ['pointerdown','mousedown','pointerup','mouseup','click'].forEach(function(t){{
                target.dispatchEvent(new MouseEvent(t, {{bubbles:true, cancelable:true, button:0}}));
            }});
            return 'dispatched';
        }})()'''
        await s.call_no_wait('Runtime.evaluate', {'expression': click_js, 'returnByValue': True})

        # dialog が 順次 auto accept される. しばし 待って 検証.
        for _ in range(15):
            await asyncio.sleep(0.5)
            n = len(s.events_of('Page.javascriptDialogOpening'))
            if n >= 2:  # confirm + alert 「削除しました」
                break
        # さらに 少し 待って 削除 反映
        await asyncio.sleep(2.0)

        # 検証: 米田 の seitocd が 一覧 に 残っていない
        r = await s.call('Runtime.evaluate', {
            'expression': f"document.body.innerText.indexOf({json.dumps(seitocd)})",
            'returnByValue': True})
        idx = r.get('result', {}).get('result', {}).get('value', -1)
        dialogs_seen = len(s.events_of('Page.javascriptDialogOpening'))
        return {
            'ok': idx < 0,
            'seitocd': seitocd,
            'dialogs_seen': dialogs_seen,
            'still_present': idx >= 0,
        }


def delete_student_sync(seitocd: str, name_hint: str = '') -> dict:
    if not _deps_ok():
        return {'ok': False, 'error': 'websockets/requests not installed'}
    return asyncio.run(delete_student(seitocd, name_hint))


if __name__ == '__main__':
    seitocd = sys.argv[1] if len(sys.argv) > 1 else ''
    name_hint = sys.argv[2] if len(sys.argv) > 2 else ''
    if not seitocd:
        print('usage: python gdls_client.py <seitocd> [name_hint]', file=sys.stderr)
        sys.exit(2)
    result = delete_student_sync(seitocd, name_hint)
    print(json.dumps(result, ensure_ascii=False, indent=2))

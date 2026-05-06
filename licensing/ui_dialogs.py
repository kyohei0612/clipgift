"""
アクティベーション UI（HTML 1 ページ）

設計方針:
- ブラウザで表示できる最小構成（既存 Flask アプリの仕組みを再利用しない、独立 HTTP サーバー）
- アクティベーション中だけ起動する一時サーバー（ポート 5050 を使用）
- 認証成功 / キャンセル時にサーバーを停止してメインアプリへ進む
"""

import http.server
import logging
import socketserver
import threading
import urllib.parse
import webbrowser
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ACTIVATION_PORT = 5050

ACTIVATION_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <title>クリップギフト ライセンス認証</title>
    <style>
        body {
            font-family: 'Segoe UI', 'Yu Gothic', sans-serif;
            background: #f5f5f7;
            margin: 0;
            padding: 40px 20px;
            color: #1d1d1f;
        }
        .container {
            max-width: 520px;
            margin: 0 auto;
            background: white;
            border-radius: 16px;
            padding: 40px;
            box-shadow: 0 4px 24px rgba(0,0,0,0.06);
        }
        h1 { font-size: 24px; margin-top: 0; }
        .description {
            color: #6e6e73;
            line-height: 1.6;
            font-size: 14px;
            margin-bottom: 24px;
        }
        .key-input {
            width: 100%;
            padding: 14px;
            font-size: 16px;
            font-family: 'Consolas', 'Menlo', monospace;
            border: 2px solid #e5e5ea;
            border-radius: 10px;
            box-sizing: border-box;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .key-input:focus {
            outline: none;
            border-color: #007aff;
        }
        .activate-btn {
            width: 100%;
            margin-top: 16px;
            padding: 14px;
            background: #007aff;
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
        }
        .activate-btn:hover { background: #0056d6; }
        .activate-btn:disabled { background: #c7c7cc; cursor: wait; }
        .error {
            color: #d70015;
            background: #ffebee;
            padding: 12px;
            border-radius: 8px;
            margin-top: 16px;
            font-size: 14px;
            display: none;
        }
        .error.show { display: block; }
        .success {
            color: #28a745;
            background: #e8f5e9;
            padding: 12px;
            border-radius: 8px;
            margin-top: 16px;
            font-size: 14px;
            display: none;
        }
        .success.show { display: block; }
        .footer {
            margin-top: 32px;
            font-size: 12px;
            color: #8e8e93;
            text-align: center;
        }
        .footer a { color: #007aff; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔑 クリップギフト ライセンス認証</h1>
        <div class="description">
            ご購入時に発行されたライセンスキーを入力してください。<br>
            キーは <code>CGFT-XXX-XXXX-XXXX-XXXX</code> の形式です。<br>
            このマシンを最大 2 台目までアクティベーションできます。
        </div>
        <input type="text" id="key" class="key-input"
               placeholder="CGFT-XXX-XXXX-XXXX-XXXX"
               autocomplete="off" autocapitalize="characters">
        <button id="activate" class="activate-btn">アクティベーションする</button>
        <div id="error" class="error"></div>
        <div id="success" class="success"></div>
        <div class="footer">
            購入したのにキーが届いていない場合は<br>
            <a href="mailto:support@clipgift.app">support@clipgift.app</a> までご連絡ください
        </div>
    </div>
    <script>
        const keyInput = document.getElementById('key');
        const button = document.getElementById('activate');
        const errorBox = document.getElementById('error');
        const successBox = document.getElementById('success');

        function showError(msg) {
            errorBox.textContent = msg;
            errorBox.classList.add('show');
            successBox.classList.remove('show');
        }
        function showSuccess(msg) {
            successBox.textContent = msg;
            successBox.classList.add('show');
            errorBox.classList.remove('show');
        }

        button.addEventListener('click', async () => {
            const key = keyInput.value.trim().toUpperCase();
            if (!key) {
                showError('ライセンスキーを入力してください');
                return;
            }
            button.disabled = true;
            button.textContent = '認証中...';
            try {
                const resp = await fetch('/api/activate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ key })
                });
                const data = await resp.json();
                if (resp.ok) {
                    showSuccess('認証成功！3 秒後にアプリが起動します...');
                    setTimeout(() => window.close(), 3000);
                } else {
                    showError(data.message + (data.hint ? '\\n\\n' + data.hint : ''));
                    button.disabled = false;
                    button.textContent = 'アクティベーションする';
                }
            } catch (e) {
                showError('通信エラー: ' + e.message);
                button.disabled = false;
                button.textContent = 'アクティベーションする';
            }
        });

        keyInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') button.click();
        });
    </script>
</body>
</html>
"""


def show_activation_dialog(
    on_activate: Callable[[str], dict]
) -> Optional[dict]:
    """
    アクティベーション UI を起動し、ユーザーがキーを入力するのを待つ。

    Args:
        on_activate: キーを受け取って activation 処理を行うコールバック。
                     成功時は credential payload dict、失敗時は例外を投げる。

    Returns:
        credential payload dict、または None（ユーザーキャンセル）
    """
    result_holder: dict = {"credential": None, "done": False}

    class ActivationHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            return  # 標準出力に出さない

        def do_GET(self):  # noqa: N802
            if self.path == "/" or self.path.startswith("/?"):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(ACTIVATION_HTML.encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):  # noqa: N802
            if self.path != "/api/activate":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", 0))
            body_raw = self.rfile.read(length).decode("utf-8")
            try:
                import json

                body = json.loads(body_raw)
                key = body.get("key", "").strip().upper()
                credential = on_activate(key)
                result_holder["credential"] = credential
                result_holder["done"] = True
                self._send_json(200, {"status": "ok"})
            except Exception as e:
                code = getattr(e, "code", "unknown")
                hint = getattr(e, "hint", "")
                self._send_json(
                    400,
                    {"status": "error", "code": code, "message": str(e), "hint": hint},
                )

        def _send_json(self, status: int, body: dict):
            import json

            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode("utf-8"))

    httpd = socketserver.TCPServer(("127.0.0.1", ACTIVATION_PORT), ActivationHandler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()

    # ブラウザで開く
    webbrowser.open(f"http://127.0.0.1:{ACTIVATION_PORT}/")
    logger.info("アクティベーション UI を起動しました（ポート %d）", ACTIVATION_PORT)

    # 認証完了 or キャンセルを待つ（最大 30 分）
    try:
        for _ in range(30 * 60):
            if result_holder["done"]:
                break
            import time

            time.sleep(1)
    finally:
        httpd.shutdown()
        httpd.server_close()
        logger.info("アクティベーション UI を停止しました")

    return result_holder["credential"]

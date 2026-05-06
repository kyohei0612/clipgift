"""
ライセンスサーバー (Cloudflare Workers) との通信クライアント

エンドポイント:
  POST /activate    - 初回アクティベーション
  POST /verify      - 30 日ハートビート
  POST /deactivate  - マシン解放
"""

import json
import logging
from typing import Optional
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

from licensing.exceptions import (
    KeyNotFoundError,
    KeyRevokedError,
    LicensingError,
    MaxMachinesReachedError,
    NetworkError,
    SignatureInvalidError,
)

logger = logging.getLogger(__name__)

# サーバー URL（実装時に kyohei さん側で確定 → ここを差し替え）
DEFAULT_SERVER_URL = "https://clipgift-license.kyohei0612.workers.dev"
REQUEST_TIMEOUT_SEC = 15


def _post(server_url: str, path: str, body: dict) -> dict:
    """POST リクエスト共通処理"""
    url = server_url.rstrip("/") + path
    data = json.dumps(body).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "ClipGift-Client/1.0",
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
            response_text = resp.read().decode("utf-8")
            return json.loads(response_text)
    except HTTPError as e:
        # サーバーが返したエラー（4xx / 5xx）
        try:
            error_text = e.read().decode("utf-8")
            error_body = json.loads(error_text)
            _raise_error(error_body)
        except (ValueError, json.JSONDecodeError):
            pass
        raise NetworkError(f"サーバーエラー (HTTP {e.code})") from e
    except URLError as e:
        raise NetworkError(f"サーバーに接続できません: {e.reason}") from e
    except (TimeoutError, json.JSONDecodeError) as e:
        raise NetworkError(f"通信エラー: {e}") from e


def _raise_error(error_body: dict) -> None:
    """サーバー応答のエラーコードに応じて例外を投げる"""
    code = error_body.get("code", "unknown")
    message = error_body.get("message", "不明なエラー")
    hint = error_body.get("hint", "")

    if code == "key_not_found":
        raise KeyNotFoundError(message)
    if code == "key_revoked":
        raise KeyRevokedError(message, hint=hint)
    if code == "max_machines_reached":
        raise MaxMachinesReachedError(message, hint=hint)
    if code == "signature_invalid":
        raise SignatureInvalidError(message, hint=hint)
    raise LicensingError(message, code=code, hint=hint)


def activate(
    key: str,
    machine_fingerprint: str,
    machine_label: str = "",
    server_url: str = DEFAULT_SERVER_URL,
) -> dict:
    """
    初回アクティベーション

    Returns:
        サーバー応答 dict（credential 含む、credential_store に保存する payload）
    """
    body = {
        "key": key,
        "machine_fingerprint": machine_fingerprint,
        "machine_label": machine_label or "",
    }
    response = _post(server_url, "/activate", body)
    if response.get("status") != "ok":
        raise LicensingError("アクティベーションに失敗しました")
    return response


def verify(
    key: str,
    machine_fingerprint: str,
    server_url: str = DEFAULT_SERVER_URL,
) -> dict:
    """
    30 日ハートビート（最新の credential を取得）

    失敗時は例外を投げる。呼び出し側でキャッチして「猶予期間延長」を判断する。
    """
    body = {
        "key": key,
        "machine_fingerprint": machine_fingerprint,
    }
    response = _post(server_url, "/verify", body)
    if response.get("status") != "ok":
        raise LicensingError("検証に失敗しました")
    return response


def deactivate(
    key: str,
    machine_fingerprint: str,
    server_url: str = DEFAULT_SERVER_URL,
) -> int:
    """
    マシン解放（このマシンのスロットを返却）

    Returns:
        残りスロット数
    """
    body = {
        "key": key,
        "machine_fingerprint": machine_fingerprint,
    }
    response = _post(server_url, "/deactivate", body)
    if response.get("status") != "ok":
        raise LicensingError("マシン解放に失敗しました")
    return int(response.get("remaining_slots", 0))


def health_check(server_url: str = DEFAULT_SERVER_URL) -> bool:
    """サーバー疎通確認（オフライン猶予判定用）"""
    url = server_url.rstrip("/") + "/health"
    try:
        with urllib_request.urlopen(url, timeout=5) as resp:
            return resp.status == 200
    except (URLError, TimeoutError, OSError):
        return False

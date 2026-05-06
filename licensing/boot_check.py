"""
起動時認証フロー

app.py の __main__ 直下から `authenticate_or_block()` を呼ぶ。
- 既に認証済み（credential あり、期限内） → そのまま OK
- credential なし → アクティベーション UI を起動
- credential あるが期限超過 → /verify でリフレッシュ、失敗時は猶予延長 or 再認証
- 認証 NG → False を返す（呼び出し側が起動中止）
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from licensing import activation_client, credential_store, plan_gate
from licensing.exceptions import (
    CredentialCorruptedError,
    LicensingError,
    NetworkError,
)
from licensing.key_validator import mask_key, normalize_key, validate_key_format
from licensing.machine_id import get_machine_fingerprint, get_machine_label
from licensing.plan_gate import PlanGate
from licensing.ui_dialogs import show_activation_dialog

logger = logging.getLogger(__name__)

# 30 日 + 7 日（オフライン猶予合計 37 日）
GRACE_EXTENSION_DAYS = 7


@dataclass
class AuthResult:
    """起動時認証の結果"""

    ok: bool
    plan: Optional[str] = None
    message: str = ""


def authenticate_or_block() -> AuthResult:
    """
    起動時の認証チェック

    Returns:
        AuthResult: ok=True なら起動継続、False なら起動中止
    """
    fingerprint = get_machine_fingerprint()
    label = get_machine_label()

    # Step 1: 既存 credential を試す
    existing = _try_load_existing(fingerprint)
    if existing is not None:
        return existing

    # Step 2: 初回アクティベーション UI
    return _run_first_time_activation(fingerprint, label)


def _try_load_existing(fingerprint: str) -> Optional[AuthResult]:
    """既存 credential があれば読み込み + 必要に応じて /verify"""
    try:
        credential = credential_store.load_credential(fingerprint)
    except CredentialCorruptedError:
        # ファイル破損 → 削除して再アクティベーションへ
        logger.warning("credential が破損しています、再アクティベーションが必要")
        credential_store.delete_credential()
        return None

    if credential is None:
        return None

    gate = PlanGate(credential)

    # 30 日経過していなければそのまま OK
    if not gate.needs_verification():
        plan_gate.set_global(gate)
        logger.info(
            "既存ライセンス検証 OK: %s (key=%s, slot %d/2)",
            gate.plan_label(),
            mask_key(gate.key),
            gate.machine_slot,
        )
        return AuthResult(ok=True, plan=gate.plan)

    # 30 日経過 → /verify を試す
    return _try_heartbeat(gate, fingerprint)


def _try_heartbeat(gate: PlanGate, fingerprint: str) -> AuthResult:
    """30 日経過時のハートビート（失敗時は猶予延長 or 再認証）"""
    try:
        response = activation_client.verify(gate.key, fingerprint)
        # credential を更新（refresh）
        new_payload = _build_credential_payload(
            response, gate.key, fingerprint
        )
        credential_store.save_credential(new_payload, fingerprint)
        plan_gate.set_global(PlanGate(new_payload))
        logger.info("ハートビート成功、credential 更新")
        return AuthResult(ok=True, plan=gate.plan)
    except NetworkError as e:
        # ネットワーク失敗 → 猶予 7 日延長
        logger.warning("ハートビート失敗（ネットワーク）: %s", e)
        return _extend_grace(gate, fingerprint)
    except LicensingError as e:
        # キー失効 / 不正等 → ハードブロック
        logger.warning("ハートビート失敗（ライセンスエラー）: %s", e)
        credential_store.delete_credential()
        return AuthResult(
            ok=False,
            message=f"ライセンスの再認証が必要です: {e}",
        )


def _extend_grace(gate: PlanGate, fingerprint: str) -> AuthResult:
    """ネットワーク失敗時の猶予期間延長"""
    new_next_verify = (
        datetime.now(timezone.utc) + timedelta(days=GRACE_EXTENSION_DAYS)
    ).isoformat()
    payload = dict(gate._credential)
    payload["next_verify_at"] = new_next_verify
    credential_store.save_credential(payload, fingerprint)
    plan_gate.set_global(PlanGate(payload))
    logger.info(
        "オフライン猶予を %d 日延長しました（ネットワーク回復後に再検証）",
        GRACE_EXTENSION_DAYS,
    )
    return AuthResult(ok=True, plan=gate.plan)


def _run_first_time_activation(fingerprint: str, label: str) -> AuthResult:
    """初回アクティベーション UI を起動"""

    def on_activate(key: str) -> dict:
        normalized = normalize_key(key)
        if validate_key_format(normalized) is None:
            raise LicensingError(
                "ライセンスキーの形式が正しくありません",
                code="signature_invalid",
                hint="CGFT-XXX-XXXX-XXXX-XXXX の形式で入力してください",
            )
        response = activation_client.activate(normalized, fingerprint, label)
        payload = _build_credential_payload(response, normalized, fingerprint)
        credential_store.save_credential(payload, fingerprint)
        plan_gate.set_global(PlanGate(payload))
        return payload

    credential = show_activation_dialog(on_activate)
    if credential is None:
        # ユーザーがキャンセル（UI を閉じた）
        return AuthResult(
            ok=False, message="ライセンス認証がキャンセルされました"
        )
    return AuthResult(ok=True, plan=credential.get("plan"))


def _build_credential_payload(
    server_response: dict, key: str, fingerprint: str
) -> dict:
    """サーバー応答 → ローカル保存用 payload"""
    return {
        "key": key,
        "plan": server_response.get("plan"),
        "machine_fingerprint": fingerprint,
        "machine_slot": server_response.get("machine_slot"),
        "support_expires_at": server_response.get("support_expires_at"),
        "extension_expires_at": server_response.get("extension_expires_at"),
        "next_verify_at": server_response.get("next_verify_at"),
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }

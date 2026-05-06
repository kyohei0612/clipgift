"""
プラン判定 API

アプリ全体で「この機能はこのプランで使えるか」「サポート期限内か」を判定する単一窓口。
既存コードへの組込みは最小侵入: ルート / UI 表示の冒頭で `is_*` 関数を呼ぶだけ。
"""

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# シングルトン（起動時に boot_check.authenticate_or_block で初期化）
_global_plan_gate: Optional["PlanGate"] = None

PLAN_LABELS = {
    "single": "ClipGift 買い切り版",
    # 後方互換: 旧 credential が "lite" / "std" / "ext" を持っていても買い切り版として表示
    "lite": "ClipGift 買い切り版",
    "std": "ClipGift 買い切り版",
    "ext": "ClipGift 買い切り版",
}


class PlanGate:
    """
    現在のライセンス情報を保持し、機能解放の可否判定を提供する。

    credential 復号後に boot_check が一度だけインスタンス化し、
    set_global() でアプリ全体に公開する。
    """

    def __init__(self, credential: dict):
        self._credential = credential
        self.plan: str = credential.get("plan", "single")
        self.support_expires_at = self._parse_iso(
            credential.get("support_expires_at")
        )
        self.extension_expires_at = self._parse_iso(
            credential.get("extension_expires_at")
        )
        self.next_verify_at = self._parse_iso(credential.get("next_verify_at"))
        self.machine_slot = credential.get("machine_slot", 1)
        self.key = credential.get("key", "")
        self.machine_fingerprint = credential.get("machine_fingerprint", "")

    @staticmethod
    def _parse_iso(value) -> Optional[datetime]:
        if not value:
            return None
        try:
            # Python 3.10 以前の fromisoformat は 'Z' を扱わないので置換
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    # -------- 機能判定 --------

    def is_extended_features_enabled(self) -> bool:
        """
        拡張機能（Twitch / 文字起こし等）が使えるか。

        Phase 1（single プラン）では全機能解放。後方互換のため
        旧 lite キーが残っていても extension_expires_at を持っていれば許可する
        （issue_license.py で全プランが extension_months=240 で発行されるようになったため、
        現実的にはここで False になるケースはほぼ無い）。
        """
        if self.extension_expires_at is None:
            # extension 期限が無いキー = single プランで全機能解放（240 ヶ月後の値が credential に入っている前提）
            # 期限不明だがプラン上は解放する
            return True
        return datetime.now(self.extension_expires_at.tzinfo) < self.extension_expires_at

    def is_support_active(self) -> bool:
        """バグ修正サポート期間内か"""
        if self.support_expires_at is None:
            return False
        return datetime.now(self.support_expires_at.tzinfo) < self.support_expires_at

    def can_receive_updates(self) -> bool:
        """auto_update 経由でアップデート受け取れるか"""
        return self.is_support_active()

    def needs_verification(self) -> bool:
        """30 日ハートビートが必要か"""
        if self.next_verify_at is None:
            return True
        return datetime.now(self.next_verify_at.tzinfo) >= self.next_verify_at

    # -------- 表示用 --------

    def plan_label(self) -> str:
        """UI 表示用のプラン名（『ライト版』『スタンダード版』『拡張無料版』）"""
        return PLAN_LABELS.get(self.plan, "不明")

    def support_remaining_days(self) -> Optional[int]:
        """サポート残日数（負の場合は期限切れ）"""
        if self.support_expires_at is None:
            return None
        delta = self.support_expires_at - datetime.now(
            self.support_expires_at.tzinfo
        )
        return delta.days

    def to_display_dict(self) -> dict:
        """ヘッダー / 設定画面用の表示データ"""
        return {
            "plan": self.plan,
            "plan_label": self.plan_label(),
            "support_active": self.is_support_active(),
            "support_remaining_days": self.support_remaining_days(),
            "extension_enabled": self.is_extended_features_enabled(),
            "machine_slot": self.machine_slot,
            "needs_verification": self.needs_verification(),
        }


def set_global(gate: PlanGate) -> None:
    """シングルトン設定（boot_check が呼ぶ）"""
    global _global_plan_gate
    _global_plan_gate = gate


def get_plan_gate() -> Optional[PlanGate]:
    """アプリ全体からの参照用"""
    return _global_plan_gate


def is_extended_features_enabled() -> bool:
    """便利関数: グローバル PlanGate を経由した判定"""
    if _global_plan_gate is None:
        return False
    return _global_plan_gate.is_extended_features_enabled()


def can_receive_updates() -> bool:
    """便利関数: auto_update から呼ぶ"""
    if _global_plan_gate is None:
        return False
    return _global_plan_gate.can_receive_updates()


def current_plan_label() -> str:
    """便利関数: UI 表示用"""
    if _global_plan_gate is None:
        return "未認証"
    return _global_plan_gate.plan_label()

"""
インシデント状態機械

メール対話フロー（A 案）の状態遷移を管理する：

  received       ユーザーからエラー報告メール受信
       ↓ kyohei「対応して」返信
  analyzing      Claude が解析・修正中
       ↓ Claude が修正案 + ユーザー返信案を提示
  awaiting_approval  kyohei の最終 OK 待ち
       ↓ kyohei「OK」返信
  executing      Claude が push + ユーザー送信
       ↓
  done           完了

状態は support_center/incoming/{error_hash}.state.json に保存。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from support_center.config import SupportConfig


VALID_STATES = (
    "received",
    "analyzing",
    "awaiting_approval",
    "executing",
    "done",
    "error",
)


@dataclass
class IncidentState:
    """1 件のインシデントの状態。"""

    error_hash: str
    state: str = "received"
    user_email: str = ""
    original_subject: str = ""
    original_body_excerpt: str = ""    # 受信メール本文の冒頭 500 字
    original_message_id: str = ""      # 受信メールの Message-ID（返信検知用）
    repair_summary: str = ""           # Claude 解析後の修正サマリ
    user_reply_draft: str = ""         # ユーザー返信案
    affected_files: list[str] = field(default_factory=list)
    claude_session_id: str = ""        # Claude セッション継続用（resume に使う）
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _now_iso()
        if not self.updated_at:
            self.updated_at = self.created_at


def state_path(error_hash: str) -> Path:
    SupportConfig.INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    return SupportConfig.INCOMING_DIR / f"{error_hash}.state.json"


def load(error_hash: str) -> Optional[IncidentState]:
    """状態ファイルを読み込む。なければ None。"""
    path = state_path(error_hash)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return IncidentState(**data)
    except (json.JSONDecodeError, TypeError):
        return None


def save(state: IncidentState) -> None:
    """状態ファイルに書き込む。updated_at を自動更新。"""
    state.updated_at = _now_iso()
    if state.state not in VALID_STATES:
        raise ValueError(f"無効な状態: {state.state}")
    path = state_path(state.error_hash)
    path.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def transition(error_hash: str, next_state: str, **updates) -> IncidentState:
    """状態を遷移させる。存在しなければ新規作成。"""
    incident = load(error_hash) or IncidentState(error_hash=error_hash)
    incident.state = next_state
    for k, v in updates.items():
        if hasattr(incident, k):
            setattr(incident, k, v)
    save(incident)
    return incident


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

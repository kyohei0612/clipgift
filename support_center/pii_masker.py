"""
個人情報マスキングユーティリティ

エラーログからユーザー固有の情報を匿名化する。
- Windows ユーザー名 → <USER>
- フルパス → 仮名化
- メアド以外の連絡先 → マスク
- ライセンスキー → 末尾 4 桁のみ

注意:
- 完全な PII 除去は不可能（ログ内のテキストフィールドにユーザーが書いた内容まではマスクしない）
- 「ユーザーが意図せず含めてしまうもの」を保護することが目的
"""

import os
import re

# Windows ユーザー名（環境変数から取得、検出時に置換）
_CURRENT_USER = os.environ.get("USERNAME") or os.environ.get("USER") or ""

# パスのパターン（C:\Users\... / C:/Users/... / /home/... / /Users/...）
_PATH_PATTERNS = [
    re.compile(
        r"[A-Za-z]:[\\/]Users[\\/](?P<user>[^\\/]+)",
        re.IGNORECASE,
    ),
    re.compile(r"/home/(?P<user>[^/]+)"),
    re.compile(r"/Users/(?P<user>[^/]+)"),
]

# ライセンスキーのパターン (CGFT-PLAN-XXXX-XXXX-XXXX)
#
# プラン部分は列挙せず [A-Z]{2,10} で受ける。
# 現行の keys.ts は planCode() が常に "STD" を返すので LITE|STD|EXT でも一致するが、
# ここを列挙にしておくと **プランコードを増やした瞬間に、キーがマスクされないまま
# サポートメールへ送られる**（PII 漏れに直結し、しかも誰も気付けない）。
# マスク側は広めに受けるのが安全。
_LICENSE_PATTERN = re.compile(
    r"CGFT-([A-Z]{2,10})-([A-Z0-9]{4})-([A-Z0-9]{4})-([A-Z0-9]{4})",
    re.IGNORECASE,
)

# IP アドレス（v4 のみ、Phase 1 では十分）
_IP_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# ホスト名 / コンピュータ名
_HOSTNAME_VAR = os.environ.get("COMPUTERNAME") or ""

# 動画ファイル名 (.mp4 / .mkv / .webm / .ts) のフルパス末尾
_VIDEO_FILE_PATTERN = re.compile(
    r"[\\/]([^\\/]+\.(mp4|mkv|webm|ts|mov|m4a|wav|aac))",
    re.IGNORECASE,
)


def mask_log(text: str) -> str:
    """エラーログ全体を匿名化する。

    返り値はマスキング済みの文字列。原文の改行 / インデントは保持する。
    """
    if not text:
        return text

    masked = text

    # Windows ユーザー名（環境変数で検出できれば）
    if _CURRENT_USER and len(_CURRENT_USER) >= 2:
        masked = re.sub(
            r"\b" + re.escape(_CURRENT_USER) + r"\b",
            "<USER>",
            masked,
        )

    # ホスト名
    if _HOSTNAME_VAR and len(_HOSTNAME_VAR) >= 2:
        masked = re.sub(
            r"\b" + re.escape(_HOSTNAME_VAR) + r"\b",
            "<HOST>",
            masked,
        )

    # パスのユーザー名部分（複数 OS 対応）
    for pat in _PATH_PATTERNS:
        masked = pat.sub(
            lambda m: m.group(0).replace(m.group("user"), "<USER>"),
            masked,
        )

    # ライセンスキー → 末尾 4 桁のみ
    def _mask_license(m: re.Match[str]) -> str:
        return f"CGFT-{m.group(1).upper()}-****-****-{m.group(4).upper()}"

    masked = _LICENSE_PATTERN.sub(_mask_license, masked)

    # IP アドレス（プライベート以外もまとめてマスク）
    masked = _IP_PATTERN.sub("<IP>", masked)

    # 動画ファイル名 → 拡張子だけ残す
    masked = _VIDEO_FILE_PATTERN.sub(
        lambda m: f"/<VIDEO_FILE>.{m.group(2).lower()}",
        masked,
    )

    return masked


def mask_email(email: str) -> str:
    """メアドを `u***@example.com` 形式にマスク（記録 / 表示用）。

    返信用の宛先には使わない（元アドレスを保持する必要があるため）。
    """
    if not email or "@" not in email:
        return "<EMAIL>"
    local, _, domain = email.partition("@")
    if len(local) <= 1:
        return f"*@{domain}"
    return f"{local[0]}***@{domain}"


def license_tail(key: str) -> str:
    """ライセンスキーの末尾 4 桁のみ抽出（既知形式 CGFT-...-XXXX 想定）。"""
    if not key:
        return ""
    parts = key.strip().split("-")
    if len(parts) < 5:
        return ""
    return parts[-1].upper()

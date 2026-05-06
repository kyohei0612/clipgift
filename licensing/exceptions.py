"""
ライセンス関連の例外クラス
"""


class LicensingError(Exception):
    """ライセンス関連の基底例外"""

    def __init__(self, message: str, code: str = "unknown", hint: str = ""):
        super().__init__(message)
        self.code = code
        self.hint = hint


class KeyNotFoundError(LicensingError):
    """サーバー側にキーが存在しない"""

    def __init__(self, message: str = "ライセンスキーが見つかりません"):
        super().__init__(message, code="key_not_found")


class KeyRevokedError(LicensingError):
    """キーが失効している（返金 / 不正検出）"""

    def __init__(
        self,
        message: str = "このライセンスキーは失効しています",
        hint: str = "サポートまでご連絡ください",
    ):
        super().__init__(message, code="key_revoked", hint=hint)


class MaxMachinesReachedError(LicensingError):
    """マシン台数の上限超過"""

    def __init__(
        self,
        message: str = "このライセンスは既に上限台数で使用中です",
        hint: str = "別マシンで使う場合は既存マシンで「ライセンス解除」を実行してください",
    ):
        super().__init__(message, code="max_machines_reached", hint=hint)


class SignatureInvalidError(LicensingError):
    """キー形式 or 署名が不正"""

    def __init__(
        self,
        message: str = "ライセンスキーの形式または署名が不正です",
        hint: str = "キーをコピペし直してください",
    ):
        super().__init__(message, code="signature_invalid", hint=hint)


class NetworkError(LicensingError):
    """サーバーに接続できない（ネットダウン / Cloudflare ダウン）"""

    def __init__(
        self,
        message: str = "ライセンスサーバーに接続できませんでした",
        hint: str = "インターネット接続を確認してください",
    ):
        super().__init__(message, code="network_error", hint=hint)


class CredentialCorruptedError(LicensingError):
    """ローカルの credential ファイルが破損 or 改ざん"""

    def __init__(
        self,
        message: str = "ライセンス情報が破損しています",
        hint: str = "再アクティベーションが必要です",
    ):
        super().__init__(message, code="credential_corrupted", hint=hint)


class GraceExpiredError(LicensingError):
    """オフライン猶予期間を超過した"""

    def __init__(
        self,
        message: str = "ライセンスの再認証が必要です",
        hint: str = "インターネット接続を確認して再起動してください",
    ):
        super().__init__(message, code="grace_expired", hint=hint)

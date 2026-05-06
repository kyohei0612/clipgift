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

    def __init__(
        self,
        message: str = "ご入力のライセンスキーは登録されておりません。",
        hint: str = "キーの誤入力でないかご確認ください。問題が解決しない場合はサポートまでお問い合わせください。",
    ):
        super().__init__(message, code="key_not_found", hint=hint)


class KeyRevokedError(LicensingError):
    """キーが失効している（返金 / 不正検出）"""

    def __init__(
        self,
        message: str = "このライセンスキーは無効化されております。",
        hint: str = "ご不明な点がございましたらサポート窓口までお問い合わせください。",
    ):
        super().__init__(message, code="key_revoked", hint=hint)


class MaxMachinesReachedError(LicensingError):
    """マシン台数の上限超過"""

    def __init__(
        self,
        message: str = "このライセンスは既に最大台数のマシンで使用されております。",
        hint: str = "別のマシンでご利用になる場合は、既存マシンの設定画面より「ライセンス解除」を行ってください。",
    ):
        super().__init__(message, code="max_machines_reached", hint=hint)


class SignatureInvalidError(LicensingError):
    """キー形式 or 署名が不正"""

    def __init__(
        self,
        message: str = "ライセンスキーの形式が正しくありません。",
        hint: str = "キーを再度ご確認のうえ、コピー＆ペーストでご入力ください。",
    ):
        super().__init__(message, code="signature_invalid", hint=hint)


class NetworkError(LicensingError):
    """サーバーに接続できない（ネットダウン / Cloudflare ダウン）"""

    def __init__(
        self,
        message: str = "ライセンスサーバーへの接続に失敗いたしました。",
        hint: str = "インターネット接続をご確認のうえ、しばらく経ってから再度お試しください。",
    ):
        super().__init__(message, code="network_error", hint=hint)


class CredentialCorruptedError(LicensingError):
    """ローカルの credential ファイルが破損 or 改ざん"""

    def __init__(
        self,
        message: str = "ライセンス情報の読み込みに失敗いたしました。",
        hint: str = "お手数ですが、再度ライセンスキーのご入力をお願いいたします。",
    ):
        super().__init__(message, code="credential_corrupted", hint=hint)


class GraceExpiredError(LicensingError):
    """オフライン猶予期間を超過した"""

    def __init__(
        self,
        message: str = "ライセンスの再認証期限を超過いたしました。",
        hint: str = "インターネット接続をご確認のうえ、アプリケーションを再起動してください。",
    ):
        super().__init__(message, code="grace_expired", hint=hint)

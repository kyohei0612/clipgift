"""
credential（サーバーから受け取ったライセンス情報）のローカル保存

セキュリティ:
- AES-256-GCM 暗号化（GCM タグで改ざん検知）
- 鍵は machine fingerprint から PBKDF2-HMAC-SHA256 で派生（100,000 iter）
- ファイル: %APPDATA%/ClipGift/license.dat

ファイル形式（バイナリ）:
  [16 byte salt] [12 byte IV] [16 byte GCM tag] [変数長 ciphertext]
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from licensing.exceptions import CredentialCorruptedError

logger = logging.getLogger(__name__)

CREDENTIAL_FILENAME = "license.dat"
PBKDF2_ITERATIONS = 100_000
KEY_LENGTH = 32  # AES-256
SALT_LENGTH = 16
IV_LENGTH = 12  # GCM 推奨


def _appdata_dir() -> Path:
    """
    %APPDATA%/ClipGift/ ディレクトリ（なければ作成）

    Windows: C:\\Users\\<user>\\AppData\\Roaming\\ClipGift
    その他: ~/.config/ClipGift（互換用、本来は Windows 専用）
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if base:
            target = Path(base) / "ClipGift"
        else:
            target = Path.home() / "AppData" / "Roaming" / "ClipGift"
    else:
        target = Path.home() / ".config" / "ClipGift"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _credential_path() -> Path:
    return _appdata_dir() / CREDENTIAL_FILENAME


def _derive_key(machine_fingerprint: str, salt: bytes) -> bytes:
    """machine fingerprint から AES 鍵を派生"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LENGTH,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(machine_fingerprint.encode("utf-8"))


def save_credential(payload: dict, machine_fingerprint: str) -> None:
    """
    credential を暗号化してローカル保存

    Args:
        payload: サーバーから受け取った dict（plan, support_expires_at, etc.）
        machine_fingerprint: get_machine_fingerprint() の戻り値
    """
    salt = os.urandom(SALT_LENGTH)
    iv = os.urandom(IV_LENGTH)
    key = _derive_key(machine_fingerprint, salt)
    aesgcm = AESGCM(key)

    plaintext = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ciphertext_with_tag = aesgcm.encrypt(iv, plaintext, associated_data=None)

    # ファイルに書き出し: salt + iv + ciphertext_with_tag
    with open(_credential_path(), "wb") as f:
        f.write(salt)
        f.write(iv)
        f.write(ciphertext_with_tag)

    logger.info("credential 保存完了 (%d bytes)", len(plaintext))


def load_credential(machine_fingerprint: str) -> Optional[dict]:
    """
    credential を復号して読み込む

    Returns:
        payload dict、または None（ファイル未存在）

    Raises:
        CredentialCorruptedError: ファイル破損 / 改ざん / マシン変更時
    """
    path = _credential_path()
    if not path.exists():
        return None

    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        logger.warning("credential 読み込み失敗: %s", e)
        return None

    if len(data) < SALT_LENGTH + IV_LENGTH + 16:
        # 16 = GCM tag 長
        logger.warning("credential ファイルサイズ不正: %d bytes", len(data))
        raise CredentialCorruptedError()

    salt = data[:SALT_LENGTH]
    iv = data[SALT_LENGTH : SALT_LENGTH + IV_LENGTH]
    ciphertext_with_tag = data[SALT_LENGTH + IV_LENGTH :]

    try:
        key = _derive_key(machine_fingerprint, salt)
        aesgcm = AESGCM(key)
        plaintext = aesgcm.decrypt(iv, ciphertext_with_tag, associated_data=None)
        payload = json.loads(plaintext.decode("utf-8"))
        if not isinstance(payload, dict):
            raise CredentialCorruptedError()
        return payload
    except Exception as e:
        # GCM タグ不一致 / 鍵不一致 / JSON パース失敗
        logger.warning("credential 復号失敗（改ざん or マシン変更の可能性）: %s", e)
        raise CredentialCorruptedError() from e


def delete_credential() -> None:
    """credential ファイルを削除（再アクティベーション用）"""
    path = _credential_path()
    if path.exists():
        try:
            path.unlink()
            logger.info("credential ファイルを削除")
        except OSError as e:
            logger.warning("credential 削除失敗: %s", e)


def credential_exists() -> bool:
    return _credential_path().exists()

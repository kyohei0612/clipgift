"""
マシン fingerprint 取得

CPU ID + MAC アドレス + Volume Serial を組み合わせて SHA-256 化した値。
ハードウェア構成変動への耐性として「3 要素中 2 つ一致」のソフトマッチングも提供。
"""

import hashlib
import logging
import os
import subprocess
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


def _get_cpu_id() -> Optional[str]:
    """Windows: wmic で CPU ID 取得（変動しにくい）"""
    try:
        result = subprocess.run(
            ["wmic", "cpu", "get", "ProcessorId"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        lines = [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip() and "ProcessorId" not in line
        ]
        if lines:
            return lines[0]
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        logger.warning("CPU ID 取得失敗: %s", e)
    return None


def _get_mac_address() -> Optional[str]:
    """物理 MAC アドレス取得（uuid.getnode）"""
    try:
        node = uuid.getnode()
        # 仮想 MAC（マルチキャストビット）でないか軽くチェック
        if (node >> 40) & 0x01:
            logger.warning("仮想 MAC を検出、参考値として使用")
        return f"{node:012x}"
    except Exception as e:
        logger.warning("MAC アドレス取得失敗: %s", e)
        return None


def _get_volume_serial() -> Optional[str]:
    """C ドライブの Volume Serial Number 取得（Windows）"""
    try:
        result = subprocess.run(
            ["cmd", "/c", "vol", "C:"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        # "ボリューム シリアル番号は XXXX-XXXX です" の最終行から抽出
        for line in result.stdout.splitlines():
            line = line.strip()
            if "-" in line and len(line) > 9:
                # 末尾の "XXXX-XXXX" 形式を探す
                parts = line.split()
                for part in parts:
                    if (
                        len(part) == 9
                        and part[4] == "-"
                        and part.replace("-", "").isalnum()
                    ):
                        return part
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        logger.warning("Volume Serial 取得失敗: %s", e)
    return None


def get_machine_fingerprint() -> str:
    """
    マシン固有の fingerprint を返す（SHA-256 hex）

    取得失敗した要素は "unknown" として組み込まれる。
    全要素失敗するとほぼ無意味だが、それでも何か返す（起動阻害よりマシ）。
    """
    cpu_id = _get_cpu_id() or "unknown_cpu"
    mac = _get_mac_address() or "unknown_mac"
    vol_serial = _get_volume_serial() or "unknown_vol"

    raw = f"{cpu_id}|{mac}|{vol_serial}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    logger.debug("Machine fingerprint generated: %s...", digest[:16])
    return digest


def get_machine_components() -> tuple[str, str, str]:
    """3 要素を個別に返す（ソフトマッチング用、保存はハッシュ化推奨）"""
    cpu_id = _get_cpu_id() or "unknown_cpu"
    mac = _get_mac_address() or "unknown_mac"
    vol_serial = _get_volume_serial() or "unknown_vol"
    return (cpu_id, mac, vol_serial)


def get_machine_label() -> str:
    """UI 表示用のマシン名（コンピューター名）"""
    try:
        import platform

        return platform.node() or "unknown-machine"
    except Exception:
        return "unknown-machine"

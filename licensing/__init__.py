"""
ライセンス・認証モジュール

クリップギフトの 3 プラン制（lite / std / ext）に対応する認証基盤。
詳細設計: .company/engineering/docs/license-system.md
"""

from licensing.exceptions import (
    LicensingError,
    KeyNotFoundError,
    KeyRevokedError,
    MaxMachinesReachedError,
    SignatureInvalidError,
    NetworkError,
    CredentialCorruptedError,
)
from licensing.plan_gate import PlanGate, get_plan_gate
from licensing.boot_check import authenticate_or_block, AuthResult

__all__ = [
    "LicensingError",
    "KeyNotFoundError",
    "KeyRevokedError",
    "MaxMachinesReachedError",
    "SignatureInvalidError",
    "NetworkError",
    "CredentialCorruptedError",
    "PlanGate",
    "get_plan_gate",
    "authenticate_or_block",
    "AuthResult",
]

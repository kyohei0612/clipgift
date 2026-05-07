"""support_center.error_reporter の単体テスト

ネットワーク呼び出し（requests.post）はモック化してテスト。
"""

import json
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support_center.error_reporter import (
    ErrorReportError,
    build_payload,
    report_error,
)


class TestBuildPayload:
    def test_basic_payload_keys(self):
        payload = build_payload(
            app_version="1.0.55",
            license_key="CGFT-STD-AAAA-BBBB-CCCC",
            user_email="user@example.com",
            user_comment="エラーが出ました",
            error_log="2026-05-07 ERROR: something broke",
        )
        assert payload["app_version"] == "1.0.55"
        assert payload["license_tail"] == "CCCC"
        assert payload["user_email"] == "user@example.com"
        assert "user_comment" in payload
        assert "error_log" in payload
        assert "env_info" in payload
        assert "python" in payload["env_info"]

    def test_log_truncation_on_huge_input(self):
        # 500 行のログ → 200 行に丸めらる
        huge_log = "\n".join(f"line {i}" for i in range(500))
        payload = build_payload(
            app_version="1.0.0",
            license_key="",
            user_email="",
            user_comment="",
            error_log=huge_log,
        )
        # 後ろから 200 行 + 省略行 1 行 = 201 行以下
        assert payload["error_log"].count("\n") <= 201
        assert "省略" in payload["error_log"]

    def test_pii_masking_applied(self):
        payload = build_payload(
            app_version="1.0.0",
            license_key="",
            user_email="",
            user_comment=r"path: C:\Users\testuser\file.mp4",
            error_log=r"open(C:\Users\testuser\file.mp4)",
        )
        # マスキング適用済み
        assert "testuser" not in payload["user_comment"]
        assert "testuser" not in payload["error_log"]

    def test_extra_env_merged(self):
        payload = build_payload(
            app_version="1.0",
            license_key="",
            user_email="",
            user_comment="",
            error_log="",
            extra_env={"ffmpeg": "6.1"},
        )
        assert payload["env_info"]["ffmpeg"] == "6.1"


class TestSendReport:
    @patch("support_center.error_reporter.requests.post")
    def test_success_returns_json(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok", "report_id": "abc123"}
        mock_post.return_value = mock_resp

        result = report_error(
            app_version="1.0",
            license_key="",
            user_email="",
            user_comment="test",
            error_log="test log",
        )
        assert result["status"] == "ok"
        assert result["report_id"] == "abc123"

        call_args = mock_post.call_args
        assert "report" in call_args[0][0]  # URL に "report" が含まれる
        sent_body = json.loads(call_args[1]["data"].decode("utf-8"))
        assert sent_body["app_version"] == "1.0"

    @patch("support_center.error_reporter.requests.post")
    def test_http_error_raises(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_post.return_value = mock_resp

        with pytest.raises(ErrorReportError):
            report_error(
                app_version="1.0",
                license_key="",
                user_email="",
                user_comment="",
                error_log="",
            )

    @patch("support_center.error_reporter.requests.post")
    def test_connection_error_raises(self, mock_post):
        import requests as _req
        mock_post.side_effect = _req.ConnectionError("network down")

        with pytest.raises(ErrorReportError):
            report_error(
                app_version="1.0",
                license_key="",
                user_email="",
                user_comment="",
                error_log="",
            )

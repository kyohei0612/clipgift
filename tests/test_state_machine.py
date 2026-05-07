"""support_center.state_machine の単体テスト"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support_center import state_machine
from support_center.config import SupportConfig


@pytest.fixture
def tmp_incoming(monkeypatch):
    """INCOMING_DIR を一時ディレクトリに差し替え"""
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(SupportConfig, "INCOMING_DIR", Path(tmp))
        yield Path(tmp)


class TestIncidentState:
    def test_default_values(self):
        state = state_machine.IncidentState(error_hash="abc123")
        assert state.error_hash == "abc123"
        assert state.state == "received"
        assert state.user_email == ""
        assert state.created_at != ""
        assert state.updated_at != ""

    def test_load_returns_none_when_not_exists(self, tmp_incoming):
        assert state_machine.load("nonexistent") is None

    def test_save_and_load_roundtrip(self, tmp_incoming):
        state = state_machine.IncidentState(
            error_hash="abc123",
            user_email="user@example.com",
            original_subject="【ClipGift エラー報告】v1.0 - test",
        )
        state_machine.save(state)

        loaded = state_machine.load("abc123")
        assert loaded is not None
        assert loaded.error_hash == "abc123"
        assert loaded.user_email == "user@example.com"
        assert loaded.original_subject == "【ClipGift エラー報告】v1.0 - test"

    def test_invalid_state_raises(self, tmp_incoming):
        state = state_machine.IncidentState(
            error_hash="abc",
            state="invalid_phase",
        )
        with pytest.raises(ValueError):
            state_machine.save(state)


class TestTransition:
    def test_creates_new_state_if_not_exists(self, tmp_incoming):
        state = state_machine.transition(
            "newhash",
            "received",
            user_email="x@y.com",
        )
        assert state.error_hash == "newhash"
        assert state.state == "received"
        assert state.user_email == "x@y.com"

        loaded = state_machine.load("newhash")
        assert loaded.user_email == "x@y.com"

    def test_updates_existing_state(self, tmp_incoming):
        state_machine.transition("hash1", "received", user_email="a@b.com")
        state_machine.transition("hash1", "analyzing")

        loaded = state_machine.load("hash1")
        assert loaded.state == "analyzing"
        assert loaded.user_email == "a@b.com"  # 上書きされない

    def test_full_phase_progression(self, tmp_incoming):
        for phase in ("received", "analyzing", "awaiting_approval", "executing", "done"):
            state_machine.transition("hash2", phase)
            loaded = state_machine.load("hash2")
            assert loaded.state == phase

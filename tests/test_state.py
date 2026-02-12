"""Tests for MigrationState."""

import json

import pytest
from migrate import MigrationState


class TestMigrationState:
    def test_new_state_empty(self, tmp_state):
        assert tmp_state.pages == {}

    def test_save_and_load(self, tmp_state):
        record = MigrationState.new_page_record("1", "Test Page", "SPACE")
        tmp_state.set_page("1", record)

        # Load into fresh instance
        loaded = MigrationState(path=tmp_state.path).load()
        assert "1" in loaded.pages
        assert loaded.pages["1"]["title"] == "Test Page"

    def test_new_page_record_defaults(self):
        rec = MigrationState.new_page_record("42", "My Page", "KEY", parent_id="10")
        assert rec["page_id"] == "42"
        assert rec["title"] == "My Page"
        assert rec["space_key"] == "KEY"
        assert rec["parent_id"] == "10"
        assert rec["has_children"] is False
        assert rec["status"] == "pending"
        assert rec["attachments"] == []
        assert rec["comments"] == []
        assert rec["error"] is None

    def test_status_transitions(self, tmp_state):
        rec = MigrationState.new_page_record("1", "Page", "SP")
        tmp_state.set_page("1", rec)
        assert tmp_state.get_page("1")["status"] == "pending"

        rec["status"] = "exported"
        tmp_state.set_page("1", rec)
        assert tmp_state.get_page("1")["status"] == "exported"

        rec["status"] = "converted"
        tmp_state.set_page("1", rec)
        assert tmp_state.get_page("1")["status"] == "converted"

        rec["status"] = "uploaded"
        tmp_state.set_page("1", rec)
        assert tmp_state.get_page("1")["status"] == "uploaded"

    def test_get_pages_by_status(self, tmp_state):
        for i, status in enumerate(["pending", "exported", "exported", "failed"]):
            rec = MigrationState.new_page_record(str(i), f"Page {i}", "SP")
            rec["status"] = status
            tmp_state.set_page(str(i), rec)

        assert len(tmp_state.get_pages_by_status("exported")) == 2
        assert len(tmp_state.get_pages_by_status("pending")) == 1
        assert len(tmp_state.get_pages_by_status("failed")) == 1
        assert len(tmp_state.get_pages_by_status("uploaded")) == 0

    def test_summary(self, tmp_state):
        for i, status in enumerate(["exported", "exported", "converted", "failed"]):
            rec = MigrationState.new_page_record(str(i), f"Page {i}", "SP")
            rec["status"] = status
            tmp_state.set_page(str(i), rec)

        summary = tmp_state.summary()
        assert summary == {"exported": 2, "converted": 1, "failed": 1}

    def test_get_nonexistent_page(self, tmp_state):
        assert tmp_state.get_page("nonexistent") is None

    def test_atomic_save(self, tmp_state):
        """Verify save uses tmp+rename pattern (file exists after save)."""
        rec = MigrationState.new_page_record("1", "Page", "SP")
        tmp_state.set_page("1", rec)
        assert tmp_state.path.exists()

        # Verify it's valid JSON
        data = json.loads(tmp_state.path.read_text(encoding="utf-8"))
        assert "1" in data

    def test_load_nonexistent_file(self, tmp_path):
        """Loading from non-existent file should give empty state."""
        state = MigrationState(path=tmp_path / "does-not-exist.json")
        state.load()
        assert state.pages == {}

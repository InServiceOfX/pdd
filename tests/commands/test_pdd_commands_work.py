"""Tests for the `pdd work` local work item command group."""
from __future__ import annotations

import json
from pathlib import Path

import click
from click.testing import CliRunner

from pdd import local_work_items
from pdd.commands import register_commands
from pdd.commands.work import work


def test_help_describes_the_github_free_replacement() -> None:
    runner = CliRunner()
    result = runner.invoke(work, ["--help"])

    assert result.exit_code == 0
    assert "GitHub-free replacement" in result.output
    assert "local:<number>" in result.output


def test_work_is_registered_on_the_cli() -> None:
    @click.group()
    def cli() -> None:
        """Root."""

    register_commands(cli)
    assert "work" in cli.commands


def test_new_creates_an_item_and_prints_the_reference(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as cwd:
        result = runner.invoke(
            work,
            ["new", "--title", "Ring buffer drops a frame", "--body", "Overwrites index 0."],
        )
        assert result.exit_code == 0, result.output
        assert "Created local:1" in result.output
        assert "pdd bug local:1" in result.output

        item = local_work_items.load_work_item(Path(cwd), 1)
        assert item["title"] == "Ring buffer drops a frame"
        assert item["body"] == "Overwrites index 0."


def test_new_reads_body_from_a_file(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as cwd:
        body_file = Path(cwd) / "repro.md"
        body_file.write_text("Fill to capacity, then push once more.", encoding="utf-8")

        result = runner.invoke(
            work, ["new", "--title", "Repro", "--body-file", str(body_file)]
        )
        assert result.exit_code == 0, result.output
        assert local_work_items.load_work_item(Path(cwd), 1)["body"].startswith("Fill to")


def test_new_rejects_both_body_sources(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as cwd:
        body_file = Path(cwd) / "b.md"
        body_file.write_text("x", encoding="utf-8")
        result = runner.invoke(
            work,
            ["new", "--title", "T", "--body", "inline", "--body-file", str(body_file)],
        )
        assert result.exit_code != 0
        assert "not both" in result.output


def test_new_emits_json(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(work, ["new", "--title", "T", "--body", "B", "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["number"] == 1
        assert payload["state"] == "open"


def test_list_reports_empty_store_helpfully(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        result = runner.invoke(work, ["list"])
        assert result.exit_code == 0
        assert "No local work items" in result.output


def test_list_filters_by_state(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        runner.invoke(work, ["new", "--title", "first"])
        runner.invoke(work, ["new", "--title", "second"])
        runner.invoke(work, ["close", "1"])

        open_only = runner.invoke(work, ["list", "--state", "open"])
        assert "local:2" in open_only.output
        assert "local:1" not in open_only.output

        closed_only = runner.invoke(work, ["list", "--state", "closed"])
        assert "local:1" in closed_only.output
        assert "local:2" not in closed_only.output


def test_comment_appends_and_show_renders_it(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        runner.invoke(work, ["new", "--title", "Fix parser"])
        commented = runner.invoke(
            work, ["comment", "1", "--text", "Use a ring buffer.", "--author", "ernest"]
        )
        assert commented.exit_code == 0, commented.output

        shown = runner.invoke(work, ["show", "1"])
        assert shown.exit_code == 0
        assert "Use a ring buffer." in shown.output
        assert "ernest" in shown.output


def test_comment_rejects_empty_body(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        runner.invoke(work, ["new", "--title", "Fix parser"])
        result = runner.invoke(work, ["comment", "1", "--text", "   "])
        assert result.exit_code != 0
        assert "must not be empty" in result.output


def test_commands_fail_clearly_on_a_missing_item(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        for args in (["show", "7"], ["comment", "7", "--text", "hi"], ["close", "7"]):
            result = runner.invoke(work, args)
            assert result.exit_code != 0
            assert "No local work item 7" in result.output


def test_close_and_reopen_round_trip(tmp_path: Path) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as cwd:
        runner.invoke(work, ["new", "--title", "Fix parser"])

        runner.invoke(work, ["close", "1"])
        assert local_work_items.load_work_item(Path(cwd), 1)["state"] == "closed"

        runner.invoke(work, ["reopen", "1"])
        assert local_work_items.load_work_item(Path(cwd), 1)["state"] == "open"

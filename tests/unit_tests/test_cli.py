"""Tests for CLI argument parsing and validation."""

import argparse
import json
from contextlib import redirect_stdout
from contextlib import redirect_stderr
from io import StringIO

import pytest

from contextseek.cli.main import build_parser, run_cli, _positive_int
from contextseek.client.contextseek import ContextSeek


class TestPositiveInt:
    def test_positive_value(self):
        assert _positive_int("5") == 5
        assert _positive_int("1") == 1

    def test_zero_raises(self):
        with pytest.raises(argparse.ArgumentTypeError, match="must be > 0"):
            _positive_int("0")

    def test_negative_raises(self):
        with pytest.raises(argparse.ArgumentTypeError, match="must be > 0"):
            _positive_int("-1")

    def test_non_integer_raises(self):
        with pytest.raises(ValueError):
            _positive_int("abc")


class TestRetrieveKValidation:
    def test_k_zero_rejected(self) -> None:
        parser = build_parser()
        err = StringIO()
        with redirect_stderr(err), pytest.raises(SystemExit):
            parser.parse_args(["retrieve", "--scope", "t", "--query", "q", "--k", "0"])
        assert "must be > 0" in err.getvalue()

    def test_k_negative_rejected(self) -> None:
        parser = build_parser()
        err = StringIO()
        with redirect_stderr(err), pytest.raises(SystemExit):
            parser.parse_args(["retrieve", "--scope", "t", "--query", "q", "--k", "-1"])
        assert "must be > 0" in err.getvalue()

    def test_k_positive_accepted(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["retrieve", "--scope", "t", "--query", "q", "--k", "5"]
        )
        assert args.k == 5


class TestRetrieveTagFiltering:
    def test_retrieve_accepts_tags_flag(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["retrieve", "--scope", "t", "--query", "q", "--tags", "a,b"]
        )

        assert args.tags == "a,b"

    def test_retrieve_filters_results_by_all_tags(self) -> None:
        ctx = ContextSeek()
        kept = ctx.add(
            "database backup runbook",
            scope="t/p",
            source="test",
            tags=["ops", "database"],
        )
        ctx.add(
            "database onboarding guide",
            scope="t/p",
            source="test",
            tags=["docs", "database"],
        )
        out = StringIO()

        with redirect_stdout(out):
            code = run_cli(
                [
                    "retrieve",
                    "--scope",
                    "t/p",
                    "--query",
                    "database",
                    "--tags",
                    "ops,database",
                    "--json",
                ],
                client=ctx,
            )

        payload = json.loads(out.getvalue())
        assert code == 0
        assert [item["id"] for item in payload["items"]] == [kept.id]


class TestExpandOutput:
    def test_expand_reports_missing_ids(self) -> None:
        ctx = ContextSeek()
        item = ctx.add("expand target", scope="t/p", source="test")
        out = StringIO()

        with redirect_stdout(out):
            code = run_cli(
                [
                    "expand",
                    "--scope",
                    "t/p",
                    "--ids",
                    f"{item.id},missing-id",
                ],
                client=ctx,
            )

        payload = json.loads(out.getvalue())
        assert code == 0
        assert [it["id"] for it in payload["items"]] == [item.id]
        assert payload["missing_ids"] == ["missing-id"]

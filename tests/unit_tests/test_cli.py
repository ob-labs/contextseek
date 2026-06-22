"""Tests for CLI argument parsing and validation."""

import argparse
import json
from contextlib import redirect_stdout
from contextlib import redirect_stderr
from io import StringIO

import pytest

from contextseek.cli.main import build_parser, run_cli, _positive_int
from contextseek.client.contextseek import ContextSeek
from contextseek.domain.tools import default_tool_specs


class _ToolSpecClient:
    def tools(self):
        return default_tool_specs()


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


class TestToolsOutput:
    def test_openai_format_outputs_valid_tool_definitions(self, capsys):
        assert run_cli(["tools", "--format", "openai"], client=_ToolSpecClient()) == 0

        payload = json.loads(capsys.readouterr().out)
        tool_names = {tool["function"]["name"] for tool in payload}

        assert tool_names == {"retrieve", "expand"}
        retrieve_tool = next(
            tool["function"]
            for tool in payload
            if tool["function"]["name"] == "retrieve"
        )
        expand_tool = next(
            tool["function"] for tool in payload if tool["function"]["name"] == "expand"
        )

        assert all(tool["type"] == "function" for tool in payload)
        assert retrieve_tool["parameters"]["type"] == "object"
        assert retrieve_tool["parameters"]["required"] == ["query", "scope"]
        assert set(retrieve_tool["parameters"]["properties"]) == {
            "query",
            "scope",
            "k",
            "full",
        }
        assert expand_tool["parameters"]["required"] == ["ids", "scope"]
        assert expand_tool["parameters"]["properties"]["ids"]["type"] == "array"

    def test_anthropic_format_outputs_valid_tool_definitions(self, capsys):
        assert (
            run_cli(["tools", "--format", "anthropic"], client=_ToolSpecClient()) == 0
        )

        payload = json.loads(capsys.readouterr().out)
        tool_names = {tool["name"] for tool in payload}

        assert tool_names == {"retrieve", "expand"}
        retrieve_tool = next(tool for tool in payload if tool["name"] == "retrieve")
        expand_tool = next(tool for tool in payload if tool["name"] == "expand")

        assert all("function" not in tool for tool in payload)
        assert retrieve_tool["input_schema"]["type"] == "object"
        assert retrieve_tool["input_schema"]["required"] == ["query", "scope"]
        assert set(retrieve_tool["input_schema"]["properties"]) == {
            "query",
            "scope",
            "k",
            "full",
        }
        assert expand_tool["input_schema"]["required"] == ["ids", "scope"]
        assert expand_tool["input_schema"]["properties"]["ids"]["items"] == {
            "type": "string"
        }


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

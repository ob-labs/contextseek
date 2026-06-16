"""Tests for CLI argument parsing and validation."""

import argparse
from contextlib import redirect_stderr
from io import StringIO

import pytest

from contextseek.cli.main import build_parser, _positive_int


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
            parser.parse_args(
                ["retrieve", "--scope", "t", "--query", "q", "--k", "-1"]
            )
        assert "must be > 0" in err.getvalue()

    def test_k_positive_accepted(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["retrieve", "--scope", "t", "--query", "q", "--k", "5"]
        )
        assert args.k == 5

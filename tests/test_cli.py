"""Tests for the gwlab CLI dispatch."""

import pytest

from gwlab import cli


def test_subcommands_route_to_their_handlers() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["start-of-day"]).handler is cli.start_of_day
    assert parser.parse_args(["run"]).handler is cli.run
    assert parser.parse_args(["end-of-day"]).handler is cli.end_of_day


@pytest.mark.parametrize(
    ("command", "task"),
    [("start-of-day", "C5"), ("run", "C1/C6"), ("end-of-day", "C5")],
)
def test_main_dispatches_and_reports_not_yet(
    command: str, task: str, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cli.main([command])
    assert rc == 2
    err = capsys.readouterr().err
    assert "not yet implemented" in err
    assert task in err


def test_counterparty_flag_selects_the_queue_set(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["--counterparty", "CPA", "start-of-day"])
    assert "GW.CPA.REQUEST.AUDIT" in capsys.readouterr().err


def test_missing_subcommand_is_an_error() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])

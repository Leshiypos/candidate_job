"""Run the advance payments report from three local CSV exports."""

import argparse
from datetime import date
from pathlib import Path
import sys

from .calculation import calculate_report
from .input import InputError, load_data, parse_date
from .output import write_report


def _report_date(value: str) -> date:
    try:
        return parse_date(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m advance_report",
        description="Построить отчёт по работам и выплатам на конец дня.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--data-dir", type=Path, required=True, metavar="КАТАЛОГ",
        help="каталог с contracts.csv, works.csv и payments.csv",
    )
    parser.add_argument(
        "--date", type=_report_date, required=True, metavar="YYYY-MM-DD",
        help="дата отчёта",
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True, metavar="КАТАЛОГ",
        help="каталог для отчёта Markdown",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Write the report and print its path, or return 1 on I/O errors.

    An input validation error also returns 1. Argument parsing exits with 0
    for help and 2 for invalid arguments.
    """
    args = _parser().parse_args(argv)
    try:
        data = load_data(args.data_dir)
    except InputError as error:
        print(f"Ошибка входных данных — {error}", file=sys.stderr)
        return 1

    report = calculate_report(data, args.date)
    try:
        path = write_report(report, args.output_dir)
    except OSError as error:
        print(f"Ошибка записи отчёта — {error}", file=sys.stderr)
        return 1

    print(path)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    raise SystemExit(main())

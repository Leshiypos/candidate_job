"""Exercise the CLI entry point and the written report."""

import csv
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from advance_report.calculation import ContractResult, Report, WorkResult
from advance_report.models import WorkStatus
from advance_report.output import render_report


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTROL_DATE = "2026-11-10"
CONTROL_REPORT = f"report-{CONTROL_DATE}.md"


class CliTests(unittest.TestCase):
    """Run the report as a command with isolated output and test inputs."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.test_dir = Path(temporary.name)
        self.output_dir = self.test_dir / "отчёты с пробелом"

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "advance_report", *arguments],
            cwd=PROJECT_ROOT, capture_output=True, encoding="utf-8",
            check=False,
        )

    def _run_report(
        self, report_date: str = CONTROL_DATE, data_dir: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self._run(
            "--data-dir", str(data_dir or PROJECT_ROOT / "data"),
            "--date", report_date, "--output-dir", str(self.output_dir),
        )

    def _read_control_report(self) -> str:
        return (self.output_dir / CONTROL_REPORT).read_text(encoding="utf-8")

    def test_help(self) -> None:
        result = self._run("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Построить отчёт", result.stdout)
        for option in ("--data-dir", "--date", "--output-dir", "YYYY-MM-DD"):
            self.assertIn(option, result.stdout)

    def test_control_report_has_payment_and_work_amounts(self) -> None:
        result = self._run_report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            Path(result.stdout.strip()), self.output_dir / CONTROL_REPORT
        )
        report = self._read_control_report()
        self.assertIn(f"# Отчёт по договорам на {CONTROL_DATE}\n", report)
        self.assertIn("Доля без привязки (расчёт)", report)
        self.assertIn("Распределение условно.", report)
        d1, d2 = report.split("## Договор D-2\n")
        self.assertIn("| Чистые фактические выплаты | 315000.00 |", d1)
        self.assertIn("| Осталось по договору | 285000.00 |", d1)
        self.assertIn("| Сумма остатков по работам | 185000.00 |", d1)
        self.assertIn("| Чистые фактические выплаты | 75000.00 |", d2)
        self.assertIn("| Осталось по договору | 15000.00 |", d2)
        self.assertIn("Иван Петров, дизайнер", d2)
        self.assertIn(
            "| W-14 | Съёмка: осень | Принята | 100000.00 | 100000.00 | "
            "15000.00 | 52577.32 | 67577.32 | 32422.68 | 0.00 |", d1,
        )
        self.assertIn("| W-23 | Упаковка | Запланирована |", d2)

    def test_invalid_date_exits_before_creating_output(self) -> None:
        cases = (
            ("20261110", "ожидается дата в формате YYYY-MM-DD"),
            ("2026-02-29", "календарная дата не существует"),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                result = self._run_report(report_date=value)
                self.assertEqual(result.returncode, 2)
                self.assertIn("--date", result.stderr)
                self.assertIn(expected, result.stderr)
                self.assertFalse(self.output_dir.exists())

    def test_missing_inputs_exit_before_creating_output(self) -> None:
        missing = self.test_dir / "missing"
        result = self._run_report(data_dir=missing)
        self.assertEqual(result.returncode, 1)
        self.assertIn("contracts.csv: файл не найден", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(self.output_dir.exists())

    def test_report_can_be_regenerated_at_the_same_path(self) -> None:
        self.output_dir.mkdir()
        report_path = self.output_dir / CONTROL_REPORT
        report_path.write_text("Устаревший отчёт", encoding="utf-8")
        result = self._run_report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Устаревший отчёт", self._read_control_report())
        self.assertEqual(list(self.output_dir.iterdir()), [report_path])

    def test_write_error_cleans_up_temporary_file(self) -> None:
        report_path = self.output_dir / CONTROL_REPORT
        report_path.mkdir(parents=True)
        result = self._run_report()
        self.assertEqual(result.returncode, 1)
        self.assertIn("Ошибка записи отчёта", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertTrue(report_path.is_dir())
        self.assertEqual(list(self.output_dir.iterdir()), [report_path])

    def test_before_contracts_were_signed(self) -> None:
        report_date = "2026-01-01"
        result = self._run_report(report_date=report_date)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self.output_dir / f"report-{report_date}.md"
        body = report.read_text(encoding="utf-8")
        self.assertIn("подписанных договоров нет", body)
        self.assertNotIn("## Договор", body)

    def test_text_from_csv_is_escaped_in_markdown(self) -> None:
        title = (
            'Съёмка "Луч" | *эскиз*\r\n<изображение> & [ссылка](пример)'
            "\x00\x1b"
        )
        inputs = {
            "contracts.csv": (
                ("contract_id", "contractor", "amount", "signed_at"),
                ("D#1", 'Студия "Луч", ИП', "1.01", "2026-01-01"),
            ),
            "works.csv": (
                (
                    "work_id", "contract_id", "title", "price", "status",
                    "completed_at", "cancelled_at",
                ),
                ("W|1", "D#1", title, "1.01", "planned", "", ""),
            ),
            "payments.csv": (
                (
                    "payment_id", "contract_id", "work_id", "purpose",
                    "amount", "status", "created_at", "paid_at", "refunded_at",
                ),
            ),
        }
        data_dir = self.test_dir / "csv"
        data_dir.mkdir()
        for name, rows in inputs.items():
            path = data_dir / name
            with path.open("w", encoding="utf-8", newline="") as handle:
                csv.writer(handle).writerows(rows)

        result = self._run_report(data_dir=data_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = self._read_control_report()
        self.assertIn("## Договор D&#35;1\n", report)
        self.assertIn('Подрядчик — Студия "Луч", ИП', report)
        lines = report.splitlines()
        work_row, = (line for line in lines if "W&#124;1" in line)
        self.assertEqual(work_row.count("|"), 11)
        self.assertIn('Съёмка "Луч" &#124; &#42;эскиз&#42;', work_row)
        self.assertIn("⏎ &#60;изображение&#62; &#38;", work_row)
        self.assertIn("&#91;ссылка&#93;(пример)", work_row)
        self.assertIn(r"\u0000\u001b", work_row)
        self.assertNotIn("\x00", report)
        self.assertNotIn("\x1b", report)
        self.assertIn("| 1.01 | 1.01 | 0.00 | 0.00 | 0.00 | 1.01 |", work_row)


class MarkdownOutputTests(unittest.TestCase):
    """Check monetary values beyond the interpreter's integer text limit."""

    def test_very_large_amount_is_exact_and_difference_is_signed(self) -> None:
        amount = 10 ** 4502 + 1
        work = WorkResult(
            "W", "Тест", WorkStatus.PLANNED, amount, amount, 0, 0,
        )
        contract = ContractResult("D", "Студия", 0, (work,), 0)
        report = render_report(Report(date(2026, 11, 10), (contract,)))
        expected = "1" + "0" * 4500 + ".01"
        self.assertIn(f"| Расчётные цены работ | {expected} |", report)
        self.assertIn(
            f"| Сумма договора минус расчётные цены | -{expected} |", report,
        )

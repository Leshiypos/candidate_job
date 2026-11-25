"""Exercise CSV validation with local exports and synthetic records."""

import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

from advance_report.input import InputError, load_data
from advance_report.models import PaymentStatus, WorkStatus


CONTRACT_HEADER = ("contract_id", "contractor", "amount", "signed_at")
WORK_HEADER = (
    "work_id", "contract_id", "title", "price", "status", "completed_at",
    "cancelled_at",
)
PAYMENT_HEADER = (
    "payment_id", "contract_id", "work_id", "purpose", "amount", "status",
    "created_at", "paid_at", "refunded_at",
)
CONTRACT = ("D-01", 'Студия "Луч", ИП', "105.08", "2026-01-01")
WORK = ("W-01", "D-01", "Съёмка\nв студии", "50.1", "planned", "", "")
PAYMENT = (
    "P-01", "D-01", "W-01", "Оплата работ", "1.01", "paid", "2026-01-02",
    "2026-01-03", "",
)


class InputTests(unittest.TestCase):
    """Verify typed results and field-specific errors from the CSV loader."""

    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.data_dir = Path(temporary_directory.name)
        self.write_csv("contracts.csv", CONTRACT_HEADER, CONTRACT)
        self.write_csv("works.csv", WORK_HEADER, WORK)
        self.write_csv("payments.csv", PAYMENT_HEADER, PAYMENT)

    def write_csv(
        self, name: str, header: tuple[str, ...], *rows: tuple[str, ...]
    ) -> None:
        """Replace one synthetic export, keeping Python's CSV quoting rules."""
        path = self.data_dir / name
        with path.open("w", encoding="utf-8-sig", newline="") as output:
            csv.writer(output).writerows((header, *rows))

    def assert_invalid(self, expected: str) -> None:
        """Check the first validation error for the expected detail."""
        with self.assertRaises(InputError) as raised:
            load_data(self.data_dir)
        self.assertIn(expected, str(raised.exception))

    def test_original_exports(self) -> None:
        data_dir = Path(__file__).resolve().parents[1] / "data"
        data = load_data(data_dir)
        self.assertEqual(len(data.contracts), 2)
        self.assertEqual(len(data.works), 9)
        self.assertEqual(len(data.payments), 8)
        self.assertEqual(data.contracts[1].contractor, "Иван Петров, дизайнер")

    def test_reads_quoted_text_dates_and_exact_kopecks(self) -> None:
        data = load_data(self.data_dir)
        contract, = data.contracts
        work, = data.works
        payment, = data.payments
        self.assertEqual(contract.contractor, 'Студия "Луч", ИП')
        self.assertEqual(work.title, "Съёмка\nв студии")
        self.assertEqual(contract.signed_at, date(2026, 1, 1))
        self.assertEqual(contract.amount_kopecks, 10508)
        self.assertEqual(work.price_kopecks, 5010)
        self.assertEqual(payment.amount_kopecks, 101)
        self.assertIsInstance(payment.amount_kopecks, int)
        self.assertIs(work.status, WorkStatus.PLANNED)
        self.assertIs(payment.status, PaymentStatus.PAID)
        self.assertIsNone(work.completed_at)
        self.assertIsNone(payment.refunded_at)

    def test_zero_amount_and_reordered_header(self) -> None:
        self.write_csv(
            "contracts.csv",
            ("amount", "signed_at", "contractor", "contract_id"),
            ("0", CONTRACT[3], CONTRACT[1], CONTRACT[0]),
        )
        data = load_data(self.data_dir)
        self.assertEqual(data.contracts[0].amount_kopecks, 0)

    def test_file_is_required(self) -> None:
        (self.data_dir / "payments.csv").unlink()
        self.assert_invalid("payments.csv: файл не найден")

    def test_invalid_encoding_names_the_file(self) -> None:
        (self.data_dir / "works.csv").write_bytes(b"\xff\xfe")
        self.assert_invalid("works.csv: ожидалась кодировка UTF-8")

    def test_header_must_have_exact_columns(self) -> None:
        cases = (
            (CONTRACT_HEADER[:-1], "signed_at: столбец отсутствует"),
            ((*CONTRACT_HEADER, "extra"), "заголовок: неизвестный столбец"),
            ((*CONTRACT_HEADER, "amount"), "заголовок: повторяющийся столбец"),
        )
        for header, expected in cases:
            with self.subTest(header=header):
                self.write_csv("contracts.csv", header, CONTRACT)
                self.assert_invalid(f"contracts.csv:1: {expected}")

    def test_shape_and_csv_syntax(self) -> None:
        self.write_csv("works.csv", WORK_HEADER, WORK[:-1])
        self.assert_invalid("works.csv:2: cancelled_at: нет поля")
        self.write_csv("works.csv", WORK_HEADER, (*WORK, "extra"))
        self.assert_invalid("works.csv:2: CSV: лишнее поле")

    def test_malformed_quotes_have_record_line(self) -> None:
        self.write_csv("works.csv", WORK_HEADER, WORK)
        path = self.data_dir / "works.csv"
        with path.open(mode="a", encoding="utf-8", newline="") as output:
            output.write('W-02,D-01,"без закрывающей кавычки\n')
        self.assert_invalid("works.csv:4: CSV: некорректная запись")

    def test_identifier_is_required_and_unambiguous(self) -> None:
        for value in ("", " ", " D-01"):
            with self.subTest(value=value):
                self.write_csv(
                    "contracts.csv", CONTRACT_HEADER, (value, *CONTRACT[1:])
                )
                self.assert_invalid("contracts.csv:2: contract_id:")

    def test_required_text_cannot_be_blank(self) -> None:
        self.write_csv("works.csv", WORK_HEADER, (*WORK[:2], " ", *WORK[3:]))
        self.assert_invalid("works.csv:2: title: обязательное поле пусто")

    def test_duplicate_ids_report_physical_line(self) -> None:
        cases = (
            ("contracts.csv", CONTRACT_HEADER, CONTRACT, 3),
            ("works.csv", WORK_HEADER, WORK, 4),
            ("payments.csv", PAYMENT_HEADER, PAYMENT, 3),
        )
        for file, header, row, line in cases:
            with self.subTest(file=file):
                self.write_csv(file, header, row, row)
                location = f"{file}:{line}: {header[0]}"
                self.assert_invalid(f"{location}: повторяющийся идентификатор")
                self.write_csv(file, header, row)

    def test_missing_contract_references(self) -> None:
        self.write_csv(
            "works.csv", WORK_HEADER, (WORK[0], "missing", *WORK[2:])
        )
        self.assert_invalid("works.csv:2: contract_id: договор отсутствует")
        self.write_csv("works.csv", WORK_HEADER, WORK)
        orphan_payment = (PAYMENT[0], "missing", *PAYMENT[2:])
        self.write_csv("payments.csv", PAYMENT_HEADER, orphan_payment)
        self.assert_invalid("payments.csv:2: contract_id: договор отсутствует")

    def test_unknown_and_cross_contract_work_references(self) -> None:
        orphan_payment = (*PAYMENT[:2], "missing", *PAYMENT[3:])
        self.write_csv("payments.csv", PAYMENT_HEADER, orphan_payment)
        self.assert_invalid("payments.csv:2: work_id: работа отсутствует")
        second_contract = ("D-02", *CONTRACT[1:])
        self.write_csv(
            "contracts.csv", CONTRACT_HEADER, CONTRACT, second_contract
        )
        self.write_csv(
            "payments.csv", PAYMENT_HEADER, (PAYMENT[0], "D-02", *PAYMENT[2:])
        )
        self.assert_invalid(
            "payments.csv:2: work_id: работа принадлежит другому договору"
        )

    def test_payment_without_work_is_valid(self) -> None:
        self.write_csv(
            "payments.csv", PAYMENT_HEADER, (*PAYMENT[:2], "", *PAYMENT[3:])
        )
        self.assertIsNone(load_data(self.data_dir).payments[0].work_id)

    def test_invalid_amounts(self) -> None:
        invalid = (
            "", "-0.01", "1.001", "1.", ".01", "+1", "1e2",
            "NaN", "inf", "1,00",
        )
        for value in invalid:
            with self.subTest(value=value):
                self.write_csv(
                    "contracts.csv", CONTRACT_HEADER,
                    (*CONTRACT[:2], value, CONTRACT[3]),
                )
                self.assert_invalid("contracts.csv:2: amount:")

    def test_invalid_calendar_dates_and_formats(self) -> None:
        invalid = (
            "2026-02-29", "2026-1-01", "20260101", "2026-W01-1", "2026-01-01 ",
        )
        for value in invalid:
            with self.subTest(value=value):
                self.write_csv(
                    "contracts.csv", CONTRACT_HEADER, (*CONTRACT[:3], value)
                )
                self.assert_invalid("contracts.csv:2: signed_at:")

    def test_unknown_statuses(self) -> None:
        unknown_work = (*WORK[:4], "unknown", "", "")
        self.write_csv("works.csv", WORK_HEADER, unknown_work)
        self.assert_invalid("works.csv:2: status: допустимые значения")
        self.write_csv("works.csv", WORK_HEADER, WORK)
        unknown_payment = (*PAYMENT[:5], "unknown", *PAYMENT[6:])
        self.write_csv("payments.csv", PAYMENT_HEADER, unknown_payment)
        self.assert_invalid("payments.csv:2: status: допустимые значения")

    def test_work_event_consistency(self) -> None:
        required = "для этого статуса требуется дата"
        forbidden = "для этого статуса дата должна быть пустой"
        cases = (
            (("done", "", ""), "completed_at", required),
            (("cancelled", "", ""), "cancelled_at", required),
            (("planned", "2026-01-02", ""), "completed_at", forbidden),
            (("done", "2026-01-02", "2026-01-03"), "cancelled_at", forbidden),
            (
                ("done", "2025-12-31", ""),
                "completed_at", "дата раньше signed_at",
            ),
            (
                ("cancelled", "", "2025-12-31"),
                "cancelled_at", "дата раньше signed_at",
            ),
            (
                ("cancelled", "2026-01-04", "2026-01-03"),
                "cancelled_at", "дата раньше completed_at",
            ),
        )
        for events, field, expected in cases:
            with self.subTest(events=events):
                self.write_csv("works.csv", WORK_HEADER, (*WORK[:4], *events))
                self.assert_invalid(f"works.csv:2: {field}: {expected}")

    def test_cancelled_work_retains_prior_acceptance(self) -> None:
        cancelled = (*WORK[:4], "cancelled", "2026-01-01", "2026-01-03")
        self.write_csv("works.csv", WORK_HEADER, cancelled)
        work, = load_data(self.data_dir).works
        self.assertIs(work.status, WorkStatus.CANCELLED)
        self.assertEqual(work.completed_at, date(2026, 1, 1))
        self.assertEqual(work.cancelled_at, date(2026, 1, 3))

    def test_payment_event_consistency(self) -> None:
        required = "для этого статуса требуется дата"
        forbidden = "для этого статуса дата должна быть пустой"
        cases = (
            (("paid", "2026-01-02", "", ""), "paid_at", required),
            (
                ("refunded", "2026-01-02", "2026-01-03", ""),
                "refunded_at", required,
            ),
            (("queued", "2026-01-02", "2026-01-03", ""), "paid_at", forbidden),
            (
                ("paid", "2026-01-02", "2026-01-03", "2026-01-03"),
                "refunded_at", forbidden,
            ),
            (
                ("queued", "2025-12-31", "", ""),
                "created_at", "дата раньше signed_at",
            ),
            (
                ("paid", "2026-01-03", "2026-01-02", ""),
                "paid_at", "дата раньше created_at",
            ),
            (
                ("refunded", "2026-01-01", "2026-01-03", "2026-01-02"),
                "refunded_at", "дата раньше paid_at",
            ),
        )
        for events, field, expected in cases:
            with self.subTest(events=events):
                self.write_csv(
                    "payments.csv", PAYMENT_HEADER, (*PAYMENT[:5], *events)
                )
                self.assert_invalid(f"payments.csv:2: {field}: {expected}")

    def test_payment_dates_may_coincide(self) -> None:
        day = "2026-01-01"
        same_day_refund = (*PAYMENT[:5], "refunded", day, day, day)
        self.write_csv("payments.csv", PAYMENT_HEADER, same_day_refund)
        payment, = load_data(self.data_dir).payments
        self.assertEqual(payment.created_at, payment.paid_at)
        self.assertEqual(payment.paid_at, payment.refunded_at)

"""Check report dates, allocation and reconciliation against fixed examples."""

import unittest
from datetime import date
from pathlib import Path

from advance_report.calculation import ContractResult, calculate_report
from advance_report.input import load_data
from advance_report.models import (
    Contract,
    Payment,
    PaymentStatus,
    ReportData,
    Work,
    WorkStatus,
)


def _day(day: int) -> date:
    return date(2026, 1, day)


CONTRACT = Contract("D-S", "Студия", 180, _day(1))


def _work(
    work_id: str,
    price: int,
    *,
    completed_at: date | None = None,
    cancelled_at: date | None = None,
) -> Work:
    """Create a valid work with an optional acceptance and cancellation."""
    status = WorkStatus.PLANNED
    if cancelled_at is not None:
        status = WorkStatus.CANCELLED
    elif completed_at is not None:
        status = WorkStatus.DONE
    return Work(
        work_id, CONTRACT.contract_id, work_id, price, status,
        completed_at, cancelled_at,
    )


def _payment(
    payment_id: str,
    amount: int,
    *,
    work_id: str | None = None,
    paid_at: date | None = CONTRACT.signed_at,
    refunded_at: date | None = None,
) -> Payment:
    """Create a valid payment whose export status reflects its final event."""
    status = PaymentStatus.PAID
    if paid_at is None:
        status = PaymentStatus.QUEUED
    elif refunded_at is not None:
        status = PaymentStatus.REFUNDED
    return Payment(
        payment_id, CONTRACT.contract_id, work_id, "Оплата", amount,
        status, CONTRACT.signed_at, paid_at, refunded_at,
    )


class CalculationTests(unittest.TestCase):
    """Check historical results and distinguish work and contract balances."""

    def assert_balances_reconcile(self, contract: ContractResult) -> None:
        """Require the four D-009 reconciliation equations."""
        paid_on_works = sum(work.paid_kopecks for work in contract.works)
        self.assertEqual(
            contract.paid_kopecks,
            contract.direct_paid_kopecks + contract.unlinked_paid_kopecks,
        )
        self.assertEqual(
            contract.paid_kopecks, paid_on_works + contract.unallocated_kopecks
        )
        self.assertEqual(
            contract.unlinked_paid_kopecks,
            sum(work.allocated_kopecks for work in contract.works)
            + contract.unallocated_kopecks,
        )
        self.assertEqual(
            contract.works_price_kopecks,
            paid_on_works
            + contract.works_remaining_kopecks
            - contract.works_overpayment_kopecks,
        )
        self.assertEqual(
            contract.amount_kopecks,
            contract.paid_kopecks
            + contract.remaining_kopecks
            - contract.overpayment_kopecks,
        )

    def test_contract_signing_is_inclusive(self) -> None:
        contract = Contract("D-Z", "Подрядчик", 180, _day(3))
        data = ReportData((contract,), (), ())
        self.assertEqual(calculate_report(data, _day(2)).contracts, ())
        for day in (3, 4):
            with self.subTest(day=day):
                report = calculate_report(data, _day(day))
                self.assertEqual(report.report_date, _day(day))
                result, = report.contracts
                self.assertEqual(result.contract_id, "D-Z")

    def test_payment_and_refund_dates_are_inclusive(self) -> None:
        works = (_work("W-1", 180),)
        payments = (
            _payment("P-1", 95, paid_at=_day(4), refunded_at=_day(7)),
        )
        data = ReportData((CONTRACT,), works, payments)
        for day, paid in ((3, 0), (4, 95), (5, 95), (6, 95), (7, 0), (8, 0)):
            with self.subTest(day=day):
                result, = calculate_report(data, _day(day)).contracts
                work, = result.works
                self.assertEqual(result.paid_kopecks, paid)
                self.assertEqual(work.allocated_kopecks, paid)
                self.assertEqual(work.remaining_kopecks, 180 - paid)
                self.assert_balances_reconcile(result)

    def test_direct_refund_reopens_work_for_advance(self) -> None:
        works = (_work("W-1", 100), _work("W-2", 100))
        payments = (
            _payment("P-1", 80, work_id="W-1", refunded_at=_day(5)),
            _payment("P-2", 90),
        )
        data = ReportData((CONTRACT,), works, payments)
        for day, direct, shares in (
            (4, 80, (15, 75)), (5, 0, (45, 45)), (6, 0, (45, 45))
        ):
            with self.subTest(day=day):
                result, = calculate_report(data, _day(day)).contracts
                self.assertEqual(result.works[0].direct_paid_kopecks, direct)
                self.assertEqual(
                    tuple(work.allocated_kopecks for work in result.works),
                    shares,
                )
                self.assertEqual(result.paid_kopecks, 90 + direct)
                self.assert_balances_reconcile(result)

    def test_queue_and_same_day_refund_do_not_count(self) -> None:
        works = (_work("W-1", 180),)
        payments = (
            _payment("P-1", 95, paid_at=None),
            _payment("P-2", 30, paid_at=_day(3), refunded_at=_day(3)),
        )
        data = ReportData((CONTRACT,), works, payments)
        for day in (2, 3, 4):
            with self.subTest(day=day):
                result, = calculate_report(data, _day(day)).contracts
                self.assertEqual(result.paid_kopecks, 0)
                self.assertEqual(result.works[0].paid_kopecks, 0)

    def test_acceptance_and_cancellation_use_event_dates(self) -> None:
        works = (
            _work("W-1", 80, completed_at=_day(3), cancelled_at=_day(6)),
            _work("W-2", 80),
        )
        data = ReportData((CONTRACT,), works, (_payment("P-1", 80),))
        for day, status, price, shares in (
            (2, WorkStatus.PLANNED, 80, (40, 40)),
            (3, WorkStatus.DONE, 80, (40, 40)),
            (4, WorkStatus.DONE, 80, (40, 40)),
            (5, WorkStatus.DONE, 80, (40, 40)),
            (6, WorkStatus.CANCELLED, 0, (0, 80)),
            (7, WorkStatus.CANCELLED, 0, (0, 80)),
        ):
            with self.subTest(day=day):
                result, = calculate_report(data, _day(day)).contracts
                first, second = result.works
                self.assertIs(first.status, status)
                self.assertEqual(first.price_kopecks, 80)
                self.assertEqual(first.effective_price_kopecks, price)
                self.assertEqual(
                    (first.allocated_kopecks, second.allocated_kopecks), shares
                )
                self.assertEqual(result.amount_kopecks, 180)
                self.assertEqual(result.remaining_kopecks, 100)
                self.assert_balances_reconcile(result)

    def test_cancellation_wins_over_same_day_acceptance(self) -> None:
        work = _work("W-1", 180, completed_at=_day(3), cancelled_at=_day(3))
        data = ReportData((CONTRACT,), (work,), ())
        before, = calculate_report(data, _day(2)).contracts
        on_date, = calculate_report(data, _day(3)).contracts
        self.assertIs(before.works[0].status, WorkStatus.PLANNED)
        self.assertIs(on_date.works[0].status, WorkStatus.CANCELLED)

    def test_no_payments_preserves_all_unpaid_balances(self) -> None:
        works = (_work("W-1", 200),)
        result, = calculate_report(
            ReportData((CONTRACT,), works, ()), _day(1)
        ).contracts
        self.assertEqual(result.paid_kopecks, 0)
        self.assertEqual(result.remaining_kopecks, 180)
        self.assertEqual(result.works[0].remaining_kopecks, 200)
        self.assertEqual(result.works_remaining_kopecks, 200)
        self.assertEqual(result.price_difference_kopecks, -20)
        self.assert_balances_reconcile(result)

    def test_direct_overpayment_does_not_cover_other_work(self) -> None:
        works = (_work("W-1", 100), _work("W-2", 80))
        payments = (_payment("P-1", 130, work_id="W-1"),)
        data = ReportData((CONTRACT,), works, payments)
        result, = calculate_report(data, _day(1)).contracts
        first, second = result.works
        self.assertEqual(first.overpayment_kopecks, 30)
        self.assertEqual(first.remaining_kopecks, 0)
        self.assertEqual(second.paid_kopecks, 0)
        self.assertEqual(second.remaining_kopecks, 80)
        self.assertEqual(result.remaining_kopecks, 50)
        self.assertEqual(result.unallocated_kopecks, 0)
        self.assert_balances_reconcile(result)

    def test_unallocated_amount_and_overpayments_reconcile(self) -> None:
        works = (_work("W-1", 100), _work("W-2", 80))
        payments = (
            _payment("P-1", 130, work_id="W-1"),
            _payment("P-2", 100),
        )
        data = ReportData((CONTRACT,), works, payments)
        result, = calculate_report(data, _day(1)).contracts
        self.assertEqual(result.allocated_kopecks, 80)
        self.assertEqual(result.unallocated_kopecks, 20)
        self.assertEqual(result.paid_kopecks, 230)
        self.assertEqual(result.overpayment_kopecks, 50)
        self.assertEqual(result.remaining_kopecks, 0)
        self.assertEqual(result.works_overpayment_kopecks, 30)
        self.assertEqual(result.works_remaining_kopecks, 0)
        self.assert_balances_reconcile(result)

    def test_cancelled_works_keep_only_direct_payments(self) -> None:
        works = (
            _work("W-1", 100, cancelled_at=_day(2)),
            _work("W-2", 80, cancelled_at=_day(2)),
        )
        payments = (
            _payment("P-1", 30, work_id="W-1"),
            _payment("P-2", 60),
        )
        data = ReportData((CONTRACT,), works, payments)
        result, = calculate_report(data, _day(2)).contracts
        first, second = result.works
        self.assertEqual(first.paid_kopecks, 30)
        self.assertEqual(first.overpayment_kopecks, 30)
        self.assertEqual(second.paid_kopecks, 0)
        self.assertEqual(result.works_price_kopecks, 0)
        self.assertEqual(result.allocated_kopecks, 0)
        self.assertEqual(result.unallocated_kopecks, 60)
        self.assert_balances_reconcile(result)

    def test_contract_without_work_shows_unallocated_payments(self) -> None:
        data = ReportData((CONTRACT,), (), (_payment("P-1", 70),))
        result, = calculate_report(data, _day(1)).contracts
        self.assertEqual(result.works, ())
        self.assertEqual(result.unallocated_kopecks, 70)
        self.assertEqual(result.remaining_kopecks, 110)
        self.assert_balances_reconcile(result)

    def test_remainder_ties_and_report_use_sorted_identifiers(self) -> None:
        other = Contract("D-A", "Подрядчик", 100, _day(1))
        contracts = (CONTRACT, other)
        works = (
            _work("W-3", 5),
            _work("W-1", 5),
            _work("W-2", 5),
            _work("W-0", 0),
        )
        payments = (_payment("P-2", 1), _payment("P-1", 1))
        data = ReportData(contracts, works, payments)
        shuffled = ReportData(contracts[::-1], works[::-1], payments[::-1])
        report = calculate_report(data, _day(1))
        self.assertEqual(report, calculate_report(data, _day(1)))
        self.assertEqual(report, calculate_report(shuffled, _day(1)))
        self.assertEqual(
            [result.contract_id for result in report.contracts], ["D-A", "D-S"]
        )
        result = report.contracts[1]
        self.assertEqual(
            [(work.work_id, work.allocated_kopecks) for work in result.works],
            [("W-0", 0), ("W-1", 1), ("W-2", 1), ("W-3", 0)],
        )
        self.assert_balances_reconcile(result)

    def test_control_contract_totals_from_original_exports(self) -> None:
        data_dir = Path(__file__).resolve().parents[1] / "data"
        report = calculate_report(load_data(data_dir), date(2026, 11, 10))
        fields = (
            "amount_kopecks",
            "works_price_kopecks",
            "price_difference_kopecks",
            "direct_paid_kopecks",
            "unlinked_paid_kopecks",
            "allocated_kopecks",
            "paid_kopecks",
            "remaining_kopecks",
            "works_remaining_kopecks",
            "overpayment_kopecks",
            "works_overpayment_kopecks",
            "unallocated_kopecks",
        )
        # Fixed expectations independently reconciled in task 001.
        expected = {
            "D-1": (
                60_000_000, 50_000_000, 10_000_000, 1_500_000, 30_000_000,
                30_000_000, 31_500_000, 28_500_000, 18_500_000, 0, 0, 0,
            ),
            "D-2": (
                9_000_000, 9_000_000, 0, 0, 7_500_000, 7_500_000, 7_500_000,
                1_500_000, 1_500_000, 0, 0, 0,
            ),
        }
        self.assertEqual(
            [result.contract_id for result in report.contracts], ["D-1", "D-2"]
        )
        for result in report.contracts:
            values = expected[result.contract_id]
            for field, value in zip(fields, values, strict=True):
                with self.subTest(contract=result.contract_id, field=field):
                    self.assertEqual(getattr(result, field), value)
            self.assert_balances_reconcile(result)

    def test_control_work_totals_from_original_exports(self) -> None:
        data_dir = Path(__file__).resolve().parents[1] / "data"
        report = calculate_report(load_data(data_dir), date(2026, 11, 10))
        fields = (
            "status", "price_kopecks", "effective_price_kopecks",
            "direct_paid_kopecks", "allocated_kopecks", "paid_kopecks",
            "remaining_kopecks", "overpayment_kopecks",
        )
        # Fixed expectations independently reconciled in task 001.
        unchanged_price = (10_000_000, 10_000_000)
        standard_balance = (0, 6_185_567, 6_185_567, 3_814_433, 0)
        done = (WorkStatus.DONE, *unchanged_price, *standard_balance)
        planned = (WorkStatus.PLANNED, *unchanged_price, *standard_balance)
        expected = {
            "W-11": done,
            "W-12": done,
            "W-13": (WorkStatus.CANCELLED, 10_000_000, 0, 0, 0, 0, 0, 0),
            "W-14": (
                WorkStatus.DONE, *unchanged_price, 1_500_000, 5_257_732,
                6_757_732, 3_242_268, 0,
            ),
            "W-15": planned,
            "W-16": planned,
            "W-21": (
                WorkStatus.DONE, 2_000_000, 2_000_000, 0, 1_666_667,
                1_666_667, 333_333, 0,
            ),
            "W-22": (
                WorkStatus.DONE, 3_000_000, 3_000_000, 0, 2_500_000,
                2_500_000, 500_000, 0,
            ),
            "W-23": (
                WorkStatus.PLANNED, 4_000_000, 4_000_000, 0, 3_333_333,
                3_333_333, 666_667, 0,
            ),
        }
        works = [work for result in report.contracts for work in result.works]
        self.assertEqual([work.work_id for work in works], list(expected))
        for work in works:
            values = expected[work.work_id]
            for field, value in zip(fields, values, strict=True):
                with self.subTest(work=work.work_id, field=field):
                    self.assertEqual(getattr(work, field), value)

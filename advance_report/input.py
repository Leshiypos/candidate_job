"""Load the three CSV exports and validate their cross-file references."""

import csv
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TypeVar

from .models import (
    Contract, Payment, PaymentStatus, ReportData, Work, WorkStatus,
)


CONTRACT_COLUMNS = ("contract_id", "contractor", "amount", "signed_at")
WORK_COLUMNS = (
    "work_id", "contract_id", "title", "price", "status", "completed_at",
    "cancelled_at",
)
PAYMENT_COLUMNS = (
    "payment_id", "contract_id", "work_id", "purpose", "amount", "status",
    "created_at", "paid_at", "refunded_at",
)
DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
AMOUNT_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]{1,2})?")
KOPECK_PRECISION = 2

StatusType = TypeVar("StatusType", WorkStatus, PaymentStatus)


class InputError(ValueError):
    """Identify an unreadable export or invalid record by its location."""


def parse_date(value: str) -> date:
    """Parse a YYYY-MM-DD calendar date or raise a descriptive ValueError."""
    if not DATE_PATTERN.fullmatch(value):
        raise ValueError("ожидается дата в формате YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("календарная дата не существует") from None


def _error(path: Path, line: int, field: str, message: str) -> InputError:
    return InputError(f"{path.name}:{line}: {field}: {message}")


@dataclass(frozen=True, slots=True)
class _CsvRow:
    """The fields of a record and its starting line in the source file."""

    path: Path
    line: int
    fields: dict[str, str]

    def error(self, field: str, message: str) -> InputError:
        return _error(self.path, self.line, field, message)

    def required(self, field: str) -> str:
        value = self.fields[field]
        if not value.strip():
            raise self.error(field, "обязательное поле пусто")
        return value

    def identifier(self, field: str) -> str:
        value = self.required(field)
        if value != value.strip():
            raise self.error(field, "пробелы по краям идентификатора")
        return value

    def amount(self, field: str) -> int:
        """Read a nonnegative ruble value exactly as an integer of kopecks."""
        value = self.required(field)
        if not AMOUNT_PATTERN.fullmatch(value):
            raise self.error(
                field, "ожидается сумма от нуля с точностью до двух знаков"
            )
        rubles, _, fraction = value.partition(".")
        try:
            return int(rubles + fraction.ljust(KOPECK_PRECISION, "0"))
        except ValueError:
            raise self.error(field, "слишком много цифр в сумме") from None

    def required_date(self, field: str) -> date:
        return self._parse_date(field, self.required(field))

    def optional_date(self, field: str) -> date | None:
        value = self.fields[field]
        return self._parse_date(field, value) if value else None

    def _parse_date(self, field: str, value: str) -> date:
        try:
            return parse_date(value)
        except ValueError as error:
            raise self.error(field, str(error)) from None

    def status(self, choices: type[StatusType]) -> StatusType:
        value = self.required("status")
        try:
            return choices(value)
        except ValueError:
            allowed = ", ".join(member.value for member in choices)
            raise self.error(
                "status", f"допустимые значения: {allowed}"
            ) from None


def _validate_header(
    path: Path, header: list[str], expected: tuple[str, ...]
) -> None:
    if not header:
        raise _error(path, 1, "заголовок", "строка заголовка отсутствует")
    if len(header) != len(set(header)):
        raise _error(path, 1, "заголовок", "повторяющийся столбец")
    for column in expected:
        if column not in header:
            raise _error(path, 1, column, "столбец отсутствует")
    if len(header) != len(expected):
        raise _error(path, 1, "заголовок", "неизвестный столбец")


def _read_rows(
    path: Path, columns: tuple[str, ...]
) -> Iterator[tuple[str, _CsvRow]]:
    """Yield records keyed by the first schema column, with unique IDs."""
    identity_column = columns[0]
    seen_ids: set[str] = set()
    line = 1
    try:
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.reader(source, strict=True)
            header = next(reader, [])
            _validate_header(path, header, columns)
            line = reader.line_num + 1
            for values in reader:
                record_line, line = line, reader.line_num + 1
                if not values:
                    continue
                if len(values) < len(header):
                    missing = header[len(values)]
                    raise _error(path, record_line, missing, "нет поля")
                if len(values) > len(header):
                    raise _error(path, record_line, "CSV", "лишнее поле")
                row = _CsvRow(path, record_line, dict(zip(header, values)))
                entity_id = row.identifier(identity_column)
                if entity_id in seen_ids:
                    raise row.error(
                        identity_column, "повторяющийся идентификатор"
                    )
                seen_ids.add(entity_id)
                yield entity_id, row
    except FileNotFoundError:
        raise InputError(f"{path.name}: файл не найден") from None
    except OSError:
        raise InputError(f"{path.name}: не удалось прочитать файл") from None
    except UnicodeError:
        raise InputError(f"{path.name}: ожидалась кодировка UTF-8") from None
    except csv.Error:
        raise _error(path, line, "CSV", "некорректная запись") from None


def _read_contract(contract_id: str, row: _CsvRow) -> Contract:
    return Contract(
        contract_id=contract_id,
        contractor=row.required("contractor"),
        amount_kopecks=row.amount("amount"),
        signed_at=row.required_date("signed_at"),
    )


def _linked_contract(row: _CsvRow, contracts: dict[str, Contract]) -> Contract:
    contract_id = row.identifier("contract_id")
    if contract_id not in contracts:
        raise row.error("contract_id", "договор отсутствует в contracts.csv")
    return contracts[contract_id]


def _expect_event(
    row: _CsvRow, field: str, event: date | None, required: bool
) -> None:
    """Enforce that a status requires or disallows the supplied event."""
    if required and event is None:
        raise row.error(field, "для этого статуса требуется дата")
    if not required and event is not None:
        raise row.error(field, "для этого статуса дата должна быть пустой")


def _check_order(
    row: _CsvRow, field: str, event: date | None, earliest: date,
    earlier_field: str,
) -> None:
    """Check chronology when the event occurred, allowing same-day events."""
    if event is not None and event < earliest:
        raise row.error(field, f"дата раньше {earlier_field}")


def _read_work(work_id: str, row: _CsvRow, contract: Contract) -> Work:
    status = row.status(WorkStatus)
    completed_at = row.optional_date("completed_at")
    cancelled_at = row.optional_date("cancelled_at")
    _expect_event(
        row, "cancelled_at", cancelled_at, status is WorkStatus.CANCELLED
    )
    # A cancelled work may retain its prior acceptance date for D-004.
    if status is not WorkStatus.CANCELLED:
        _expect_event(
            row, "completed_at", completed_at, status is WorkStatus.DONE
        )
    _check_order(
        row, "completed_at", completed_at, contract.signed_at, "signed_at"
    )
    _check_order(
        row, "cancelled_at", cancelled_at, contract.signed_at, "signed_at"
    )
    if completed_at is not None:
        _check_order(
            row, "cancelled_at", cancelled_at, completed_at, "completed_at"
        )
    return Work(
        work_id=work_id,
        contract_id=contract.contract_id,
        title=row.required("title"),
        price_kopecks=row.amount("price"),
        status=status,
        completed_at=completed_at,
        cancelled_at=cancelled_at,
    )


def _linked_work_id(
    row: _CsvRow, contract: Contract, works: dict[str, Work]
) -> str | None:
    if row.fields["work_id"] == "":
        return None
    work_id = row.identifier("work_id")
    work = works.get(work_id)
    if work is None:
        raise row.error("work_id", "работа отсутствует в works.csv")
    if work.contract_id != contract.contract_id:
        raise row.error("work_id", "работа принадлежит другому договору")
    return work_id


def _read_payment(
    payment_id: str, row: _CsvRow, contract: Contract, works: dict[str, Work]
) -> Payment:
    status = row.status(PaymentStatus)
    created_at = row.required_date("created_at")
    paid_at = row.optional_date("paid_at")
    refunded_at = row.optional_date("refunded_at")
    _expect_event(row, "paid_at", paid_at, status is not PaymentStatus.QUEUED)
    _expect_event(
        row, "refunded_at", refunded_at, status is PaymentStatus.REFUNDED
    )
    _check_order(
        row, "created_at", created_at, contract.signed_at, "signed_at"
    )
    _check_order(row, "paid_at", paid_at, created_at, "created_at")
    if paid_at is not None:
        _check_order(row, "refunded_at", refunded_at, paid_at, "paid_at")
    return Payment(
        payment_id=payment_id,
        contract_id=contract.contract_id,
        work_id=_linked_work_id(row, contract, works),
        purpose=row.required("purpose"),
        amount_kopecks=row.amount("amount"),
        status=status,
        created_at=created_at,
        paid_at=paid_at,
        refunded_at=refunded_at,
    )


def load_data(data_dir: Path) -> ReportData:
    """Return validated records from three UTF-8 CSV files in ``data_dir``.

    Raise ``InputError`` on the first unreadable file or invalid record.
    The files are opened for reading only.
    """
    contracts = {
        contract_id: _read_contract(contract_id, row)
        for contract_id, row in _read_rows(
            data_dir / "contracts.csv", CONTRACT_COLUMNS
        )
    }
    works = {
        work_id: _read_work(work_id, row, _linked_contract(row, contracts))
        for work_id, row in _read_rows(data_dir / "works.csv", WORK_COLUMNS)
    }
    payments = tuple(
        _read_payment(payment_id, row, _linked_contract(row, contracts), works)
        for payment_id, row in _read_rows(
            data_dir / "payments.csv", PAYMENT_COLUMNS
        )
    )
    return ReportData(
        tuple(contracts.values()), tuple(works.values()), payments
    )

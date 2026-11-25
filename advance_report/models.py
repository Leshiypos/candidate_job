"""Represent validated contract, work, and payment records."""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class WorkStatus(StrEnum):
    """Work statuses recognized in the input export."""

    PLANNED = "planned"
    DONE = "done"
    CANCELLED = "cancelled"


class PaymentStatus(StrEnum):
    """Payment statuses recognized in the input export."""

    PAID = "paid"
    QUEUED = "queued"
    REFUNDED = "refunded"


@dataclass(frozen=True, slots=True)
class Contract:
    """A signed contract with its official amount in kopecks."""

    contract_id: str
    contractor: str
    amount_kopecks: int
    signed_at: date


@dataclass(frozen=True, slots=True)
class Work:
    """A contract work item with its original price in kopecks."""

    work_id: str
    contract_id: str
    title: str
    price_kopecks: int
    status: WorkStatus
    completed_at: date | None
    cancelled_at: date | None


@dataclass(frozen=True, slots=True)
class Payment:
    """A payment of a nonnegative number of kopecks and its event dates."""

    payment_id: str
    contract_id: str
    work_id: str | None
    purpose: str
    amount_kopecks: int
    status: PaymentStatus
    created_at: date
    paid_at: date | None
    refunded_at: date | None


@dataclass(frozen=True, slots=True)
class ReportData:
    """The validated records from the three exports in file order."""

    contracts: tuple[Contract, ...]
    works: tuple[Work, ...]
    payments: tuple[Payment, ...]

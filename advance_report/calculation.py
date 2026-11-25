"""Calculate contract balances and allocate payments at the end of a day."""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date

from .models import Contract, Payment, ReportData, Work, WorkStatus


@dataclass(frozen=True, slots=True)
class WorkResult:
    """A work's historical status, prices and payments in kopecks."""

    work_id: str
    title: str
    status: WorkStatus
    price_kopecks: int
    effective_price_kopecks: int
    direct_paid_kopecks: int
    allocated_kopecks: int

    @property
    def paid_kopecks(self) -> int:
        """Return direct payments plus the estimated allocation."""
        return self.direct_paid_kopecks + self.allocated_kopecks

    @property
    def remaining_kopecks(self) -> int:
        """Return the uncovered portion of the effective price."""
        return max(self.effective_price_kopecks - self.paid_kopecks, 0)

    @property
    def overpayment_kopecks(self) -> int:
        """Return the amount paid in excess of the effective price."""
        return max(self.paid_kopecks - self.effective_price_kopecks, 0)


@dataclass(frozen=True, slots=True)
class ContractResult:
    """A signed contract, its work results and contract payments in kopecks."""

    contract_id: str
    contractor: str
    amount_kopecks: int
    works: tuple[WorkResult, ...]
    unlinked_paid_kopecks: int

    @property
    def works_price_kopecks(self) -> int:
        """Return the total effective price of the contract's work."""
        return sum(work.effective_price_kopecks for work in self.works)

    @property
    def price_difference_kopecks(self) -> int:
        """Return the contract amount minus the total effective work price."""
        return self.amount_kopecks - self.works_price_kopecks

    @property
    def direct_paid_kopecks(self) -> int:
        """Return the total of payments explicitly linked to work."""
        return sum(work.direct_paid_kopecks for work in self.works)

    @property
    def allocated_kopecks(self) -> int:
        """Return the amount of unlinked payments allocated to work."""
        return sum(work.allocated_kopecks for work in self.works)

    @property
    def paid_kopecks(self) -> int:
        """Return actual payments net of refunds."""
        return self.direct_paid_kopecks + self.unlinked_paid_kopecks

    @property
    def unallocated_kopecks(self) -> int:
        """Return unlinked payments above total allocatable work prices."""
        return self.unlinked_paid_kopecks - self.allocated_kopecks

    @property
    def remaining_kopecks(self) -> int:
        """Return the positive unpaid balance of the contract."""
        return max(self.amount_kopecks - self.paid_kopecks, 0)

    @property
    def overpayment_kopecks(self) -> int:
        """Return actual payments in excess of the contract amount."""
        return max(self.paid_kopecks - self.amount_kopecks, 0)

    @property
    def works_remaining_kopecks(self) -> int:
        """Return the sum of the remaining balances for individual works."""
        return sum(work.remaining_kopecks for work in self.works)

    @property
    def works_overpayment_kopecks(self) -> int:
        """Return the sum of overpayments for individual works."""
        return sum(work.overpayment_kopecks for work in self.works)


@dataclass(frozen=True, slots=True)
class Report:
    """Historical results for the contracts signed through the report date."""

    report_date: date
    contracts: tuple[ContractResult, ...]


def _status_on(work: Work, report_date: date) -> WorkStatus:
    """Reconstruct work status with cancellation taking precedence (D-004)."""
    if work.cancelled_at is not None and work.cancelled_at <= report_date:
        return WorkStatus.CANCELLED
    if work.completed_at is not None and work.completed_at <= report_date:
        return WorkStatus.DONE
    return WorkStatus.PLANNED


def _is_paid_on(payment: Payment, report_date: date) -> bool:
    """Check whether the payment was made and remained with the contractor."""
    return (
        payment.paid_at is not None
        and payment.paid_at <= report_date
        and (payment.refunded_at is None or report_date < payment.refunded_at)
    )


def _allocate(amount: int, weights: dict[str, int]) -> dict[str, int]:
    """Allocate up to the uncovered prices using largest remainders."""
    total_weight = sum(weights.values())
    allocatable = min(amount, total_weight)
    shares = dict.fromkeys(weights, 0)
    if allocatable == 0:
        return shares

    remainders: list[tuple[int, str]] = []
    for work_id, weight in weights.items():
        share, remainder = divmod(allocatable * weight, total_weight)
        shares[work_id] = share
        # D-008 gives ties to the smallest work identifiers.
        remainders.append((-remainder, work_id))
    remaining_kopecks = allocatable - sum(shares.values())
    for _, work_id in sorted(remainders)[:remaining_kopecks]:
        shares[work_id] += 1
    return shares


def _initial_work_result(
    work: Work, report_date: date, direct_paid: int
) -> WorkResult:
    """Apply historical status and direct payments before allocation."""
    status = _status_on(work, report_date)
    effective_price = work.price_kopecks
    if status is WorkStatus.CANCELLED:
        effective_price = 0
    return WorkResult(
        work_id=work.work_id,
        title=work.title,
        status=status,
        price_kopecks=work.price_kopecks,
        effective_price_kopecks=effective_price,
        direct_paid_kopecks=direct_paid,
        allocated_kopecks=0,
    )


def _calculate_contract(
    contract: Contract,
    works: list[Work],
    direct_paid: dict[str, int],
    unlinked_paid: int,
    report_date: date,
) -> ContractResult:
    """Group the work results and allocate the contract's shared payments."""
    initial_works = [
        _initial_work_result(
            work, report_date, direct_paid.get(work.work_id, 0)
        )
        for work in sorted(works, key=lambda work: work.work_id)
    ]
    weights = {work.work_id: work.remaining_kopecks for work in initial_works}
    shares = _allocate(unlinked_paid, weights)
    return ContractResult(
        contract_id=contract.contract_id,
        contractor=contract.contractor,
        amount_kopecks=contract.amount_kopecks,
        works=tuple(
            replace(work, allocated_kopecks=shares[work.work_id])
            for work in initial_works
        ),
        unlinked_paid_kopecks=unlinked_paid,
    )


def calculate_report(data: ReportData, report_date: date) -> Report:
    """Calculate end-of-day balances from validated records.

    Contract and work results are sorted by ID. Monetary amounts are integer
    kopecks. Allocated shares estimate the use of payments without a work ID.
    """
    works_by_contract: defaultdict[str, list[Work]] = defaultdict(list)
    for work in data.works:
        works_by_contract[work.contract_id].append(work)

    direct_paid: defaultdict[str, int] = defaultdict(int)
    unlinked_paid: defaultdict[str, int] = defaultdict(int)
    for payment in data.payments:
        if not _is_paid_on(payment, report_date):
            continue
        if payment.work_id is None:
            unlinked_paid[payment.contract_id] += payment.amount_kopecks
        else:
            direct_paid[payment.work_id] += payment.amount_kopecks

    return Report(
        report_date=report_date,
        contracts=tuple(
            _calculate_contract(
                contract,
                works_by_contract[contract.contract_id],
                direct_paid,
                unlinked_paid[contract.contract_id],
                report_date,
            )
            for contract in sorted(
                data.contracts, key=lambda contract: contract.contract_id
            )
            if contract.signed_at <= report_date
        ),
    )

"""Render and save the contract report as a UTF-8 Markdown file."""

from decimal import Decimal
from pathlib import Path
from tempfile import NamedTemporaryFile

from .calculation import ContractResult, Report, WorkResult
from .models import WorkStatus


KOPECKS_PER_RUBLE = 100
WORK_STATUS_LABELS = {
    WorkStatus.PLANNED: "Запланирована",
    WorkStatus.DONE: "Принята",
    WorkStatus.CANCELLED: "Отменена",
}
MARKDOWN_ESCAPES = str.maketrans({
    **{chr(code): f"\\u{code:04x}" for code in (*range(32), *range(127, 160))},
    **{char: f"&#{ord(char)};" for char in r"\|`*_~[]#<>!&"},
    "\r": " ⏎ ",
    "\n": " ⏎ ",
    "\u2028": " ⏎ ",
    "\u2029": " ⏎ ",
})


def _money(amount: int) -> str:
    """Render integer kopecks in rubles, including signed differences."""
    rubles, kopecks = divmod(abs(amount), KOPECKS_PER_RUBLE)
    sign = "-" if amount < 0 else ""
    # Decimal(int) stays exact even if a total exceeds int's text limit.
    return f"{sign}{Decimal(rubles):f}.{kopecks:02d}"


def _text(value: str) -> str:
    """Render CSV text literally in a Markdown heading or table cell."""
    # GFM tables split on pipes before parsing inline Markdown and entities.
    return value.replace("\r\n", "\n").translate(MARKDOWN_ESCAPES)


def _table_row(*cells: str) -> str:
    return f"| {' | '.join(cells)} |"


def _work_row(work: WorkResult) -> str:
    return _table_row(
        _text(work.work_id),
        _text(work.title),
        WORK_STATUS_LABELS[work.status],
        _money(work.price_kopecks),
        _money(work.effective_price_kopecks),
        _money(work.direct_paid_kopecks),
        _money(work.allocated_kopecks),
        _money(work.paid_kopecks),
        _money(work.remaining_kopecks),
        _money(work.overpayment_kopecks),
    )


def _contract_lines(contract: ContractResult) -> list[str]:
    """Build a contract summary followed by the detailed work table."""
    totals = (
        ("Сумма договора", contract.amount_kopecks),
        ("Расчётные цены работ", contract.works_price_kopecks),
        (
            "Сумма договора минус расчётные цены",
            contract.price_difference_kopecks,
        ),
        ("Чистые фактические выплаты", contract.paid_kopecks),
        ("Прямые выплаты", contract.direct_paid_kopecks),
        ("Выплаты без привязки", contract.unlinked_paid_kopecks),
        ("Распределено по работам (расчёт)", contract.allocated_kopecks),
        ("Нераспределённые выплаты", contract.unallocated_kopecks),
        ("Осталось по договору", contract.remaining_kopecks),
        ("Переплата по договору", contract.overpayment_kopecks),
        ("Сумма остатков по работам", contract.works_remaining_kopecks),
        ("Сумма переплат по работам", contract.works_overpayment_kopecks),
    )
    lines = [
        "",
        f"## Договор {_text(contract.contract_id)}",
        "",
        f"Подрядчик — {_text(contract.contractor)}",
        "",
        _table_row("Показатель по договору", "Рубли"),
        _table_row("---", "---:"),
        *(_table_row(label, _money(amount)) for label, amount in totals),
        "",
        "### Работы",
        "",
    ]
    if not contract.works:
        return [*lines, "Работ в выгрузке нет."]

    lines.extend((
        _table_row(
            "Код", "Работа", "Статус", "Цена", "Расчётная цена",
            "Прямые выплаты", "Доля без привязки (расчёт)", "Выплачено",
            "Осталось оплатить", "Переплата",
        ),
        _table_row("---", "---", "---", *(["---:"] * 7)),
    ))
    lines.extend(_work_row(work) for work in contract.works)
    return lines


def render_report(report: Report) -> str:
    """Return a Markdown report for a renderer that supports tables."""
    lines = [
        f"# Отчёт по договорам на {report.report_date.isoformat()}",
        "",
        "Суммы в рублях. Статусы и выплаты показаны на конец дня отчёта.",
        "",
        "- **Чистые фактические выплаты** — оплаченные суммы за вычетом "
        "возвратов. Прямые выплаты привязаны к работе в выгрузке.",
        "- **Доля без привязки (расчёт)** — часть общих выплат, отнесённая "
        "к работе пропорционально цене, ещё не покрытой прямыми выплатами. "
        "Распределение условно. Расчётная доля входит в колонку "
        "**Выплачено** вместе с прямыми выплатами.",
        "- Отмена обнуляет расчётную цену работы. Остаток запланированной "
        "работы показывает её непокрытую цену. Срок оплаты требует сверки.",
        "- Нераспределённые выплаты уже входят в фактический итог договора. "
        "Переплаты по работам уже входят в суммы выплат по этим работам.",
        "- Остаток по договору рассчитан от его полной суммы. Остатки работ "
        "рассчитаны по их расчётным ценам и показаны отдельно.",
    ]
    if not report.contracts:
        lines.extend(("", "К концу этого дня подписанных договоров нет."))
    for contract in report.contracts:
        lines.extend(_contract_lines(contract))
    return "\n".join(lines) + "\n"


def write_report(report: Report, output_dir: Path) -> Path:
    """Create the directory and atomically replace the dated UTF-8 report.

    Return its path. File system failures propagate as OSError.
    """
    content = render_report(report)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"report-{report.report_date.isoformat()}.md"
    temporary_path = None
    try:
        # The temporary file must be closed before replacing on Windows.
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=output_dir,
            suffix=".tmp", delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(content)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return path

#!/usr/bin/env python3

import csv
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import re


def ensure_package(pkg_name, import_name=None):
    """Install a package if it is missing, then import it."""
    import_name = import_name or pkg_name
    try:
        return __import__(import_name)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg_name])
        return __import__(import_name)


try:
    from PyQt6.QtCore import QThread, Qt, pyqtSignal
    from PyQt6.QtGui import QFont, QIcon, QTextCursor
    from PyQt6.QtWidgets import (
        QApplication,
        QCheckBox,
        QFileDialog,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QSizePolicy,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    ensure_package("PyQt6")
    from PyQt6.QtCore import QThread, Qt, pyqtSignal
    from PyQt6.QtGui import QFont, QIcon, QTextCursor
    from PyQt6.QtWidgets import (
        QApplication,
        QCheckBox,
        QFileDialog,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QSizePolicy,
        QVBoxLayout,
        QWidget,
    )

try:
    import requests
except ImportError:
    requests = ensure_package("requests")

try:
    from tabulate import tabulate
except ImportError:
    try:
        tabulate = ensure_package("tabulate").tabulate
    except Exception:
        tabulate = None


APP_TITLE = "Splitwise Settlement"
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
MONEY = Decimal("0.01")
DEBUG_DUMP_ENV = "SPLITWISE_SETTLE_DUMP_OUTPUT_PATH"
REPOSITORY_URL = "https://github.com/g-damilano/splitwise-settle"
DEFAULT_DISPLAY_CURRENCIES = ["EUR", "USD", "GBP"]
AVAILABLE_DISPLAY_CURRENCIES = [
    "EUR",
    "USD",
    "GBP",
    "CHF",
    "JPY",
    "CAD",
    "AUD",
    "NZD",
    "SEK",
    "NOK",
    "DKK",
]


def resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return str(Path(__file__).resolve().parent / relative_path)


def money(value):
    return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)


def get_ecb_rates():
    response = requests.get(ECB_URL, timeout=20)
    response.raise_for_status()

    root = ET.fromstring(response.content)
    ns = {
        "gesmes": "http://www.gesmes.org/xml/2002-08-01",
        "ecb": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref",
    }

    rates = {"EUR": 1.0}
    for cube in root.findall(".//ecb:Cube/ecb:Cube/ecb:Cube", ns):
        rates[cube.attrib["currency"]] = float(cube.attrib["rate"])

    return rates


def get_exchange_rates(base_currency, currencies):
    ecb_rates = get_ecb_rates()
    base_rate = ecb_rates.get(base_currency)
    if base_rate is None:
        raise ValueError(f"Currency {base_currency} is not available in the ECB feed.")

    rates_to_base = {}
    for curr in currencies:
        curr_rate = ecb_rates.get(curr)
        if curr_rate is None:
            rates_to_base[curr] = 1.0
        else:
            rates_to_base[curr] = base_rate / curr_rate

    return rates_to_base


def convert_from_base(amount, target_currency, rates_to_base):
    rate = Decimal(str(rates_to_base.get(target_currency, 1.0)))
    if rate == 0:
        return money(amount)
    return money(Decimal(str(amount)) / rate)


def _markdown_table(headers, rows):
    """Return a simple markdown table string from header + row data."""
    if not headers:
        return ""

    all_rows = [headers, *rows]
    col_count = max(len(row) for row in all_rows)

    normalized = []
    for row in all_rows:
        padded = [str(value) for value in row]
        if len(padded) < col_count:
            padded.extend([""] * (col_count - len(padded)))
        normalized.append(padded)

    widths = [0] * col_count
    for row in normalized:
        widths = [max(widths[i], len(row[i])) for i in range(col_count)]

    def _fmt(row, sep):
        return "| " + f" | ".join(
            row[i].ljust(widths[i]) for i in range(col_count)
        ) + " |"

    divider = " | ".join("-" * width if width > 0 else "-" for width in widths)
    header = _fmt(headers, "| ")
    bar = f"| {divider} |"

    body_lines = [_fmt(row, "| ") for row in rows]
    return "\n".join([header, bar] + body_lines)


def _extract_table_rows(lines, start, end):
    rows = []
    for raw in lines[start:end]:
        line = raw.rstrip()
        if not line:
            continue
        if set(line.strip()) == {"-"}:
            continue
        if re.match(r"^\s*From\s+To(\s+\w+){1,}\s*$", line):
            continue
        parts = [part for part in re.split(r"\s{2,}", line.strip()) if part]
        if len(parts) >= 2:
            rows.append(parts)
    return rows


def _extract_balances_rows(lines, start, end):
    rows = []
    for raw in lines[start:end]:
        line = raw.rstrip()
        if not line:
            continue
        if set(line.strip()) == {"-"}:
            continue
        if re.match(r"^\s*Name\s+Balance", line):
            continue
        parts = [part for part in re.split(r"\s{2,}", line.strip()) if part]
        if len(parts) >= 2 and re.match(r"[+-]?\d+\.\d{2}\s+\w+", parts[-1]):
            rows.append(parts[:2])
    return rows


def _section_end(lines, start):
    for idx in range(start, len(lines)):
        if lines[idx].startswith("File: "):
            return idx
    return len(lines)


def format_output_for_whatsapp(text):
    """
    Convert the plain text result into WhatsApp-friendly markdown tables.
    """
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.split("\n")
    def section_idx(name):
        return next((i for i, line in enumerate(lines) if line.strip() == name), None)

    net_idx = section_idx("Net balances")
    trans_idx = section_idx("Transactions to settle")
    if net_idx is None or trans_idx is None:
        return f"```\n{text.strip()}\n```"

    preamble = lines[:net_idx]
    balance_rows = _extract_balances_rows(lines, net_idx + 1, trans_idx)

    trans_stop = _section_end(lines, trans_idx + 1)
    trans_rows = _extract_table_rows(lines, trans_idx + 1, trans_stop)

    trans_headers = ["From", "To", "EUR", "USD", "GBP"]
    for raw in lines[trans_idx + 1 : trans_stop]:
        line = raw.strip()
        if re.match(r"^\s*From\s+To(\s+\w+){1,}\s*$", line):
            parts = [part for part in re.split(r"\s{2,}", line) if part]
            if len(parts) >= 2:
                trans_headers = parts[:2] + parts[2:]
            break

    if not trans_rows and any("Everyone is already settled." in line for line in lines):
        trans_rows = [["Everyone is already settled."]]

    if not balance_rows and any("Error:" in line for line in lines):
        return text

    output_lines = [line for line in preamble if line.strip()]
    output_lines.append("```")
    output_lines.append("Net balances")
    if balance_rows:
        output_lines.append(_markdown_table(["Name", "Balance"], balance_rows))
    else:
        output_lines.append("No balances available.")

    output_lines.append("")
    output_lines.append("Transactions to settle")
    if trans_rows:
        if len(trans_rows[0]) > len(trans_headers):
            trans_headers.extend([""] * (len(trans_rows[0]) - len(trans_headers)))
        elif len(trans_rows[0]) < len(trans_headers):
            trans_headers = trans_headers[: len(trans_rows[0])]
        output_lines.append(_markdown_table(trans_headers, trans_rows))
    else:
        output_lines.append("No settlements required.")

    # Keep the branding line with the final output line.
    promo_line = next(
        (
            line
            for line in reversed(lines)
            if line.startswith("Settle it easily with Splitwise Settle:")
        ),
        "",
    )
    if promo_line:
        output_lines.append("")
        output_lines.append(promo_line)

    output_lines.append("```")

    return "\n".join(output_lines)


def parse_csv(filepath):
    with open(filepath, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        rows = [row for row in reader if any(cell.strip() for cell in row)]

    if not rows or len(rows[0]) < 6:
        raise ValueError("The CSV does not look like a Splitwise export.")

    header = rows[0]
    people = [person.strip() for person in header[5:] if person.strip()]
    transactions = []

    for row in rows[1:]:
        if len(row) < 5:
            continue
        if row[0].strip().lower().startswith("bilancio"):
            continue

        try:
            amount = float(row[3])
        except ValueError:
            continue

        splits = {}
        for person, value in zip(people, row[5:]):
            try:
                splits[person] = float(value)
            except ValueError:
                splits[person] = 0.0

        transactions.append(
            {
                "date": row[0],
                "description": row[1],
                "category": row[2],
                "amount": amount,
                "currency": row[4].strip(),
                "splits": splits,
            }
        )

    if not transactions:
        raise ValueError("No valid transactions were found in the CSV.")

    return transactions, people


def convert_transactions(transactions, rates_to_base):
    for tx in transactions:
        rate = rates_to_base.get(tx["currency"], 1.0)
        tx["amount_converted"] = tx["amount"] * rate
        tx["splits_converted"] = {k: v * rate for k, v in tx["splits"].items()}
    return transactions


def compute_balances(transactions, people):
    balances = defaultdict(float)
    for tx in transactions:
        for person in people:
            balances[person] += tx["splits_converted"].get(person, 0.0)
    return balances


def minimize_transactions(balances):
    from heapq import heappop, heappush

    creditors = []
    debtors = []

    for person, balance in balances.items():
        rounded_balance = float(money(balance))
        if rounded_balance > 0:
            heappush(creditors, (-rounded_balance, person))
        elif rounded_balance < 0:
            heappush(debtors, (rounded_balance, person))

    transactions = []
    while creditors and debtors:
        credit_amt, creditor = heappop(creditors)
        debit_amt, debtor = heappop(debtors)

        settle_amt = min(-credit_amt, -debit_amt)
        transactions.append((debtor, creditor, round(settle_amt, 2)))

        remaining_credit = credit_amt + settle_amt
        remaining_debit = debit_amt + settle_amt

        if remaining_credit < 0:
            heappush(creditors, (remaining_credit, creditor))
        if remaining_debit < 0:
            heappush(debtors, (remaining_debit, debtor))

    return transactions


def process_file(filepath, display_currencies=None):
    basename = os.path.basename(filepath)
    transactions, people = parse_csv(filepath)
    currencies_used = [tx["currency"] for tx in transactions if tx["currency"]]
    base_currency = Counter(currencies_used).most_common(1)[0][0]
    display_currencies = display_currencies or DEFAULT_DISPLAY_CURRENCIES

    unique_currencies = set(currencies_used)
    all_needed = unique_currencies.union({base_currency, *display_currencies})
    exchange_rates = get_exchange_rates(base_currency, all_needed)

    converted = convert_transactions(transactions, exchange_rates)
    balances = compute_balances(converted, people)
    simplified = minimize_transactions(balances)

    if tabulate is None:
        return _format_output_plain_fallback(
            basename,
            base_currency,
            balances,
            simplified,
            exchange_rates,
            display_currencies,
        )

    return _format_output_tabulate(
        basename,
        base_currency,
        balances,
        simplified,
        exchange_rates,
        display_currencies,
    )


def _format_output_tabulate(
    basename, base_currency, balances, simplified, exchange_rates, display_currencies
):
    sorted_balances = sorted(
        balances.items(),
        key=lambda item: Decimal(str(item[1])),
        reverse=True,
    )
    selected_currencies = [base_currency] + [
        currency for currency in display_currencies if currency != base_currency
    ]

    balance_rows = []
    for person, bal in sorted_balances:
        balance_rows.append([person, f"{money(bal):+.2f} {base_currency}"])

    balances_table = tabulate(
        balance_rows,
        headers=["Name", "Balance"],
        tablefmt="plain",
        colalign=("left", "right"),
        disable_numparse=True,
    )

    settlement_rows = []
    for debtor, creditor, amt in simplified:
        row = [debtor, creditor]
        for currency in selected_currencies:
            row.append(f"{convert_from_base(amt, currency, exchange_rates):.2f}")
        settlement_rows.append(row)

    if simplified:
        settlements_table = tabulate(
            settlement_rows,
            headers=["From", "To"] + selected_currencies,
            tablefmt="plain",
            colalign=("left", "left") + ("right",) * len(selected_currencies),
            disable_numparse=True,
        )
        settlement_divider = "-" * max(1, max(len(line) for line in settlements_table.splitlines()))
    else:
        settlements_table = "Everyone is already settled."
        settlement_divider = None

    lines = [
        f"File: {basename}",
        f"Converting everything to: {base_currency}",
        "",
        "Net balances",
        balances_table,
        "",
        "Transactions to settle",
    ]
    if not simplified:
        lines.append(settlements_table)
        lines.append("")
        lines.append(f"Settle it easily with Splitwise Settle: {REPOSITORY_URL}")
        return "\n".join(lines)

    lines.extend(["", settlement_divider, settlements_table, "", f"Settle it easily with Splitwise Settle: {REPOSITORY_URL}"])
    return "\n".join(lines)


def _format_output_plain_fallback(
    basename, base_currency, balances, simplified, exchange_rates, display_currencies
):
    sorted_balances = sorted(
        balances.items(),
        key=lambda item: Decimal(str(item[1])),
        reverse=True,
    )
    name_width = max(len("Name"), max((len(person) for person, _ in sorted_balances), default=0)
    )
    balance_value_width = max(
        max((len(f"{money(bal):+.2f}") for _, bal in sorted_balances), default=0),
        len("Balance"),
    )
    balance_col_width = balance_value_width + 1 + len(base_currency)

    lines = [
        f"File: {basename}",
        f"Converting everything to: {base_currency}",
        "",
        "Net balances",
        "",
        f"{'Name'.ljust(name_width)}  {'Balance'.rjust(balance_col_width)}",
        "-" * (name_width + 2 + balance_col_width),
    ]

    for person, bal in sorted_balances:
        lines.append(
            f"{person.ljust(name_width)}  "
            f"{f'{money(bal):+.2f} {base_currency}'.rjust(balance_col_width)}"
        )

    lines.extend(["", "Transactions to settle", ""])

    if not simplified:
        lines.append("Everyone is already settled.")
        lines.append("")
        lines.append(f"Settle it easily with Splitwise Settle: {REPOSITORY_URL}")
        return "\n".join(lines)

    selected_currencies = [base_currency] + [
        currency for currency in display_currencies if currency != base_currency
    ]
    from_width = max(len("From"), max((len(debtor) for debtor, _, _ in simplified), default=0))
    to_width = max(len("To"), max((len(creditor) for _, creditor, _ in simplified), default=0))
    value_width = max(
        max((len(curr) for curr in selected_currencies), default=0),
        max(
            (
                len(f"{convert_from_base(amt, curr, exchange_rates):.2f}")
                for _, _, amt in simplified
                for curr in selected_currencies
            ),
            default=0,
        ),
    )

    header = (
        f"{'From'.ljust(from_width)}  "
        f"{'To'.ljust(to_width)}  "
        + "  ".join(f"{curr.rjust(value_width)}" for curr in selected_currencies)
    )
    settlement_divider = (
        "-" * from_width
        + "  "
        + "-" * to_width
        + "  "
        + "  ".join("-" * value_width for _ in selected_currencies)
    )
    lines.append(header)
    lines.append(settlement_divider)

    for debtor, creditor, amt in simplified:
        amounts = "  ".join(
            f"{convert_from_base(amt, currency, exchange_rates):.2f}".rjust(value_width)
            for currency in selected_currencies
        )
        lines.append(f"{debtor.ljust(from_width)}  {creditor.ljust(to_width)}  {amounts}")

    lines.append("")
    lines.append(f"Settle it easily with Splitwise Settle: {REPOSITORY_URL}")
    return "\n".join(lines)


class DropArea(QFrame):
    files_dropped = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setObjectName("dropArea")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(6)

        title = QLabel("Drop your Splitwise export CSV here")
        title.setObjectName("dropTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        detail = QLabel("Export the group from Splitwise as CSV, then drop that file here.")
        detail.setObjectName("dropDetail")
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(detail)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                paths.append(url.toLocalFile())
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()


class SettlementWorker(QThread):
    status_changed = pyqtSignal(str)
    result_ready = pyqtSignal(int, int, str)
    finished_batch = pyqtSignal(int)

    def __init__(self, paths, display_currencies):
        super().__init__()
        self.paths = paths
        self.display_currencies = display_currencies

    def run(self):
        total = len(self.paths)
        for index, path in enumerate(self.paths, start=1):
            self.status_changed.emit(
                f"Processing {index} of {total}: {os.path.basename(path)}"
            )
            try:
                result = process_file(path, self.display_currencies)
            except Exception as exc:
                result = f"File: {os.path.basename(path)}\nError: {exc}"
            self.result_ready.emit(index, total, result)
        self.finished_batch.emit(total)


class SplitwiseSettleWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker = None

        self.setWindowTitle(APP_TITLE)
        self.resize(780, 560)
        self.setMinimumSize(600, 420)

        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        self.guidelines = QLabel(
            "How to use: export your group from Splitwise as a CSV, then drop the "
            "Splitwise export into this window or choose it with Browse. Results "
            "appear below; the app no longer writes an output file. The app settles "
            "in the most common currency in the file to reduce conversion noise, "
            "then shows converted equivalents at the end of each settlement line. "
            f'Public repository: <a href="{REPOSITORY_URL}">{REPOSITORY_URL}</a>'
        )
        self.guidelines.setObjectName("guidelines")
        self.guidelines.setTextFormat(Qt.TextFormat.RichText)
        self.guidelines.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self.guidelines.setOpenExternalLinks(True)
        self.guidelines.setWordWrap(True)
        main_layout.addWidget(self.guidelines)

        top_layout = QHBoxLayout()
        top_layout.setSpacing(10)

        self.drop_area = DropArea()
        self.drop_area.files_dropped.connect(self.process_paths)
        top_layout.addWidget(self.drop_area, stretch=1)

        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self.browse_files)
        self.browse_button.setMinimumHeight(52)
        top_layout.addWidget(self.browse_button)

        main_layout.addLayout(top_layout)

        currency_layout = QHBoxLayout()
        currency_layout.setSpacing(8)

        currency_label = QLabel("Display currencies")
        currency_layout.addWidget(currency_label)

        self.currency_checks = []
        currency_grid = QGridLayout()
        currency_grid.setHorizontalSpacing(12)
        currency_grid.setVerticalSpacing(4)

        for index, currency in enumerate(AVAILABLE_DISPLAY_CURRENCIES):
            checkbox = QCheckBox(currency)
            checkbox.setChecked(currency in DEFAULT_DISPLAY_CURRENCIES)
            self.currency_checks.append(checkbox)
            currency_grid.addWidget(checkbox, index // 6, index % 6)

        currency_layout.addLayout(currency_grid)
        currency_layout.addStretch(1)

        main_layout.addLayout(currency_layout)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Settlement results will appear here.")
        self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.output.setFont(QFont("Consolas", 10))
        self.output.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        main_layout.addWidget(self.output, stretch=1)

        footer = QHBoxLayout()
        footer.setSpacing(10)

        self.status_label = QLabel("Ready")
        footer.addWidget(self.status_label, stretch=1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFixedWidth(180)
        footer.addWidget(self.progress, alignment=Qt.AlignmentFlag.AlignRight)

        self.copy_button = QPushButton("Copy")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy_output)
        footer.addWidget(self.copy_button)

        self.whatsapp_copy_button = QPushButton("Copy for WhatsApp")
        self.whatsapp_copy_button.setEnabled(False)
        self.whatsapp_copy_button.clicked.connect(self.copy_whatsapp_output)
        footer.addWidget(self.whatsapp_copy_button)

        self.save_button = QPushButton("Save output...")
        self.save_button.clicked.connect(self.save_output_snapshot)
        footer.addWidget(self.save_button)

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear_output)
        footer.addWidget(self.clear_button)

        main_layout.addLayout(footer)
        self.setStyleSheet(
            """
            QWidget {
                background: #f7f8fb;
                color: #1b1f2a;
                font-family: Segoe UI, Arial, sans-serif;
                font-size: 10pt;
            }
            QLabel#guidelines {
                color: #303747;
                line-height: 1.35;
            }
            QFrame#dropArea {
                background: #d9e6ff;
                border: 2px dashed #5879b7;
                border-radius: 8px;
            }
            QLabel#dropTitle {
                font-size: 15pt;
                font-weight: 600;
                background: transparent;
            }
            QLabel#dropDetail {
                color: #46536d;
                background: transparent;
            }
            QPlainTextEdit {
                background: #ffffff;
                border: 1px solid #c8cfdd;
                border-radius: 6px;
                padding: 8px;
                selection-background-color: #b8cdf7;
                font-family: "Consolas", "Courier New", monospace;
                font-size: 10pt;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #b8c1d1;
                border-radius: 6px;
                padding: 8px 12px;
            }
            QPushButton:hover {
                background: #eef3ff;
            }
            QCheckBox {
                spacing: 5px;
                background: transparent;
            }
            QPushButton:disabled {
                color: #8a92a3;
                background: #f1f3f7;
            }
            QProgressBar {
                background: #ffffff;
                border: 1px solid #b8c1d1;
                border-radius: 6px;
                height: 18px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: #4677c8;
                border-radius: 5px;
            }
            """
        )

    def browse_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Choose Splitwise CSV files",
            "",
            "CSV files (*.csv);;All files (*.*)",
        )
        self.process_paths(paths)

    def process_paths(self, paths):
        csv_paths = [path for path in paths if path.lower().endswith(".csv")]
        if not csv_paths:
            QMessageBox.warning(self, APP_TITLE, "Drop or choose at least one CSV file.")
            return
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, APP_TITLE, "A batch is already running.")
            return

        display_currencies = self.selected_display_currencies()
        if not display_currencies:
            QMessageBox.warning(self, APP_TITLE, "Choose at least one display currency.")
            return

        self.progress.setRange(0, len(csv_paths))
        self.progress.setValue(0)
        self.status_label.setText(f"Processing 0 of {len(csv_paths)}")
        self.append_output(f"Processing {len(csv_paths)} file(s)...\n")
        self._set_busy(True)

        self.worker = SettlementWorker(csv_paths, display_currencies)
        self.worker.status_changed.connect(self.status_label.setText)
        self.worker.result_ready.connect(self.handle_result)
        self.worker.finished_batch.connect(self.handle_done)
        self.worker.start()

    def handle_result(self, index, _total, result):
        self.append_output(result + "\n\n")
        self.progress.setValue(index)
        self._update_copy_state()

    def handle_done(self, total):
        self.status_label.setText(f"Done - processed {total} file(s)")
        self._dump_output_text_for_debug()
        self._update_copy_state()
        self._set_busy(False)
        self.worker = None

    def append_output(self, text):
        self.output.moveCursor(QTextCursor.MoveOperation.End)
        self.output.insertPlainText(text)
        self.output.moveCursor(QTextCursor.MoveOperation.End)

    def clear_output(self):
        if self.worker and self.worker.isRunning():
            return
        self.output.clear()
        self.progress.setValue(0)
        self.status_label.setText("Ready")
        self._update_copy_state()

    def copy_output(self):
        text = self.output.toPlainText()
        if not text.strip():
            self.status_label.setText("Nothing to copy.")
            return
        QApplication.clipboard().setText(text)
        self.status_label.setText("Results copied to clipboard.")

    def save_output_snapshot(self):
        text = self.output.toPlainText()
        if not text.strip():
            self.status_label.setText("Nothing to save.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save output snapshot",
            "splitwise_settle_output.txt",
            "Text files (*.txt);;All files (*.*)",
        )
        if not path:
            return

        Path(path).write_text(text, encoding="utf-8")
        self.status_label.setText(f"Saved output to: {path}")

    def _dump_output_text_for_debug(self):
        dump_path = os.getenv(DEBUG_DUMP_ENV)
        if not dump_path:
            return
        path = Path(dump_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = self.output.toPlainText()
        path.write_text(text, encoding="utf-8")
        self.status_label.setText(f"Output snapshot saved: {path}")

    def _update_copy_state(self):
        self.copy_button.setEnabled(bool(self.output.toPlainText().strip()))
        self.whatsapp_copy_button.setEnabled(bool(self.output.toPlainText().strip()))

    def copy_whatsapp_output(self):
        text = self.output.toPlainText()
        if not text.strip():
            self.status_label.setText("Nothing to copy.")
            return

        whatsapp_text = format_output_for_whatsapp(text)
        QApplication.clipboard().setText(whatsapp_text)
        self.status_label.setText("WhatsApp-formatted output copied.")

    def selected_display_currencies(self):
        return [checkbox.text() for checkbox in self.currency_checks if checkbox.isChecked()]

    def _set_busy(self, busy):
        self.browse_button.setDisabled(busy)
        self.clear_button.setDisabled(busy)
        for checkbox in self.currency_checks:
            checkbox.setDisabled(busy)


def main():
    app = QApplication(sys.argv)
    app_icon = QIcon(resource_path("assets/icon.png"))
    app.setWindowIcon(app_icon)
    window = SplitwiseSettleWindow()
    window.setWindowIcon(app_icon)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

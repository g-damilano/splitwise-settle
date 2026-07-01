#!/usr/bin/env python3

import csv
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


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
        QFileDialog,
        QFrame,
        QHBoxLayout,
        QLabel,
        QLineEdit,
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
        QFileDialog,
        QFrame,
        QHBoxLayout,
        QLabel,
        QLineEdit,
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


APP_TITLE = "Splitwise Settlement"
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
MONEY = Decimal("0.01")


def resource_path(filename):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, filename)
    return str(Path(__file__).with_name(filename))


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


def parse_display_currencies(text):
    currencies = []
    for item in text.replace(";", ",").split(","):
        currency = item.strip().upper()
        if currency and currency not in currencies:
            currencies.append(currency)
    return currencies or ["EUR", "USD", "GBP"]


def process_file(filepath, display_currencies=None):
    basename = os.path.basename(filepath)
    transactions, people = parse_csv(filepath)
    currencies_used = [tx["currency"] for tx in transactions if tx["currency"]]
    base_currency = Counter(currencies_used).most_common(1)[0][0]
    display_currencies = display_currencies or ["EUR", "USD", "GBP"]

    unique_currencies = set(currencies_used)
    all_needed = unique_currencies.union({base_currency, *display_currencies})
    exchange_rates = get_exchange_rates(base_currency, all_needed)

    converted = convert_transactions(transactions, exchange_rates)
    balances = compute_balances(converted, people)
    simplified = minimize_transactions(balances)

    lines = [
        f"File: {basename}",
        f"Converting everything to: {base_currency}",
        "",
        "Net balances:",
    ]

    for person, bal in balances.items():
        lines.append(f"  {person}: {money(bal)} {base_currency}")

    lines.extend(["", "Transactions to settle:"])
    if simplified:
        for debtor, creditor, amt in simplified:
            converted_amounts = []
            for currency in display_currencies:
                converted = convert_from_base(amt, currency, exchange_rates)
                converted_amounts.append(f"{converted} {currency}")
            conversion_note = ", ".join(converted_amounts)
            lines.append(
                f"  {debtor} pays {creditor}: {money(amt)} {base_currency} "
                f"(about {conversion_note})"
            )
    else:
        lines.append("  Everyone is already settled.")

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
            "then shows converted equivalents at the end of each settlement line."
        )
        self.guidelines.setObjectName("guidelines")
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

        self.currency_input = QLineEdit("EUR, USD, GBP")
        self.currency_input.setPlaceholderText("EUR, USD, GBP")
        self.currency_input.setFixedWidth(180)
        currency_layout.addWidget(self.currency_input)
        currency_layout.addStretch(1)

        main_layout.addLayout(currency_layout)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Settlement results will appear here.")
        self.output.setFont(QFont("Consolas", 10))
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
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #b8c1d1;
                border-radius: 6px;
                padding: 8px 12px;
            }
            QLineEdit {
                background: #ffffff;
                border: 1px solid #b8c1d1;
                border-radius: 6px;
                padding: 6px 8px;
            }
            QPushButton:hover {
                background: #eef3ff;
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

        display_currencies = parse_display_currencies(self.currency_input.text())

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

    def handle_done(self, total):
        self.status_label.setText(f"Done - processed {total} file(s)")
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

    def _set_busy(self, busy):
        self.browse_button.setDisabled(busy)
        self.clear_button.setDisabled(busy)
        self.currency_input.setDisabled(busy)


def main():
    app = QApplication(sys.argv)
    app_icon = QIcon(resource_path("icon.png"))
    app.setWindowIcon(app_icon)
    window = SplitwiseSettleWindow()
    window.setWindowIcon(app_icon)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

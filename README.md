![Splitwise Settle banner](banner.png)

# Splitwise Settle

Splitwise Settle is a small desktop app for turning a Splitwise group CSV export into a compact settlement plan.

It is useful when your group used multiple currencies and you do not have Splitwise Pro currency conversion available. The app reads the exported expenses, converts every split through the European Central Bank daily euro foreign exchange reference rates, minimizes who needs to pay whom, and shows the settlement amounts in the app.

## What it does

- Opens one or more Splitwise CSV exports by drag-and-drop or file picker.
- Uses the most common currency in the export as the settlement currency. This keeps most rows in their original currency and reduces conversion noise.
- Uses the ECB daily exchange-rate feed for currency conversion.
- Minimizes the final payments needed to settle the group.
- Shows the result in EUR, USD, and GBP by default.
- Lets you add more display currencies, for example `CHF, JPY, CAD`.
- Does not upload your Splitwise export anywhere. The CSV is processed locally; only the ECB exchange-rate XML is downloaded.

## Getting the CSV out of Splitwise

1. Open Splitwise in a browser and sign in.
2. Open the group you want to settle.
3. Look for the group settings or export option.
4. Export or download the group expenses as a CSV file.
5. Save that CSV somewhere on your computer.
6. Open Splitwise Settle and drop the CSV into the window.

Splitwise changes its interface from time to time, so the exact menu label may vary. The important part is that you need the group expense export in CSV format.

## Currency conversion

The app uses the ECB euro foreign exchange reference rates:

https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html

ECB rates are published as euro-based reference rates. Splitwise Settle converts from the original expense currencies into the most common currency in your CSV, calculates the settlement there, and then converts each final payment into the display currencies you choose.

## Run from source

Install Python 3.10 or newer, then run:

```powershell
python -m pip install -r requirements.txt
python splitwise_settle.py
```

## Use the prebuilt Windows executable

This repository includes a Windows executable at:

```text
dist\splitwise_settle.exe
```

Download or run that file if you do not want to install Python locally.

## Build a Windows executable

Install the dependencies, then run PyInstaller:

```powershell
python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --onefile --windowed --name splitwise_settle splitwise_settle.py
```

The executable will be created at:

```text
dist\splitwise_settle.exe
```

## Notes

- The app needs internet access when processing files so it can download the latest ECB exchange rates.
- Currencies not present in the ECB feed cannot be converted accurately.
- This is not affiliated with Splitwise or the European Central Bank.

# MR vs CTR Comparator — User Guide

---

## What the app does

The MR vs CTR Comparator is a desktop tool for the SOCAR Cape commercial team. It lets you load one or more **Material Requisition (MR)** Excel files and one or more **Cost Time Resource (CTR)** Excel files, then compares them side-by-side to find:

- Items that appear in **both** documents (matched)
- Items that are in the MR but **missing from the CTR**
- Items that are in the CTR but **missing from the MR**
- Rows where the **quantity or unit** of a matched item differs between the two documents

The result can be reviewed on screen and downloaded as an Excel report.

---

## Application layout

The application window is divided into two main areas stacked vertically:

**Top area — Upload & Configure**
A scrollable panel with two columns side by side: the left column for MR files and the right column for CTR files. Each column has three numbered steps.

**Bottom area — Results**
A collapsible panel that appears after you click Compare. It shows a row of summary counts at the top, then three tabs (Matched / Only in MR / Only in CTR), and a download button at the bottom.

A progress indicator at the very top of the window shows which step you are on: Upload Files → Select Tables → Compare & Review.

---

## Step-by-step usage

### Step 1 — Upload files

1. In the **MR column** (left side, blue border), click **Browse…**
2. Select one or more Excel files that contain MR data. You can select multiple files at once.
3. The file names appear in a list below the button.
4. Repeat the same process in the **CTR column** (right side, teal/green border).

> You can upload multiple MR files and multiple CTR files. The app merges all checked tables from all files before comparing.

To remove all loaded files from one side, click **Clear**.

---

### Step 2 — Select tables

After files are loaded, the app automatically detects tables inside each Excel file (using named ranges — see the Excel preparation section below).

Each detected table appears as a **checkbox item** in the table list. Tables are checked by default. Uncheck any table you do not want to include in the comparison.

Below the checklist is a **live preview** of the data that will be used (the combined rows from all checked tables). This updates automatically as you check or uncheck items.

---

### Step 3 — Settings

**Join key** — a dropdown that lets you choose which column to use for matching rows between MR and CTR. The default is **Stock Code**. Only choose a different column if your files do not contain a Stock Code column.

**Filter** — an optional comma-separated list of stock codes. If filled in, only those codes are included in the comparison. Leave blank to compare all items.

Both the MR column and the CTR column have their own join key and filter settings.

---

### Clicking Compare

Once at least one MR table and one CTR table are loaded, the **Compare** button at the bottom of the upload area becomes active.

Click **Compare** to run the comparison. The results panel expands to fill most of the window.

---

## Reading the results

### Summary counts (top of results panel)

Three boxes show:
- **Matched keys** — number of stock codes found in both MR and CTR
- **Only in MR** — number of stock codes found in MR but not in CTR
- **Only in CTR** — number of stock codes found in CTR but not in MR

### Result tabs

**Matched tab**
Shows every item found in both documents. Columns are arranged in comparison pairs so you can read across one row:

| Stock Code | Description (MR) | Description (CTR) | Qty (MR) | Qty (CTR) | Unit (MR) | Unit (CTR) | Rechargeable | Allocation | Rate (CTR) | MR Document | CTR Document |

MR columns have a light blue tint. CTR columns have a light teal tint. Neutral columns (Stock Code) have no tint.

**Only in MR tab**
Shows items present in MR but absent from CTR. The same column layout is used; CTR columns are empty (shown as blank teal cells).

**Only in CTR tab**
Shows items present in CTR but absent from MR. MR columns are empty (shown as blank blue cells).

All tables are sortable — click any column header to sort.

---

## Compare Values feature

In the top-right corner of the result tabs there is a **Compare Values** button.

Click it while the **Matched** tab is open to highlight every row where:
- **Qty (MR)** and **Qty (CTR)** are different, or
- **Unit (MR)** and **Unit (CTR)** are different

Mismatched cells are highlighted in **red** with dark red text. The mismatch highlight stays visible even when the row is selected (blue selection bar).

The **Matched keys** box shows a summary below the count:
- ⚠ N value mismatches — shown in red when mismatches exist
- ✓ All values match — shown in green when all values are identical

**Navigation arrows** (↑ ↓) appear to the left of the Compare Values button. Use them to jump to the previous or next mismatched row. The status bar at the bottom of the window shows which mismatch you are on and the stock code of that row.

Click **Compare Values** again to clear all highlights.

---

## Downloading the report

Click **Download Excel Report…** at the bottom of the results panel. Choose a save location and file name. The downloaded file contains three sheets:

- **Matched** — all matched items with all comparison columns
- **Only in MR** — items found only in MR
- **Only in CTR** — items found only in CTR

---

## How to prepare your Excel file

The app reads Excel files (.xlsx, .xls, .xlsm). It finds tables automatically using **named ranges**. You do not need to select rows manually.

### Named ranges (recommended)

Define the following named ranges in your Excel workbook using the Name Manager (Formulas tab → Name Manager):

| Named range  | Points to                        | Used for                  |
|--------------|----------------------------------|---------------------------|
| `equip_start`| Top-left cell of the table       | Plant & Equipment (CTR)   |
| `equip_end`  | Bottom-right cell of the table   | Plant & Equipment (CTR)   |
| `cons_start` | Top-left cell of the table       | Consumables / MR table    |
| `cons_end`   | Bottom-right cell of the table   | Consumables / MR table    |

Each `_start` named range should point to the **header row, first column** of the table.
Each `_end` named range should point to the **last data row, last column** of the table.

The rectangle between `_start` and `_end` is read as the full table, including the header row.

> If named ranges are not present, the app falls back to searching for sheets whose names contain keywords such as "consumable", "material", "equipment", or "MR". The first detected header row is used automatically.

---

### Required columns for MR files

The app looks for the following columns in MR tables (column names are matched case-insensitively and with spaces/underscores ignored):

| Column name     | Required | Notes                               |
|-----------------|----------|-------------------------------------|
| Stock Code      | Yes      | Used as the join key                |
| Description     | Yes      | Item description                    |
| Qty             | Yes      | Quantity requested                  |
| Unit            | Yes      | Unit of measure (e.g. EA, M, L)     |
| Rechargeable    | No       | Whether the item is rechargeable    |
| Allocation      | No       | Cost allocation code                |
| Delivery Date   | No       | Expected delivery date              |
| Return Date     | No       | Expected return date                |

---

### Required columns for CTR files

| Column name  | Required | Notes                               |
|--------------|----------|-------------------------------------|
| Stock Code   | Yes      | Used as the join key                |
| Description  | Yes      | Item description                    |
| Quantity     | Yes      | Quantity in the CTR                 |
| Unit         | Yes      | Unit of measure                     |
| Rate         | No       | Unit rate / price                   |
| Days         | No       | Number of days                      |
| Total        | No       | Total cost                          |

---

### Tips for clean matching

- **Stock codes must be consistent.** The app matches rows by stock code. If the MR uses `HT7100021000` and the CTR uses `HT-7100021000`, they will not match. Ensure both files use the same format.
- **Avoid merged cells** in the table area — merged cells can confuse the column detection.
- **One header row only.** The app treats the first row of the named range rectangle as the column header row.
- **No blank rows** inside the data area. Blank rows end the table read.
- **Numeric quantities** should be stored as numbers, not text, to avoid false mismatch warnings (e.g., `1` stored as text vs `1.0` stored as a number). The app attempts to handle this automatically by converting to float before comparing, but clean data produces cleaner results.

---

## Typical workflow summary

```
1. Open the app
2. Browse → select MR Excel file(s)          [left column]
3. Browse → select CTR Excel file(s)         [right column]
4. Review detected tables, uncheck any to exclude
5. Confirm the join key is "Stock Code"
6. (Optional) enter stock code filter
7. Click Compare
8. Review Matched / Only in MR / Only in CTR tabs
9. Click Compare Values to highlight quantity/unit differences
10. Use ↑ ↓ to step through each mismatch
11. Click Download Excel Report to save results
```

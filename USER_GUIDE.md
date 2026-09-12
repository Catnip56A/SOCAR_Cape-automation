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

---

---

# CTR Generator — User Guide

---

## What the CTR Generator does

The **CTR Generator** tab produces the two standard CTR spreadsheets (AZN and USD) plus their PDF equivalents from a single CTR Request form. It:

1. Reads the requested manpower, equipment, and consumables from the **Combined DB** file's CTR Request sheet.
2. Looks up each requested item in the **AZN Pricebook** (manpower) and, for equipment/consumables, in the **Combined DB** file's SAGE and equipment-names sheets, to find the correct rate.
3. Lets you review, correct, and approve every match on screen before writing any output.
4. Writes filled copies of the **AZN Template** and **USD Template** with all rows, totals, and header fields populated — and exports each to PDF via LibreOffice.

---

## Required files

| File | Purpose | Format |
|---|---|---|
| **AZN Template** | Blank CTR template for AZN currency | `.xlsx` |
| **USD Template** | Blank CTR template for USD currency | `.xlsx` |
| **AZN Pricebook** | Rates for manpower (labor) items | `.xlsx` |
| **Combined DB** | SAGE rates, equipment names bridge, and the CTR Request — all in one workbook | `.xlsx` |

The Combined DB file replaces what used to be three separate files (SAGE Export, Equipment Names DB, CTR Request). It must contain these sheets:

| Sheet | Purpose |
|---|---|
| `SAGE` | Rates for consumables **and** equipment, keyed by stock code |
| `CTR_NAMES_DB_USD` | Bridges a CTR Request equipment stock code to the canonical name used to search SAGE by description |
| `CTR_REQUEST` | The customer's request form |

---

## Input file formats

### AZN Pricebook
Must contain a sheet named **"Item Details and Rates"**. The app reads from row 8 onwards and expects these columns (1-indexed):

| Column | Content |
|---|---|
| D (4) | Stock Code |
| E (5) | Product Type |
| F (6) | Unit of Measure |
| I (9) | Supplier Description |
| N (14) | Unit Price (AZN) |

### Combined DB — SAGE sheet
Sheet named **"SAGE"** with at least these columns (exact header names):

| Header | Content |
|---|---|
| `product` | Stock code / product code |
| `long_description` | Item description |
| `unit_code` | Unit of measure |
| `local_expect_cost` | Unit cost |

### Combined DB — CTR_NAMES_DB_USD sheet
Read by column position (A, B, C) — header text doesn't matter:

| Column | Content |
|---|---|
| A | Stock code (as written in the CTR Request) |
| B | Legacy name (fallback display name) |
| C | Canonical pricebook name used to search SAGE by description, or the literal text `NON-RECHARGEABLE` |

Equipment matching tries, in order: (1) if the stock code is tagged `NON-RECHARGEABLE` in column C, the row is auto-matched using the column B name at a rate of **0** — these items are never billed to the client, even if SAGE happens to show a rate for that code; (2) otherwise, if column C has a canonical name, SAGE is searched by that description; (3) if neither applies, the stock code (or description) is looked up directly in SAGE.

### Combined DB — CTR_REQUEST sheet
Must be named **"CTR_REQUEST"**. The standard SOCAR Cape CTR Request form already has the correct layout. Header fields (requester, client, date, etc.) are in rows 5–9. Line items start at row 13:

| Columns A–G | Manpower items |
|---|---|
| Columns H–L | Plant & Equipment items |
| Columns M–P | Consumables |

Beside the line-item table the form also carries an extras block — **Additional Information** (Floatel, Flight tickets, Accommodation, Per Diem, Meal, Training, each *Required* or *Not Required*), **Type of Scaffold System** (a scaffold system and its tonnage) and **TRANSPORT** (type / quantity / duration per vehicle). The app finds this block by its own heading text, so it works whether the block sits to the right of the table (current form) or below it (older forms), and a request form without the block loads exactly as before. See "Additional Info & Transport" below.

---

## Step-by-step usage

### 1. Select templates and Combined DB

In the **Source Files** group at the top, use the **Browse…** buttons next to **AZN Template**, **USD Template**, and **Combined DB (SAGE + Names + Request)** to select your files. All three are shared by both sub-tabs below.

---

### 2. Load pricebook / SAGE reference data

Click the **Pricebook-Based** sub-tab. Select **AZN Pricebook**, then click **Load Files**. This loads the AZN Pricebook plus the SAGE and equipment-names sheets from the Combined DB file selected above. The button is greyed out while loading (files are parsed in parallel in the background — you can continue working). A status line shows how many rows were loaded from each source.

---

### 3. Load a CTR Request

Click the **CTR Request-Based** sub-tab and click **Load CTR Request**. This reads the CTR_REQUEST sheet from the Combined DB file selected above.

The app fills in the **CTR Header Info** fields below (Client, Location, Scope, Date, Comments) and populates three tables:

- **Manpower — AZN**: each requested labor row
- **Plant & Equipment — USD**: each requested equipment row
- **Consumables — USD**: each requested consumable row
- **Additional Info & Transport — AZN**: the request's extras block (see below)

Every row immediately shows its match status:
- **✓ Matched** — an exact match was found in the pricebook/SAGE; rate is pre-filled
- **✗ No match** — highlighted in red; the row needs attention before generating

---

### 4. Fix unmatched rows

Red rows could not be matched automatically. For each:

1. Look at the **Match By** column — this is the search key used for lookup.
2. Edit **Match By** to the correct pricebook description or stock code; the app re-runs the lookup instantly.
3. Alternatively, clear the rate field and tick it as **✓ Manual** if you want to enter a rate by hand.

Use the **◀ Prev Unmatched** and **Next Unmatched ▶** buttons to jump between red rows without scrolling.

Once you fix a mismatch, the correction is saved automatically. The next time the same CTR Request description appears, the app applies the saved rename without you having to fix it again.

---

### 4b. Additional Info & Transport

The **Additional Info & Transport — AZN** group shows what the request's extras block asked for.

**Additional Information.** Items marked *Required* are listed at the top of the group and appended to the AZN CTR's activities section header, e.g. `Offshore Activities : Accomadion, Per Diem required`. Nothing marked *Required* leaves the header as the plain `Onshore Activities` / `Offshore Activities` label.

**Transport.** Each requested vehicle becomes one or more lines of the AZN CTR's **Third Party Activities** section (a section the template doesn't ship with — it's added only when there are transport lines, and the Summary block gains a matching *Total Other Activities* item). The request form only says which vehicle, how many, and for how long, so the **Description**, **Rate AZN**, **Mark Up %** and **Duration UOM** come from the transport rate table in `ctr_tools/template_config.json` and are editable in the table before generating:

- one requested vehicle can bill as several lines — a minibus charges vehicle+driver and its fuel separately, and only the former carries mark-up;
- a vehicle that isn't in the rate table still gets a line, at a 0.00 rate, so it's there to price by hand — generating warns about any line still priced at 0.00.

Each row's total is `Quantity × Rate × Duration × (1 + Mark Up)`.

**Scaffold.** Scaffold is equipment, so a requested scaffold tonnage is added to the **Plant & Equipment — USD** table instead of appearing here — as an ordinary equipment line carrying the three things the request states: the system's name, the tonnage as **Quantity**, and **TON** as the unit. It reaches the USD CTR's Pricing sheet exactly like every other equipment line:

`1 | Conventional Scaffold | 66.96 | TON | rate/day | days | total`

The request gives no rate and no duration for scaffold, so **Rate/Day and Days both arrive blank** — they're yours to fill in. The row is marked **✓ Manual** rather than red: it can't match the pricebook, but the request did ask for scaffold, so the line is written to the CTR either way with its name, tonnage and unit, totalling 0.00 until you price it. If you set **Match By** to the pricebook description instead of typing a rate, that correction is remembered and the next request naming the same scaffold system matches on its own.

---

### 5. Review totals

Running totals are shown below each table:
- **AZN**: Project Support (onshore) + Total Offshore + Total Third Party Activities = AZN CTR Total
- **USD**: Equipment total + Consumables (with 6.5% markup) = USD CTR Total

Only **✓ Matched** and **✓ Manual** rows are included in totals and in the generated output. Red **✗ No match** rows are excluded and never written to the output file at a rate of zero.

---

### 6. Fill in the Generate section

Scroll down to the **Generate** group:

| Field | Notes |
|---|---|
| **Client** | Written to cell B3 of both templates |
| **Sub-Client** | Written to cell C3 |
| **Location** | Written to cell B4 |
| **Scope** | Written to cell A6 |
| **Date** | Defaults to today; written to cell E4 |
| **Contract No (AZN)** | Written to cell G4 of the AZN template |
| **Contract No (USD)** | Written to cell G4 of the USD template |
| **Revision** | Defaults to 0; written to cell E5 |
| **Comments** | Free text from the request's own Comments box (O6); written to cell C1 of both templates. Always written, even when blank, so a previous request's comments can't linger in an unrelated CTR. |
| **Job Ref** | Must be a number (e.g. 217); used in the file name and cell O3 |
| **Output folder** | Where the generated files are saved |

**Presets** — save common field combinations (client, location, scope, revision, job ref, output folder) under a name so you don't have to re-type them for recurring projects. Use **Save…** to store the current values, then **Load** next time. Contract No (AZN/USD) is deliberately *not* included in presets, since it's specific to each generated document — loading a preset never overwrites whatever you've typed there.

---

### 7. Generate

Click **Generate CTR Documents**. The app:

1. Copies both template files to the output folder
2. Writes all matched rows and totals into the copies
3. Exports each spreadsheet to PDF via LibreOffice (requires LibreOffice to be installed; PDF step is skipped with a clear warning if LibreOffice is not found)

A progress dialog is shown during generation. When complete, a message lists the four output files (AZN xlsx, USD xlsx, AZN pdf, USD pdf).

Output file names follow the pattern:  
`{JobRef}_AZN_WCH_CTR.xlsx` / `{JobRef}_USD_WCH_CTR.xlsx`

---

## Common errors and fixes

| Error message | What it means | Fix |
|---|---|---|
| *"AZN Pricebook not selected"* | The required AZN Pricebook field is empty | Select the pricebook file and click Load Files |
| *"Combined DB file not selected"* | The required Combined DB field is empty | Select the Combined DB file in the Source Files section |
| *"…is missing expected column(s)…"* on the SAGE sheet | The Combined DB's SAGE sheet doesn't have the expected headers | Check that the header row uses exactly `product`, `long_description`, `unit_code`, `local_expect_cost` |
| *"Could not find a sheet named CTR_REQUEST"* | The Combined DB file uses a different sheet name | Open the file in Excel and rename the sheet to `CTR_REQUEST` |
| *"Job Ref must be a number"* | Non-numeric text was entered in Job Ref | Enter a number only, e.g. `217` |
| *"AZN Template not found"* | The template file was moved or deleted since it was selected | Browse to the file again |
| *"Cannot save … it may be open in Excel"* | The output file is already open in Excel | Close the file in Excel and click Generate again |
| *PDF export skipped* | LibreOffice (soffice) is not on PATH | Install LibreOffice — the xlsx files are still produced correctly |

---

## Saved Renames

When you fix a **Match By** value to get a ✓ match, the correction is remembered permanently in `ctr_tools/match_aliases.json`. Next time the same CTR Request description appears, the corrected search key is applied automatically.

To view, delete, or export saved renames: click the **Manage Saved Renames…** button in the Manpower section header. You can also use **Import…** and **Export…** to share a renames file between machines.

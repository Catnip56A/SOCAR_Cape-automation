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
A collapsible panel that appears after you click Compare. It shows a row of summary counts at the top, then the result tabs (Matched / Only in MR / Only in CTR / Needs Review / Error Data / Statistics), and download and save buttons at the bottom. In a narrow window the Statistics toolbar wraps onto a second line, and the tab can be scrolled.

Under the progress indicator, **Compare** and **Load Comparison…** share one line (same size), and the **SAGE Export** field (the file used for the cost statistics) sits on the line below them, followed by one line with the **USD Pricebook** and **Combined DB** fields (the files the *Document price* view needs for equipment rates; all three are shared with the CTR Generator tab).

**Ctrl+F** in any table or tree opens a find bar over it: type to search every visible column, **Enter** (or ▶) goes to the next match and **Shift+Enter** (◀) to the previous one, with a counter such as “3 of 12”; **Esc** closes it. It only moves between matches — the “Filter rows” boxes are what hide rows.

In every table, if a cell's text is cut off because the column is too narrow, rest the mouse on the cell to see the whole text in a tooltip (a cell that already has its own hint shows the full text first, then the hint).

A progress indicator at the very top of the window shows which step you are on: Upload Files → Select Tables → Compare & Review.

---

## Step-by-step usage

### Step 1 — Upload files

1. In the **MR column** (left side, blue border), click **Browse…**
2. Select one or more Excel files that contain MR data. You can select multiple files at once.
3. The file names appear in a list below the button, each with a **✕** button that removes just that file (and all its tables) without touching the others.
4. Repeat the same process in the **CTR column** (right side, teal/green border).

> You can upload multiple MR files and multiple CTR files. The app merges all checked tables from all files before comparing.

To remove all loaded files from one side at once, click **Clear**.

**If a file is edited after you added it** (for example you fixed a quantity in Excel), its name turns orange with ⟳ and a **⟳ Reload changed files** button appears above the lists — the tables you see are still the old version until you reload. Press it (or choose *Reload them and compare* in the warning Compare shows) and the app re-reads the file from where you added it and replaces its tables; sheets you had unticked stay unticked. If the file was deleted or moved you choose: **Locate it…**, **Remove it from the list**, or **Keep the old tables**. A file that cannot be read (for example locked) keeps its old tables and is named in a message; the other files still reload. After a reload press **Compare** again. Files opened from a saved comparison have no file to reload from.

---

### Step 2 — Select tables

After files are loaded, the app automatically detects tables inside each Excel file (using named ranges — see the Excel preparation section below).

Each detected table appears as a **checkbox item** in the table list. Tables are checked by default. Uncheck any table you do not want to include in the comparison.

**MR Socar-Cape sheet.** Each MR also gets a "Socar-Cape sheet" row at the end of its group. It is the MR's cover sheet, which has the same structure as the `CH-*`/`NCH-*` sheets but repeats the whole request, so it is **unticked by default**. Tick it when you need it — instead of, or together with, the `CH-*`/`NCH-*` sheets (using both counts every row twice). If an MR has no `CH-*`/`NCH-*` data, its row is highlighted amber as a hint; the decision is always yours. If a file has no readable cover sheet the row is shown greyed out. This applies to MR files only.

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

All tables are sortable — click any column header to sort. **Each header click cycles through three states: ascending, descending, and back to the original row order** (the arrow on the header disappears in the third state).

**Days and Local cost.** The last columns of **Matched**, **Only in MR** and **Only in CTR** are **Days (MR)**, **Days (CTR)** and **Local cost**. *Days* are the days of that side's line, for **equipment** only (a consumable shows none): a CTR line's own Days, an MR line's days taken from the matching CTR, or what you typed or picked in the Statistics tab. *Local cost* is SAGE's expected unit cost (`local_expect_cost`, the same figure as *Unit cost* in the Statistics tab) for the row's Stock Code; it is blank until the statistics have been calculated, and for a Stock Code that is not in the SAGE Export. Tick **Local cost total** (toolbar above the tabs; it only appears on these three tabs) to show the **total** instead, by the statistics' formula: **equipment = days × cost × quantity, consumables = cost × quantity**. **Matched** then shows **Local cost total (MR)** (with the MR quantity) and **Local cost total (CTR)** (with the CTR quantity); the Only tabs fill the column of their own side. A total that cannot be worked out stays blank, and hovering the cell says why (not in SAGE, no quantity, **needs the Days**, type unknown or undecided). Typing or picking days in the Statistics tab updates these columns at once. All of it is part of the Excel report's *Matched*, *Only in MR* and *Only in CTR* sheets. **Combined View** gets the same columns at its end (after *Flag*): one row there is a Stock Code built from several lines, so *Days* shows one number when the lines agree and **"7 / 182"** when they differ (hover for which document says what), and the **total is summed line by line** — each line's quantity × its own days × cost. They are in the *Combined* sheet of the Excel report too.

**Selecting rows.** Selecting a row turns it blue, but a **red or orange cell keeps its colour** (with a thin blue frame around it), so a mismatch or a missing Days cell never disappears under the selection. The same is true in the Statistics list for red "not counted" rows and orange cells.

**Hiding columns.** Right-click a column header and choose **Hide column** to hide it from view (any result table). This is visual only — the downloaded Excel report always contains every column, and hidden columns reset when the table is reloaded (for example on a new Compare). To bring columns back, click the **Columns (n)** button in the toolbar above the tabs, or right-click a header and choose **Unhide columns…**, then tick the ones to show. The last visible column can't be hidden.

---

## Compare Values feature

In the top-right corner of the result tabs there is a **Compare Values** button.

Click it while the **Matched** tab is open to highlight every row where:
- **Qty (MR)** and **Qty (CTR)** are different, or
- **Unit (MR)** and **Unit (CTR)** are different, or
- MR's **Rechargeable** flag and CTR's **Rate** disagree about whether the item is billable (a number in Rate means rechargeable; `NONRECHARG` means not)

This Rate/Rechargeable check is only highlighted while Compare Values is on.

Mismatched cells are highlighted in **red** with dark red text. The mismatch highlight stays visible even when the row is selected (blue selection bar).

The **Matched keys** box shows a summary below the count:
- ⚠ N value mismatches — shown in red when mismatches exist
- ✓ All values match — shown in green when all values are identical

**Navigation arrows** (↑ ↓) appear to the left of the Compare Values button. Use them to jump to the previous or next mismatched row. They follow the **order you see on screen**: after you sort a column, ↓ goes to the next highlighted row below the one selected, and the arrows wrap around at the ends. The status bar at the bottom of the window shows which mismatch you are on and the stock code of that row.

Click **Compare Values** again to clear all highlights.

In **Combined View** a second, separate **Compare Values** button appears (the original one becomes **Show Combined**, which marks Stock Codes built from several documents in orange). It highlights in red every Stock Code whose **Qty (MR) Total** and **Qty (CTR) Total** differ (including the Diff column), or whose MR and CTR units differ (shown in the **Unit** column as both units). The ↑ ↓ arrows step through the highlighted rows; with both buttons on, red wins on any shared cell. When both colours are present, a small selector next to the arrows (**All / Red / Orange**) lets the arrows step through **only the red rows** (Qty or Unit differs) or **only the orange rows** (built from several documents); choosing a colour jumps to the first row of that colour.

---

## Filter documents

Above the result tabs, the **Filter documents** box limits what you see to rows that came from the MR/CTR files (and sheets) you choose. It only changes the view — the Excel download and Save Comparison still contain the full comparison.

Write each name as `MR:<document>` or `CTR:<document>`, optionally followed by `>` and a sheet name:

| You type | Shows rows where… |
|---|---|
| `MR:Jan` | the MR side came from a file whose name contains "Jan" |
| `MR:Jan>CH-01` | … and from the sheet whose name contains "CH-01" |
| `CTR:sage` | the CTR side came from a file containing "sage" |

Part of a name is enough and capitals don't matter. As you type, a list of matching documents and sheets from your uploaded files appears (up to 200 entries; scroll for more) — pick one to complete the name: click it, or use ↑ ↓ and **Enter** / **Tab** (**Esc** closes the list, ↓ opens it again). After you choose a document, the list immediately offers that document's sheets (or type `>` yourself); press **Esc** to keep just the document. The list also appears after `(`, `and`, `or` and `not`.

Combine names with **and**, **or**, **not**, and use **( )** to group, like a search tool:

`(MR:Jan>CH-01 or MR:Feb) and not CTR:draft`

`not` is applied first, then `and`, then `or`. If a name contains brackets, `>` or the words and/or/not, put it in quotes: `MR:"Rock and Roll (1).xlsx"`. The sheet always follows the file with `>` and no spaces around it: `MR:Jan_MR.xlsx>CH-1` — writing `MR:Jan_MR.xlsx CH-1` looks for a file literally named that. When a name matches nothing, an orange note appears beside the box and the status bar explains (and points out a missing `>`). If the expression isn't valid yet (for example an open bracket), the reason is shown in red and the previous filter stays on. Clear the box to show everything. The filter resets whenever you run a new Compare or load a saved comparison.

It applies to **Matched**, **Only in MR** and **Only in CTR**, and a count ("Showing 12 of 40 matched …") sits beside the box. The ↑ ↓ arrows only visit rows you can see. **Show Document Names** now also shows each row's MR/CTR sheet.

In **Combined View** the Stock Codes that don't involve the chosen documents are hidden, but the totals shown are still the full totals. Hover a **Qty (MR) Total**, **Qty (CTR) Total** or **Diff** cell to see how much of it comes from the chosen documents, for example "MR from selected documents: 10 of 40 (25%)". Comparisons saved before version 1.0.19 can still be filtered by document, but they hold no sheet names or quantity breakdown: `>sheet` can't match and no sheets are suggested, and an orange note says so. Click **Compare** again (or upload the files and Compare) to get sheet filtering.

---

## Combined View, Needs Review and Error Data

After a Compare, the **Combined View** button (next to Compare Values) switches the Matched tab to one row per Stock Code. MR and CTR quantities are summed across every matched row, and a **Diff (CTR − MR)** column shows the difference.

Quantities can only be summed safely if the rows use the same unit. If the MR and CTR rows for a Stock Code use different units (for example `EA` in MR and `BOX` in CTR), that code is held out of the Combined table and listed in the **Needs Review** tab. For each one you choose:

- **Approve** — sum the quantities anyway. The code moves to the Combined table and is marked in the **Flag** column.
- **Reject** — leave the code out of the totals. It moves to the **Error Data** tab with the reason "Unit conflict … rejected, excluded from Combined totals".

### Flag column

The **Flag** column appears in the Combined view and in the Excel report's Combined sheet. It has two possible values:

| Flag | Meaning |
|---|---|
| *(blank)* | MR and CTR use the same unit; the totals are safe to compare. |
| ⚠ Unit conflict — approved | The units disagreed and you approved summing them in Needs Review. The Unit column shows both units (for example `BOX / EA`). Check the totals by hand before relying on them. |

Approve/Reject decisions are cleared when you run a new Compare.

**No quantity.** Rows whose quantity is blank or not a number are also listed in **Needs Review**, under a red "No quantity" heading (the tab badge counts them). There is nothing to approve: they cannot be costed, so they are left out of the cost statistics until the quantity is filled in in the source file and you Compare again.

---

## Statistics tab (cost of the MR and CTR)

The **Statistics** tab shows how much of the **SAGE expected cost** of your MR and CTR documents is rechargeable and how much is not. It is SAGE's expected cost (`local_expect_cost`), **not what the client is billed**, and it is calculated for every row of the compared files, matched or not.

**Before you start.** Choose the **SAGE Export** file in the field under the **Compare** and **Load Comparison…** buttons. It is the same field as in the CTR Generator tab — setting it in one sets it in the other. Without it nothing can be costed: a yellow banner in the tab says so and cannot be closed. Statistics are calculated in the background after Compare (the banner says they will appear shortly; you can keep working) and the banner disappears when they are ready. Changing the SAGE file after a Compare recalculates them automatically; **Reload statistics** reads the file again after you have edited it. The statistics also notice an edited file on their own: if the SAGE Export, USD Pricebook or Combined DB changed on disk since it was loaded, it is read again, and pressing **Load Files** in the CTR Generator recalculates the current statistics. When you press **Compare**, a warning names any MR / CTR file that was edited after you added it (you can *Compare anyway*, or cancel and add the file again).

**Reading it.** The first rows are **MR — all documents shown** and **CTR — all documents shown**. Each expands to one row per document, and each document expands to its Stock Codes. Every row shows **Rechargeable**, **Non-Rechargeable** and **Unknown** cost, with a percentage beside each (the share of that group's costed total), plus **Total cost** and **Non ÷ Rech** — the Non-Rechargeable cost divided by the Rechargeable cost, in money (for example `1.34` means 1.34 of non-rechargeable cost for every 1.00 of rechargeable). It follows **Multiply cost by quantity**, which is on by default, so out of the box it compares quantity-weighted cost; with the switch off it compares unit costs. It shows **n/a** when there is non-rechargeable cost but no rechargeable cost to divide by, and is left blank on individual Stock Code lines. *Unknown* means the document does not say whether the item is rechargeable. When there is an Unknown amount, Rechargeable % + Non-Rechargeable % is less than 100 — that is intended.

**Where rechargeability comes from.** From the documents themselves: MR's *Rechargeable* column (`RECHARGE` / `NONRECHARG`) and, for the CTR, the *Rate* column (a number means rechargeable, `NONRECHARG` means not). This is the working default and is open for discussion with the department; SAGE is used only for the cost.

**Controls above the table**
- **Multiply cost by quantity** (on by default): cost = SAGE unit cost × quantity, and **× days for equipment** (the same formula as Document price; consumables are cost × quantity). Off shows the unit cost alone for each row (no quantity, no days).
- **All types / Equipment / Consumable / Uncategorised**, **All items / Rechargeable / Non-Rechargeable / Unknown / Not counted**, and a sort order for the Stock Codes inside each document (highest or lowest cost, highest or lowest unit cost, Stock Code). The totals follow these filters.
- The **Filter documents** box above the tabs also applies: only the chosen documents are listed and totalled. A CTR row stays when the MR document you chose contains the same Stock Code, as in the other tabs.

**Items that cannot be costed** are shown in red and are **not counted**: Stock Codes that are not in the SAGE Export, and rows with no quantity (while *Multiply cost by quantity* is on). For a code that is not in SAGE, double-click its **Unit cost** cell and type the cost — it is then used for every row with that Stock Code (SAGE wins if it later contains the code); clear the cell to remove it. Typed costs are kept when you save the comparison. Alternatively, update the SAGE file and press **Reload statistics**.

**Type (Equipment / Consumable).** The type of a Stock Code is the CTR section it is listed under (*Plant & Equipment* or *Consumables & Materials*). A code that is in no CTR — which includes everything in **Only in MR** — or that appears in both sections, is **Uncategorised**. The **Type** box next to *Filter documents* filters Matched, Only in MR, Only in CTR and the Combined View the same way (view only), and hovering a Stock Code shows its type. Comparisons saved before this version have no sections stored, so the Type box is disabled for them until you Compare again.

**Saving and the report.** Save Comparison stores the statistics with the comparison, so reopening it shows them straight away without SAGE (Reload statistics needs the original files). The Excel report gains the sheets **Statistics**, **Statistics Items**, **Price Statistics**, **Price Items** and **No Quantity**; they always contain everything, whatever the filters show.

### Document costs and Document price

Two buttons at the left of the toolbar choose what the figures mean:

- **Document costs** — SAGE's expected unit cost × quantity, **× days for equipment** (what the items cost us). Since version 1.0.21 equipment needs its days here too, exactly as in Document price: a line whose days are unknown, or whose type is undecided, is not counted until you supply them.
- **Document price** — what the documents **bill**: **quantity × rate**, and **× days** for equipment. A **Non-Rechargeable** item is billed at **0**, whatever its quantity and rate. The rate is looked up like the CTR Generator does it, with no fallback between the two: **equipment** from the **USD Pricebook** (the Combined DB's equipment-names sheet turns a stock code into the pricebook description; the normal `-NOR` rate is preferred), **consumables** from **SAGE**. Whether a Stock Code is equipment or a consumable comes from the **Stock Code types list** (see below).

Example: a rechargeable pump, quantity 2, rate 100, 5 days = 2 × 100 × 5 = **1,000**; the same pump non-rechargeable = **0**. A rechargeable roll of sheeting, quantity 10, rate 34 = **340**.

**Before you use Document price.** Set the **USD Pricebook** (and, if you use it, the **Combined DB**) in the line under the SAGE Export field; they are shared with the CTR Generator tab like the SAGE Export. A yellow note in the tab says when the USD Pricebook is missing, and equipment then has no rate.

**Days.** A **CTR line carries its own Days**. An **MR line takes the days of the matching CTR line** (same Stock Code) when the CTR lines agree; its Note says *days: from the matching CTR row*. When **two or more CTRs give different days** for a code (for example 7 in one CTR and 182 in another), the line is **not priced yet**: its Days cell shows *choose…* — **double-click it and pick** one of the values (each is shown with the CTR it comes from) **or type your own number**. When there is **no matching CTR line, or that CTR line has no days**, the Days cell is highlighted in **orange** (*type days*) and the line waits: double-click it and type the days. What you type or pick is used for every row with that Stock Code, overrides days taken from a CTR, and is saved with the comparison; clear the cell to remove it. **You can also edit the days directly in the results tables:** double-click a **Days (MR)** or **Days (CTR)** cell of an equipment line (Matched, Only in MR, Only in CTR, Combined View), type the days and press Enter (an empty cell clears them). If the other side has a line of the same Stock Code, a pop-up asks whether to **sync** it (*Sync with CTR/MR*), change **only this side**, or **Cancel**. Days typed for one side win over that line's own days, and are saved with the comparison. **Days affect costs as well as prices** (equipment = days × cost × quantity), so the **Days** column is shown in both views. An **orange** Days cell means the days are missing; an empty or dash cell always says why when you hover it (*a consumable is not billed per day*, *the type is not decided yet*, *no CTR line*). The same days and the same orange highlight appear in the **Days (MR) / Days (CTR)** columns of Matched, Only in MR and Only in CTR, where a total that is blank because the days are missing says so too.

**Nothing is assumed.** A row with no quantity, no rate or (equipment) no days is **not counted**; it is shown in red and the banner counts the Stock Codes. Disparities are still left out until you choose a source (in the Disparities window, or in *Settings → Rechargeability choices*).

**Equipment or Consumable.** Document price needs to know which a Stock Code is, because the rate source and the days differ. The answer comes from the **Stock Code types list** that ships with the app (Settings → *Stock Code types*, editable). The order is: **a type you confirmed**, then **the list** (a code that is in both of its lists counts as Equipment), then **the CTR section** the code is listed under. The Type column, the Type filter and the Stock Code tooltips use the same answer.

When the sources **disagree** — the list says one thing and the CTR section another, or a code is in both lists and nothing backs Equipment up — the code is **not priced** until you decide. A **Stock Code types (n)** button appears in the toolbar; it opens a window listing each such code **with its description**, what the list says, what the CTR section says and the proposal. Press **Decide…** to *accept the proposal* or choose *Equipment* / *Consumable* yourself, or **Accept all proposals**. Your decision is saved into the list (marked as confirmed) and you are not asked again. A code that **no source knows** is also not priced ("Type unknown"): choose it in the same window, or add it in Settings.

Every line that cannot be priced says **why and how to fix it** — in its Note (hover for the full text), in the yellow banner, and in a **How to fix** column of the Excel *Price Items* sheet. For example: *no rate* for equipment → add the item to the USD Pricebook (or its code to the Combined DB equipment-names sheet) and Reload statistics; *no rate* for a consumable → add the code with a cost to the SAGE Export; *no days* → type the days; *type unknown* → add it in Settings → Stock Code types.

**Include scaffold.** A tick box, **off by default** (the default can be changed in Settings). Scaffold is rechargeable equipment, so the box exists only in the CTR Generator, where the scaffold line exists; the MR vs CTR Statistics tab shows "Scaffold: not included here" in its place. It has no SAGE cost, so it never shows in Document costs.

There is **no markup option in this tab**: prices are without markup. (The CTR Generator's statistics have **With markup**, see *4c*.)

---

**Disparities.** A **Disparities** button (red exclamation mark, with the number of Stock Codes) appears in the toolbar when a Stock Code's rechargeability in the MR/CTR file contradicts SAGE's own tag. It opens a list; those lines are not counted in the totals and show in red. See the CTR Generator's *Cost statistics* for the same button.

## Downloading the report

Click **Download Excel Report…** at the bottom of the results panel. Choose a save location and file name. The downloaded file contains three sheets:

- **Matched** — all matched items with all comparison columns
- **Only in MR** — items found only in MR
- **Only in CTR** — items found only in CTR

It also contains **Combined** and **Error Data** when the Combined View has been used, and — when the cost statistics have been calculated — **Statistics**, **Statistics Items**, **Price Statistics**, **Price Items** and **No Quantity** (see *Statistics tab* above). The document and type filters never shorten the report.

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
2. Looks up each requested item to find the correct rate: manpower in the **AZN Pricebook**, equipment in the **USD Pricebook** (the Combined DB's equipment-names sheet translates a request stock code into the pricebook description), and consumables in the **SAGE Export**.
3. Lets you review, correct, and approve every match on screen before writing any output.
4. Writes filled copies of the **AZN Template** and **USD Template** with all rows, totals, and header fields populated — and exports each to PDF via LibreOffice.

---

## Required files

| File | Purpose | Format | Needed for |
|---|---|---|---|
| **AZN Template** | Blank CTR template for AZN currency | `.xlsx` | Generate |
| **USD Template** | Blank CTR template for USD currency | `.xlsx` | Generate |
| **AZN Pricebook** | Rates for manpower (labor) items | `.xlsx` | Load Files |
| **SAGE Export** | Reference catalog and rates for consumables (and their rechargeable flag) | `.xlsx` / `.xlsm` | Load Files |
| **USD Pricebook** | Rental rates for equipment | `.xlsx` | Load Files |
| **Combined DB** | The CTR Request sheet and the equipment-names bridge sheet, in one workbook | `.xlsx` | Load CTR Request (the names sheet is optional for Load Files) |

The **AZN and USD templates are maintained by the whole Commercial department** — there is no single owner. The app writes into fixed cell positions (`ctr_tools/template_config.json`) and refuses a template whose layout differs, so any change to a template's layout has to be agreed within the department and carried into `template_config.json` at the same time.

Load Files refuses to start until the AZN Pricebook, SAGE Export and USD Pricebook are all selected. The Combined DB is optional there: without it, equipment is matched by its "Match By" description directly against the USD Pricebook, with no stock-code bridge. It is required to load a CTR Request. It must contain these sheets:

| Sheet | Purpose |
|---|---|
| `CTR_NAMES_DB_USD` | Bridges a CTR Request equipment stock code to the description used to look the item up in the USD Pricebook |
| `CTR_REQUEST` | The customer's request form |

The templates are not installed with the app. Each user keeps their own copies and must replace them when the app's layout changes (see the template check under Generate).

---

## Input file formats

### AZN Pricebook and USD Pricebook
Both files share one layout. Each must contain a sheet named **"Item Details and Rates"**. The app reads from row 8 onwards and expects these columns:

| Column | Content |
|---|---|
| E | Stock Code |
| F | Product Type |
| G | Unit of Measure |
| J | Supplier Description |
| O | Unit Price (AZN or USD) |

On the **USD Pricebook** the stock code is not unique per item: each equipment line carries one of two generic rate codes, ending `-NOR` (normal) or `-STBY` (standby). The app matches equipment by Supplier Description and prefers the `-NOR` rate when both exist.

### SAGE Export
Sheet named **"FROM SAGE"** (in a dedicated file, usually `.xlsm`) with these columns (exact header names):

| Header | Content |
|---|---|
| `product` | Stock code / product code |
| `long_description` | Item description |
| `unit_code` | Unit of measure |
| `local_expect_cost` | Unit cost |
| `analysis_b` | Rechargeability tag. `NONRECHARG` (or `NONRECHAR`) marks a non-rechargeable consumable: it is shown as "NONRECHARG" instead of a rate |

The placeholder code `S0000000000` is ignored.

### Combined DB — CTR_NAMES_DB_USD sheet
Read by column position (A, B, C) — header text doesn't matter:

| Column | Content |
|---|---|
| A | Stock code (as written in the CTR Request) |
| B | Legacy name (fallback display name) |
| C | Canonical pricebook name used to search the USD Pricebook by description, or the literal text `NON-RECHARGEABLE` |

Equipment matching tries, in order: (1) if the stock code is tagged `NON-RECHARGEABLE` in column C, the row is auto-matched using the column B name at a rate of **0** — these items are never billed to the client, even if SAGE happens to show a rate for that code; (2) otherwise, if column C has a canonical name, the USD Pricebook is searched by that description; (3) if neither applies, the description is looked up directly in the USD Pricebook.

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

In the **Source Files** group at the top, use the **Browse…** buttons next to **AZN Template**, **USD Template**, and **Combined DB (Names + Request)** to select your files. All three are shared by both sub-tabs below.

---

### 2. Load pricebook / SAGE reference data

Click the **Pricebook-Based** sub-tab. Select the **AZN Pricebook**, **SAGE Export** and **USD Pricebook** files, then click **Load Files**. This loads those three files, plus the equipment-names sheet from the Combined DB file selected above if there is one. The button is greyed out while loading (files are parsed in parallel in the background — you can continue working). A status line shows how many rows were loaded from each source.

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

**Nothing is assumed.** If the request does not give a value a CTR line needs, that cell stays **empty and turns red** — it is never replaced by 1, 0 or 30. The values checked are: *Manpower* — Num Employees, Qty (hours), Rate, Shift, Shift Type, Nationality; *Equipment* — Qty, Unit, Rate, Days; *Consumables* — Qty, Unit, Price; *Transport* — Qty, Duration, Unit, Rate. A rate or price may be a number **or `NONRECHARG`** (a non-rechargeable line needs no rate; a rechargeable one does); a 0 you type counts as filled in. A yellow warning above the *Cost statistics* box counts the missing values. Next to it the **Go to value** button helps you find them: *click* it to go to the next one (a missing required value first, then a Stock Code that is not in SAGE) and click again for the one after; use its **▾ arrow** to open a **list** and choose which one. A missing value is shown by selecting its whole row (blue) and painting the cell itself **orange** with a border; a Stock Code not in SAGE is opened in the *Cost statistics* list with its *Unit cost* ready for typing. A row added with **+ Add Row** starts empty for the same reason. This includes **Shift** and **Shift Type**: when the request does not state them the cells stay empty, the row stays unmatched, and you choose them from the **drop-down** in each cell (Day/Night, Normal/Overtime/Standby) — they are never assumed to be Day/Normal.

If you press **Generate** with missing values, a **red list** shows each affected row and what it lacks. **Generate without them** creates the CTRs *without those rows* (they are named in the log and the final message says how many were left out); **Cancel** takes you to the first empty cell. Enter the values to include them.

---

### 3b. Manpower: the Encoding

The **Encoding** column (e.g. `NS-NAT-OFF-12`) decides a manpower row's Type (Onshore/Offshore), Shift, Shift Type and shift length, and with them the pricebook rate. It is filled from the request. **If the request gives none, no rate is written**: the row shows *“the request gives no Encoding”* and stays red until you pick one from the cell's drop-down (click anywhere on it), which lists only the encodings valid for that role; typing letters jumps to the matching entry and Enter confirms. The Type, Shift and Shift Type are then filled in for you. Changing or clearing the Encoding clears the old rate, so a rate never stays from an earlier answer.

**Onshore and Offshore are never mixed.** An Offshore row only accepts `-OFF-` encodings and an Onshore row only `-ON-` ones; an encoding that disagrees with the request's Project Type is refused with an explanation. The exception is the roles listed as *always Onshore* in the Combined DB (sheet `ENC_LIST`, column H — in the test file Project Engineer, Senior Project Engineer, TAR Coordinator and Planner): they use Onshore encodings even in an Offshore request. The valid encodings per role come from the Combined DB's *Possible Variants* sheet and are read when you press **Load Files**. Allowance codes (floatel, hot-bed, per-diem) are not handled yet.

**Before Load Files** the rows are shown neutral with *“⏳ Waiting for Load Files”* instead of red; they are matched, and coloured if they really do not match, once the files are loaded. **Generate** will not run while rows are still waiting.

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

**Quantities of 0 or less.** Generate warns about any quantity that is 0 or negative (it goes into the CTR as entered, so check it). A *transport* line with a quantity of 0 or less is **hidden** in the AZN CTR (the row is written but hidden, so it does not print and counts as 0); if every transport line is like that, the Third Party Activities section is left out.

- one requested vehicle can bill as several lines — a minibus charges vehicle+driver and its fuel separately, and only the former carries mark-up;
- a vehicle that isn't in the rate table still gets a line, with the **Rate left empty** (red), so it's there to price by hand — generating leaves out any transport line whose Qty, Duration, Unit or Rate is still empty (after you confirm; see *Nothing is assumed* above). A quantity or duration the request does not state is left empty too, never assumed to be 1.

Each row's total is `Quantity × Rate × Duration × (1 + Mark Up)`. **Duration** is read from the number at the start of the *Duration & UOM* cell (`6 Days`, `2 Trips`, `1.5 Hours`, or just `4`), so you can edit that cell in the finished CTR and the total follows; keep the number first and a space before the unit.

**Scaffold.** Scaffold is equipment, so a requested scaffold tonnage is added to the **Plant & Equipment — USD** table instead of appearing here — as an ordinary equipment line carrying the three things the request states: the system's name, the tonnage as **Quantity**, and **TON** as the unit. It reaches the USD CTR's Pricing sheet exactly like every other equipment line:

`1 | Conventional Scaffold | 66.96 | TON | rate/day | days | total`

The request gives no rate and no duration for scaffold, so **Rate/Day and Days both arrive blank** — they're yours to fill in. The row is marked **✓ Manual** rather than red: it can't match the pricebook, but the request did ask for scaffold, so the line is written to the CTR either way with its name, tonnage and unit, totalling 0.00 until you price it. If you set **Match By** to the pricebook description instead of typing a rate, that correction is remembered and the next request naming the same scaffold system matches on its own.

---

### 4c. Cost statistics

The **Cost statistics** box (after Additional Info & Transport) shows what the loaded CTR Request costs at **SAGE's expected unit cost** — not what the client is billed (use the **Document price** button for what the CTR bills, see below) — split into **Rechargeable**, **Non-Rechargeable** and **Unknown**, with a percentage beside each. Under **CTR Request** it lists **Equipment** and **Consumables**; expand either to see its lines, sort them by cost or unit cost, or filter them. It uses the same panel as the Comparator's *Statistics tab* (see that section for the controls, including *Multiply cost by quantity*, which is on by default).

- Only **✓ matched** lines are counted — the ones that go into the CTR. A grey note says how many unmatched lines are left out. Scaffold has no SAGE cost, so it is not in **Document costs**.
- **Document costs / Document price.** The two buttons at the left of the toolbar (the same as in the MR vs CTR *Statistics* tab, see *Document costs and Document price* there). In **Document price** each line is priced **as its own table row carries it**: quantity × the row's *Rate/Day* or *Unit Price* cell (× the row's *Days* for equipment and scaffold); `NONRECHARG` = 0; a row with a missing quantity, rate or days is not counted (red). Two extra tick boxes appear: **Include scaffold** (off by default; scaffold = tonnage × rate/day × days) and **With markup (6.5%)** — adds the Markup Rate from the box above to **consumables only** (the percentage follows the Markup Rate field). Both defaults can be set in Settings.
- **Price totals** (under the list, always visible): the price of the request **per category** and **per currency** — **AZN:** Manpower, Transport (Third Party Activities) and the **AZN CTR total**; **USD:** Equipment, Consumables and the **USD CTR total**. AZN and USD are **never added together**. The figures are the tables' own *Total* cells (what a generated CTR carries). Transport lines at a quantity of 0 or less count as 0 (they are hidden in the CTR). A row that is missing a required value is left out, as Generate leaves it out, and the box says how many (*“n line(s) not priced”*). Scaffold is added to Equipment only when *Include scaffold* is ticked, and consumables carry the markup only when *With markup* is ticked — the box says which.
- Rechargeability follows how the CTR is generated: a line priced `NONRECHARG` is non-rechargeable, a priced line is rechargeable. This is the working default and is open for discussion with the department.
- It needs SAGE: until the files have been loaded with **Load Files** a yellow warning says so; the figures then appear by themselves and follow every edit you make to the tables.
- A line whose Match By is not in SAGE, or whose quantity is empty, is shown in red and not counted. For a code not in SAGE, double-click its *Unit cost* to type one in.
- **Stock Codes not in SAGE have no SAGE cost.** The box says so in a yellow banner and asks you to type the cost by hand (double-click the red row's *Unit cost*). That cost fills the CTR's internal *SC cost* cell on the Pricing sheet; with none typed in, the cell is left **empty** (never 0), and **Generate** warns you which lines are affected before it goes ahead.
- **Disparities.** When a Stock Code is Rechargeable in the document but Non-Rechargeable in SAGE (or the other way round), a **Disparities** button with a red exclamation mark appears in the toolbar of the list, with the number of Stock Codes. Click it for the list (each line shows the Stock Code and the item's name from SAGE) and press **Choose source…** on a line to say which source is right: **the document** or **SAGE**. Until you choose, the line is not counted (it shows in red) and the **CTR Generator will not generate**. Choosing SAGE makes the line in the Equipment / Consumables table say what SAGE says (non-rechargeable → `NONRECHARG`; rechargeable → the rate/price cell is emptied and you enter one); choosing the document changes nothing. **The change is reversible:** if you clear your choice, or choose the document instead, the cell gets back the text it had before — unless you have edited that cell since (your own edit is never overwritten). Your choices are remembered per Stock Code, are the same in the MR vs CTR *Statistics* tab and in the Generator, and can be reset or restored in Settings. Only rechargeability is compared.
- **Double-click a row to find it.** Double-click a Stock Code in the list and the same line is selected and scrolled to in the Equipment or Consumables table above (a code on several rows selects them all); double-click a document row to bring that table into view (it still opens/closes as well). Double-clicking the *Unit cost* cell types a cost instead, as before; a single click does nothing, so the page never scrolls by accident.
- **Save statistics (Excel)…** (under the box) saves a workbook with *Statistics* (per side), *Statistics Items* (every line, its unit cost and why it was not counted), **Price Statistics** and **Price Items** (the same lines at their price, with Rate, Days and Description) and *Info* (job, SAGE file, cost basis, price basis, lines not counted). A small window shows the folder remembered from last time: **Save here** uses it, **Change folder…** picks another one (the first time it opens the folder chooser straight away). The chosen folder is remembered and shown again before every save.

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

**Estimated CTR Total.** In both CTRs this is a `SUM` of the whole Summary block, **Contingency included**. Contingency is a figure you type into the Summary yourself (0 by default); once you do, the Estimated CTR Total — and the value the CTR Tracker picks up — follows.

**Template check.** Before writing, the app confirms the selected AZN/USD template has the layout it expects. If not, nothing is generated and the message says which cell was wrong. See the first row of the errors table below.

---

## Common errors and fixes

| Error message | What it means | Fix |
|---|---|---|
| *"AZN Pricebook not selected"* | The required AZN Pricebook field is empty | Select the pricebook file and click Load Files |
| *"SAGE Export not selected"* / *"USD Pricebook not selected"* | A required Pricebook-Based field is empty | Select the file and click Load Files |
| *"Select the Combined DB file first"* | You clicked Load CTR Request with no Combined DB | Select the Combined DB file in the Source Files section |
| *"…is missing expected column(s)…"* on the "FROM SAGE" sheet | The SAGE Export doesn't have the expected headers | Check that the header row uses exactly `product`, `long_description`, `unit_code`, `local_expect_cost`, `analysis_b` |
| *"Could not find a sheet named CTR_REQUEST"* | The Combined DB file uses a different sheet name | Open the file in Excel and rename the sheet to `CTR_REQUEST` |
| *"Missing values — rows will not be included"* (red list on Generate) | The request, or a row you added, left a required value empty (shown in red) | Fill in each red cell — **Go to value** takes you there — or press **Generate without them** to leave those rows out of the CTRs. Nothing is ever assumed in place of a missing value |
| *"Job Ref must be a number"* | Non-numeric text was entered in Job Ref | Enter a number only, e.g. `217` |
| *"AZN/USD template layout doesn't match template_config.json (… Is … an outdated template?)"* | The template selected in Source Files is an older (or otherwise different) layout than this version of the app writes into — e.g. an AZN template without the Comments box, whose Summary sits 5 rows higher | Replace it with the current `AZN_TEMPLATE.xlsx` / `USD_TEMPLATE.xlsx` (any custom edits must be carried over) and click Generate again |
| *"AZN Template not found"* | The template file was moved or deleted since it was selected | Browse to the file again |
| *"Cannot save … it may be open in Excel"* | The output file is already open in Excel | Close the file in Excel and click Generate again |
| *PDF export skipped* | LibreOffice (soffice) is not on PATH | Install LibreOffice — the xlsx files are still produced correctly |

---

## Settings

The **⚙ gear button** at the left end of the tab bar (same row as the tabs) opens the **Settings window** — one place for what the app remembers between runs:

- **SAGE Export** — the path used by both the CTR Generator and the MR vs CTR statistics (changing it here changes it there).
- **Default output folder** — where the CTR Generator saves generated CTRs.
- **Statistics save folder** — where *Save statistics (Excel)* saves. It is still shown before every save; **Forget** makes the next save ask for a folder again.
- **Statistics defaults** — which view the statistics start in (**Document costs** or **Document price**), whether **Include scaffold** is ticked (off by default) and whether **With markup** is ticked (on by default). They set where the toggles start; you can still change them in the statistics at any time.
- **Rechargeability choices** — which source is right (**the document** or **SAGE**) for each Stock Code whose rechargeability differs between a document and SAGE. You normally make them with **Choose source…** in the Disparities window; **Edit…** opens the same list as a table, so you can **add** a code to settle it before it ever shows up, **change** a choice, or **remove** one. It is one list for the whole app (the MR vs CTR *Statistics* tab and the CTR Generator share it). *Reset…* empties it (a backup is kept) and *Restore…* brings a backup back.
- **Stock Code types** — which Stock Codes are **Equipment** and which are **Consumables**, used by Document price in the MR vs CTR tab. The list **ships with the app** and is copied to your data folder on first use; that copy is what you edit. **Edit…** opens a searchable table (with each item's description when the SAGE Export is loaded): select lines and set their type, add codes, remove codes — your changes count as your decisions. **Merge shipped…** adds the codes of the list that came with this version that your list does not have yet (your own entries and decisions are kept); **Reset…** replaces your list with the shipped one; **Restore…** brings a backup back. *The shipped list is hidden from the public GitHub repository for privacy purposes (it is the company's Stock Code catalogue); it is bundled into the app when the .exe is built from a folder that contains it.*
- **Match By aliases**, **Description renames** and **CTR presets** — how many are saved. *Open manager* opens the manager window for aliases and renames. **Reset…** asks for confirmation, copies the current file next to the original as `<name>.bak-<date>-<time>.json`, then restores the aliases that ship with the app and empties the renames and the presets (no preset ships with the app). The reset values apply from the next request load; rows already on screen are not changed. To undo a reset, press **Restore…** next to that list: it lists the backups (date and number of entries) and brings the one you choose back; the list as it is then is backed up first, so a restore can be undone too. Only the newest 5 backups of each list are kept. If the folder cannot be written to, the window says so and changes nothing.

---

## Saved Renames

When you fix a **Match By** value to get a ✓ match, the correction is remembered permanently in `ctr_tools/match_aliases.json`. Next time the same CTR Request description appears, the corrected search key is applied automatically.

To view, delete, or export saved renames: click the **Manage Saved Renames…** button in the Manpower section header. You can also use **Import…** and **Export…** to share a renames file between machines.

---

---

# CTR Tracker — User Guide

---

## What the CTR Tracker does

The **CTR Tracker** tab writes a generated CTR's header fields and cost-breakdown totals into the master CTR Tracker workbook (the shared `.xlsm` file used to log every CTR). It reads the fields straight off a generated CTR file instead of you retyping them, lets you build up a batch of up to 10 CTRs and review/correct every field before anything is written, then writes the whole batch in one go.

It does **not** create or reorder rows in the tracker on its normal path — each CTR is written into a row a human has already reserved for it by hand. The only exception is a new revision with no spare row available (or whose spare would break revision order), which inserts a row via Excel automation (see below).

---

## Required files

| File | Purpose | Format |
|---|---|---|
| **CTR Tracker File** | The master tracker workbook — picked once and remembered across runs | `.xlsm` (or `.xlsx`) |
| **CTR File** | A generated CTR output file (AZN or USD template) to read fields from | `.xlsx` |

---

## Step-by-step usage

### 1. Select the tracker workbook

In the **Tracker Workbook** group, browse to the CTR Tracker `.xlsm` file. This is remembered the next time you open the app.

One option lives in this group:

- **Save a timestamped backup copy before writing** — on by default. A row-insert (see the revision rules below) falls back to driving Excel directly via COM automation, which has no verification against real Excel behind it — the backup is the safety net against it corrupting the shared file.
Every write behaves the same way — there is no overwrite option to choose:

- A CTR whose number, currency and revision already exist in the tracker updates that row in place.
- A different revision is written to its own row, in ascending revision order (negative, then blank, then 0 and up). A blank spare row is used only if it keeps that order; otherwise a row is inserted at the right place via Excel automation (requires Excel installed on this machine) and the spare is left blank. The results dialog lists any spare row left blank this way.

### 2. Add a CTR

1. Browse to a **CTR File** (a generated AZN or USD output).
2. Click **Load Fields From File** — this reads Client, CTR Number, Date, Revision, Description, Value, and Currency off the file.
   - If the file has no cached computed total (e.g. it needs to be opened and saved in Excel first), a warning appears and Value must be entered by hand.
3. **Location** auto-selects when the file's site text matches a configured site name, which also fills **Project Code** and the **Tracker Location** (Onshore / Offshore / Georgia) bucket. If it doesn't match, pick a Location by hand.
   - Some clients (e.g. Turan Drilling, CDC) always use the same Project Code regardless of Location — picking such a client fills Project Code from the client instead, and picking a Location afterward won't override it.
4. Every field is editable before adding to the batch.
5. Select a **Job Type** — required, from a fixed list (Core Crew, Projects, FM, FM Process, FM Drilling, Drilling, TAR, TAR & Shutdown).
6. Click **+ Add to Batch**. CTR Number, Location, and Job Type are required; the form then clears for the next CTR (nothing is assumed to carry over safely between CTRs).

### 3. Review the batch

The batch table (up to 10 CTRs) shows Client, CTR Number, Date, Location, Project Code, Job Type, Description, Value, Currency, and Revision for each queued entry. Click the **✕** on a row to remove it from the batch without writing it.

### 4. Write to Tracker

Click **Write Batch to Tracker…**. A confirmation dialog summarizes the backup and overwrite settings before anything is written. The write runs in the background (a raw-XML patch that touches only the target sheet — everything else in the `.xlsm`, including macros, is copied through untouched).

Each CTR is matched to the existing tracker row whose CTR Number column already contains its base number (currency suffix stripped, so one contract's AZN and USD documents share one row reservation). A CTR is **skipped with a warning** instead of guessed at when:
- no row with that CTR number exists and is still empty, or
- the CTR's own Onshore/Offshore labor section doesn't match the selected Location.

Skipped entries stay in the batch table so they're easy to fix and resubmit. Where available, cost-breakdown totals (Labor, Equipment, Consumables, Customs & Transportation, 3rd Party) are filled in from the CTR's own summary section.

After writing, a summary lists what was written (with the row number) and what was skipped (with the reason), any spare rows left blank to keep revision order, plus the backup file's name if one was made.

**Formula cells look empty after writing?** The written formulas (USD equivalent, labor, totals) carry no stored result. Since version 1.0.21 the tracker is saved with *“recalculate on open”* switched on, so Excel recalculates every formula when you open it (Excel may then ask whether to save the file when you close it — that is the recalculation, not a problem). If a cell still looks stale, in Excel press **Ctrl+Alt+Shift+F9** — it recalculates every formula in the workbook — instead of refreshing each cell with F2 + Enter. Plain F9 won't work, since it only recalculates cells Excel already thinks are out of date.

### In-batch conflicts (two revisions of the same CTR)

If a batch contains two entries for the same CTR number + currency (e.g. a CTR and a later revision of it, submitted together), only the first can be placed correctly in one pass — the second is skipped with a matchable reason, stays in the batch, and the app automatically counts down 3 seconds and retries it in a follow-up write (cancellable). By the second round the first entry is genuinely on disk and the conflict resolves correctly. A batch with more than two revisions of the same CTR + currency may need several such rounds.

---

## Common errors and fixes

| Message | What it means | Fix |
|---|---|---|
| *"Choose the CTR Tracker workbook first."* | No tracker file selected | Browse to the tracker `.xlsm` in the Tracker Workbook group |
| *"Choose a CTR file first."* | Load Fields clicked with no CTR File selected | Browse to a generated CTR output file |
| *"Enter or load a CTR number first."* | CTR Number is blank when adding to the batch | Type it or use Load Fields From File |
| *"Select a Location for this CTR."* | Location dropdown left on the placeholder | Pick a Location, or check that the file's site text matches a configured one |
| *"Select a Job Type for this CTR."* | Job Type dropdown left on the placeholder | Pick one of the fixed Job Type options |
| CTR skipped after writing, "no matching row" | No tracker row has that CTR number yet | Pre-create the row in the tracker by hand, then retry |
| CTR skipped after writing, Location mismatch | The CTR's own Onshore/Offshore section doesn't match the selected Location | Confirm the correct Location for this CTR and resubmit |
| *"Cannot save … it is locked, most likely open in Excel or being synced"* | The tracker file could not be replaced at the final save, so **no CTR data was written** | Close the tracker in Excel (and any other program or sync using it), then write the batch again. If the message adds that blank rows were already inserted, those spare rows stay in the tracker and are reused next time |
| *"Excel stopped responding or was closed while the row was being inserted"* | Excel went away while the app was inserting a revision row; that entry was skipped | Close any open Excel windows and write the batch again |
| Row insert requires Excel | A new revision needed a row inserted (no spare, or the spare would break revision order) | Run this on a machine with a licensed Excel install, or pre-create a spare row by hand instead |
| Formula cells (e.g. USD equivalent, totals) empty after writing, formula visible in the cell | The writer stores formulas without a calculated result and Excel hasn't recalculated them yet | In Excel press **Ctrl+Alt+Shift+F9** to recalculate the whole workbook |

---

# Inspector (F12)

Press **F12** anywhere to open the **Inspector** — a read-only panel that shows what the app is doing. It never changes your data. It opens docked on the right; drag its title to move it to another side. Press **Float** to turn it into its own window — an ordinary window with a normal title bar you can drag, edges you can resize, and minimise/maximise buttons — and **Dock** to put it back. Press F12 again to hide it. The app remembers whether it was docked or floating, and the window's size and position, but it always starts closed.

| Tab | What it shows |
|---|---|
| **Overview** | Version, memory, CPU, running jobs, log folder, and the **last error with its date** and how long ago it happened. |
| **Logs** | The log files (`app.log`, `crash.log`, older rotations). Filter by **Level**, **Search**, and tick **Follow** to keep scrolling to the newest line as the log grows (untick it to stay where you are). **Open folder** / **Copy path** find the file. |
| **Data** | Everything currently held in memory — pricebooks, SAGE, request lines, MR/CTR tables — with rows, size and source path. Sources that aren't loaded show the path they are connected to. Select a loaded table to preview its first rows. |
| **Storage** | Every file and folder the app is connected to, as a **path only** (nothing is opened): whether it exists, size, date, and whether the app itself holds its contents. |
| **Threads** | What every thread is doing right now (its call stack). **Copy all stacks** is handy for a bug report. |
| **Variables** | A read-only list of the app's variables with a search box. |
| **Tasks** | The background work in order, with timings. **Stop after current task** lets the running task finish and doesn't start the next ones. |
| **Charts** | Memory and CPU over time (tick **Record usage** to start sampling), time per task, memory per loaded table, space per folder. |

**Snapshot.** The **Snapshot** button (next to Float) saves everything the tabs show — as of that moment — into one text file: overview and last error, the recent log and `crash.log`, what is loaded in memory (rows, size, source path and column names, but never table contents), every connected path, every thread's call stack, the variables list, the tasks with timings, and the chart data. The file goes into a `snapshots` folder inside the log folder (`snapshot_YYYYMMDD_HHMMSS.txt`; the 30 newest are kept) and a message shows the path with **Open folder** and **Copy path**. To keep it safe to pass around, the user name in paths is replaced with `<user>` and variables named like value / rate / price / cost / total / markup / contingency / amount are shown as `<hidden>`. It still contains client names, contract numbers, job references and file names, so send it to people inside the company only. Send it with a problem report: it shows what the app was doing when the problem happened.

**Stopping work.** Generate and file parsing also show a **Stop after current step** button in their progress window. A stop never interrupts a task that has already started — a spreadsheet being written is always finished — so the files saved so far are complete, and the result message lists them. A tracker write is a single task and can't be stopped.

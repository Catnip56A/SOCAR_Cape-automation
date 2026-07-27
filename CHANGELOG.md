# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and version numbers come from the [VERSION](VERSION) file.

## [1.0.6] - 2026-07-27
### Changed
- CTR Tracker: raised the row-search ceiling for locating a CTR's summary totals (Estimated CTR Total, section subtotals, Onshore/Offshore activity label) from 400 to 800 rows. The search already follows the summary section wherever it lands rather than assuming a fixed cell, but a CTR with enough line items to push that section past row 400 would have silently returned "not found" instead of the real total. `search_max_row` in `ctr_extract` (`template_config.json`).

## [1.0.5] - 2026-07-24
### Fixed
- MR vs CTR Comparator: on sheets parsed via the fallback (keyword-based) reader — i.e. sheets without `cons_start`/`cons_end` named ranges — a blank Stock Code no longer slips past the placeholder filter. `_filter_mr_stock_codes` relied on `Series.astype(str)` to stringify `NaN`/blank cells before checking them against the placeholder list, but that conversion doesn't happen for `NaN` in the pandas version this app uses, so blank-Stock-Code rows were silently kept. In practice this let footer/signature rows (e.g. "Checked By Onshore/Offshore Coordinator", "Manager Approval", "Commercial Rep Approval" — text spilled into other columns from merged cells below the item table) appear in the comparison as if they were real line items. Now uses the existing NaN-safe `_norm()` helper instead.

## [1.0.4] - 2026-07-20
### Added
- New "CTR Tracker" tab: reads client, CTR number, date, revision, description, value and currency straight off up to 10 generated CTR files. Each CTR is written into the existing tracker row whose CTR number column already contains its base number (currency suffix stripped, e.g. both the AZN and USD document for "CTR-26-217" target a row containing "CTR-26-217") — that row is pre-created by hand; a CTR with no matching row still empty in Date/Description/Value/Currency/Revision is skipped with a warning rather than the tool guessing a placement or inserting/shifting rows. Client, Location and Project Code may already be filled in on that row (a human often sets those by hand when creating it) — a match doesn't require them to be blank, and whatever's already there is preserved rather than overwritten. An "Overwrite rows that already have data" checkbox (off by default, not remembered across restarts) allows replacing an already-filled match instead of skipping it. The Location dropdown auto-selects from the file's site text when it matches a configured site (falling back to manual pick otherwise), which also fills Project Code and the tracker's Onshore/Offshore/Georgia bucket — both stay editable. Site list lives in `ctr_tracker_locations` in `template_config.json`. The value column's number format switches to match the entry's currency (₼ for AZN, $ for USD) instead of inheriting whatever the row happened to have, and the USD-equivalent column is always rewritten with `=IF(AC{row}="USD", S{row}, IF(AC{row}="AZN", S{row}/1.7, ""))` using that row's own number. An optional "Save a timestamped backup copy before writing" checkbox (off by default) makes a dated copy of the tracker workbook next to it before writing. The write runs on a background thread with a progress dialog. Cell/column layout is configurable via the new `ctr_extract` and `ctr_tracker` sections of `template_config.json`.
- Writing to the tracker patches only the target sheet's raw XML and copies every other part of the `.xlsm` byte-for-byte (`ctr_generator/tracker_fast.py`), instead of round-tripping the whole workbook through openpyxl — cuts a write from ~11s to ~3s on the real tracker file, and as a side effect stops openpyxl from silently dropping the sheet's data-validation dropdown lists, drawings, print settings, and calc chain on every save.
- The CTR Tracker write now also fills in cost-breakdown columns (Labor, Equipment, 3rd Party Activities, Customs & Transportation, Recharg. Consumables, Consumables markup, and the row's USD total) from whatever totals the CTR's own summary section has — not every column applies to every CTR, so one with no matching line in the file is simply left blank. Labor and Consumables markup/Total follow the same formula conventions already used throughout the tracker's real historical rows (an AZN CTR's labor total is embedded as a computed literal divided by the AZN→USD rate; markup and the row total are formulas over the tracker's own row). A CTR is skipped with a warning — nothing written — if its own Onshore/Offshore labor section doesn't match the Location selected for it, rather than writing possibly-mismatched figures. All these labels are found by searching the CTR file for their text rather than assuming a fixed row, same as the existing CTR-total lookup, since the summary section's row shifts with how many line items a CTR has. New `azn_to_usd_rate`, `consumables_markup_rate`, and several `label_*`/`col_*` keys in the `ctr_extract`/`ctr_tracker` sections of `template_config.json`.

## [1.0.3] - 2026-07-14
### Changed
- Equipment now prices off a dedicated USD Pricebook file, matched by description (preferring the "-NOR" rate over "-STBY" when both exist), instead of being looked up in SAGE. The Equipment Names DB bridge sheet no longer carries its own rate column — it only resolves a request stock code to the description used for the USD Pricebook lookup.
- "Pricebook-Based" source files split into three separate pickers — AZN Pricebook, SAGE Export, and USD Pricebook — instead of bundling SAGE and equipment rates into the Combined DB file. Combined DB is now optional and only supplies the Equipment Names DB bridge and CTR Request sheets.
- Consumable rechargeability is now read from SAGE's own `analysis_b` tag (`NONRECHARG` / `NONRECHAR`, configurable) instead of the Equipment Names DB bridge; non-rechargeable consumables display "NONRECHARG" instead of a misleading 0.00.
- SAGE export sheet name defaults to "FROM SAGE" (was "SAGE").

### Fixed
- Non-rechargeable consumable rows in the USD template no longer produce an Excel `#VALUE!` error — the total column is now guarded so a "NONRECHARG" price totals 0.

## [1.0.2] - 2026-07-07
### Changed
- Fixed issue with AZN pricebook UOMin order to calculate using working hours rather than working days

## [1.0.1] - 2026-07-07
### Changed
- Consumable and equipment costs moved to their own line items on the pricing page.
- Version is now single-sourced from the root `VERSION` file, read by both the
  app and the Inno Setup installer, so the two can no longer drift apart.

## [1.0.0] - 2026-07-06
- Initial release of the CTR generator desktop app.

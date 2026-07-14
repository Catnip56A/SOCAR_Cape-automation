# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and version numbers come from the [VERSION](VERSION) file.

## [Unreleased]

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

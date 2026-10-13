# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-12

First release.

### Added

- Config flow: one confirm step and a single instance. Options cover the digest time, what
  triggers a notification, notify entities, legacy notify services, persistent notification,
  daily overdue reminders, auto-archive and purge periods, and the fallback tracking site.
- Package storage in Home Assistant's `.storage`. Each package has a description, vendor,
  notes, an expected date or delivery window, and an optional custom tracking link.
- Carrier detection from the tracking number's format, with check-digit validation for UPS,
  USPS, FedEx, DHL Express, OnTrac and UPU S10 numbers. Carrier links are parsed too.
- Deep links to carrier tracking pages, with a homepage, copy button and 17TRACK or
  Parcels fallback when a carrier has no link.
- Extraction of tracking numbers from pasted text and shipping e-mails, with filters for
  order ids, phone numbers, ZIP+4 codes, IMEIs, gift cards and marketing links.
- Services: `add_package`, `update_package`, `mark_arrived`, `archive_package`,
  `restore_package`, `remove_package` and `extract_tracking_numbers`.
- Events: `package_tracker_added`, `_due_today`, `_overdue`, `_arrived`, `_archived` and
  `_removed`, with logbook descriptions.
- A daily digest of packages due today and overdue, sent to notify entities, legacy notify
  services and a persistent notification. An optional notification when a package is
  marked as arrived.
- Sensors for active, overdue and due-today counts and the next delivery date, plus a
  "Deliveries" calendar.
- Arrived packages are archived automatically, and history can be purged.
- A bundled Lovelace card that loads automatically. It has the package table with overdue
  and due-today highlighting, links, copy, an arrived checkbox, an add form with live
  carrier detection, a paste dialog, inline editing, history, a visual editor, and
  mobile and dark-mode layouts.
- Diagnostics with tracking numbers, notes, links and user ids redacted.
- Brand icons.

[Unreleased]: https://github.com/your-github-user/HAPackageTracker/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/your-github-user/HAPackageTracker/releases/tag/v0.1.0

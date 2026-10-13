// Unit tests for the card's pure helpers. Run with: node --test tests/js
//
// The card module itself imports lit (needs a browser), so all testable logic lives in
// card-helpers.js, which has no DOM dependency at import time.

// A negative UTC offset: naive `new Date("2026-10-12")` formatting would show Oct 11 here.
process.env.TZ = "America/Los_Angeles";

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const WWW = new URL("../../custom_components/package_tracker/www/", import.meta.url);
const h = await import(new URL("card-helpers.js", WWW));

test("VERSION matches manifest.json version", async () => {
  const manifest = JSON.parse(
    await readFile(new URL("../manifest.json", WWW), "utf8"),
  );
  assert.equal(h.VERSION, manifest.version);
});

test("card module imports helpers before lit and re-exports VERSION", async () => {
  const src = await readFile(new URL("package-tracker-card.js", WWW), "utf8");
  const helpersAt = src.indexOf('from "./card-helpers.js"');
  const litAt = src.indexOf('from "./vendor/lit-all.min.js"');
  assert.ok(helpersAt > 0 && litAt > helpersAt, "card-helpers.js must be imported before lit");
  assert.match(src, /export \{[^}]*VERSION[^}]*\}/);
  assert.match(src, /if \(!customElements\.get\(CARD_TAG\)\)/);
});

test("formatDate does not shift calendar dates in a negative-offset zone", () => {
  assert.equal(new Date().getTimezoneOffset() > 0, true, "test needs a negative UTC offset");
  const out = h.formatDate("2026-10-12", "en-US", { today: "2026-10-01" });
  assert.match(out, /Oct 12/);
  assert.doesNotMatch(out, /Oct 11/);
  assert.doesNotMatch(out, /2026/, "same year as today: year omitted");
  assert.match(h.formatDate("2027-01-01", "en-US", { today: "2026-12-31" }), /Jan 1, 2027/);
  // Year boundary: Jan 1 must not become Dec 31.
  assert.doesNotMatch(h.formatDate("2027-01-01", "en-US"), /Dec/);
  assert.match(h.formatDate("2026-10-12", "de-DE", { today: "2026-10-12" }), /12\./);
});

test("formatDate/parseISODate reject invalid input", () => {
  assert.equal(h.formatDate(null), "");
  assert.equal(h.formatDate("2026-02-30"), "");
  assert.equal(h.formatDate("12/10/2026"), "");
  assert.equal(h.parseISODate("2026-10-12"), Date.UTC(2026, 9, 12));
});

test("formatRange collapses equal/missing ends and formats windows", () => {
  const today = "2026-10-12";
  assert.equal(h.formatRange("2026-10-14", null, "en-US", { today }), h.formatDate("2026-10-14", "en-US", { today }));
  assert.equal(h.formatRange("2026-10-14", "2026-10-14", "en-US", { today }), h.formatDate("2026-10-14", "en-US", { today }));
  const range = h.formatRange("2026-10-14", "2026-10-16", "en-US", { today });
  assert.match(range, /14/);
  assert.match(range, /16/);
  assert.doesNotMatch(range, /13|15/);
});

test("formatInstant uses the given server time zone", () => {
  // 03:00 UTC on Oct 13 is still Oct 12 in Chicago.
  assert.match(h.formatInstant("2026-10-13T03:00:00+00:00", "en-US", "America/Chicago"), /Oct 12/);
  assert.match(h.formatInstant("2026-10-13T03:00:00+00:00", "en-US", "UTC"), /Oct 13/);
  assert.equal(h.formatInstant("nope"), "");
});

test("relativeLabel for -3/-1/0/1/2 days", () => {
  assert.equal(h.relativeLabel(-3, "en"), "3 days late");
  assert.equal(h.relativeLabel(-1, "en"), "1 day late");
  assert.equal(h.relativeLabel(0, "en"), "today");
  assert.equal(h.relativeLabel(1, "en"), "tomorrow");
  assert.equal(h.relativeLabel(2, "en"), "in 2 days");
  assert.equal(h.relativeLabel(null, "en"), "");
});

test("expectedInfo measures against the server's today, not the browser", () => {
  const today = "2026-10-12";
  const overdue = { status: "overdue", expected_date: "2026-10-05", expected_date_end: "2026-10-09" };
  assert.equal(h.expectedInfo(overdue, today, "en").relative, "3 days late");
  const transit = { status: "in_transit", expected_date: "2026-10-14", expected_date_end: null };
  assert.equal(h.expectedInfo(transit, today, "en").relative, "in 2 days");
  assert.equal(h.expectedInfo({ status: "due_today", expected_date: today }, today, "en").relative, "today");
  assert.deepEqual(h.expectedInfo({ status: "no_date", expected_date: null }, today, "en"), {
    date: "",
    relative: "",
  });
});

test("sortPackages: overdue, due today, by expected date, no date, arrived", () => {
  const pkgs = [
    { id: "a", status: "arrived", delivered_at: "2026-10-10T10:00:00+00:00", created_at: "1" },
    { id: "b", status: "no_date", expected_date: null, created_at: "2" },
    { id: "c", status: "in_transit", expected_date: "2026-10-20", created_at: "3" },
    { id: "d", status: "in_transit", expected_date: "2026-10-14", created_at: "4" },
    { id: "e", status: "due_today", expected_date: "2026-10-12", created_at: "5" },
    { id: "f", status: "overdue", expected_date: "2026-10-09", created_at: "6" },
    { id: "g", status: "overdue", expected_date: "2026-10-01", created_at: "7" },
    { id: "h", status: "arrived", delivered_at: "2026-10-11T10:00:00+00:00", created_at: "8" },
  ];
  assert.deepEqual(
    h.sortPackages(pkgs).map((p) => p.id),
    ["g", "f", "e", "d", "c", "b", "h", "a"],
  );
  assert.equal(pkgs[0].id, "a", "input array is not mutated");
  assert.deepEqual(
    h.visiblePackages(pkgs, { hideArrived: true }).map((p) => p.id),
    ["g", "f", "e", "d", "c", "b"],
  );
});

test("copyStrategy branches", () => {
  assert.equal(h.copyStrategy({ isSecureContext: true, hasClipboard: true, hasExecCommand: true }), "clipboard");
  // Plain-HTTP Home Assistant: navigator.clipboard is unavailable/insecure.
  assert.equal(h.copyStrategy({ isSecureContext: false, hasClipboard: true, hasExecCommand: true }), "execCommand");
  assert.equal(h.copyStrategy({ isSecureContext: true, hasClipboard: false, hasExecCommand: true }), "execCommand");
  assert.equal(h.copyStrategy({ isSecureContext: false, hasClipboard: false, hasExecCommand: false }), "manual");
  assert.equal(h.copyStrategy(), "manual");
});

test("error mapping: translation_key, code, stripped message", () => {
  assert.equal(h.stripValidationPrefix("Validation error: bad date"), "bad date");
  assert.equal(h.stripValidationPrefix("validation error:   x "), "x");
  assert.equal(h.stripValidationPrefix(undefined), "");
  const dup = { code: "service_validation_error", translation_key: "duplicate_package", message: "x" };
  assert.equal(h.errorKey(dup), "duplicate_package");
  assert.equal(h.errorMessage(dup), h.STRINGS.error_duplicate_package);
  assert.equal(h.errorField("invalid_date_range"), "expected_date_end");
  assert.equal(h.errorField("in_history"), "tracking_number");
  assert.equal(h.errorKey({ code: "unknown_command", message: "Unknown command." }), "unknown_command");
  assert.equal(h.errorMessage({ code: "invalid_format", message: "Validation error: expected a date" }), "expected a date");
  assert.equal(h.errorMessage({}), h.STRINGS.error_generic);
});

test("t() interpolates and resolveLocale handles 'system'", () => {
  assert.equal(h.t("added", { tn: "1Z1" }), "Added 1Z1");
  assert.equal(h.t("missing_key"), "missing_key");
  assert.equal(h.resolveLocale("system", "fr-FR"), "fr-FR");
  assert.equal(h.resolveLocale(undefined, undefined), "en");
  assert.equal(h.resolveLocale("de", "fr-FR"), "de");
  assert.equal(h.resolveLocale("not a locale!!", "fr"), "en");
});

test("normalizeConfig validates and fills defaults", () => {
  assert.deepEqual(h.normalizeConfig({ type: "custom:package-tracker-card" }), {
    type: "custom:package-tracker-card",
    ...h.CONFIG_DEFAULTS,
  });
  assert.equal(h.normalizeConfig({ hide_arrived: true }).hide_arrived, true);
  assert.throws(() => h.normalizeConfig(null), /Invalid configuration/);
  assert.throws(() => h.normalizeConfig({ show_history: "yes" }), /show_history/);
  assert.throws(() => h.normalizeConfig({ title: 5 }), /title/);
});

test("buildAddData / buildUpdateData", () => {
  assert.deepEqual(
    h.buildAddData({ tracking_number: " 1Z999AA10123456784 ", carrier: "auto", description: "Shoes", vendor: "", expected_date: "2026-10-14" }),
    { tracking_number: "1Z999AA10123456784", description: "Shoes", expected_date: "2026-10-14" },
  );
  assert.equal(h.buildAddData({ tracking_number: "X", carrier: "ups" }).carrier, "ups");
  const pkg = {
    description: "Shoes", vendor: null, notes: null, expected_date: "2026-10-14",
    expected_date_end: null, tracking_url_override: null, carrier: "ups", carrier_source: "auto",
  };
  assert.equal(h.buildUpdateData(h.editDraft(pkg), pkg), null);
  const draft = { ...h.editDraft(pkg), description: "", vendor: "Acme", carrier: "fedex" };
  assert.deepEqual(h.buildUpdateData(draft, pkg), { description: null, vendor: "Acme", carrier: "fedex" });
});

test("detectionSummary, carrierOptions and paste preselection", () => {
  const det = {
    candidates: [
      { carrier: "usps", carrier_name: "USPS", checksum_valid: true, service: "IMpb" },
      { carrier: "fedex", carrier_name: "FedEx", checksum_valid: null, service: null },
    ],
    duplicate_of: null,
    in_history: "01HIST",
  };
  const s = h.detectionSummary(det);
  assert.equal(s.state, "in_history");
  assert.equal(s.packageId, "01HIST");
  assert.equal(s.checksum, "ok");
  assert.equal(h.detectionSummary({ candidates: [], duplicate_of: null, in_history: null }).state, "unknown");
  const opts = h.carrierOptions(det.candidates, [
    { id: "ups", name: "UPS" }, { id: "usps", name: "USPS" }, { id: "fedex", name: "FedEx" },
  ]);
  assert.deepEqual(opts.detected.map((c) => c.id), ["usps", "fedex"]);
  assert.deepEqual(opts.all.map((c) => c.id), ["ups"]);
  assert.equal(h.shouldPreselect({ already_tracked: false, confidence: 0.8 }), true);
  assert.equal(h.shouldPreselect({ already_tracked: false, confidence: 0.79 }), false);
  assert.equal(h.shouldPreselect({ already_tracked: true, confidence: 1 }), false);
});

test("countsSummary", () => {
  assert.equal(h.countsSummary({ active: 5, overdue: 1, due_today: 2, arrived: 1 }), "1 overdue · 2 due today · 5 active");
  assert.equal(h.countsSummary({ active: 0, overdue: 0, due_today: 0 }), "0 active");
});

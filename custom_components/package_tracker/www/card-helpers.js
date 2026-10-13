/**
 * Pure helpers for the Package Tracker card.
 *
 * Everything here is free of DOM and lit dependencies at import time so that
 * `node --test tests/js` can import this module directly. The card module
 * (package-tracker-card.js) imports lit, which needs a browser, so all logic
 * worth testing lives here instead.
 *
 * Dates: the backend sends calendar dates as "YYYY-MM-DD" and a server-side
 * "today". Calendar dates are turned into Date.UTC() instants and formatted with
 * timeZone "UTC", so a browser in a negative-offset zone never shows the day
 * before. Relative labels are measured against the server's today, never the
 * browser clock.
 */

// Must equal manifest.json "version" (asserted by tests/js and the release workflow).
export const VERSION = "0.1.0";

// The lit-all bundle prints a "loaded from a bundle" warning unless this flag is
// set. Shipping the prebuilt bundle is deliberate (no build step), so silence it.
// The card imports this module before lit, so the flag is set in time.
if (typeof globalThis !== "undefined" && globalThis.litDisableBundleWarning === undefined) {
  globalThis.litDisableBundleWarning = true;
}

export const DOMAIN = "package_tracker";
const DAY_MS = 86_400_000;

/* ------------------------------------------------------------------ strings */

export const STRINGS = {
  default_title: "Packages",
  loading: "Loading packages…",
  empty: "No packages yet. Add a tracking number to get started.",
  empty_hidden: "No active packages.",
  not_loaded_title: "Package Tracker is not loaded",
  not_loaded_body:
    "Add or enable the Package Tracker integration under Settings → Devices & services.",
  connect_error: "Couldn't load packages: {message}",
  retry: "Retry",

  // header / toolbar
  count_active: "{n} active",
  count_overdue: "{n} overdue",
  count_due_today: "{n} due today",
  count_arrived: "{n} arrived",
  paste: "Paste text",
  history: "History",
  hide_history: "Hide history",

  // table
  col_arrived: "Arrived",
  col_package: "Package",
  col_carrier: "Carrier",
  col_tracking: "Tracking #",
  col_expected: "Expected",
  col_actions: "Actions",
  mark_arrived: "Mark {name} as arrived",
  unmark_arrived: "Mark {name} as not arrived",
  notes: "Notes",
  no_description: "Untitled package",
  no_date: "No date",
  badge_overdue: "Overdue",
  badge_due_today: "Due today",
  badge_arrived: "Arrived",
  arrived_on: "Arrived {date}",
  open_tracking: "Open tracking page for {tn} (new tab)",
  carrier_site: "Carrier site",
  copy: "Copy tracking number {tn}",
  copied: "Copied {tn}",
  copy_manual: "Press Ctrl+C (or ⌘C) to copy",
  close: "Close",
  edit: "Edit {name}",
  archive: "Archive {name}",
  delete: "Delete {name}",
  confirm_delete: "Confirm delete",
  confirm_delete_label: "Really delete {name}? This cannot be undone.",
  cancel: "Cancel",
  save: "Save",
  archived: "Archived {name}",
  deleted: "Deleted {name}",
  restored: "Restored {name}",
  saved: "Saved {name}",
  status_changed: "Updated {name}",

  // add form
  add_title: "Add package",
  tracking_number: "Tracking number",
  tracking_placeholder: "Paste or type a tracking number",
  carrier: "Carrier",
  carrier_auto: "Auto ({name})",
  carrier_auto_pending: "Auto-detect",
  carrier_detected: "Detected",
  carrier_all: "All carriers",
  description: "Description",
  vendor: "Vendor",
  expected_date: "Expected",
  expected_date_end: "Latest (optional)",
  tracking_url: "Tracking URL override",
  add: "Add",
  adding: "Adding…",
  added: "Added {tn}",
  detecting: "Detecting carrier…",
  detect_unknown: "Unknown format: will be saved with no carrier link",
  checksum_ok: "checksum OK",
  checksum_bad: "possible typo",
  duplicate: "Already tracked",
  in_history: "In history",
  restore: "Restore",
  restore_label: "Restore {name}",

  // paste dialog
  paste_title: "Find tracking numbers",
  paste_hint: "Paste an email or any text. It is scanned on your Home Assistant server and not stored.",
  paste_text: "Text to scan",
  scan: "Find numbers",
  scanning: "Scanning…",
  paste_none: "No tracking numbers found.",
  paste_found: "{n} found",
  paste_already: "Already tracked",
  paste_confidence: "{pct}% confidence",
  paste_shared: "Applied to every added package",
  add_selected: "Add selected ({n})",
  back: "Back",
  paste_added: "Added",
  paste_select: "Add {tn}",

  // history
  history_title: "History",
  history_empty: "No archived packages.",
  history_loading: "Loading history…",
  archived_on: "Archived {date}",

  // errors (keys match the backend translation keys)
  error_not_loaded: "Package Tracker is not loaded.",
  error_package_not_found: "That package no longer exists.",
  error_duplicate_package: "This tracking number is already being tracked.",
  error_in_history: "This tracking number is in history. Restore it instead.",
  error_invalid_date_range: "The latest date must be on or after the expected date.",
  error_invalid_tracking_number: "Enter a tracking number.",
  error_unknown_command: "Package Tracker is not loaded.",
  error_generic: "Something went wrong.",

  // editor
  cfg_title: "Title",
  cfg_show_add_form: "Show add form",
  cfg_show_history: "Show history button",
  cfg_hide_arrived: "Hide arrived packages",
};

/**
 * Look up a UI string and interpolate {placeholders}.
 * When vars.n is a number and a `${key}_one` variant exists, it is used for n === 1.
 */
export function t(key, vars = {}) {
  let template = STRINGS[key];
  if (typeof vars.n === "number" && vars.n === 1 && STRINGS[`${key}_one`] !== undefined) {
    template = STRINGS[`${key}_one`];
  }
  if (template === undefined) return key;
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    vars[name] === undefined || vars[name] === null ? match : String(vars[name]),
  );
}

/* -------------------------------------------------------------------- dates */

/** Parse "YYYY-MM-DD" into a UTC-midnight timestamp, or null for anything else. */
export function parseISODate(iso) {
  if (typeof iso !== "string") return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const ms = Date.UTC(y, mo - 1, d);
  const check = new Date(ms);
  // Reject roll-overs such as 2026-02-30.
  if (check.getUTCFullYear() !== y || check.getUTCMonth() !== mo - 1 || check.getUTCDate() !== d) {
    return null;
  }
  return ms;
}

/** Whole days from `fromIso` to `toIso` (positive when `toIso` is later). */
export function daysBetween(fromIso, toIso) {
  const a = parseISODate(fromIso);
  const b = parseISODate(toIso);
  if (a === null || b === null) return null;
  return Math.round((b - a) / DAY_MS);
}

/**
 * Pick the UI locale: HA's language setting, where "system" (or nothing) means
 * the browser language. Invalid tags fall back to "en".
 */
export function resolveLocale(language, navigatorLanguage) {
  const tag = !language || language === "system" ? navigatorLanguage || "en" : language;
  try {
    return Intl.DateTimeFormat.supportedLocalesOf([tag]).length ? tag : "en";
  } catch {
    return "en";
  }
}

const dtfCache = new Map();
function dtf(locale, options) {
  const key = `${locale}|${JSON.stringify(options)}`;
  let fmt = dtfCache.get(key);
  if (!fmt) {
    try {
      fmt = new Intl.DateTimeFormat(locale, options);
    } catch {
      fmt = new Intl.DateTimeFormat("en", options);
    }
    dtfCache.set(key, fmt);
  }
  return fmt;
}

function dateOptions(ms, today) {
  const opts = { timeZone: "UTC", weekday: "short", month: "short", day: "numeric" };
  const todayMs = parseISODate(today);
  if (todayMs === null || new Date(todayMs).getUTCFullYear() !== new Date(ms).getUTCFullYear()) {
    opts.year = "numeric";
  }
  return opts;
}

/**
 * Format a calendar date ("YYYY-MM-DD") for display. The year is shown only when
 * it differs from `today`'s year. Returns "" for invalid input.
 */
export function formatDate(iso, locale = "en", { today } = {}) {
  const ms = parseISODate(iso);
  if (ms === null) return "";
  return dtf(locale, dateOptions(ms, today)).format(new Date(ms));
}

/** Format a delivery window; a missing or equal end collapses to a single date. */
export function formatRange(start, end, locale = "en", { today } = {}) {
  const a = parseISODate(start);
  if (a === null) return "";
  const b = parseISODate(end);
  if (b === null || b === a) return formatDate(start, locale, { today });
  const fmt = dtf(locale, dateOptions(Math.max(a, b), today));
  if (typeof fmt.formatRange === "function" && b > a) {
    return fmt.formatRange(new Date(a), new Date(b));
  }
  return `${fmt.format(new Date(a))} – ${fmt.format(new Date(b))}`;
}

/**
 * Format an instant (ISO datetime, e.g. delivered_at) as a short day in the given
 * IANA zone (normally the HA server's zone). Returns "" for invalid input.
 */
export function formatInstant(iso, locale = "en", timeZone = undefined) {
  if (typeof iso !== "string" || !iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const opts = { month: "short", day: "numeric", year: "numeric" };
  if (timeZone) opts.timeZone = timeZone;
  try {
    return dtf(locale, opts).format(date);
  } catch {
    return dtf(locale, { month: "short", day: "numeric", year: "numeric" }).format(date);
  }
}

const rtfCache = new Map();
function rtf(locale) {
  let fmt = rtfCache.get(locale);
  if (!fmt) {
    try {
      fmt = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
    } catch {
      fmt = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
    }
    rtfCache.set(locale, fmt);
  }
  return fmt;
}

/**
 * Relative label for a day offset from the server's today. Late packages get a
 * custom "N days late" (more useful than "3 days ago"); everything else uses
 * Intl.RelativeTimeFormat with numeric "auto" ("today", "tomorrow", "in 2 days").
 */
export function relativeLabel(days, locale = "en") {
  if (typeof days !== "number" || !Number.isFinite(days)) return "";
  if (days < 0) {
    const n = Math.abs(days);
    return n === 1 ? "1 day late" : `${n} days late`;
  }
  return rtf(locale).format(days, "day");
}

/**
 * Date text and relative label for a package's Expected cell, using the
 * server-computed status so browser and server never disagree about "today".
 */
export function expectedInfo(pkg, today, locale = "en") {
  const date = pkg.expected_date
    ? formatRange(pkg.expected_date, pkg.expected_date_end, locale, { today })
    : "";
  let relative = "";
  switch (pkg.status) {
    case "overdue": {
      const late = daysBetween(pkg.expected_date_end || pkg.expected_date, today);
      relative = late === null ? "" : relativeLabel(-Math.max(1, late), locale);
      break;
    }
    case "due_today":
      relative = relativeLabel(0, locale);
      break;
    case "in_transit": {
      const until = daysBetween(today, pkg.expected_date);
      relative = until === null ? "" : relativeLabel(until, locale);
      break;
    }
    default:
      relative = "";
  }
  return { date, relative };
}

/* --------------------------------------------------------------------- sort */

const STATUS_RANK = {
  overdue: 0,
  due_today: 1,
  in_transit: 2,
  no_date: 3,
  arrived: 4,
  archived: 5,
};

function cmp(a, b) {
  if (a === b) return 0;
  if (a === null || a === undefined) return 1;
  if (b === null || b === undefined) return -1;
  return a < b ? -1 : 1;
}

/**
 * Order: overdue, due today, in transit (all by expected date), no date, arrived
 * (most recently delivered first). Ties fall back to creation time then id so the
 * order is stable across snapshots. Returns a new array.
 */
export function sortPackages(packages) {
  return [...(packages || [])].sort((a, b) => {
    const ra = STATUS_RANK[a.status] ?? 9;
    const rb = STATUS_RANK[b.status] ?? 9;
    if (ra !== rb) return ra - rb;
    let c = 0;
    if (a.status === "arrived" || a.status === "archived") {
      const ka = a.status === "arrived" ? a.delivered_at : a.archived_at;
      const kb = b.status === "arrived" ? b.delivered_at : b.archived_at;
      c = -cmp(ka ?? "", kb ?? "");
    } else {
      c =
        cmp(a.expected_date, b.expected_date) ||
        cmp(a.expected_date_end || a.expected_date, b.expected_date_end || b.expected_date);
    }
    return c || cmp(a.created_at, b.created_at) || cmp(a.id, b.id);
  });
}

/* --------------------------------------------------------------------- copy */

/**
 * Decide how to copy. Pure so it can be tested:
 *   "clipboard"   - async Clipboard API (secure contexts only)
 *   "execCommand" - hidden textarea + document.execCommand("copy") (plain-HTTP HA)
 *   "manual"      - show a selected read-only input for the user to copy
 */
export function copyStrategy(env = {}) {
  if (env.isSecureContext && env.hasClipboard) return "clipboard";
  if (env.hasExecCommand) return "execCommand";
  return "manual";
}

/* ------------------------------------------------------------------- errors */

/** HA prefixes some websocket validation messages; the user does not need that. */
export function stripValidationPrefix(message) {
  if (typeof message !== "string") return "";
  return message.replace(/^\s*validation error:\s*/i, "").trim();
}

/** Stable key for a websocket/service error: translation_key, else code, else null. */
export function errorKey(err) {
  if (!err || typeof err !== "object") return null;
  return err.translation_key || err.code || null;
}

/** User-facing error text: a known translated key wins, else the stripped message. */
export function errorMessage(err) {
  const key = errorKey(err);
  if (key && STRINGS[`error_${key}`] !== undefined) return STRINGS[`error_${key}`];
  const msg = stripValidationPrefix(
    typeof err === "string" ? err : err && typeof err.message === "string" ? err.message : "",
  );
  return msg || STRINGS.error_generic;
}

/** Which add/edit form field an error belongs to, for aria-invalid. */
export function errorField(key) {
  switch (key) {
    case "duplicate_package":
    case "in_history":
    case "invalid_tracking_number":
      return "tracking_number";
    case "invalid_date_range":
      return "expected_date_end";
    default:
      return null;
  }
}

/* ------------------------------------------------------------------- config */

export const CONFIG_DEFAULTS = Object.freeze({
  title: STRINGS.default_title,
  show_add_form: true,
  show_history: true,
  hide_arrived: false,
});

const BOOL_KEYS = ["show_add_form", "show_history", "hide_arrived"];

/** Validate a Lovelace config and fill in defaults. Throws Error on bad input. */
export function normalizeConfig(config) {
  if (!config || typeof config !== "object" || Array.isArray(config)) {
    throw new Error("Invalid configuration");
  }
  if (config.title !== undefined && config.title !== null && typeof config.title !== "string") {
    throw new Error("title must be a string");
  }
  for (const key of BOOL_KEYS) {
    if (config[key] !== undefined && typeof config[key] !== "boolean") {
      throw new Error(`${key} must be true or false`);
    }
  }
  return { ...CONFIG_DEFAULTS, ...config };
}

/* ------------------------------------------------------------ add / edit data */

const TEXT_FIELDS = ["description", "vendor", "notes"];
const DATE_FIELDS = ["expected_date", "expected_date_end"];

function clean(value) {
  return typeof value === "string" ? value.trim() : "";
}

/** Service data for add_package from raw form values; empty fields are omitted. */
export function buildAddData(fields) {
  const data = { tracking_number: clean(fields.tracking_number) };
  const carrier = clean(fields.carrier);
  if (carrier && carrier !== "auto") data.carrier = carrier;
  for (const key of [...TEXT_FIELDS, ...DATE_FIELDS, "tracking_url"]) {
    const value = clean(fields[key]);
    if (value) data[key] = value;
  }
  return data;
}

/** Editable values of a package, as strings for form inputs. */
export function editDraft(pkg) {
  return {
    description: pkg.description ?? "",
    vendor: pkg.vendor ?? "",
    notes: pkg.notes ?? "",
    expected_date: pkg.expected_date ?? "",
    expected_date_end: pkg.expected_date_end ?? "",
    tracking_url: pkg.tracking_url_override ?? "",
    carrier: pkg.carrier_source === "manual" ? pkg.carrier : "auto",
  };
}

/**
 * Service data for update_package: only fields that changed; a cleared field is
 * sent as null (which the service treats as "clear"). Returns null when nothing changed.
 */
export function buildUpdateData(draft, pkg) {
  const original = editDraft(pkg);
  const data = {};
  for (const key of [...TEXT_FIELDS, ...DATE_FIELDS, "tracking_url"]) {
    const value = clean(draft[key]);
    if (value !== clean(original[key])) data[key] = value === "" ? null : value;
  }
  if (draft.carrier && draft.carrier !== original.carrier) data.carrier = draft.carrier;
  return Object.keys(data).length ? data : null;
}

/* ---------------------------------------------------------- detect / paste */

/** Summarize a package_tracker/detect result for the detection chip. */
export function detectionSummary(result) {
  if (!result) return null;
  const top = result.candidates?.[0] ?? null;
  let checksum = null;
  if (top?.checksum_valid === true) checksum = "ok";
  else if (top?.checksum_valid === false) checksum = "bad";
  let state = "ok";
  if (result.duplicate_of) state = "duplicate";
  else if (result.in_history) state = "in_history";
  else if (!top) state = "unknown";
  return {
    state,
    carrier: top?.carrier ?? null,
    carrierName: top?.carrier_name ?? null,
    service: top?.service ?? null,
    checksum,
    packageId: result.duplicate_of || result.in_history || null,
  };
}

/**
 * Options for a carrier <select>: "auto" first, then detected candidates, then the
 * full carrier list minus anything already listed.
 */
export function carrierOptions(candidates = [], carriers = []) {
  const seen = new Set();
  const detected = [];
  for (const c of candidates || []) {
    if (!c?.carrier || seen.has(c.carrier)) continue;
    seen.add(c.carrier);
    detected.push({ id: c.carrier, name: c.carrier_name || c.carrier });
  }
  const all = (carriers || [])
    .filter((c) => c?.id && !seen.has(c.id))
    .map((c) => ({ id: c.id, name: c.name || c.id }));
  return { detected, all };
}

/** Paste results are pre-checked only when new and confidently detected. */
export function shouldPreselect(result) {
  return !!result && !result.already_tracked && Number(result.confidence) >= 0.8;
}

/** Visible packages for the main table. */
export function visiblePackages(packages, { hideArrived = false } = {}) {
  const list = sortPackages(packages);
  return hideArrived ? list.filter((p) => p.status !== "arrived") : list;
}

/** Short header summary, e.g. "1 overdue · 2 due today · 5 active". */
export function countsSummary(counts) {
  if (!counts) return "";
  const parts = [];
  if (counts.overdue) parts.push(t("count_overdue", { n: counts.overdue }));
  if (counts.due_today) parts.push(t("count_due_today", { n: counts.due_today }));
  parts.push(t("count_active", { n: counts.active ?? 0 }));
  return parts.join(" · ");
}

/** Display name for a package in labels and toasts. */
export function packageName(pkg) {
  return pkg?.description || pkg?.tracking_number || STRINGS.no_description;
}

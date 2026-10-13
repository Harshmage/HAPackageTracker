# Architecture

Package Tracker is a Home Assistant custom integration with a bundled Lovelace card. It
has no backend service and no Python requirements, and it never talks to a carrier. All
state lives in a single Home Assistant `Store`. Everything else is derived from it: links,
statuses, counts, calendar events and notifications.

## Components

```
custom_components/package_tracker/
├── __init__.py            async_setup (services, websocket, card) / setup / unload / remove entry
├── manifest.json          single_config_entry, dependencies http + websocket_api, after frontend
├── const.py               constants only
├── helpers.py             SIGNAL_UPDATED, manager lookup (get_manager_or_none / _get_manager)
├── models.py              Package dataclass, Status enum, compute_status(), API/event dicts
├── store.py               Store wrapper (key package_tracker.packages, v1.1)
├── manager.py             PackageManager: CRUD, uniqueness, status transitions, events, commit
├── scheduler.py           midnight tick, daily digest timer, startup catch-up
├── notifications.py       digest/arrived message building and delivery
├── services.py            service actions (+ services.yaml)
├── websocket.py           card API: subscribe, history, detect, extract, carriers
├── card_registration.py   static path + add_extra_js_url for the card
├── entity.py              shared entity base (one service device)
├── sensor.py              count sensors + next delivery
├── calendar.py            "Deliveries" calendar
├── config_flow.py         confirm-only config flow, options flow (OptionsFlowWithReload)
├── diagnostics.py         redacted diagnostics
├── logbook.py             logbook descriptions for bus events
├── strings.json, translations/en.json, icons.json, brand/
├── carriers/              pure standard library, no Home Assistant imports
│   ├── checksums.py       check-digit algorithms
│   ├── rules.py           format rules, confidences, acceptance policies
│   ├── registry.py        carriers, URL templates, build_links(), link parsing
│   ├── classifier.py      normalize / canonicalize / classify
│   └── extract.py         find numbers in free text
└── www/
    ├── package-tracker-card.js   the card (lit element)
    ├── card-helpers.js           pure helpers (dates, sorting, config) – unit-tested in node
    └── vendor/lit-all.min.js     vendored lit 3.3.1 (BSD-3-Clause)
```

## Data flow

```
 service call ─┐                                  ┌─> bus events (package_tracker_*) ─> automations, logbook
 websocket ────┼─> PackageManager ─> _async_commit┼─> Store.async_save()  (.storage/package_tracker.packages)
 scheduler ────┘    (validate,       (recompute   ├─> entity listeners ─> sensors, calendar ─> state machine
                     mutate)          statuses)   └─> dispatcher SIGNAL_UPDATED ─> websocket subscriptions
                                                                                     └─> card re-renders
```

Every mutation goes through `PackageManager._async_commit`, which does these steps in
order:

1. Compute today's status for every package and collect the transitions into
   `due_today` and `overdue`. A transition is new only when the status differs from the
   persisted `last_status`.
2. Save the store.
3. Fire the bus events: the explicit events (`added`, `arrived`, `archived`, `removed`)
   first, then the transitions.
4. Call the entity listeners, which write state.
5. Send `SIGNAL_UPDATED`. Each websocket subscription then pushes a fresh **full
   snapshot** to its card.

Because this order lives in one place, the card, the entities and the store cannot
disagree.

The services and websocket commands are registered once in `async_setup`, so they exist
before a config entry does. They look the manager up on **every** call through
`hass.config_entries.async_loaded_entries(DOMAIN)`, which means they keep working across
reloads. When no entry is loaded they raise the translated `not_loaded` error, or report
`loaded: false`.

## Key decisions

### No backend, no live tracking

Carrier tracking APIs require business accounts, approval or paid plans, and scraping
breaks and may break terms of service. The previous version of this project ran a FastAPI
service against those APIs and was stuck on access. This version accepts the limit. It
detects the carrier offline and links to the public tracking page, and the user records
the expected date. That keeps installation to HACS only, with no Docker, no pip
requirements, no credentials and nothing to keep running.

### Card served from the integration with `add_extra_js_url` and a hashed static path

Users should not have to add a Lovelace resource by hand. `card_registration.py` works
like this:

- At startup it hashes the whole `www/` directory (sha256, first 8 hex characters) in the
  executor.
- It serves `www/` at `/package_tracker_static/<hash>/` with cache headers.
- It calls `add_extra_js_url` for `<hash>/package-tracker-card.js`.

The hash sits in the **directory**, not a `?v=` query, so the card's relative imports
also get new URLs: `./card-helpers.js` and `./vendor/lit-all.min.js`. A browser therefore
never mixes a new card with a cached old helper module, and long cache lifetimes are
safe.

Two edge cases:

- If an entry is removed and re-added without a restart, `async_setup_entry` adds the URL
  again.
- `async_remove_entry` removes the URL from the frontend's list. Browsers that already
  loaded the module keep it until reload, and the static route stays until restart,
  because aiohttp routes cannot be removed.

### Websocket snapshots instead of large entity attributes

The card needs every package with derived links and statuses. Putting that on an entity
attribute would write the whole list to the recorder on every change, would hit the
state-size limit, and would expose order details in history. The card subscribes to
`package_tracker/subscribe` instead and gets full snapshots pushed over the websocket.

The count sensors keep a small `packages` attribute, capped at 25, for templates. It is
listed in `_unrecorded_attributes`, so it is never written to history.

Full snapshots, not diffs, keep the client simple and correct after reconnects. The
lists are small. The card uses lit's keyed `repeat()`, so a snapshot that arrives during
an inline edit does not reset focus or half-typed input.

### Extraction over the websocket keeps e-mail text out of the recorder

Every service call fires a `call_service` event, and the recorder stores its data. A
pasted shipping e-mail can contain addresses, names and order details. The card's paste
dialog therefore uses the `package_tracker/extract` websocket command, which fires no
event. The `extract_tracking_numbers` service still exists for automations such as the
IMAP recipe, and its description and the README warn that its input is recorded.

### The `carriers/` package has no Home Assistant imports

Format rules, checksums, link templates and extraction are plain Python, which brings
three benefits:

- They can be unit-tested in milliseconds without the HA test harness. There are
  hundreds of vectors.
- They could be reused outside Home Assistant.
- They cannot accidentally block the event loop on HA internals.

Extraction runs in the executor because regex work on 100 kB of HTML is CPU-bound. Every
public function here **never raises**, because it runs on arbitrary user text.

### "Today" and status are computed on the server

`compute_status(pkg, today)` is a pure function. `today` is always
`dt_util.now().date()` in Home Assistant's configured time zone, and the snapshot
includes `today` so the card measures relative labels ("in 2 days") against the server,
not the browser clock. A phone in another time zone, or a browser with the wrong clock,
therefore sees the same overdue state as the sensors, calendar and notifications.

Calendar dates travel as `YYYY-MM-DD` strings. The card formats them as UTC instants, so
a negative-offset browser never shows the previous day.

### Notification and event dedupe

- **Transition events.** `due_today` and `overdue` fire once per transition. The
  persisted `last_status` is the dedupe key, so a restart or reload does not fire them
  again.
- **Digest.** Each package records the date it was last mentioned for each kind
  (`notified`). A digest only includes packages not yet notified, or, with
  `remind_overdue_daily`, overdue packages not notified *today*. Changing a package's
  dates clears `notified`. `meta.last_digest_date` makes the startup catch-up skip a
  digest that already went out today, and an empty digest is never sent.
- **Delivery.** Each target (notify entity, legacy service, persistent notification) is
  tried independently. A failing phone is logged and skipped. The persistent notification
  uses a fixed id, so a new digest replaces the old one.

### Deferring startup work with `async_at_started`

Config entries load before many automations and notify services are ready. Events fired
during startup could reach nobody, and a digest could fail because `notify.mobile_app_*`
does not exist yet. The manager therefore starts with transition events held back,
leaving `last_status` untouched so nothing is lost. `async_at_started` then runs
`manager.async_start()`, which releases them, and the missed-digest catch-up.

The callback creates an **entry task**, so unloading the entry waits for it instead of
racing it.

Websocket subscribers learn about entry state changes from
`entry.async_on_state_change`. There is no manual dispatch during setup or unload:
`async_loaded_entries` only sees the entry once it is `LOADED`, and the state-change
hook fires at exactly that moment.

### lit vendored instead of a build toolchain

The card uses [lit](https://lit.dev) because keyed `repeat()` and reactive properties
make the frequently pushed snapshots safe to render. The alternative was hand-rolled DOM
diffing or `innerHTML`, which loses focus and invites XSS mistakes.

lit ships as a prebuilt ESM file, `lit-all.min.js` (BSD-3-Clause; the license headers
are kept in the file). Vendoring it means:

- no npm, bundler or `dist/` build artifact in the repository;
- the files under `www/` are exactly what runs, and they can be reviewed directly;
- CI only needs `node --check` and `node --test` on the pure helpers.

The card does not rely on Home Assistant's internal, lazily loaded `ha-*` elements,
which can be missing when a dashboard first renders. The only one it uses is
`ha-card`. The visual editor is Home Assistant's own form, generated from
`getConfigForm()`. Native elements are used for the rest, including `<dialog>`,
`<details>`, `<select>` and `<input type=date>`.

`card-helpers.js` is imported before lit so it can set `litDisableBundleWarning` first.

### Other choices

- **One config entry** (`single_config_entry`). There is one package list per Home
  Assistant instance, and every user sees it. `added_by` is stored for a future
  per-user filter.
- **No per-package entities.** Packages come and go daily. Entities for them would
  clutter the entity registry and the recorder. Sensors, the calendar and events cover
  automations.
- **Options reload the entry** (`OptionsFlowWithReload`) instead of using update
  listeners. Timers and the fallback site are rebuilt cleanly.
- **Removing the entry deletes the store.** `async_remove_entry` deletes the package
  history. The README warns about this.
- **Module names.** `notifications.py` is not called `notify.py`, because HA would treat
  that as a notify platform. `card_registration.py` is not called `frontend.py`, to
  avoid confusion with the `frontend` integration.

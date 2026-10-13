# Development

## Requirements

- **Python 3.14** (≥ 3.14.2). Home Assistant 2026.7 requires it.
- **Node.js 22 or newer.** It runs only the card's syntax check and unit tests. There is
  no JavaScript build step.
- Git.

The test dependencies are pinned in [`requirements_test.txt`](../requirements_test.txt):

- `pytest-homeassistant-custom-component`, which pins Home Assistant 2026.7.3, pytest,
  pytest-asyncio and freezegun;
- `ruff`.

The integration itself has **no** Python requirements (`"requirements": []`).

## Set up a virtual environment

With [uv](https://docs.astral.sh/uv/):

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv -r requirements_test.txt
```

Or with plain pip:

```bash
python3.14 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements_test.txt
```

## Run the checks

These are the same checks CI runs (see [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)):

```bash
# Python tests: unit (no Home Assistant, fast) and HA integration tests
.venv/bin/pytest -q
.venv/bin/pytest tests/unit -q          # carriers/ and models only
.venv/bin/pytest tests/ha -q            # services, websocket, entities, scheduler, ...

# Lint and format
.venv/bin/ruff check .
.venv/bin/ruff format --check .         # `ruff format .` to fix

# Card
node --check custom_components/package_tracker/www/package-tracker-card.js
node --check custom_components/package_tracker/www/card-helpers.js
node --test "tests/js/*.test.mjs"
```

Pass a glob to `node --test`. With a bare directory (`node --test tests/js`), recent Node
versions try to load the directory as a module and fail.

The HA tests use `pytest-homeassistant-custom-component` fixtures (`hass`,
`hass_storage`, `hass_ws_client`, `freezer`). `tests/ha/conftest.py` enables custom
integrations automatically.

### hassfest and HACS validation

CI runs `home-assistant/actions/hassfest` and `hacs/action`. To run hassfest locally,
use a Home Assistant core checkout:

```bash
python -m script.hassfest --integration-path /path/to/HAPackageTracker/custom_components/package_tracker
```

## Run a local development Home Assistant

The `dev-config/` directory is git-ignored. Use a **separate** virtual environment
for it, so the full Home Assistant install does not disturb the pinned test
environment:

```bash
python3.14 -m venv .venv-ha
.venv-ha/bin/pip install homeassistant home-assistant-frontend

mkdir -p dev-config/custom_components
ln -s ../../custom_components/package_tracker dev-config/custom_components/package_tracker

.venv-ha/bin/hass -c ./dev-config
```

Open <http://localhost:8123>, finish onboarding, then go to **Settings → Devices &
services → Add integration → Package Tracker**.

- **Python changes** need a Home Assistant restart.
- **Card changes** under `www/` need a restart too, because the content hash in the URL
  is computed at startup. After the restart, reload the browser.

Set `logger:` in `dev-config/configuration.yaml` to see debug output:

```yaml
logger:
  default: warning
  logs:
    custom_components.package_tracker: debug
```

### Manual browser checklist

Run this before every release, in a fresh dev instance:

- [ ] Add the integration. The **Package Tracker** card appears in the dashboard card
      picker **without** adding a resource.
- [ ] Add a UPS (`1Z999AA10123456784`), a USPS (`9400109699939938223564`), an Amazon
      (`TBA502887274000`) and an unknown number (`ABC12345XYZ`).
  - [ ] Each link opens the right carrier page in a new tab.
  - [ ] The unknown number shows the homepage and fallback links.
  - [ ] Copy works over plain `http://` (LAN IP), over `https://`, and in the companion
        app.
- [ ] Set a package's expected date to yesterday. The row is tinted red with an
      **Overdue** badge, and `sensor.package_tracker_overdue_packages` is 1.
- [ ] Set a date of today. The row shows the due-today style.
- [ ] Tick **Arrived**. Set `archive_after_days` to 1 and move the clock, or wait. The
      package auto-archives and shows in **History**, where Restore and Delete work.
- [ ] Paste a real shipping e-mail into **Paste text**. The right numbers are
      pre-checked, and **Add selected** adds them.
- [ ] Open the add form and type a number with a typo. The chip shows "possible typo".
      Type an already-tracked number. It shows "Already tracked".
- [ ] Edit a package inline while another browser changes a different package. Your
      edit keeps focus and text.
- [ ] The **Deliveries** calendar shows the expected dates as all-day events.
- [ ] Set `notify_time` to the next minute. The digest arrives exactly once, as a
      persistent notification and on any configured notify target.
- [ ] Check light and dark themes at phone width (≈ 375 px) and desktop width.
- [ ] Remove the integration. The card shows "not loaded", and after a restart it is
      gone from the card picker.

## Release checklist

1. Bump the version in **both** places. CI and the release workflow check that they
   match:
   - `custom_components/package_tracker/manifest.json` → `"version"`
   - `custom_components/package_tracker/www/card-helpers.js` → `export const VERSION`
2. Move the `Unreleased` entries in [`CHANGELOG.md`](../CHANGELOG.md) to a new
   `## [x.y.z] - YYYY-MM-DD` section and update the compare links at the bottom.
3. Run every check above, plus the manual browser checklist.
4. Commit, tag and push:

   ```bash
   git tag vX.Y.Z
   git push origin main vX.Y.Z
   ```

   [`release.yml`](../.github/workflows/release.yml) checks that the tag, manifest
   version and card `VERSION` are equal. It then publishes a GitHub release whose notes
   come from that CHANGELOG section. HACS picks up the release.

## Adding a carrier

1. **Registry.** Add a `Carrier(...)` entry to `CARRIERS` in
   `carriers/registry.py`. Insertion order is the tie-breaker and the UI order, and
   `unknown` must stay last. An entry has these fields:
   - `id`, `name` and `homepage`;
   - `url_template` with `{tn}`, or `None` if there is no stable deep link;
   - `keywords`, uppercase words used for context ranking;
   - `hosts` (domain suffixes) and `url_params`, so pasted links are recognised. Use
     `PATH_PARAM` when the number is the last path segment.
   - If it is a postal operator, add its ISO country to `S10_COUNTRY_CARRIER`.
2. **Rules.** Add a `_r(...)` rule to `RULES` in `carriers/rules.py`. It needs a regex
   over the *canonical* number, a service, a policy (`STRONG`, `CHECKSUM` or `CONTEXT`),
   an optional checksum, and confidences. Bare-digit shapes must never be `STRONG`.
   Check how the new rule overlaps existing shapes of the same length.
3. **Checksum** (if any). Add a function to `carriers/checksums.py`. It takes a
   normalized string, returns a bool and never raises. Add known-valid vectors to
   `tests/unit/test_checksums.py`. The single-digit mutation test then covers the
   failure cases.
4. **Tests.**
   - Add classifier cases to `tests/unit/test_classifier.py`.
   - Add the expected URL to `EXPECTED_URLS` in `tests/unit/test_registry_urls.py`.
   - If the format can appear in e-mails, add an extraction case, including a negative
     case.
5. **UI strings.** Add the id to **both** `carrier` select option lists in
   `services.yaml` (`add_package` and `update_package`). Add the label to
   `selector.carrier.options` in `strings.json`, and copy `strings.json` to
   `translations/en.json`. A test asserts that the two JSON files are identical.
   - Nothing checks the services.yaml list against the registry, so keep them in sync
     by hand.
6. **Docs.** Add rows to the format, template and link tables in
   [`carriers.md`](carriers.md). Mark the template's "last verified" honestly.

## Updating the vendored lit

The card imports `www/vendor/lit-all.min.js`, the prebuilt single-file lit bundle
(currently 3.3.1, BSD-3-Clause). To update it:

```bash
VER=3.3.1   # the new version
curl -fsSL "https://cdn.jsdelivr.net/gh/lit/dist@${VER}/all/lit-all.min.js" \
  | sed '/^\/\/# sourceMappingURL=/d' \
  > custom_components/package_tracker/www/vendor/lit-all.min.js
```

The `sourceMappingURL` line is removed because the map file is not shipped. Keep the
`@license` headers. Then:

- run `node --check` on the card and the JS tests;
- go through the manual checklist, at least the add form, paste dialog and history;
- update the version mentioned here, in [`architecture.md`](architecture.md) and in
  `CHANGELOG.md`.

Every file under `www/` is hashed into the served URL, so browsers fetch the new bundle
automatically after a Home Assistant restart.

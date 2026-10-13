/**
 * Package Tracker Lovelace card.
 *
 * Plain ES module served by the integration itself (card_registration.py), so
 * there is no build step and no Lovelace resource to configure. lit is vendored
 * as a single prebuilt file; its keyed repeat() keeps focus and half-typed inline
 * edits intact when the backend pushes a fresh snapshot.
 *
 * The card talks to the backend only through the package_tracker websocket
 * commands (subscribe, history, detect, extract, carriers) and services
 * (add/update/mark_arrived/archive/restore/remove). It deliberately uses no
 * lazily-loaded ha-* elements (only ha-card, which HA always defines), so it
 * renders the same everywhere the frontend is loaded.
 *
 * Pure logic (dates, sorting, copy strategy, error mapping, strings) lives in
 * card-helpers.js so it can be unit tested in node. card-helpers.js must be
 * imported before lit: it sets the flag that silences lit's bundle warning.
 */
import {
  VERSION,
  DOMAIN,
  t,
  resolveLocale,
  expectedInfo,
  formatInstant,
  copyStrategy,
  errorKey,
  errorMessage,
  errorField,
  normalizeConfig,
  buildAddData,
  editDraft,
  buildUpdateData,
  detectionSummary,
  carrierOptions,
  shouldPreselect,
  visiblePackages,
  countsSummary,
  packageName,
} from "./card-helpers.js";
import { LitElement, html, css, nothing, svg, repeat, live } from "./vendor/lit-all.min.js";

const CARD_TAG = "package-tracker-card";
const DETECT_DEBOUNCE_MS = 250;
const CONFIRM_TIMEOUT_MS = 5000;

// Material Design Icons paths (Apache-2.0, Pictogrammers), inlined to avoid ha-icon.
const ICONS = {
  copy: "M19,21H8V7H19M19,5H8A2,2 0 0,0 6,7V21A2,2 0 0,0 8,23H19A2,2 0 0,0 21,21V7A2,2 0 0,0 19,5M16,1H4A2,2 0 0,0 2,3V17H4V3H16V1Z",
  openInNew:
    "M14,3V5H17.59L7.76,14.83L9.17,16.24L19,6.41V10H21V3M19,19H5V5H12V3H5C3.89,3 3,3.9 3,5V19A2,2 0 0,0 5,21H19A2,2 0 0,0 21,19V12H19V19Z",
  pencil:
    "M20.71,7.04C21.1,6.65 21.1,6 20.71,5.63L18.37,3.29C18,2.9 17.35,2.9 16.96,3.29L15.12,5.12L18.87,8.87M3,17.25V21H6.75L17.81,9.93L14.06,6.18L3,17.25Z",
  archive:
    "M3,3H21V7H3V3M4,8H20V21H4V8M9.5,11A0.5,0.5 0 0,0 9,11.5V13H15V11.5A0.5,0.5 0 0,0 14.5,11H9.5Z",
  delete: "M19,4H15.5L14.5,3H9.5L8.5,4H5V6H19M6,19A2,2 0 0,0 8,21H16A2,2 0 0,0 18,19V7H6V19Z",
  restore:
    "M13,3A9,9 0 0,0 4,12H1L4.89,15.89L4.96,16.03L9,12H6A7,7 0 0,1 13,5A7,7 0 0,1 20,12A7,7 0 0,1 13,19C11.07,19 9.32,18.21 8.06,16.94L6.64,18.36C8.27,20 10.5,21 13,21A9,9 0 0,0 22,12A9,9 0 0,0 13,3Z",
  history:
    "M13.5,8H12V13L16.28,15.54L17,14.33L13.5,12.25V8M13,3A9,9 0 0,0 4,12H1L4.96,16.03L9,12H6A7,7 0 0,1 13,5A7,7 0 0,1 20,12A7,7 0 0,1 13,19C11.07,19 9.32,18.21 8.06,16.94L6.64,18.36C8.27,20 10.5,21 13,21A9,9 0 0,0 22,12A9,9 0 0,0 13,3",
  plus: "M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z",
  clipboardText:
    "M19,3H14.82C14.4,1.84 13.3,1 12,1C10.7,1 9.6,1.84 9.18,3H5A2,2 0 0,0 3,5V19A2,2 0 0,0 5,21H19A2,2 0 0,0 21,19V5A2,2 0 0,0 19,3M12,3A1,1 0 0,1 13,4A1,1 0 0,1 12,5A1,1 0 0,1 11,4A1,1 0 0,1 12,3M7,7H17V5H19V19H5V5H7V7M17,11H7V9H17V11M15,15H7V13H15V15Z",
  close:
    "M19,6.41L17.59,5L12,10.59L6.41,5L5,6.41L10.59,12L5,17.59L6.41,19L12,13.41L17.59,19L19,17.59L13.41,12L19,6.41Z",
};

const icon = (name) =>
  html`<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
    ${svg`<path d=${ICONS[name]}></path>`}
  </svg>`;

const linkAttrs = { target: "_blank", rel: "noopener noreferrer", referrerpolicy: "no-referrer" };

class PackageTrackerCard extends LitElement {
  static properties = {
    _config: { state: true },
    _snapshot: { state: true },
    _subError: { state: true },
    _carriers: { state: true },
    _detect: { state: true },
    _addCarrier: { state: true },
    _addBusy: { state: true },
    _addError: { state: true },
    _pending: { state: true },
    _busy: { state: true },
    _editing: { state: true },
    _confirmDelete: { state: true },
    _manualCopy: { state: true },
    _showHistory: { state: true },
    _history: { state: true },
    _historyError: { state: true },
    _paste: { state: true },
  };

  constructor() {
    super();
    this._config = normalizeConfig({});
    this._hass = undefined;
    this._subP = undefined;
    this._snapshot = null;
    this._subError = null;
    this._carriers = null;
    this._carriersP = undefined;
    this._detect = null;
    this._detectSeq = 0;
    this._detectTimer = undefined;
    this._addCarrier = "auto";
    this._addBusy = false;
    this._addError = null;
    this._pending = new Map(); // package id -> requested arrived value
    this._busy = new Set(); // package ids with an in-flight archive/delete/save/restore
    this._editing = new Map(); // package id -> draft (never overwritten by snapshots)
    this._confirmDelete = null;
    this._confirmTimer = undefined;
    this._manualCopy = null;
    this._showHistory = false;
    this._history = null;
    this._historyError = null;
    this._historySeq = 0;
    this._paste = null;
    this._pasteOpener = null;
    this._lang = "en";
    this._dark = false;
  }

  /* --------------------------------------------------------- Lovelace API */

  static getStubConfig() {
    return { title: t("default_title"), show_add_form: true, show_history: true, hide_arrived: false };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        {
          type: "grid",
          name: "",
          schema: [
            { name: "show_add_form", selector: { boolean: {} } },
            { name: "show_history", selector: { boolean: {} } },
            { name: "hide_arrived", selector: { boolean: {} } },
          ],
        },
      ],
      computeLabel: (schema) => t(`cfg_${schema.name}`),
    };
  }

  setConfig(config) {
    this._config = normalizeConfig(config);
  }

  getCardSize() {
    const rows = this._snapshot?.packages?.length ?? 0;
    return Math.max(3, Math.min(12, 2 + rows + (this._config.show_add_form ? 2 : 0)));
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  /* -------------------------------------------------- hass + subscription */

  set hass(hass) {
    this._hass = hass;
    const lang = resolveLocale(hass?.locale?.language, globalThis.navigator?.language);
    const dark = !!hass?.themes?.darkMode;
    // hass changes on every state change in HA; only re-render when it matters here.
    if (lang !== this._lang || dark !== this._dark || !this.hasUpdated) {
      this._lang = lang;
      this._dark = dark;
      this.requestUpdate();
    }
    this._subscribe();
  }

  get hass() {
    return this._hass;
  }

  connectedCallback() {
    super.connectedCallback();
    this._subscribe();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._unsubscribe();
    clearTimeout(this._detectTimer);
    clearTimeout(this._confirmTimer);
  }

  _subscribe() {
    if (!this.isConnected || !this._hass?.connection || this._subP) return;
    this._subError = null;
    const p = this._hass.connection.subscribeMessage((msg) => this._onSnapshot(msg), {
      type: `${DOMAIN}/subscribe`,
    });
    this._subP = p;
    p.catch((err) => {
      if (this._subP !== p) return;
      this._subP = undefined;
      this._snapshot = null;
      this._subError = err || {};
    });
  }

  _unsubscribe() {
    const p = this._subP;
    this._subP = undefined;
    p?.then((unsub) => unsub()).catch(() => {});
  }

  _retry() {
    this._unsubscribe();
    this._subError = null;
    this._subscribe();
  }

  _onSnapshot(msg) {
    this._snapshot = msg;
    this._subError = null;
    // Drop drafts/pending flags for packages that disappeared (archived elsewhere).
    const ids = new Set((msg?.packages || []).map((p) => p.id));
    let changed = false;
    for (const id of this._editing.keys()) {
      if (!ids.has(id)) {
        this._editing.delete(id);
        changed = true;
      }
    }
    if (changed) this._editing = new Map(this._editing);
    if (this._showHistory) this._loadHistory();
  }

  /* ------------------------------------------------------------- helpers */

  _toast(message) {
    this.dispatchEvent(
      new CustomEvent("hass-notification", {
        detail: { message },
        bubbles: true,
        composed: true,
      }),
    );
  }

  _callService(service, data) {
    return this._hass.callService(DOMAIN, service, data, undefined, false);
  }

  _ws(type, extra = {}) {
    return this._hass.callWS({ type: `${DOMAIN}/${type}`, ...extra });
  }

  _setBusy(id, on) {
    const next = new Set(this._busy);
    if (on) next.add(id);
    else next.delete(id);
    this._busy = next;
  }

  _ensureCarriers() {
    if (this._carriers || this._carriersP || !this._hass) return;
    this._carriersP = this._ws("carriers")
      .then((res) => {
        this._carriers = res?.carriers || [];
      })
      .catch(() => {
        this._carriers = [];
      })
      .finally(() => {
        this._carriersP = undefined;
      });
  }

  get _locale() {
    return this._lang;
  }

  get _serverTz() {
    return this._hass?.config?.time_zone;
  }

  /* ------------------------------------------------------------ actions */

  async _toggleArrived(pkg, ev) {
    const arrived = ev.target.checked;
    this._pending = new Map(this._pending).set(pkg.id, arrived);
    try {
      await this._callService("mark_arrived", { package_id: pkg.id, arrived });
    } catch (err) {
      this._toast(errorMessage(err));
    } finally {
      const next = new Map(this._pending);
      next.delete(pkg.id);
      this._pending = next;
    }
  }

  async _simpleAction(pkg, service, doneKey) {
    this._setBusy(pkg.id, true);
    try {
      await this._callService(service, { package_id: pkg.id });
      this._toast(t(doneKey, { name: packageName(pkg) }));
      return true;
    } catch (err) {
      this._toast(errorMessage(err));
      return false;
    } finally {
      this._setBusy(pkg.id, false);
    }
  }

  _archive(pkg) {
    return this._simpleAction(pkg, "archive_package", "archived");
  }

  async _restore(pkg) {
    const ok = await this._simpleAction(pkg, "restore_package", "restored");
    if (ok && this._showHistory) this._loadHistory();
  }

  _askDelete(pkg) {
    clearTimeout(this._confirmTimer);
    this._confirmDelete = pkg.id;
    this._confirmTimer = setTimeout(() => {
      this._confirmDelete = null;
    }, CONFIRM_TIMEOUT_MS);
    this.updateComplete.then(() => {
      this.renderRoot.querySelector(`[data-confirm="${CSS.escape(pkg.id)}"]`)?.focus();
    });
  }

  _cancelDelete(pkg) {
    clearTimeout(this._confirmTimer);
    this._confirmDelete = null;
    this.updateComplete.then(() => {
      this.renderRoot.querySelector(`[data-delete="${CSS.escape(pkg.id)}"]`)?.focus();
    });
  }

  async _delete(pkg) {
    clearTimeout(this._confirmTimer);
    this._confirmDelete = null;
    const ok = await this._simpleAction(pkg, "remove_package", "deleted");
    if (ok && this._showHistory) this._loadHistory();
  }

  /* --------------------------------------------------------------- copy */

  _copy(pkg, ev) {
    // Runs synchronously inside the click so the user activation is still valid.
    const tn = pkg.tracking_number;
    const button = ev.currentTarget;
    const strategy = copyStrategy({
      isSecureContext: globalThis.isSecureContext,
      hasClipboard: typeof globalThis.navigator?.clipboard?.writeText === "function",
      hasExecCommand: typeof document.execCommand === "function",
    });
    const done = () => this._toast(t("copied", { tn }));
    const fallback = () => {
      if (this._execCopy(tn, button)) done();
      else this._showManualCopy(pkg.id);
    };
    if (strategy === "clipboard") {
      navigator.clipboard.writeText(tn).then(done, fallback);
    } else if (strategy === "execCommand") {
      fallback();
    } else {
      this._showManualCopy(pkg.id);
    }
  }

  _execCopy(text, returnFocus) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.setAttribute("aria-hidden", "true");
    Object.assign(ta.style, {
      position: "fixed",
      top: "0",
      left: "0",
      width: "1px",
      height: "1px",
      opacity: "0",
      pointerEvents: "none",
    });
    document.body.appendChild(ta);
    let ok = false;
    try {
      ta.focus();
      ta.select();
      ta.setSelectionRange(0, text.length);
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    ta.remove();
    returnFocus?.focus?.();
    return ok;
  }

  _showManualCopy(id) {
    this._manualCopy = id;
    this.updateComplete.then(() => {
      const input = this.renderRoot.querySelector("input.manual-copy");
      input?.focus();
      input?.select();
    });
  }

  /* ----------------------------------------------------------- add form */

  _onTrackingInput(ev) {
    const value = ev.target.value.trim();
    this._addError = null;
    clearTimeout(this._detectTimer);
    const seq = ++this._detectSeq;
    if (!value) {
      this._detect = null;
      this._addCarrier = "auto";
      return;
    }
    this._detect = { query: value, loading: true, result: this._detect?.result ?? null };
    this._detectTimer = setTimeout(() => {
      this._ws("detect", { tracking_number: value })
        .then((result) => {
          if (seq === this._detectSeq) this._detect = { query: value, loading: false, result };
        })
        .catch(() => {
          if (seq === this._detectSeq) this._detect = { query: value, loading: false, result: null };
        });
    }, DETECT_DEBOUNCE_MS);
  }

  async _submitAdd(ev) {
    ev.preventDefault();
    const form = ev.currentTarget;
    const fields = Object.fromEntries(new FormData(form).entries());
    const data = buildAddData(fields);
    if (!data.tracking_number) {
      this._addError = { key: "invalid_tracking_number", message: t("error_invalid_tracking_number") };
      form.querySelector("[name=tracking_number]")?.focus();
      return;
    }
    this._addBusy = true;
    this._addError = null;
    try {
      await this._callService("add_package", data);
      form.reset();
      clearTimeout(this._detectTimer);
      this._detectSeq++;
      this._detect = null;
      this._addCarrier = "auto";
      this._toast(t("added", { tn: data.tracking_number }));
      form.querySelector("[name=tracking_number]")?.focus();
    } catch (err) {
      this._addError = { key: errorKey(err), message: errorMessage(err) };
      const field = errorField(this._addError.key);
      if (field) form.querySelector(`[name=${field}]`)?.focus();
    } finally {
      this._addBusy = false;
    }
  }

  async _restoreFromDetect(packageId) {
    try {
      await this._callService("restore_package", { package_id: packageId });
      const form = this.renderRoot.querySelector("form.add");
      form?.reset();
      this._detectSeq++;
      this._detect = null;
      this._addCarrier = "auto";
      this._addError = null;
      this._toast(t("restored", { name: this._detect?.query ?? "" }));
    } catch (err) {
      this._addError = { key: errorKey(err), message: errorMessage(err) };
    }
  }

  /* ---------------------------------------------------------- inline edit */

  _startEdit(pkg) {
    this._ensureCarriers();
    this._editing = new Map(this._editing).set(pkg.id, editDraft(pkg));
    this.updateComplete.then(() => {
      this.renderRoot.querySelector(`[data-edit="${CSS.escape(pkg.id)}"] input`)?.focus();
    });
  }

  _cancelEdit(pkg) {
    const next = new Map(this._editing);
    next.delete(pkg.id);
    this._editing = next;
    this.updateComplete.then(() => {
      this.renderRoot.querySelector(`[data-editbtn="${CSS.escape(pkg.id)}"]`)?.focus();
    });
  }

  _editField(pkg, key, ev) {
    const draft = this._editing.get(pkg.id);
    if (!draft) return;
    draft[key] = ev.target.value;
    if (draft.error) {
      draft.error = null;
      this.requestUpdate();
    }
  }

  async _saveEdit(pkg, ev) {
    ev.preventDefault();
    const draft = this._editing.get(pkg.id);
    if (!draft) return;
    const data = buildUpdateData(draft, pkg);
    if (!data) {
      this._cancelEdit(pkg);
      return;
    }
    this._setBusy(pkg.id, true);
    try {
      await this._callService("update_package", { package_id: pkg.id, ...data });
      this._toast(t("saved", { name: packageName({ ...pkg, ...data }) }));
      this._cancelEdit(pkg);
    } catch (err) {
      draft.error = { key: errorKey(err), message: errorMessage(err) };
      this.requestUpdate();
    } finally {
      this._setBusy(pkg.id, false);
    }
  }

  /* -------------------------------------------------------------- history */

  _toggleHistory() {
    this._showHistory = !this._showHistory;
    if (this._showHistory) {
      this._history = null;
      this._loadHistory();
    }
  }

  _loadHistory() {
    const seq = ++this._historySeq;
    this._ws("history")
      .then((res) => {
        if (seq !== this._historySeq) return;
        this._history = res?.packages || [];
        this._historyError = null;
      })
      .catch((err) => {
        if (seq !== this._historySeq) return;
        this._historyError = errorMessage(err);
      });
  }

  /* --------------------------------------------------------- paste dialog */

  _openPaste(ev) {
    this._ensureCarriers();
    this._pasteOpener = ev.currentTarget;
    this._paste = {
      stage: "input",
      text: "",
      busy: false,
      error: null,
      results: [],
      shared: { description: "", vendor: "", expected_date: "" },
    };
    this.updateComplete.then(() => {
      const dialog = this.renderRoot.querySelector("dialog.paste");
      if (dialog && !dialog.open) dialog.showModal();
      dialog?.querySelector("textarea")?.focus();
    });
  }

  _closePaste() {
    this.renderRoot.querySelector("dialog.paste")?.close();
  }

  _onPasteClosed() {
    this._paste = null;
    const opener = this._pasteOpener;
    this._pasteOpener = null;
    opener?.focus?.();
  }

  _updatePaste(patch) {
    this._paste = { ...this._paste, ...patch };
  }

  async _scanPaste(ev) {
    ev.preventDefault();
    const text = this._paste.text;
    if (!text.trim()) return;
    this._updatePaste({ busy: true, error: null });
    try {
      const res = await this._ws("extract", { text: text.slice(0, 100_000) });
      const results = (res?.results || []).map((r) => ({
        ...r,
        checked: shouldPreselect(r),
        selectedCarrier: r.carrier,
        outcome: null,
      }));
      this._updatePaste({ busy: false, stage: "results", results });
    } catch (err) {
      this._updatePaste({ busy: false, error: errorMessage(err) });
    }
  }

  _updatePasteRow(index, patch) {
    const results = this._paste.results.map((r, i) => (i === index ? { ...r, ...patch } : r));
    this._updatePaste({ results });
  }

  async _addPasted(ev) {
    ev.preventDefault();
    const shared = this._paste.shared;
    this._updatePaste({ busy: true, error: null });
    let added = 0;
    for (let i = 0; i < this._paste.results.length; i++) {
      const row = this._paste.results[i];
      if (!row.checked || row.outcome === "added") continue;
      const data = buildAddData({ ...shared, tracking_number: row.tracking_number });
      if (row.selectedCarrier && row.selectedCarrier !== row.carrier) {
        data.carrier = row.selectedCarrier;
      }
      let outcome;
      try {
        await this._callService("add_package", data);
        added++;
        outcome = { outcome: "added", checked: false, message: null };
      } catch (err) {
        outcome = { outcome: "error", message: errorMessage(err) };
      }
      if (!this._paste) return; // dialog closed while the call was in flight
      this._updatePasteRow(i, outcome);
    }
    this._updatePaste({ busy: false });
    if (added) this._toast(t("added", { tn: added === 1 ? "1 package" : `${added} packages` }));
  }

  /* -------------------------------------------------------------- render */

  updated(changed) {
    super.updated?.(changed);
    if (this._config.show_add_form) this._ensureCarriers();
  }

  render() {
    const title = this._config.title ?? t("default_title");
    const snap = this._snapshot;
    const counts = snap?.loaded ? countsSummary(snap.counts) : "";
    const canAdd = this._config.show_add_form && snap?.loaded;
    return html`
      <ha-card>
        <div class="wrap" style="color-scheme: ${this._dark ? "dark" : "light"}" lang=${this._lang}>
          <div class="header">
            <div class="titles">
              ${title ? html`<h2 class="title">${title}</h2>` : nothing}
              ${counts ? html`<div class="counts">${counts}</div>` : nothing}
            </div>
            <div class="toolbar">
              ${canAdd
                ? html`<button type="button" class="btn" @click=${this._openPaste}>
                    ${icon("clipboardText")}<span>${t("paste")}</span>
                  </button>`
                : nothing}
              ${this._config.show_history && snap?.loaded
                ? html`<button
                    type="button"
                    class="btn"
                    aria-pressed=${this._showHistory ? "true" : "false"}
                    @click=${this._toggleHistory}
                  >
                    ${icon("history")}<span>${t("history")}</span>
                  </button>`
                : nothing}
            </div>
          </div>
          ${this._renderBody()}
          ${this._showHistory && snap?.loaded ? this._renderHistory() : nothing}
          ${this._paste ? this._renderPaste() : nothing}
        </div>
      </ha-card>
    `;
  }

  _renderBody() {
    if (this._subError) {
      const code = errorKey(this._subError);
      if (code === "unknown_command" || code === "not_loaded") return this._renderNotLoaded();
      return html`<div class="state error" role="alert">
        <p>${t("connect_error", { message: errorMessage(this._subError) })}</p>
        <button type="button" class="btn" @click=${this._retry}>${t("retry")}</button>
      </div>`;
    }
    const snap = this._snapshot;
    if (!snap) {
      return html`<div class="state" role="status" aria-busy="true">${t("loading")}</div>`;
    }
    if (!snap.loaded) return this._renderNotLoaded();
    const all = snap.packages || [];
    const rows = visiblePackages(all, { hideArrived: this._config.hide_arrived });
    return html`
      ${this._config.show_add_form ? this._renderAddForm() : nothing}
      ${rows.length
        ? this._renderTable(rows, snap.today)
        : html`<div class="state empty" role="status">
            ${all.length ? t("empty_hidden") : t("empty")}
          </div>`}
    `;
  }

  _renderNotLoaded() {
    return html`<div class="state notloaded" role="status">
      <p class="state-title">${t("not_loaded_title")}</p>
      <p>${t("not_loaded_body")}</p>
    </div>`;
  }

  _carrierSelect({ name, id, selected, autoLabel, candidates, onChange, label }) {
    const opts = carrierOptions(candidates, this._carriers || []);
    return html`<select
      name=${name ?? nothing}
      id=${id ?? nothing}
      aria-label=${label ?? nothing}
      @change=${onChange}
    >
      <option value="auto" ?selected=${selected === "auto"}>${autoLabel}</option>
      ${opts.detected.length
        ? html`<optgroup label=${t("carrier_detected")}>
            ${opts.detected.map(
              (c) => html`<option value=${c.id} ?selected=${selected === c.id}>${c.name}</option>`,
            )}
          </optgroup>`
        : nothing}
      ${opts.all.length
        ? html`<optgroup label=${t("carrier_all")}>
            ${opts.all.map(
              (c) => html`<option value=${c.id} ?selected=${selected === c.id}>${c.name}</option>`,
            )}
          </optgroup>`
        : nothing}
    </select>`;
  }

  _renderAddForm() {
    const det = this._detect;
    const summary = detectionSummary(det?.result);
    const err = this._addError;
    const badField = errorField(err?.key);
    const autoLabel = summary?.carrierName
      ? t("carrier_auto", { name: summary.carrierName })
      : t("carrier_auto_pending");
    return html`
      <form class="add" @submit=${this._submitAdd} novalidate aria-labelledby="add-title">
        <h3 class="visually-hidden" id="add-title">${t("add_title")}</h3>
        <div class="add-grid">
          <label class="field tn">
            <span>${t("tracking_number")}</span>
            <input
              name="tracking_number"
              autocomplete="off"
              autocapitalize="characters"
              spellcheck="false"
              placeholder=${t("tracking_placeholder")}
              required
              aria-invalid=${badField === "tracking_number" ? "true" : "false"}
              aria-describedby="detect-chip"
              @input=${this._onTrackingInput}
            />
          </label>
          <label class="field">
            <span>${t("carrier")}</span>
            ${this._carrierSelect({
              name: "carrier",
              selected: this._addCarrier,
              autoLabel,
              candidates: det?.result?.candidates || [],
              onChange: (e) => {
                this._addCarrier = e.target.value;
              },
            })}
          </label>
          <label class="field">
            <span>${t("description")}</span>
            <input name="description" autocomplete="off" />
          </label>
          <label class="field">
            <span>${t("vendor")}</span>
            <input name="vendor" autocomplete="off" />
          </label>
          <label class="field">
            <span>${t("expected_date")}</span>
            <input name="expected_date" type="date" />
          </label>
          <label class="field">
            <span>${t("expected_date_end")}</span>
            <input
              name="expected_date_end"
              type="date"
              aria-invalid=${badField === "expected_date_end" ? "true" : "false"}
            />
          </label>
          <label class="field notes">
            <span>${t("notes")}</span>
            <textarea name="notes" rows="1"></textarea>
          </label>
          <div class="field submit">
            <button type="submit" class="btn primary" ?disabled=${this._addBusy}>
              ${icon("plus")}<span>${this._addBusy ? t("adding") : t("add")}</span>
            </button>
          </div>
        </div>
        <div class="chip-row" id="detect-chip" aria-live="polite">${this._renderDetectChip(
          det,
          summary,
        )}</div>
        ${err ? html`<div class="form-error" role="alert">${err.message}</div>` : nothing}
      </form>
    `;
  }

  _renderDetectChip(det, summary) {
    if (!det) return nothing;
    if (det.loading && !summary) return html`<span class="chip muted">${t("detecting")}</span>`;
    if (!summary) return nothing;
    const parts = [];
    if (summary.state === "unknown") {
      parts.push(html`<span class="chip muted">${t("detect_unknown")}</span>`);
    } else {
      parts.push(html`<span class="chip">
        <strong>${summary.carrierName}</strong>${summary.service ? html` · ${summary.service}` : nothing}
        ${summary.checksum === "ok"
          ? html` · <span class="ok">${t("checksum_ok")}</span>`
          : summary.checksum === "bad"
            ? html` · <span class="warn-text">${t("checksum_bad")}</span>`
            : nothing}
      </span>`);
    }
    if (summary.state === "duplicate") {
      parts.push(html`<span class="chip warn">${t("duplicate")}</span>`);
    } else if (summary.state === "in_history") {
      parts.push(html`<span class="chip warn"
        >${t("in_history")}
        <button
          type="button"
          class="linkish"
          @click=${() => this._restoreFromDetect(summary.packageId)}
        >
          ${t("restore")}
        </button></span
      >`);
    }
    return parts;
  }

  _renderTable(rows, today) {
    return html`
      <div class="table-wrap">
        <table role="table" aria-label=${this._config.title || t("default_title")}>
          <thead role="rowgroup">
            <tr role="row">
              <th scope="col" role="columnheader" class="c-arrived">${t("col_arrived")}</th>
              <th scope="col" role="columnheader">${t("col_package")}</th>
              <th scope="col" role="columnheader">${t("col_carrier")}</th>
              <th scope="col" role="columnheader">${t("col_tracking")}</th>
              <th scope="col" role="columnheader">${t("col_expected")}</th>
              <th scope="col" role="columnheader" class="c-actions">${t("col_actions")}</th>
            </tr>
          </thead>
          <tbody role="rowgroup">
            ${repeat(
              rows,
              (p) => p.id,
              (p) => (this._editing.has(p.id) ? this._renderEditRow(p) : this._renderRow(p, today)),
            )}
          </tbody>
        </table>
      </div>
    `;
  }

  _renderRow(p, today) {
    const name = packageName(p);
    const pending = this._pending.has(p.id);
    const checked = pending ? this._pending.get(p.id) : p.status === "arrived";
    const busy = this._busy.has(p.id);
    const info = expectedInfo(p, today, this._locale);
    const confirming = this._confirmDelete === p.id;
    return html`
      <tr role="row" class="row status-${p.status}">
        <td role="cell" class="c-arrived" data-label=${t("col_arrived")}>
          <input
            type="checkbox"
            .checked=${live(checked)}
            ?disabled=${pending}
            aria-label=${checked
              ? t("unmark_arrived", { name })
              : t("mark_arrived", { name })}
            @change=${(e) => this._toggleArrived(p, e)}
          />
        </td>
        <td role="cell" class="c-package" data-label=${t("col_package")}>
          <div class="desc">${p.description || html`<span class="muted">${t("no_description")}</span>`}</div>
          ${p.vendor ? html`<div class="sub">${p.vendor}</div>` : nothing}
          ${p.notes
            ? html`<details class="notes">
                <summary>${t("notes")}</summary>
                <div class="notes-body">${p.notes}</div>
              </details>`
            : nothing}
        </td>
        <td role="cell" class="c-carrier" data-label=${t("col_carrier")}>
          <div>${p.carrier_name}</div>
          ${p.service ? html`<div class="sub">${p.service}</div>` : nothing}
        </td>
        <td role="cell" class="c-tracking" data-label=${t("col_tracking")}>
          ${this._renderTracking(p)}
        </td>
        <td role="cell" class="c-expected" data-label=${t("col_expected")}>
          ${this._renderExpected(p, info)}
        </td>
        <td role="cell" class="c-actions" data-label=${t("col_actions")}>
          ${confirming
            ? html`<div class="confirm" role="group" aria-label=${t("confirm_delete_label", { name })}>
                <button
                  type="button"
                  class="btn danger"
                  data-confirm=${p.id}
                  ?disabled=${busy}
                  aria-label=${t("confirm_delete_label", { name })}
                  @click=${() => this._delete(p)}
                >
                  ${t("confirm_delete")}
                </button>
                <button type="button" class="btn" @click=${() => this._cancelDelete(p)}>
                  ${t("cancel")}
                </button>
              </div>`
            : html`<div class="actions">
                <button
                  type="button"
                  class="icon-btn"
                  data-editbtn=${p.id}
                  ?disabled=${busy}
                  aria-label=${t("edit", { name })}
                  title=${t("edit", { name })}
                  @click=${() => this._startEdit(p)}
                >
                  ${icon("pencil")}
                </button>
                <button
                  type="button"
                  class="icon-btn"
                  ?disabled=${busy}
                  aria-label=${t("archive", { name })}
                  title=${t("archive", { name })}
                  @click=${() => this._archive(p)}
                >
                  ${icon("archive")}
                </button>
                <button
                  type="button"
                  class="icon-btn"
                  data-delete=${p.id}
                  ?disabled=${busy}
                  aria-label=${t("delete", { name })}
                  title=${t("delete", { name })}
                  @click=${() => this._askDelete(p)}
                >
                  ${icon("delete")}
                </button>
              </div>`}
        </td>
      </tr>
    `;
  }

  _renderTracking(p) {
    const tn = p.tracking_number;
    const fallbackName =
      this._snapshot?.fallback_tracker === "parcelsapp" ? "Parcelsapp" : "17TRACK";
    const number = p.tracking_url
      ? html`<a
          class="tn-link mono"
          href=${p.tracking_url}
          target=${linkAttrs.target}
          rel=${linkAttrs.rel}
          referrerpolicy=${linkAttrs.referrerpolicy}
          aria-label=${t("open_tracking", { tn })}
          >${tn}${icon("openInNew")}</a
        >`
      : html`<span class="mono">${tn}</span>`;
    return html`
      <div class="tn-line">
        ${number}
        <button
          type="button"
          class="icon-btn small"
          aria-label=${t("copy", { tn })}
          title=${t("copy", { tn })}
          @click=${(e) => this._copy(p, e)}
        >
          ${icon("copy")}
        </button>
      </div>
      ${this._manualCopy === p.id
        ? html`<div class="manual">
            <input
              class="manual-copy mono"
              readonly
              .value=${tn}
              aria-label=${t("copy_manual")}
              @focus=${(e) => e.target.select()}
            />
            <button
              type="button"
              class="icon-btn small"
              aria-label=${t("close")}
              @click=${() => {
                this._manualCopy = null;
              }}
            >
              ${icon("close")}
            </button>
            <div class="sub">${t("copy_manual")}</div>
          </div>`
        : nothing}
      ${p.copy_only
        ? html`<div class="sub links">
            <a
              href=${p.homepage_url}
              target=${linkAttrs.target}
              rel=${linkAttrs.rel}
              referrerpolicy=${linkAttrs.referrerpolicy}
              >${t("carrier_site")}</a
            >${p.fallback_url
              ? html` ·
                  <a
                    href=${p.fallback_url}
                    target=${linkAttrs.target}
                    rel=${linkAttrs.rel}
                    referrerpolicy=${linkAttrs.referrerpolicy}
                    >${fallbackName}</a
                  >`
              : nothing}
          </div>`
        : nothing}
    `;
  }

  _renderExpected(p, info) {
    if (p.status === "arrived") {
      const when = formatInstant(p.delivered_at, this._locale, this._serverTz);
      return html`<span class="badge arrived">${t("badge_arrived")}</span>
        ${when ? html`<div class="sub">${when}</div>` : nothing}`;
    }
    if (!info.date) return html`<span class="muted">${t("no_date")}</span>`;
    return html`
      ${p.status === "overdue"
        ? html`<span class="badge overdue">${t("badge_overdue")}</span>`
        : p.status === "due_today"
          ? html`<span class="badge due">${t("badge_due_today")}</span>`
          : nothing}
      <div class="date">${info.date}</div>
      ${info.relative ? html`<div class="sub">${info.relative}</div>` : nothing}
    `;
  }

  _renderEditRow(p) {
    const draft = this._editing.get(p.id);
    const busy = this._busy.has(p.id);
    const err = draft.error;
    const badField = errorField(err?.key);
    const fid = (k) => `edit-${p.id}-${k}`;
    const text = (key, labelKey, type = "text") => html`<label class="field">
      <span>${t(labelKey)}</span>
      <input
        type=${type}
        .value=${live(draft[key])}
        aria-invalid=${badField === key ? "true" : "false"}
        @input=${(e) => this._editField(p, key, e)}
      />
    </label>`;
    return html`
      <tr role="row" class="row editing status-${p.status}">
        <td role="cell" colspan="6" class="edit-cell">
          <form data-edit=${p.id} class="edit" @submit=${(e) => this._saveEdit(p, e)}>
            <div class="edit-head mono">${p.tracking_number}</div>
            <div class="add-grid">
              ${text("description", "description")} ${text("vendor", "vendor")}
              <label class="field">
                <span>${t("carrier")}</span>
                ${this._carrierSelect({
                  id: fid("carrier"),
                  selected: draft.carrier,
                  autoLabel: t("carrier_auto", { name: p.carrier_name }),
                  candidates: [],
                  onChange: (e) => this._editField(p, "carrier", e),
                })}
              </label>
              ${text("expected_date", "expected_date", "date")}
              ${text("expected_date_end", "expected_date_end", "date")}
              ${text("tracking_url", "tracking_url", "url")}
              <label class="field notes">
                <span>${t("notes")}</span>
                <textarea
                  rows="2"
                  .value=${live(draft.notes)}
                  @input=${(e) => this._editField(p, "notes", e)}
                ></textarea>
              </label>
            </div>
            ${err ? html`<div class="form-error" role="alert">${err.message}</div>` : nothing}
            <div class="edit-actions">
              <button type="button" class="btn" @click=${() => this._cancelEdit(p)}>
                ${t("cancel")}
              </button>
              <button type="submit" class="btn primary" ?disabled=${busy}>${t("save")}</button>
            </div>
          </form>
        </td>
      </tr>
    `;
  }

  _renderHistory() {
    const items = this._history;
    let body;
    if (this._historyError && !items) {
      body = html`<div class="state error" role="alert">${this._historyError}</div>`;
    } else if (!items) {
      body = html`<div class="state" role="status" aria-busy="true">${t("history_loading")}</div>`;
    } else if (!items.length) {
      body = html`<div class="state empty">${t("history_empty")}</div>`;
    } else {
      body = html`<ul class="history-list">
        ${repeat(
          items,
          (p) => p.id,
          (p) => {
            const name = packageName(p);
            const busy = this._busy.has(p.id);
            const when = formatInstant(p.archived_at, this._locale, this._serverTz);
            const confirming = this._confirmDelete === p.id;
            return html`<li class="history-item">
              <div class="h-main">
                <div class="desc">${name}</div>
                <div class="sub">
                  <span class="mono">${p.tracking_number}</span> · ${p.carrier_name}
                  ${p.vendor ? html` · ${p.vendor}` : nothing}
                </div>
                ${when ? html`<div class="sub">${t("archived_on", { date: when })}</div>` : nothing}
              </div>
              <div class="actions">
                ${confirming
                  ? html`<button
                        type="button"
                        class="btn danger"
                        data-confirm=${p.id}
                        ?disabled=${busy}
                        aria-label=${t("confirm_delete_label", { name })}
                        @click=${() => this._delete(p)}
                      >
                        ${t("confirm_delete")}
                      </button>
                      <button type="button" class="btn" @click=${() => this._cancelDelete(p)}>
                        ${t("cancel")}
                      </button>`
                  : html`<button
                        type="button"
                        class="icon-btn"
                        ?disabled=${busy}
                        aria-label=${t("restore_label", { name })}
                        title=${t("restore_label", { name })}
                        @click=${() => this._restore(p)}
                      >
                        ${icon("restore")}
                      </button>
                      <button
                        type="button"
                        class="icon-btn"
                        data-delete=${p.id}
                        ?disabled=${busy}
                        aria-label=${t("delete", { name })}
                        title=${t("delete", { name })}
                        @click=${() => this._askDelete(p)}
                      >
                        ${icon("delete")}
                      </button>`}
              </div>
            </li>`;
          },
        )}
      </ul>`;
    }
    return html`<section class="history" aria-labelledby="history-title">
      <h3 class="section-title" id="history-title">${t("history_title")}</h3>
      ${body}
    </section>`;
  }

  _renderPaste() {
    const st = this._paste;
    const selected = st.results.filter((r) => r.checked && r.outcome !== "added").length;
    return html`
      <dialog
        class="paste"
        aria-labelledby="paste-title"
        @close=${this._onPasteClosed}
      >
        <div class="dialog-head">
          <h2 id="paste-title">${t("paste_title")}</h2>
          <button
            type="button"
            class="icon-btn"
            aria-label=${t("close")}
            @click=${this._closePaste}
          >
            ${icon("close")}
          </button>
        </div>
        ${st.stage === "input"
          ? html`<form @submit=${this._scanPaste}>
              <p class="sub">${t("paste_hint")}</p>
              <label class="field">
                <span>${t("paste_text")}</span>
                <textarea
                  rows="8"
                  maxlength="100000"
                  .value=${live(st.text)}
                  @input=${(e) => {
                    this._paste.text = e.target.value;
                  }}
                ></textarea>
              </label>
              ${st.error ? html`<div class="form-error" role="alert">${st.error}</div>` : nothing}
              <div class="edit-actions">
                <button type="button" class="btn" @click=${this._closePaste}>${t("cancel")}</button>
                <button type="submit" class="btn primary" ?disabled=${st.busy}>
                  ${st.busy ? t("scanning") : t("scan")}
                </button>
              </div>
            </form>`
          : this._renderPasteResults(st, selected)}
      </dialog>
    `;
  }

  _renderPasteResults(st, selected) {
    if (!st.results.length) {
      return html`<p class="state" role="status">${t("paste_none")}</p>
        <div class="edit-actions">
          <button type="button" class="btn" @click=${() => this._updatePaste({ stage: "input" })}>
            ${t("back")}
          </button>
          <button type="button" class="btn primary" @click=${this._closePaste}>${t("close")}</button>
        </div>`;
    }
    const shared = (key, labelKey, type = "text") => html`<label class="field">
      <span>${t(labelKey)}</span>
      <input
        type=${type}
        .value=${live(st.shared[key])}
        @input=${(e) => {
          this._paste.shared = { ...this._paste.shared, [key]: e.target.value };
        }}
      />
    </label>`;
    return html`<form @submit=${this._addPasted}>
      <p class="sub" role="status">${t("paste_found", { n: st.results.length })}</p>
      <ul class="paste-list">
        ${st.results.map(
          (r, i) => html`<li class="paste-item ${r.outcome ?? ""}">
            <input
              type="checkbox"
              id="paste-${i}"
              .checked=${live(r.checked)}
              ?disabled=${r.outcome === "added" || st.busy}
              @change=${(e) => this._updatePasteRow(i, { checked: e.target.checked })}
            />
            <div class="p-main">
              <label for="paste-${i}" class="mono">${r.tracking_number}</label>
              <div class="sub">
                ${r.carrier_name}${r.service ? html` · ${r.service}` : nothing} ·
                ${t("paste_confidence", { pct: Math.round((r.confidence ?? 0) * 100) })}
                ${r.already_tracked
                  ? html` · <span class="warn-text">${t("paste_already")}</span>`
                  : nothing}
              </div>
              ${r.context ? html`<div class="sub ctx">${r.context}</div>` : nothing}
              ${r.outcome === "added"
                ? html`<div class="ok">${t("paste_added")}</div>`
                : r.outcome === "error"
                  ? html`<div class="form-error" role="alert">${r.message}</div>`
                  : nothing}
            </div>
            ${(r.candidates?.length ?? 0) > 1 && r.outcome !== "added"
              ? html`<select
                  aria-label=${t("carrier")}
                  @change=${(e) => this._updatePasteRow(i, { selectedCarrier: e.target.value })}
                >
                  ${r.candidates.map(
                    (c) =>
                      html`<option value=${c.carrier} ?selected=${c.carrier === r.selectedCarrier}>
                        ${c.carrier_name}
                      </option>`,
                  )}
                </select>`
              : nothing}
          </li>`,
        )}
      </ul>
      <fieldset class="shared">
        <legend>${t("paste_shared")}</legend>
        <div class="add-grid">
          ${shared("description", "description")} ${shared("vendor", "vendor")}
          ${shared("expected_date", "expected_date", "date")}
        </div>
      </fieldset>
      ${st.error ? html`<div class="form-error" role="alert">${st.error}</div>` : nothing}
      <div class="edit-actions">
        <button type="button" class="btn" @click=${() => this._updatePaste({ stage: "input" })}>
          ${t("back")}
        </button>
        <button type="submit" class="btn primary" ?disabled=${st.busy || !selected}>
          ${t("add_selected", { n: selected })}
        </button>
      </div>
    </form>`;
  }

  static styles = css`
    :host {
      display: block;
      --pt-error: var(--error-color, #db4437);
      --pt-warning: var(--warning-color, #ffa600);
      --pt-success: var(--success-color, #43a047);
      --pt-divider: var(--divider-color, rgba(127, 127, 127, 0.3));
      --pt-text: var(--primary-text-color, #212121);
      --pt-muted: var(--secondary-text-color, #727272);
      --pt-primary: var(--primary-color, #03a9f4);
      --pt-input-bg: var(--card-background-color, var(--ha-card-background, transparent));
      --pt-radius: 8px;
    }
    ha-card {
      overflow: hidden;
    }
    .wrap {
      container-type: inline-size;
      container-name: pt;
      padding: 12px 16px 16px;
      color: var(--pt-text);
      /* break-word (not anywhere) so short words like carrier names keep their
         min-content width; long tracking numbers opt in to "anywhere" below. */
      overflow-wrap: break-word;
    }
    .visually-hidden {
      position: absolute;
      width: 1px;
      height: 1px;
      overflow: hidden;
      clip-path: inset(50%);
      white-space: nowrap;
    }
    .header {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 8px;
    }
    .title {
      margin: 0;
      font-size: var(--ha-card-header-font-size, 1.5rem);
      font-weight: 400;
      line-height: 1.3;
    }
    .counts,
    .sub,
    .muted {
      color: var(--pt-muted);
      font-size: 0.875rem;
    }
    .toolbar,
    .actions,
    .confirm,
    .edit-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      align-items: center;
    }
    .edit-actions {
      justify-content: flex-end;
      gap: 8px;
      margin-top: 8px;
    }
    .icon {
      width: 20px;
      height: 20px;
      fill: currentColor;
      flex: none;
    }
    .btn {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      min-height: 36px;
      padding: 0 12px;
      border-radius: var(--pt-radius);
      border: 1px solid var(--pt-divider);
      background: transparent;
      color: var(--pt-primary);
      font: inherit;
      font-size: 0.875rem;
      font-weight: 500;
      cursor: pointer;
    }
    .btn.primary {
      background: var(--pt-primary);
      border-color: var(--pt-primary);
      color: var(--text-primary-color, #fff);
    }
    .btn.danger {
      background: var(--pt-error);
      border-color: var(--pt-error);
      color: var(--text-primary-color, #fff);
    }
    .btn[aria-pressed="true"] {
      background: color-mix(in srgb, var(--pt-primary) 14%, transparent);
    }
    .btn:disabled,
    .icon-btn:disabled {
      opacity: 0.5;
      cursor: default;
    }
    .icon-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 36px;
      height: 36px;
      padding: 0;
      border: none;
      border-radius: 50%;
      background: transparent;
      color: var(--pt-muted);
      cursor: pointer;
    }
    .icon-btn.small {
      width: 30px;
      height: 30px;
    }
    .icon-btn.small .icon {
      width: 18px;
      height: 18px;
    }
    .icon-btn:hover:not(:disabled),
    .btn:hover:not(:disabled) {
      background: color-mix(in srgb, currentColor 10%, transparent);
    }
    .btn.primary:hover:not(:disabled) {
      background: var(--pt-primary);
      filter: brightness(1.08);
    }
    .btn.danger:hover:not(:disabled) {
      background: var(--pt-error);
      filter: brightness(1.08);
    }
    :is(button, a, input, select, textarea, summary):focus-visible {
      outline: 2px solid var(--pt-primary);
      outline-offset: 2px;
    }
    .linkish {
      border: none;
      background: none;
      padding: 0 0 0 6px;
      color: var(--pt-primary);
      font: inherit;
      text-decoration: underline;
      cursor: pointer;
    }
    a {
      color: var(--pt-primary);
    }
    .mono {
      font-family: var(--code-font-family, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace);
      font-size: 0.875rem;
    }

    /* native form controls: theme vars + color-scheme keep them readable in dark mode */
    input:not([type="checkbox"]),
    select,
    textarea {
      box-sizing: border-box;
      width: 100%;
      min-height: 36px;
      padding: 6px 8px;
      border: 1px solid var(--pt-divider);
      border-radius: var(--pt-radius);
      background: var(--pt-input-bg);
      color: var(--pt-text);
      font: inherit;
      font-size: 0.9375rem;
    }
    select option,
    select optgroup {
      background: var(--card-background-color, Canvas);
      color: var(--pt-text);
    }
    textarea {
      resize: vertical;
    }
    input[aria-invalid="true"] {
      border-color: var(--pt-error);
    }
    input[type="checkbox"] {
      width: 20px;
      height: 20px;
      margin: 0;
      accent-color: var(--pt-primary);
      cursor: pointer;
    }
    .field {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
    }
    .field > span {
      font-size: 0.75rem;
      color: var(--pt-muted);
    }

    /* add form */
    form.add {
      margin: 0 0 12px;
      padding: 12px;
      border: 1px solid var(--pt-divider);
      border-radius: var(--pt-radius);
    }
    .add-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
      gap: 8px;
      align-items: end;
    }
    .add-grid .tn {
      grid-column: span 2;
    }
    .add-grid .notes {
      grid-column: span 2;
    }
    .add-grid .submit {
      justify-content: flex-end;
    }
    .add-grid .submit .btn {
      justify-content: center;
    }
    .chip-row {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .chip-row:not(:empty) {
      margin-top: 8px;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 2px;
      padding: 4px 10px;
      border-radius: 16px;
      font-size: 0.8125rem;
      background: var(--secondary-background-color, rgba(127, 127, 127, 0.12));
      color: var(--pt-text);
    }
    .chip.muted {
      color: var(--pt-muted);
    }
    .chip.warn {
      background: color-mix(in srgb, var(--pt-warning) 22%, transparent);
      border: 1px solid var(--pt-warning);
    }
    .ok {
      color: var(--pt-success);
    }
    .warn-text {
      color: var(--pt-error);
      font-weight: 500;
    }
    .form-error {
      margin-top: 8px;
      color: var(--pt-error);
      font-size: 0.875rem;
    }

    /* table */
    .table-wrap {
      width: 100%;
    }
    table {
      width: 100%;
      border-collapse: collapse;
    }
    th {
      text-align: start;
      font-size: 0.75rem;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--pt-muted);
      padding: 6px 8px;
      border-bottom: 1px solid var(--pt-divider);
      white-space: nowrap;
    }
    td {
      padding: 8px;
      vertical-align: top;
      border-bottom: 1px solid var(--pt-divider);
    }
    tr.row > td:first-child {
      border-left: 4px solid transparent;
    }
    .c-arrived {
      width: 1%;
      text-align: center;
    }
    td.c-arrived {
      padding-top: 10px;
    }
    .c-actions {
      width: 1%;
      white-space: nowrap;
    }
    th.c-actions {
      text-align: end;
    }
    td.c-actions .actions,
    td.c-actions .confirm {
      flex-wrap: nowrap;
      justify-content: flex-end;
    }
    /* Status is shown by colour AND by a text badge. border-left is the fallback
       for browsers without color-mix(). */
    tr.status-overdue > td:first-child {
      border-left-color: var(--pt-error);
    }
    tr.status-overdue {
      background: color-mix(in srgb, var(--pt-error) 14%, transparent);
    }
    tr.status-due_today > td:first-child {
      border-left-color: var(--pt-warning);
    }
    tr.status-due_today {
      background: color-mix(in srgb, var(--pt-warning) 14%, transparent);
    }
    tr.status-arrived > td:not(.c-actions):not(.c-arrived) {
      opacity: 0.65;
    }
    tr.editing {
      background: var(--secondary-background-color, rgba(127, 127, 127, 0.08));
    }
    .desc {
      font-weight: 500;
    }
    .badge {
      display: inline-block;
      padding: 1px 8px;
      border-radius: 10px;
      font-size: 0.75rem;
      font-weight: 600;
      line-height: 1.5;
      border: 1px solid currentColor;
    }
    .badge.overdue {
      color: var(--text-primary-color, #fff);
      background: var(--pt-error);
      border-color: var(--pt-error);
    }
    .badge.due {
      color: var(--pt-text);
      background: color-mix(in srgb, var(--pt-warning) 45%, transparent);
      border-color: var(--pt-warning);
    }
    .badge.arrived {
      color: var(--pt-success);
    }
    .date {
      white-space: nowrap;
    }
    .tn-line {
      display: flex;
      align-items: center;
      gap: 2px;
    }
    .tn-link {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      text-decoration: none;
      min-width: 0;
    }
    .c-tracking .mono {
      white-space: nowrap;
    }
    .tn-link:hover {
      text-decoration: underline;
    }
    .tn-link .icon {
      width: 14px;
      height: 14px;
    }
    .links a {
      white-space: nowrap;
    }
    .manual {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 4px;
      margin-top: 4px;
    }
    .manual input {
      flex: 1 1 140px;
      width: auto;
    }
    .manual .sub {
      flex-basis: 100%;
    }
    details.notes summary {
      cursor: pointer;
      color: var(--pt-primary);
      font-size: 0.8125rem;
    }
    .notes-body {
      white-space: pre-wrap;
      font-size: 0.875rem;
      margin-top: 4px;
    }
    .edit-head {
      margin-bottom: 8px;
      font-weight: 600;
    }
    .state {
      padding: 24px 8px;
      text-align: center;
      color: var(--pt-muted);
    }
    .state p {
      margin: 0 0 8px;
    }
    .state-title {
      font-weight: 500;
      color: var(--pt-text);
    }
    .state.error {
      color: var(--pt-error);
    }

    /* history */
    .history {
      margin-top: 16px;
    }
    .section-title {
      margin: 0 0 4px;
      font-size: 1rem;
      font-weight: 500;
    }
    .history-list,
    .paste-list {
      list-style: none;
      margin: 0;
      padding: 0;
    }
    .history-item {
      display: flex;
      gap: 8px;
      align-items: center;
      justify-content: space-between;
      padding: 8px 0;
      border-bottom: 1px solid var(--pt-divider);
    }
    .h-main {
      min-width: 0;
    }

    /* paste dialog */
    dialog.paste {
      width: min(640px, calc(100vw - 32px));
      max-height: calc(100vh - 48px);
      box-sizing: border-box;
      padding: 16px;
      border: 1px solid var(--pt-divider);
      border-radius: 12px;
      background: var(--card-background-color, var(--ha-card-background, Canvas));
      color: var(--pt-text);
      overflow-wrap: anywhere;
    }
    dialog.paste::backdrop {
      background: rgba(0, 0, 0, 0.5);
    }
    .dialog-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .dialog-head h2 {
      margin: 0;
      font-size: 1.25rem;
      font-weight: 500;
    }
    .paste-item {
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 8px;
      align-items: start;
      padding: 8px 0;
      border-bottom: 1px solid var(--pt-divider);
    }
    .paste-item input[type="checkbox"] {
      margin-top: 2px;
    }
    .paste-item select {
      width: auto;
      max-width: 180px;
    }
    .paste-item.added {
      opacity: 0.7;
    }
    .ctx {
      font-style: italic;
    }
    .paste-item .mono,
    .history-item .mono,
    .manual-copy {
      overflow-wrap: anywhere;
    }
    fieldset.shared {
      margin: 12px 0 0;
      padding: 8px 12px 12px;
      border: 1px solid var(--pt-divider);
      border-radius: var(--pt-radius);
    }
    fieldset.shared legend {
      font-size: 0.75rem;
      color: var(--pt-muted);
      padding: 0 4px;
    }

    /* Narrow: stack each row as a block. Explicit ARIA roles on the table keep its
       semantics when display:table is overridden. */
    @container pt (max-width: 600px) {
      table,
      thead,
      tbody,
      tr,
      td {
        display: block;
      }
      thead {
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
        clip-path: inset(50%);
      }
      tr.row {
        position: relative;
        padding: 8px 8px 8px 44px;
        border-bottom: 1px solid var(--pt-divider);
        border-left: 4px solid transparent;
      }
      tr.status-overdue {
        border-left-color: var(--pt-error);
      }
      tr.status-due_today {
        border-left-color: var(--pt-warning);
      }
      tr.row > td,
      tr.row > td:first-child {
        border: none;
        padding: 2px 0;
        width: auto;
        text-align: start;
      }
      tr.row > td.c-arrived {
        position: absolute;
        left: 8px;
        top: 10px;
        padding: 0;
      }
      tr.row > td.c-carrier,
      tr.row > td.c-expected {
        display: inline-block;
        vertical-align: top;
        margin-right: 16px;
      }
      tr.row > td.c-actions .actions,
      tr.row > td.c-actions .confirm {
        justify-content: flex-start;
      }
      tr.row.editing {
        padding-left: 8px;
      }
      .c-tracking .mono {
        white-space: normal;
        overflow-wrap: anywhere;
      }
      .add-grid {
        grid-template-columns: 1fr 1fr;
      }
      .add-grid .tn,
      .add-grid .notes,
      .add-grid .submit {
        grid-column: 1 / -1;
      }
      .add-grid .submit .btn {
        width: 100%;
      }
      .paste-item {
        grid-template-columns: auto 1fr;
      }
      .paste-item select {
        grid-column: 2;
        max-width: none;
        width: 100%;
      }
    }
  `;
}

if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, PackageTrackerCard);
  (window.customCards ??= []).push({
    type: CARD_TAG,
    name: "Package Tracker",
    description: "Tracked packages with carrier links, expected dates and overdue highlighting.",
    preview: false,
    documentationURL: "https://github.com/your-github-user/HAPackageTracker",
  });
}

export { PackageTrackerCard, VERSION };

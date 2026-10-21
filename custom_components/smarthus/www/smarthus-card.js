/*
 * SmartHus-kort til Home Assistant
 * Følger med SmartHus-integrationen og registreres automatisk.
 * Brug:  type: custom:smarthus-card
 */
const SMARTHUS_CARD_VERSION = "1.20.3";

const COLORS = {
  cheap: "#3FA88F",
  mid: "#8C97A4",
  expensive: "#C2416B",
  ev: "#3E6FD8",
  ev2: "#1B3F8F",
  ev3: "#7FA6F0",
  battery_charge: "#3FA88F",
  battery_discharge: "#8A5CC8",
  hotwater: "#2BA3C9",
  heating_boost: "#E0902F",
  heating_reduce: "#7B8794",
  solar_self: "#3FA88F",
  solar_sold: "#E0A21B",
};

// Vejrforhold fra Home Assistants vejr-entiteter
const WEATHER_DA = {
  "clear-night": "Klart", cloudy: "Overskyet", exceptional: "Usædvanligt vejr", fog: "Tåge", hail: "Hagl",
  lightning: "Torden", "lightning-rainy": "Torden og regn", partlycloudy: "Let skyet", pouring: "Skybrud",
  rainy: "Regn", snowy: "Sne", "snowy-rainy": "Slud", sunny: "Sol", windy: "Blæsende", "windy-variant": "Blæst og skyer",
};

// Kategorier i SmartHus-besparelsen (solceller vises for sig)
const SAVING_PARTS = [
  { key: "ev", label: "Elbil", icon: "mdi:car-electric" },
  { key: "battery", label: "Batteri", icon: "mdi:home-battery" },
  { key: "heatpump", label: "Varme", icon: "mdi:heat-pump" },
  { key: "hotwater", label: "Varmt vand", icon: "mdi:water-boiler" },
];

const SOLAR_TABS = [
  { id: "today", label: "I dag" },
  { id: "month", label: "Måned" },
  { id: "year", label: "År" },
  { id: "total", label: "I alt" },
];

const MONTHS = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec"];

const LANES = [
  { key: "battery_charge_now", label: "Batteri lader", color: COLORS.battery_charge, module: "control_battery" },
  { key: "battery_discharge_now", label: "Batteri aflader", color: COLORS.battery_discharge, module: "control_battery" },
  { key: "hotwater_now", label: "Varmt vand", color: COLORS.hotwater, module: "control_hotwater" },
  { key: "heating_boost_now", label: "Ekstra varme", color: COLORS.heating_boost, module: "control_heating" },
  { key: "heating_reduce_now", label: "Sparer på varmen", color: COLORS.heating_reduce, module: "control_heating" },
];

// Indstillinger pr. modul, i grupper. ["a_from","a_to"] vises som ét tidsrum.
const MODULES = [
  {
    id: "battery", name: "Batteri", icon: "mdi:home-battery", sw: "control_battery",
    groups: [
      { title: "Opladning fra nettet", items: ["battery_charge_hours", ["battery_charge_from", "battery_charge_to"]] },
      { title: "Brug af batteriet", items: ["battery_discharge_hours", ["battery_discharge_from", "battery_discharge_to"]] },
      { title: "Grænser", items: ["battery_min_spread", "battery_min_soc", "battery_max_soc", "battery_capacity"] },
      { title: "Solstrøm: sælg eller gem", items: ["battery_solar_sell_margin"] },
    ],
  },
  {
    id: "hotwater", name: "Varmt vand", icon: "mdi:water-boiler", sw: "control_hotwater",
    groups: [
      { title: null, items: ["hotwater_hours", ["hotwater_from", "hotwater_to"]] },
      { title: "Solcelleoverskud", items: ["hotwater_surplus_kw", { sw: "hotwater_surplus_smart", label: "Kun når det betaler sig",
        note: "Sælg i stedet, hvis salgsprisen er højere end den billige time senere" }] },
      { title: "Temperatur", items: ["hotwater_boost_temp", "hotwater_normal_temp"] },
    ],
  },
  {
    id: "heating", name: "Varme", icon: "mdi:heat-pump", sw: "control_heating",
    groups: [
      { title: null, items: ["heating_base_temp", "heating_max_outdoor"] },
      { title: "Ekstra varme i billige timer", items: ["heating_boost_degrees", "heating_boost_hours", ["heating_boost_from", "heating_boost_to"]] },
      { title: "Spar i dyre timer", items: ["heating_reduce_degrees", "heating_reduce_hours", ["heating_reduce_from", "heating_reduce_to"]] },
    ],
  },
  { id: "solar", name: "Solceller", icon: "mdi:solar-power", sw: "control_solar",
    groups: [{ title: null, items: ["solar_export_min_price"] }] },
];

const CAR_COLORS = [COLORS.ev, COLORS.ev2, COLORS.ev3];

/** Et felt pr. elbil. Bilens id er fx "ev" eller "ev_tesla". */
function carModule(cid, name, first) {
  const groups = [
    { title: null, items: [`${cid}_target_soc`, `${cid}_ready_hour`, `${cid}_hours`] },
    { title: "Bil og lader", items: [`${cid}_battery_capacity`, `${cid}_charge_power`] },
    { title: "Solcelleladning", items: [{ sw: `${cid}_solar`, label: "Lad med solcelleoverskud",
      note: "Ladestrømmen følger overskuddet, 1 A ad gangen" }, `${cid}_solar_start_kw`, `${cid}_solar_min_a`, `${cid}_solar_max_a`] },
  ];
  if (first) groups.push({ title: "Alle elbiler", items: ["ev_max_total_power"] });
  return { id: cid, car: true, name, icon: "mdi:car-electric", sw: `control_${cid}`, groups };
}

const EV_LABELS = {
  solar_start_kw: "Start ved sol over",
  solar_min_a: "Mindste ladestrøm",
  solar_max_a: "Største ladestrøm",
  target_soc: "Lad til",
  ready_hour: "Klar kl.",
  charge_power: "Ladeeffekt",
  battery_capacity: "Batteristørrelse",
  hours: "Timer at lade",
};

const NUMBER_LABELS = {
  hotwater_surplus_kw: "Overskud over",
  solar_export_min_price: "Stop eksport under",
  ev_max_total_power: "Maks. samlet ladeeffekt",
  ev_target_soc: "Lad til",
  ev_ready_hour: "Klar kl.",
  ev_charge_power: "Ladeeffekt",
  ev_battery_capacity: "Batteristørrelse",
  ev_hours: "Timer at lade",
  battery_charge_hours: "Timer at lade",
  battery_discharge_hours: "Timer at aflade",
  battery_min_spread: "Min. prisforskel",
  battery_min_soc: "Laveste niveau",
  battery_max_soc: "Højeste niveau",
  battery_capacity: "Batteriets størrelse",
  battery_solar_sell_margin: "Sælg når gevinsten er mindst",
  hotwater_hours: "Timer at opvarme",
  hotwater_boost_temp: "Opvarm til",
  hotwater_normal_temp: "Normal temperatur",
  heating_base_temp: "Grundtemperatur",
  heating_max_outdoor: "Aktiv under udetemperatur",
  heating_boost_degrees: "Hæv med",
  heating_boost_hours: "Timer at hæve",
  heating_reduce_degrees: "Sænk med",
  heating_reduce_hours: "Timer at sænke",
};

const nf = (v, d = 2) =>
  v === null || v === undefined || isNaN(v)
    ? "–"
    : Number(v).toLocaleString("da-DK", { minimumFractionDigits: d, maximumFractionDigits: d });

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const hh = (d) => `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;

/** Saml 15-minutters tidsrum til kommende perioder, fx "i morgen 02:00–03:45". */
function upcoming(slots) {
  const now = Date.now();
  const times = (slots || []).map((s) => new Date(s).getTime()).sort((a, b) => a - b);
  const out = [];
  for (const t of times) {
    const last = out[out.length - 1];
    if (last && t === last.end) last.end = t + 900e3;
    else out.push({ start: t, end: t + 900e3 });
  }
  const today = new Date().toDateString();
  return out
    .filter((p) => p.end > now)
    .map((p) => {
      const a = new Date(p.start), b = new Date(p.end);
      const day = a.toDateString() === today ? "" : "i morgen ";
      return { text: `${day}${hh(a)}–${hh(b)}`, active: p.start <= now };
    });
}

class SmartHusCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._closed = new Set(); // alle felter er foldet ud fra start
    this._panel = null;       // åbent indstillingspanel (null = overblikket)
    this._sel = null;
    this._solarSel = null;
    this._solarTab = "month";
    try {
      this._solarTab = localStorage.getItem("smarthus-card-solar-tab") || "month";
    } catch (e) { /* ingen lagring */ }
    this.shadowRoot.addEventListener("click", (ev) => this._onClick(ev));
  }

  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = { prefix: "smarthus", title: "SmartHus", logo: "/smarthus_static/smarthus-mark.png", ...(config || {}) };
    this._sig = null;
  }

  getCardSize() {
    return 9;
  }

  getGridOptions() {
    return { columns: "full", min_columns: 6 };
  }

  set hass(hass) {
    this._hass = hass;
    const sig = this._signature();
    if (sig !== this._sig) {
      this._sig = sig;
      this._render();
    }
  }

  // --- Hjælpere ---------------------------------------------------------
  _id(domain, key) {
    return `${domain}.${this._config.prefix}_${key}`;
  }
  _st(domain, key) {
    return this._hass?.states[this._id(domain, key)];
  }
  _num(domain, key) {
    const s = this._st(domain, key);
    if (!s || s.state === "unknown" || s.state === "unavailable") return null;
    const v = parseFloat(s.state);
    return isNaN(v) ? null : v;
  }
  _cars() {
    if (!this._hass) return [];
    const pre = `switch.${this._config.prefix}_control_`;
    return Object.keys(this._hass.states)
      .filter((id) => id.startsWith(pre) && /^ev(_|$)/.test(id.slice(pre.length)))
      .map((id) => id.slice(pre.length))
      .sort((a, b) => (a === "ev" ? -1 : b === "ev" ? 1 : a.localeCompare(b)))
      .map((cid, i) => ({
        cid,
        name: this._st("sensor", `${cid}_smart_saving`)?.attributes?.car_name || "Elbil",
        color: CAR_COLORS[i % CAR_COLORS.length],
        connected: this._st("binary_sensor", `${cid}_charge_now`)?.attributes?.connected,
      }));
  }
  /** Biler i overblikket: en bil, der ikke sidder i laderen, skjules – medmindre ingen er tilsluttet. */
  _visibleCars(list) {
    const shown = list.filter((c) => c.connected !== false);
    return shown.length ? shown : list;
  }
  _forceKey(cid) {
    return cid === "ev" ? "ev_force" : `${cid}_force`;
  }
  _modules() {
    const all = this._cars();
    const cars = this._visibleCars(all).map((c) => carModule(c.cid, c.name, c.cid === all[0]?.cid));
    return [...cars, ...MODULES].filter((m) => this._st("switch", m.sw));
  }
  _label(key) {
    if (key === "hotwater_boost_temp" && !this._st("number", "hotwater_normal_temp")) return "Maks. temperatur";
    if (NUMBER_LABELS[key]) return NUMBER_LABELS[key];
    const suffix = Object.keys(EV_LABELS).find((k) => key.endsWith(`_${k}`));
    return suffix ? EV_LABELS[suffix] : this._st("number", key)?.attributes.friendly_name || key;
  }

  _signature() {
    if (!this._hass) return "";
    const tag = `.${this._config.prefix}_`;
    const parts = [];
    for (const [id, s] of Object.entries(this._hass.states)) {
      if (id.includes(tag)) parts.push(id, s.last_updated);
    }
    return parts.join("|") + `|${this._hass.themes?.darkMode}`;
  }

  // --- Prisdata ---------------------------------------------------------
  _hours() {
    const s = this._st("sensor", "current_price");
    if (!s) return [];
    const all = [...(s.attributes.today || []), ...(s.attributes.tomorrow || [])];
    const map = new Map();
    for (const p of all) {
      const t = new Date(p.start);
      const h = new Date(t);
      h.setMinutes(0, 0, 0);
      const k = h.getTime();
      if (!map.has(k)) map.set(k, { start: h, prices: [] });
      map.get(k).prices.push(p.price);
    }
    return [...map.values()]
      .sort((a, b) => a.start - b.start)
      .map((h) => ({ start: h.start, price: h.prices.reduce((x, y) => x + y, 0) / h.prices.length }));
  }

  // --- Render -----------------------------------------------------------
  _render() {
    if (!this._hass || !this._config) return;
    const priceState = this._st("sensor", "current_price");
    const html = !priceState
      ? `<div class="empty">Kunne ikke finde SmartHus-integrationen. Tjek at den er installeret, eller angiv <code>prefix</code> i kortets opsætning.</div>`
      : `${(() => { const scene = this._renderScene(), solar = this._renderSolar();
          return `<div class="top${scene ? "" : " no-scene"}${solar ? "" : " no-solar"}">${scene}${this._renderHeader()}${solar}${this._renderStrip()}</div>`; })()}
        ${this._renderDock()}
        <div class="ver">SmartHus v${SMARTHUS_CARD_VERSION}</div>`;
    this._update(html, priceState && this._hass.themes?.darkMode ? "dark" : "");
  }

  /**
   * Opdatér kortet uden at siden hopper: stilarket og ha-card bevares, højden holdes under
   * udskiftningen, og rullepositionen i dashboardet sættes tilbage bagefter.
   */
  _update(html, cls) {
    let card = this.shadowRoot.querySelector("ha-card");
    if (!card) {
      this.shadowRoot.innerHTML = `${this._css()}<ha-card></ha-card>`;
      card = this.shadowRoot.querySelector("ha-card");
    }
    const scrollers = this._scrollers();
    const h = card.offsetHeight;
    if (h) card.style.minHeight = `${h}px`;
    card.className = cls;
    card.innerHTML = html;
    const restore = () => scrollers.forEach(([el, top]) => { if (Math.abs(el.scrollTop - top) > 1) el.scrollTop = top; });
    restore();
    requestAnimationFrame(() => {
      card.style.minHeight = "";
      restore();
    });
  }

  /** Alle elementer over kortet, der er rullet (også gennem Home Assistants shadow DOM). */
  _scrollers() {
    const out = [];
    let n = this;
    while (n) {
      if (n.nodeType === 1 && n.scrollTop > 0) out.push([n, n.scrollTop]);
      n = n.parentNode || n.host;
    }
    const root = document.scrollingElement;
    if (root && root.scrollTop > 0 && !out.some(([el]) => el === root)) out.push([root, root.scrollTop]);
    return out;
  }

  /** Klokken og datoen øverst på kortet. */
  _clockHtml() {
    const d = new Date();
    const time = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1);
    const long = cap(d.toLocaleDateString("da-DK", { weekday: "long", day: "numeric", month: "long" }));
    const short = cap(d.toLocaleDateString("da-DK", { weekday: "short", day: "numeric", month: "short" }));
    return `<span class="clock-time">${time}</span><span class="clock-date long">${esc(long)}</span><span class="clock-date short">${esc(short)}</span>`;
  }

  // Klokken tikker selv – uden at hele kortet tegnes forfra
  connectedCallback() {
    clearInterval(this._clockTimer);
    this._clockTimer = setInterval(() => {
      const el = this.shadowRoot.querySelector(".clock");
      if (el) {
        const html = this._clockHtml();
        if (el.innerHTML !== html) el.innerHTML = html;
      }
    }, 5000);
  }

  disconnectedCallback() {
    clearInterval(this._clockTimer);
  }

  _renderHeader() {
    const price = this._num("sensor", "current_price");
    const sale = this._num("sensor", "sale_price");
    const cheap = this._st("binary_sensor", "cheap_now")?.state === "on";
    const exp = this._st("binary_sensor", "expensive_now")?.state === "on";
    const level = cheap ? "cheap" : exp ? "expensive" : "mid";
    const levelText = cheap ? "Billig strøm lige nu" : exp ? "Dyr strøm lige nu" : "Almindelig pris lige nu";
    const status = this._st("sensor", "status")?.state || "";
    const master = this._st("switch", "control_master");
    const masterOn = master?.state === "on";
    const sv = (k) => this._num("sensor", k);
    return `
      <div class="head">
        <div class="title-row">
          <div class="brand">
            ${this._config.logo ? `<img class="logo" src="${esc(this._config.logo)}" alt="" onerror="this.remove()">` : ""}
            <div class="title">${esc(this._config.title)}</div>
          </div>
          <div class="title-end">
            ${this._config.clock === false ? "" : `<div class="clock">${this._clockHtml()}</div>`}
            ${master ? this._toggle(master.entity_id, masterOn, masterOn ? "Styring til" : "Styring fra") : ""}
          </div>
        </div>
        <div class="now level-${level}">
          <div class="price"><span class="value">${nf(price)}</span><span class="unit">kr/kWh</span></div>
          <div class="now-meta">
            <div class="level-text">${levelText}</div>
            <div class="sub">Salgspris ${nf(sale)} kr/kWh${this._st("sensor", "current_price")?.attributes?.price_source === "Strømligning" ? "<br>Pris fra Strømligning" : ""}</div>
          </div>
        </div>
        <div class="status" data-action="more" data-entity="${this._id("sensor", "status")}">${esc(status)}</div>
        <div class="sec-title">SmartHus-styringen har sparet dig</div>
        <div class="savings">
          ${this._saving("I dag", sv("savings_today"), "savings_today")}
          ${this._saving("Denne måned", sv("savings_month"), "savings_month")}
          ${this._saving("I alt", sv("savings_total"), "savings_total")}
        </div>
        ${this._savingParts()}
      </div>`;
  }

  /** Fordeling af månedens SmartHus-besparelse på elbil, batteri, varme og varmt vand. */
  _savingParts() {
    const month = this._st("sensor", "savings_month")?.attributes || {};
    const parts = SAVING_PARTS.filter((p) => this._st("sensor", `savings_month_${p.key}`) && month[p.key] != null)
      .map((p) => ({ ...p, v: month[p.key] }))
      .filter((p) => Math.abs(p.v) >= 0.005 || this._partConfigured(p.key));
    if (!parts.length) return "";
    return `<div class="parts" aria-label="Denne måned fordelt">
      ${parts.map((p) => `<button class="part" data-action="more" data-entity="${this._id("sensor", `savings_month_${p.key}`)}">
        <ha-icon icon="${p.icon}"></ha-icon><span class="p-lbl">${p.label}</span><span class="p-val ${p.v < 0 ? "neg" : ""}">${nf(p.v, 0)} kr</span></button>`).join("")}
    </div>`;
  }

  _partConfigured(key) {
    if (key === "ev") return this._cars().length > 0;
    return !!this._st("switch", `control_${key === "heatpump" ? "heating" : key}`);
  }

  // --- Solceller --------------------------------------------------------
  _renderSolar() {
    const tab = SOLAR_TABS.some((t) => t.id === this._solarTab) ? this._solarTab : "month";
    const st = this._st("sensor", `solar_value_${tab}`);
    if (!st) {
      const fc = this._solarForecast();
      return fc ? `<div class="solar"><div class="solar-head"><div class="sec-title"><ha-icon icon="mdi:solar-power-variant"></ha-icon>Solprognose</div></div>${fc}</div>` : "";
    }
    const a = st.attributes || {};
    const total = (a.self_value || 0) + (a.export_income || 0);
    const produced = a.produced || 0;
    const selfShare = produced > 0 ? (a.self_kwh || 0) / produced : 0;
    const tabs = SOLAR_TABS.map((t) => `<button class="tab ${t.id === tab ? "on" : ""}" data-action="solar-tab" data-tab="${t.id}" aria-pressed="${t.id === tab}">${t.label}</button>`).join("");
    const noExport = a.has_export_meter === false;
    return `
      <div class="solar">
        <div class="solar-head">
          <div class="sec-title"><ha-icon icon="mdi:solar-power-variant"></ha-icon>Solceller</div>
          <div class="tabs" role="group" aria-label="Periode">${tabs}</div>
        </div>
        <button class="solar-big" data-action="more" data-entity="${st.entity_id}">
          <span class="value">${nf(total, 0)}</span><span class="unit">kr</span>
          <span class="big-lbl">samlet værdi af din solcellestrøm</span>
        </button>
        ${this._solarRoi()}
        <div class="kpis">
          ${this._kpi("Produceret", `${nf(produced, produced >= 100 ? 0 : 1)} kWh`, null, `solar_produced_${tab}`)}
          ${this._kpi("Sparet", `${nf(a.self_value, 0)} kr`, `${nf(a.self_kwh, 0)} kWh brugt selv`, `solar_saved_${tab}`, COLORS.solar_self)}
          ${this._kpi("Tjent ved salg", noExport ? "–" : `${nf(a.export_income, 0)} kr`, noExport ? "Kræver eksportmåler" : `${nf(a.export_kwh, 0)} kWh solgt${a.export_kwh > 0.05 ? `<br>snit ${nf(a.export_income / a.export_kwh, 2)} kr/kWh` : ""}`, `solar_sold_${tab}`, COLORS.solar_sold)}
        </div>
        ${produced > 0 && !noExport ? `
        <div class="share" aria-label="Egetforbrug ${Math.round(selfShare * 100)} procent">
          <i style="width:${selfShare * 100}%;background:${COLORS.solar_self}"></i><i style="width:${(1 - selfShare) * 100}%;background:${COLORS.solar_sold}"></i>
        </div>
        <div class="share-lbl"><span>${Math.round(selfShare * 100)} % brugt selv</span><span>${Math.round((1 - selfShare) * 100)} % solgt</span></div>` : ""}
        ${a.has_import_meter ? this._gridKpis(a, tab) : ""}
        ${this._solarForecast(true)}
        ${this._solarChart(tab)}
      </div>`;
  }

  /** Hvor stor en del af anlæggets pris strømmen har tjent hjem (kræver anlæggets pris i opsætningen). */
  _solarRoi() {
    const st = this._st("sensor", "solar_roi");
    if (!st || st.state === "unavailable" || st.state === "unknown") return "";
    const a = st.attributes || {};
    const pct = parseFloat(st.state) || 0;
    const sub = a.payback_year
      ? `Anslået tilbagebetalt i ${a.payback_year} · ca. ${nf(a.per_year, 0)} kr/år`
      : "Tilbagebetalingstid anslås efter en måneds data";
    return `
      <button class="roi" data-action="more" data-entity="${st.entity_id}" aria-label="Tilbagebetalt ${nf(pct, 1)} procent">
        <div class="roi-top"><span class="roi-lbl"><ha-icon icon="mdi:cash-refund"></ha-icon>Tilbagebetalt</span><span class="roi-val">${nf(pct, 1)} %</span></div>
        <div class="roi-bar"><i style="width:${Math.min(Math.max(pct, 0), 100)}%"></i></div>
        <div class="roi-sub"><span>${nf(a.earned, 0)} af ${nf(a.investment, 0)} kr</span><span>${sub}</span></div>
      </button>`;
  }

  /** Strøm købt fra nettet i perioden: mængde, pris, snitpris og hvor selvforsynende huset er. */
  _gridKpis(a, tab) {
    const kwh = a.import_kwh || 0;
    const own = a.self_kwh || 0;
    const self = own + kwh > 0 ? Math.round((own / (own + kwh)) * 100) : null;
    return `<div class="fc-title grid-title">Købt fra nettet</div>
      <div class="kpis fc-kpis">
        ${this._kpi("Købt", `${nf(kwh, kwh >= 100 ? 0 : 1)} kWh`, self != null ? `${self} % selvforsynende` : null, `grid_import_${tab}`, COLORS.expensive)}
        ${this._kpi("Betalt", `${nf(a.import_cost || 0, 0)} kr`, null, `grid_cost_${tab}`)}
        ${this._kpi("Snitpris", a.import_price != null ? `${nf(a.import_price, 2)} kr` : "–", "pr. kWh købt", `grid_cost_${tab}`)}
      </div>`;
  }

  /** Solprognose: i dag, resten af dagen, i morgen og en timekurve for i dag. */
  _solarForecast(withTitle = false) {
    const st = this._st("sensor", "solar_forecast");
    const a = st?.attributes || {};
    if (!st || !a.available) return "";
    const kwh = (v) => (v == null ? "–" : `${nf(v, v >= 100 ? 0 : 1)} kWh`);
    const peak = a.peak_time ? `top kl. ${hh(new Date(a.peak_time))}${a.peak_kw != null ? ` · ${nf(a.peak_kw, 1)} kW` : ""}` : null;
    const now = Date.now();
    const hours = (a.hours || []).filter((h) => h.kw != null);
    let chart = "";
    const first = hours.findIndex((h) => h.kw > 0.01);
    const last = hours.length - 1 - [...hours].reverse().findIndex((h) => h.kw > 0.01);
    if (first >= 0) {
      const rows = hours.slice(Math.max(0, first - 1), Math.min(hours.length, last + 2));
      const W = rows.length * 10, H = 46;
      const max = Math.max(...rows.map((r) => r.kw), 0.1);
      let nowX = null;
      const bars = rows.map((r, i) => {
        const t = new Date(r.start).getTime();
        const past = t + 3600e3 <= now;
        if (t <= now && now < t + 3600e3) nowX = i * 10 + ((now - t) / 3600e3) * 10;
        const h = (r.kw / max) * H;
        return `<rect x="${i * 10 + 1.5}" y="${H - h}" width="7" height="${h}" rx="1.5" fill="${COLORS.solar_sold}" opacity="${past ? 0.35 : 1}"><title>${hh(new Date(r.start))}: ${nf(r.kw, 1)} kW</title></rect>`;
      }).join("");
      const ticks = rows.map((r, i) => {
        const d = new Date(r.start);
        return d.getHours() % 3 === 0 ? `<span style="left:${((i * 10 + 5) / W) * 100}%">${String(d.getHours()).padStart(2, "0")}</span>` : "";
      }).join("");
      chart = `<div class="fc-chart">
          <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Forventet solproduktion time for time i dag">${bars}
            ${nowX != null ? `<line x1="${nowX}" x2="${nowX}" y1="0" y2="${H}" class="fc-now"/>` : ""}</svg>
          <div class="ticks c">${ticks}</div></div>`;
    }
    const pnow = a.power_now_kw != null ? ` · forventet nu ${nf(a.power_now_kw, 1)} kW` : "";
    return `<div class="fc">
        ${withTitle ? `<div class="fc-title">Solprognose${pnow}</div>` : pnow ? `<div class="fc-title">${pnow.slice(3)}</div>` : ""}
        <div class="kpis fc-kpis">
          ${this._kpi("I dag", kwh(a.today_kwh), peak, "solar_forecast")}
          ${this._kpi("Resten af dagen", kwh(a.remaining_kwh), null, "solar_forecast", COLORS.solar_sold)}
          ${this._kpi("I morgen", kwh(a.tomorrow_kwh), null, "solar_forecast")}
        </div>
        ${chart}
      </div>`;
  }

  _kpi(label, value, sub, key, color) {
    const ent = this._st("sensor", key);
    return `<button class="kpi" ${ent ? `data-action="more" data-entity="${ent.entity_id}"` : ""}>
      <span class="k-lbl">${color ? `<i style="background:${color}"></i>` : ""}${label}</span>
      <span class="k-val">${value}</span>${sub ? `<span class="k-sub">${sub}</span>` : ""}</button>`;
  }

  /** Søjler: dage (i dag/måned) eller måneder (år/i alt), delt i sparet og solgt. */
  _solarChart(tab) {
    const hist = this._st("sensor", "solar_value_total")?.attributes || {};
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    let rows;
    if (tab === "today" || tab === "month") {
      const map = new Map((hist.days || []).map((d) => [d.period, d]));
      rows = [];
      for (let i = 29; i >= 0; i--) {
        const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i);
        const k = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
        rows.push({ k, label: `${d.getDate()}/${d.getMonth() + 1}`, tick: d.getDate() === 1 || i === 29 || i === 0 ? `${d.getDate()}/${d.getMonth() + 1}` : "", ...(map.get(k) || {}) });
      }
    } else if (tab === "year") {
      const map = new Map((hist.months || []).map((d) => [d.period, d]));
      rows = MONTHS.map((m, i) => {
        const k = `${now.getFullYear()}-${pad(i + 1)}`;
        return { k, label: `${m} ${now.getFullYear()}`, tick: m, ...(map.get(k) || {}) };
      });
    } else {
      const list = hist.months || [];
      const step = Math.ceil(list.length / 12);
      rows = list.map((d, i) => {
        const [y, m] = d.period.split("-");
        const show = i === 0 || m === "01" || i % step === 0;
        return { k: d.period, label: `${MONTHS[+m - 1]} ${y}`, tick: show ? (i === 0 || m === "01" ? `${MONTHS[+m - 1]} ${y.slice(2)}` : MONTHS[+m - 1]) : "", ...d };
      });
    }
    if (!rows.length || !rows.some((r) => r.produced)) {
      return `<div class="solar-empty">Historikken bygges op fra nu af – her kommer et søjlediagram over ${tab === "year" || tab === "total" ? "månederne" : "dagene"}.</div>`;
    }
    const W = rows.length * 10, H = 70;
    const max = Math.max(...rows.map((r) => (r.self_value || 0) + Math.max(r.export_income || 0, 0)), 0.01);
    const bars = rows.map((r, i) => {
      const a = Math.max(r.self_value || 0, 0) / max * H;
      const b = Math.max(r.export_income || 0, 0) / max * H;
      const sel = this._solarSel === r.k;
      return `<g data-action="solar-bar" data-k="${r.k}" class="sbar${sel ? " sel" : ""}">
        <rect x="${i * 10}" y="0" width="10" height="${H}" fill="transparent"></rect>
        <rect x="${i * 10 + 1.5}" y="${H - a}" width="7" height="${a}" fill="${COLORS.solar_self}"></rect>
        <rect x="${i * 10 + 1.5}" y="${H - a - b}" width="7" height="${b}" fill="${COLORS.solar_sold}"></rect>
      </g>`;
    }).join("");
    const picked = rows.find((r) => r.k === this._solarSel);
    const info = picked
      ? `${picked.label}: <b>${nf(picked.produced, 1)} kWh</b> · sparet <b>${nf(picked.self_value)} kr</b> · solgt <b>${nf(picked.export_income)} kr</b>`
      : `<span class="lg-dot" style="background:${COLORS.solar_self}"></span>Sparet <span class="lg-dot" style="background:${COLORS.solar_sold}"></span>Solgt <span class="muted">· tryk på en søjle</span>`;
    const ticks = rows.map((r, i) => (r.tick ? `<span style="left:${((i * 10 + 5) / W) * 100}%">${r.tick}</span>` : "")).join("");
    return `
      <div class="schart">
        <div class="strip-info">${info}</div>
        <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Solcellernes værdi pr. ${tab === "year" || tab === "total" ? "måned" : "dag"}">${bars}</svg>
        <div class="ticks c">${ticks}</div>
      </div>`;
  }

  _saving(label, v, key) {
    return `<button class="saving" data-action="more" data-entity="${this._id("sensor", key)}">
      <span class="s-val">${nf(v, 0)} kr</span><span class="s-lbl">${label}</span></button>`;
  }

  _renderStrip() {
    const hours = this._hours();
    if (!hours.length) return `<div class="strip empty">Venter på elpriser…</div>`;
    const n = hours.length;
    const W = n * 10;
    const H = 100;
    const prices = hours.map((h) => h.price);
    const max = Math.max(...prices, 0.01);
    const min = Math.min(...prices, 0);
    const sorted = [...prices].sort((a, b) => a - b);
    const q = (p) => sorted[Math.floor((sorted.length - 1) * p)];
    const lo = q(0.25), hi = q(0.75);
    const y0 = H * (max / (max - min));
    const scale = H / (max - min);
    const now = Date.now();
    const t0 = hours[0].start.getTime();
    const t1 = hours[n - 1].start.getTime() + 3600e3;
    const xOf = (t) => ((t - t0) / (t1 - t0)) * W;

    const bars = hours
      .map((h, i) => {
        const c = h.price <= lo ? COLORS.cheap : h.price >= hi ? COLORS.expensive : COLORS.mid;
        const top = h.price >= 0 ? y0 - h.price * scale : y0;
        const bh = Math.max(Math.abs(h.price) * scale, 0.8);
        const past = h.start.getTime() + 3600e3 <= now;
        const sel = this._sel === i;
        return `<rect x="${i * 10 + 1}" y="${top}" width="8" height="${bh}" rx="1.5" fill="${c}"
          opacity="${past ? 0.3 : sel ? 1 : 0.85}" data-action="bar" data-i="${i}" class="bar${sel ? " sel" : ""}"></rect>`;
      })
      .join("");

    const nowX = xOf(now);
    const midnight = hours.find((h, i) => i > 0 && h.start.getHours() === 0);
    const midX = midnight ? xOf(midnight.start.getTime()) : null;

    // Spor for planlagte handlinger
    const carLanes = this._visibleCars(this._cars()).map((c) => ({ key: `${c.cid}_charge_now`, label: `${c.name} lader`, color: c.color, module: `control_${c.cid}` }));
    const lanes = [...carLanes, ...LANES].filter((l) => this._st("switch", l.module) && this._st("binary_sensor", l.key));
    const laneH = 7, gap = 4;
    const lanesSvg = lanes
      .map((l, li) => {
        const slots = this._st("binary_sensor", l.key).attributes.slots || [];
        const y = li * (laneH + gap);
        const op = this._st("switch", l.module)?.state === "on" ? 1 : 0.3;
        const rects = slots
          .map((s) => {
            const t = new Date(s).getTime();
            if (t < t0 || t >= t1) return "";
            return `<rect x="${xOf(t)}" y="${y}" width="${xOf(t + 900e3) - xOf(t) + 0.3}" height="${laneH}" fill="${l.color}" opacity="${op}"></rect>`;
          })
          .join("");
        return `<rect x="0" y="${y}" width="${W}" height="${laneH}" fill="var(--sh-track)"></rect>${rects}`;
      })
      .join("");
    const lanesHeight = lanes.length ? lanes.length * (laneH + gap) - gap : 0;

    const ticks = hours
      .map((h, i) => ({ h, i }))
      .filter(({ h }) => h.start.getHours() % 6 === 0)
      .map(({ h, i }) => `<span style="left:${((i * 10) / W) * 100}%">${String(h.start.getHours()).padStart(2, "0")}</span>`)
      .join("");

    const selInfo = (() => {
      const i = this._sel;
      if (i === null || !hours[i]) {
        const cheapest = this._st("sensor", "cheapest_hour")?.state;
        const d = cheapest && !["unknown", "unavailable"].includes(cheapest) ? new Date(cheapest) : null;
        return d ? `Billigste time starter kl. ${hh(d)}${d.getDate() !== new Date().getDate() ? " i morgen" : ""}` : "Tryk på en søjle for at se prisen";
      }
      const h = hours[i];
      const day = h.start.getDate() === new Date().getDate() ? "i dag" : "i morgen";
      const end = new Date(h.start.getTime() + 3600e3);
      return `${day} kl. ${hh(h.start)}–${hh(end)}: <b>${nf(h.price)} kr/kWh</b>`;
    })();

    const legend = lanes
      .map((l) => {
        const periods = upcoming(this._st("binary_sensor", l.key).attributes.slots);
        const txt = periods.length ? periods.map((p) => (p.active ? `nu–${p.text.split("–")[1]}` : p.text)).join(", ") : "Intet planlagt";
        const off = this._st("switch", l.module)?.state !== "on";
        return `<div class="lg ${off ? "off" : ""}"><i style="background:${l.color}"></i><span class="lg-name">${l.label}</span><span class="lg-val">${esc(txt)}</span></div>`;
      })
      .join("");

    return `
      <div class="strip">
        <div class="strip-info">${selInfo}</div>
        <div class="chart">
          <svg class="bars" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Elpris pr. time i dag og i morgen">
            ${midX !== null ? `<line x1="${midX}" x2="${midX}" y1="0" y2="${H}" class="midline"></line>` : ""}
            ${bars}
            ${nowX >= 0 && nowX <= W ? `<line x1="${nowX}" x2="${nowX}" y1="0" y2="${H}" class="nowline"></line>` : ""}
          </svg>
          ${midX !== null ? `<div class="daylbl" style="left:${(midX / W) * 100}%">I morgen</div>` : ""}
          <div class="daylbl first">I dag</div>
        </div>
        ${lanes.length ? `<svg class="lanes" viewBox="0 0 ${W} ${lanesHeight}" preserveAspectRatio="none" style="height:${lanesHeight}px" aria-hidden="true">${lanesSvg}${nowX >= 0 && nowX <= W ? `<line x1="${nowX}" x2="${nowX}" y1="0" y2="${lanesHeight}" class="nowline"></line>` : ""}</svg>` : ""}
        <div class="ticks">${ticks}</div>
        ${legend ? `<div class="legend">${legend}</div>` : ""}
      </div>`;
  }

  /** Knapper for hvert område + det åbne indstillingspanel. */
  _renderDock() {
    const mods = this._modules();
    const cars = mods.filter((m) => m.car);
    const groups = [];
    if (cars.length) groups.push({ id: "ev", name: cars.length > 1 ? "Elbiler" : "Elbil", icon: "car", mods: cars, sub: cars.length === 1 ? cars[0].name : null });
    for (const [id, icon] of [["hotwater", "water"], ["heating", "heat"], ["battery", "battery"], ["solar", "solar"]]) {
      const m = mods.find((x) => x.id === id);
      if (m) groups.push({ id, name: m.name, icon, mods: [m] });
    }
    if (!groups.length) return "";
    if (this._panel && !groups.some((g) => g.id === this._panel)) this._panel = null;
    const tiles = groups.map((g) => {
      const on = g.mods.some((m) => this._st("switch", m.sw)?.state === "on");
      const line = g.mods.length === 1 ? this._moduleLine(g.mods[0], this._st("switch", g.mods[0].sw)?.state === "on") : `${g.mods.length} biler`;
      const sel = this._panel === g.id;
      return `<button class="tile${sel ? " sel" : ""}${on ? " on" : ""}" data-action="panel" data-panel="${g.id}" aria-expanded="${sel}"
          aria-label="${esc(g.name)}: ${esc(line)}">
        <span class="tile-ic">${this._dockIcon(g.icon)}<i class="tile-dot" title="${on ? "Styres af SmartHus" : "Styring slået fra"}"></i></span>
        <span class="tile-name">${esc(g.sub || g.name)}</span>
        <span class="tile-line">${esc(line)}</span>
      </button>`;
    }).join("");
    const g = groups.find((x) => x.id === this._panel);
    const panel = g ? `
      <section class="panel${this._panelAnim ? " anim" : ""}" aria-label="Indstillinger for ${esc(g.name)}">
        <div class="panel-head">
          <span class="panel-ic">${this._dockIcon(g.icon)}</span>
          <span class="panel-title">${esc(g.name)}<span>Indstillinger</span></span>
          <button class="panel-close" data-action="close-panel" aria-label="Luk og vis overblikket">
            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M6 6 18 18M18 6 6 18" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>Luk</button>
        </div>
        ${g.id === "ev" ? this._connectedPicker() : ""}
        <div class="panel-mods${g.mods.length > 1 ? " multi" : ""}">${g.mods.map((m) => this._renderModule(m, true)).join("")}</div>
      </section>` : "";
    this._panelAnim = false;
    return `<div class="dock" role="group" aria-label="Indstillinger">${tiles}</div>${panel}`;
  }

  /** Ikoner i SmartHus-stil: grønne streger med gul og blå detalje. */
  _dockIcon(kind) {
    const G = "var(--sh-ic, #1E9E5C)", A = "#F7B21B", B = "#0A6FB8";
    const st = `fill="none" stroke="${G}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"`;
    const icons = {
      car: `<path d="M5 20v-4.5l2.4-5.2A2 2 0 0 1 9.2 9h11.6a2 2 0 0 1 1.8 1.3L25 15.5V20" ${st}/>
            <path d="M3.5 20h25v3.5a1 1 0 0 1-1 1H4.5a1 1 0 0 1-1-1Z" ${st}/>
            <circle cx="9.5" cy="24.5" r="2.4" fill="var(--card-background-color,#fff)" ${st}/><circle cx="22.5" cy="24.5" r="2.4" fill="var(--card-background-color,#fff)" ${st}/>
            <path d="M16.6 10.8 14 15.4h3l-2 4.2" fill="none" stroke="${A}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`,
      water: `<rect x="8.5" y="4" width="15" height="23" rx="4" ${st}/>
            <path d="M16 11.5c2.6 3 3.8 4.9 3.8 6.6a3.8 3.8 0 0 1-7.6 0c0-1.7 1.2-3.6 3.8-6.6Z" fill="${B}" opacity=".9"/>
            <path d="M12 27.5v2M20 27.5v2" ${st}/>`,
      heat: `<rect x="5" y="11" width="22" height="14" rx="2.5" ${st}/>
            <path d="M10.5 11v14M16 11v14M21.5 11v14M7 28h18" ${st}/>
            <path d="M11 7.5c-1-1.3 1-2.2 0-3.5M16 7.5c-1-1.3 1-2.2 0-3.5M21 7.5c-1-1.3 1-2.2 0-3.5" fill="none" stroke="${A}" stroke-width="1.9" stroke-linecap="round"/>`,
      battery: `<rect x="9" y="6" width="14" height="22" rx="2.5" ${st}/><path d="M13 3.5h6" ${st}/>
            <path d="M19.3 12.4a4.6 4.6 0 1 1-6.6 0" fill="none" stroke="${B}" stroke-width="2" stroke-linecap="round"/>
            <path d="M16 10.5v5" fill="none" stroke="${B}" stroke-width="2" stroke-linecap="round"/>
            <path d="M12 23.5h8" stroke="${G}" stroke-width="2.6" stroke-linecap="round"/>`,
      solar: `<circle cx="24" cy="8" r="3" fill="${A}"/>
            <path d="M24 2.2v1.3M24 12.5v1.3M18.2 8h1.3M28.5 8h1.3M19.9 3.9l.9.9M27.2 11.2l.9.9M28.1 3.9l-.9.9" stroke="${A}" stroke-width="1.6" stroke-linecap="round"/>
            <path d="M4 25.5 7.5 13h12.5L23.5 25.5Z" ${st}/>
            <path d="M5.8 19.3h16M11.6 13l-1.8 12.5M15.9 13l1.8 12.5M13.8 25.5v3.5M9.5 29h8.5" ${st}/>`,
    };
    return `<svg viewBox="0 0 32 32" width="30" height="30" aria-hidden="true">${icons[kind] || ""}</svg>`;
  }

  _renderModule(m, inPanel = false) {
    const sw = this._st("switch", m.sw);
    const on = sw.state === "on";
    const open = inPanel || !this._closed.has(m.id);
    const line = this._moduleLine(m, on);
    const body = open ? this._moduleBody(m) : "";
    return `
      <section class="mod ${open ? "open" : ""} ${on ? "on" : ""}">
        <div class="mod-head">
          ${inPanel
            ? `<div class="mod-main"><span class="mod-text"><span class="mod-name">${m.car ? esc(m.name) : "Styring"}</span><span class="mod-line">${esc(line)}</span></span></div>`
            : `<button class="mod-main" data-action="expand" data-mod="${m.id}" aria-expanded="${open}">
            <ha-icon icon="${m.icon}"></ha-icon>
            <span class="mod-text"><span class="mod-name">${m.name}</span><span class="mod-line">${esc(line)}</span></span>
            <span class="chev-lbl">${open ? "Skjul" : "Indstillinger"}</span><ha-icon class="chev" icon="mdi:chevron-down"></ha-icon>
          </button>`}
          ${this._toggle(sw.entity_id, on, `Styr ${m.name.toLowerCase()}`)}
        </div>
        ${body}
      </section>`;
  }

  _periods(key) {
    return upcoming(this._st("binary_sensor", key)?.attributes.slots).map((p) => p.text);
  }

  _moduleLine(m, on) {
    const isOn = (k) => this._st("binary_sensor", k)?.state === "on";
    const next = (k) => {
      const p = this._periods(k);
      return p.length ? p[0] : null;
    };
    if (!on) return "Styring slået fra";
    const prefix = "";
    if (m.car) {
      const a = this._st("sensor", `${m.id}_smart_saving`)?.attributes || {};
      const k = `${m.id}_charge_now`;
      if (a.target_reached) return `${prefix}Ladet til ${nf(a.soc, 0)} %`;
      const ca = this._st("binary_sensor", k)?.attributes || {};
      if (ca.connected === false) return `${prefix}Ikke tilsluttet laderen`;
      if (a.short_of_time) return `${prefix}Ikke tid nok – når ca. ${nf(a.soc_at_ready, 0)} % kl. ${a.ready_at ? hh(new Date(a.ready_at)) : ""}`;
      if (ca.solar_active) return `${prefix}Lader med sol · ${ca.solar_current} A${a.soc != null ? ` · ${nf(a.soc, 0)} %` : ""}`;
      if (isOn(k)) return `${prefix}Lader nu${a.soc != null ? ` · ${nf(a.soc, 0)} %` : ""}`;
      const n = next(k);
      return `${prefix}${n ? `Lader ${n}` : "Venter på priser"}${a.soc != null ? ` · ${nf(a.soc, 0)} % af ${nf(a.target_soc, 0)} %` : ""}`;
    }
    switch (m.id) {
      case "battery": {
        const r = this._st("sensor", "battery_reserve");
        const ra = r?.attributes || {};
        if (isOn("battery_charge_now") && !ra.grid_charge?.skipping) return `${prefix}Lader fra nettet${ra.grid_charge && !ra.grid_charge.sell_solar_better ? ` til ${nf(ra.grid_charge.target_soc, 0)} %` : ""}`;
        if (ra.holding_for_reserve) return `Gemmer ${nf(ra.reserve_kwh, 1)} kWh til huset`;
        if (ra.sell_solar?.active) return `Sælger solstrøm · lader billigt senere`;
        if (ra.grid_charge?.skipping) return "Lader ikke fra nettet – solen fylder batteriet";
        if (isOn("battery_discharge_now")) return `${prefix}Sælger${ra.sellable_kwh != null ? ` · ${nf(ra.sellable_kwh, 1)} kWh tilbage at sælge` : ""}`;
        if (r && ra.reserve_kwh != null && ra.until) return `Reserve ${nf(ra.reserve_kwh, 1)} kWh til kl. ${hh(new Date(ra.until))}`;
        return `${prefix}${next("battery_charge_now") ? `Lader ${next("battery_charge_now")}` : "Normal drift"}`;
      }
      case "hotwater": {
        const a = this._st("binary_sensor", "hotwater_now")?.attributes || {};
        const t = a.water_temp != null ? ` · ${nf(a.water_temp, 0)} °C` : "";
        if (isOn("hotwater_now")) return `${prefix}${a.warm_enough ? "Varmt nok – venter" : "Opvarmer nu"}${t}`;
        if (a.heated_today && !isOn("hotwater_now")) return `${prefix}Varmet op i dag${t}`;
        if (a.solar_surplus) return `${prefix}${a.warm_enough ? "Varmt nok – venter" : "Varmer med solcelleoverskud"}${t}`;
        return `${prefix}${next("hotwater_now") ? `Opvarmer ${next("hotwater_now")}` : "Ingen plan"}${t}`;
      }
      case "heating": {
        const ha = this._st("binary_sensor", "heating_boost_now")?.attributes || {};
        const out = ha.outdoor_temp != null ? ` · ${nf(ha.outdoor_temp, 1)} °C` : "";
        if (ha.paused_warm_outside) return `${prefix}Pause – varmt ude${out}`;
        if (isOn("heating_boost_now")) return `${prefix}Ekstra varme nu${out}`;
        if (isOn("heating_reduce_now")) return `${prefix}Sparer på varmen nu${out}`;
        return `${prefix}${next("heating_boost_now") ? `Ekstra varme ${next("heating_boost_now")}` : "Normal drift"}${out}`;
      }
      case "solar":
        {
          const lim = this._num("number", "solar_export_min_price");
          const lt = lim === null ? "" : ` · grænse ${nf(lim)} kr/kWh`;
          return `${prefix}${isOn("solar_curtail_now") ? "Eksport stoppet – salgsprisen er for lav" : "Sælger strøm normalt"}${lt}`;
        }
    }
    return "";
  }

  _moduleBody(m) {
    let extra = "";
    if (m.car) extra = this._evCompare(m.id);
    if (m.id === "battery") extra = this._tradeBox() + this._reserveBox();
    if (m.id === "hotwater") {
      const a = this._st("binary_sensor", "hotwater_now")?.attributes || {};
      if (a.water_temp != null) {
        const pct = a.max_temp ? Math.max(0, Math.min(1, a.water_temp / a.max_temp)) : 0;
        extra = `<div class="water">
          <div class="water-row"><span>Vandets temperatur</span><b>${nf(a.water_temp, 1)} °C</b></div>
          <div class="water-bar"><i style="width:${pct * 100}%"></i></div>
          <div class="water-sub">${a.warm_enough ? "Maks. temperatur nået – opvarmningen er sat på pause" : `Maks. ${nf(a.max_temp, 0)} °C`}</div>
        </div>`;
      }
    }
    if (m.id === "heating") {
      const a = this._st("binary_sensor", "heating_boost_now")?.attributes || {};
      if (a.outdoor_temp != null) {
        extra = `<div class="water">
          <div class="water-row"><span>Udetemperatur <small class="muted">${a.outdoor_source === "sensor" ? "fra din føler" : "fra vejret"}</small></span><b>${nf(a.outdoor_temp, 1)} °C</b></div>
          <div class="water-sub">${a.paused_warm_outside
            ? `Varmestyringen holder pause, fordi det er ${nf(a.outdoor_limit, 1)} °C eller varmere ude. Den starter igen ved ${nf(a.outdoor_limit - 1, 1)} °C.`
            : a.outdoor_limit != null ? `Varmestyringen er aktiv, fordi det er koldere end ${nf(a.outdoor_limit, 1)} °C ude.` : ""}</div>
        </div>`;
      }
    }
    if (m.id === "solar") extra = `<div class="hint">Eksporten stoppes, når salgsprisen er under grænsen, og starter selv igen, når prisen kommer over. 0 kr betyder, at der kun stoppes ved negativ pris.</div>`;
    const evHasSoc = m.car && this._st("sensor", `${m.id}_smart_saving`)?.attributes?.soc != null;
    const hide = (k) =>
      (m.car && evHasSoc && k === `${m.id}_hours`) ||
      (m.car && !evHasSoc && [`${m.id}_target_soc`, `${m.id}_battery_capacity`].includes(k));
    const groups = m.groups
      .map((g) => {
        const rows = g.items
          .map((it) => {
            if (it && it.sw) return this._st("switch", it.sw) ? this._switchRow(it) : "";
            if (Array.isArray(it)) return this._st("number", it[0]) && this._st("number", it[1]) ? this._window(it[0], it[1]) : "";
            return this._st("number", it) && !hide(it) ? this._stepper(it) : "";
          })
          .join("");
        return rows ? `<div class="grp">${g.title ? `<div class="grp-title">${g.title}</div>` : ""}${rows}</div>` : "";
      })
      .join("");
    let actions = "";
    const force = m.car ? this._st("switch", this._forceKey(m.id)) : null;
    if (force) {
      const fon = force.state === "on";
      actions = `<button class="action ${fon ? "active" : ""}" data-action="toggle" data-entity="${force.entity_id}">
        <ha-icon icon="mdi:lightning-bolt"></ha-icon>${fon ? "Stop opladning nu" : `Lad ${esc(m.name)} nu`}</button>`;
    }
    return `<div class="mod-body">${extra}${groups}${actions}</div>`;
  }

  // --- 3D-energiflow i SmartHus-stil ------------------------------------
  _renderScene() {
    const st = this._st("sensor", "energy_flow");
    if (!st || st.attributes.grid_kw == null) return "";
    const f = st.attributes;
    const smartOn = this._st("switch", "control_master")?.state === "on";
    const S = 19;
    const P = (x, y, z = 0) => [+((x - y) * 0.866 * S).toFixed(1), +((x + y) * 0.5 * S - z * S).toFixed(1)];
    const pts = (a) => a.map((p) => P(...p).join(",")).join(" ");
    const poly = (a, fill, extra = "") => `<polygon points="${pts(a)}" fill="${fill}" ${extra}/>`;
    const box = (x, y, w, d, h, c, z = 0) =>
      poly([[x, y + d, z], [x + w, y + d, z], [x + w, y + d, z + h], [x, y + d, z + h]], c.l) +
      poly([[x + w, y, z], [x + w, y + d, z], [x + w, y + d, z + h], [x + w, y, z + h]], c.r) +
      poly([[x, y, z + h], [x + w, y, z + h], [x + w, y + d, z + h], [x, y + d, z + h]], c.t);
    // SmartHus-logoets farver
    const C = {
      green: "#1E9E5C", greenDark: "#137A45", greenDeep: "#0E5E36",
      blue: "#0A6FB8", amber: "#F7B21B",
      wall: { t: "#FFFFFF", l: "#F3F6F4", r: "#DDE6E0" },
      grid: "#6F7F91", ev: "#3E6FD8",
    };
    const kwTxt = (v) => `${nf(Math.abs(v || 0), Math.abs(v || 0) < 10 ? 1 : 0)} kW`;
    const label = (x, y, z, title, value, color, dx = 0, anchor = "middle") => {
      const [sx, sy] = P(x, y, z);
      return `<g class="lbl" style="text-anchor:${anchor}" transform="translate(${sx + dx},${sy})">
        <text y="-4" class="lbl-t">${esc(title)}</text><text y="9" class="lbl-v" fill="${color}">${value}</text></g>`;
    };
    const flow = (a, b, kw, color, bend = 0) => {
      const [ax, ay] = P(...a), [bx, by] = P(...b);
      const d = `M${ax},${ay} Q${(ax + bx) / 2},${(ay + by) / 2 + bend} ${bx},${by}`;
      const live = Math.abs(kw || 0) >= 0.05;
      const dur = Math.min(6, Math.max(0.5, 2.6 / Math.max(Math.abs(kw || 0), 0.15))).toFixed(2);
      return `<path d="${d}" class="fl-base" stroke="${color}"/>` +
        (live ? `<path d="${d}" class="fl-dash" stroke="${color}" style="animation-duration:${dur}s"/>` : "");
    };

    // Grøn bakke (som i logoet) – en rund ø med kant
    let svg = `<defs>
        <filter id="sh-glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.2"/></filter>
      </defs>
`;

    // Strømlinjer under objekterne
    const g = f.grid_kw || 0;
    const hub = [5, 6.6, 0.5];
    svg += g >= 0 ? flow([1.7, 7.7, 0.1], hub, g, C.grid, 10) : flow(hub, [1.7, 7.7, 0.1], g, C.grid, 10);
    if (f.has_battery && f.battery_kw != null) {
      const bk = f.battery_kw;
      svg += bk >= 0 ? flow([8.0, 3.3, 0.1], [6.6, 4.6, 0.5], bk, C.blue, 8) : flow([6.6, 4.6, 0.5], [8.0, 3.3, 0.1], bk, C.blue, 8);
    }
    // Begge biler vises i billedet (så SoC kan ses), den der ikke sidder i laderen mere gennemsigtig
    const cars = (f.cars || []).slice(0, 2);
    cars.forEach((c, i) => {
      const at = i === 0 ? [8.1, 6.4, 0.1] : [8.4, 8.1, 0.1];
      if (c.connected !== false) svg += flow([6.6, 5.8, 0.4], at, c.kw || 0, C.ev, 4);
    });

    // Elmast
    const [px, py] = P(1.7, 7.7, 0), [tx, ty] = P(1.7, 7.7, 3.0);
    svg += `<g stroke="${C.grid}" stroke-width="1.6" fill="none" stroke-linecap="round">
      <line x1="${px - 7}" y1="${py}" x2="${tx}" y2="${ty}"/><line x1="${px + 7}" y1="${py}" x2="${tx}" y2="${ty}"/>
      <line x1="${tx - 12}" y1="${ty + 9}" x2="${tx + 12}" y2="${ty + 9}"/><line x1="${tx - 9}" y1="${ty + 17}" x2="${tx + 9}" y2="${ty + 17}"/>
      <line x1="${px - 4.5}" y1="${py - 18}" x2="${px + 4.5}" y2="${py - 18}"/></g>`;

    // Batteri: hvidt skab med blåt tænd-symbol og SoC-søjle
    if (f.has_battery) {
      svg += box(7.5, 2.7, 1.0, 0.8, 1.9, C.wall);
      const soc = f.battery_soc;
      if (soc != null) {
        const h = 1.6 * Math.max(0, Math.min(100, soc)) / 100;
        svg += poly([[8.5, 2.85, 0.15], [8.5, 3.0, 0.15], [8.5, 3.0, 1.75], [8.5, 2.85, 1.75]], "#E5ECF3");
        svg += poly([[8.5, 2.85, 0.15], [8.5, 3.0, 0.15], [8.5, 3.0, 0.15 + h], [8.5, 2.85, 0.15 + h]], C.green);
      }
      const [bx, by] = P(8.5, 3.25, 1.2);
      svg += this._powerIcon(bx, by, 6.5, C.blue, false);
    }

    // Huset
    svg += box(3.4, 3.6, 3.2, 3.0, 2.0, C.wall);
    // Skorsten på den bagerste tagflade
    svg += box(3.9, 4.0, 0.45, 0.45, 1.25, { t: C.greenDark, l: C.green, r: C.greenDark }, 2.3);
    // Tag: tyk grøn kant som i logoet
    svg += poly([[3.15, 3.3, 1.85], [6.85, 3.3, 1.85], [6.85, 5.1, 3.45], [3.15, 5.1, 3.45]], C.greenDark);
    svg += poly([[3.15, 5.1, 3.45], [6.85, 5.1, 3.45], [6.85, 6.9, 1.85], [3.15, 6.9, 1.85]], C.green);
    svg += poly([[3.15, 6.9, 1.85], [6.85, 6.9, 1.85], [6.85, 6.9, 1.65], [3.15, 6.9, 1.65]], C.greenDeep);
    // Gavl (højre) med blåt tænd-symbol – lyser når SmartHus styrer
    svg += poly([[6.6, 3.6, 2.0], [6.6, 6.6, 2.0], [6.6, 5.1, 3.3]], C.wall.r);
    svg += poly([[6.85, 6.9, 1.85], [6.85, 5.1, 3.45], [6.85, 5.1, 3.25], [6.85, 6.7, 1.85]], C.greenDeep);
    const [gx, gy] = P(6.6, 5.1, 1.25);
    svg += this._powerIcon(gx, gy, 9, C.blue, smartOn);
    // Vinduer 2×2 i grønt (forsiden) og dør
    const win = (x, z) => {
      let w = "";
      for (const [dx, dz] of [[0, 0], [0.34, 0], [0, 0.32], [0.34, 0.32]])
        w += poly([[x + dx, 6.6, z + dz], [x + dx + 0.28, 6.6, z + dz], [x + dx + 0.28, 6.6, z + dz + 0.26], [x + dx, 6.6, z + dz + 0.26]], C.green);
      return w;
    };
    svg += win(3.75, 0.85) + win(5.55, 0.85);
    svg += poly([[4.65, 6.6, 0], [5.15, 6.6, 0], [5.15, 6.6, 1.15], [4.65, 6.6, 1.15]], C.greenDeep);
    // Lynpil på forsiden – lyser når huset bruger strøm
    const [lx, ly] = P(3.62, 6.6, 0.55);
    svg += `<g transform="translate(${lx},${ly})"><path d="M2,-12 L-4,1 L0.5,1 L-2,11 L5,-3 L0.6,-3 L3,-12 Z" fill="${C.amber}"
      class="${(f.house_kw || 0) > 0.05 ? "bolt on" : "bolt"}"/></g>`;
    // Solpaneler på forreste tagflade
    if (f.has_solar) {
      for (let i = 0; i < 3; i++) {
        const x0 = 3.5 + i * 1.08;
        svg += poly([[x0, 5.4, 3.2], [x0 + 0.95, 5.4, 3.2], [x0 + 0.95, 6.55, 2.2], [x0, 6.55, 2.2]], "#0F3D6B",
          `class="panel${(f.solar_kw || 0) > 0.05 ? " on" : ""}"`);
        const [l1x, l1y] = P(x0, 5.97, 2.7), [l2x, l2y] = P(x0 + 0.95, 5.97, 2.7);
        svg += `<line x1="${l1x}" y1="${l1y}" x2="${l2x}" y2="${l2y}" stroke="#2D6AA6" stroke-width=".8"/>`;
      }
      svg += flow([5.1, 5.95, 2.7], [5.1, 6.75, 1.6], f.solar_kw || 0, C.amber, 0);
    }
    // Antenne med wifi-buer på rygningen – det "smarte" i SmartHus
    const [ax0, ay0] = P(5.9, 5.1, 3.45), [ax1, ay1] = P(5.9, 5.1, 4.25);
    svg += `<line x1="${ax0}" y1="${ay0}" x2="${ax1}" y2="${ay1}" stroke="${C.green}" stroke-width="2.4" stroke-linecap="round"/>
      <circle cx="${ax1}" cy="${ay1}" r="2.6" fill="${C.green}"/>
      <g class="wifi${smartOn ? " on" : ""}" fill="none" stroke="${C.green}" stroke-width="2.2" stroke-linecap="round">
        <path d="M${ax1 - 6},${ay1 - 5} A8,8 0 0 1 ${ax1 + 6},${ay1 - 5}"/>
        <path d="M${ax1 - 10},${ay1 - 9} A13,13 0 0 1 ${ax1 + 10},${ay1 - 9}"/>
        <path d="M${ax1 - 14},${ay1 - 13} A18,18 0 0 1 ${ax1 + 14},${ay1 - 13}"/></g>`;
    // Varmt vand: damp fra skorstenen
    if (f.hotwater_on) {
      const [hx, hy] = P(4.12, 4.22, 3.75);
      svg += `<g class="steam" stroke="#A9B4BE" stroke-width="1.4" fill="none" stroke-linecap="round">
        <path d="M${hx},${hy} q4,-5 0,-10 q-4,-5 0,-10"/><path d="M${hx + 6},${hy + 1} q4,-5 0,-9 q-4,-5 0,-9"/></g>`;
    }

    // Biler
    const wheel = (x, y) => { const [wx, wy] = P(x, y, 0.14); return `<ellipse cx="${wx}" cy="${wy}" rx="3.2" ry="3.6" fill="#24303B"/>`; };
    const car = (x, y, color) => box(x, y, 0.85, 1.6, 0.42, { t: color, l: color, r: color }, 0.12)
      + box(x + 0.05, y + 0.35, 0.75, 0.85, 0.32, { t: "#E6EEF8", l: "#C9D7EA", r: "#B1C3DD" }, 0.54)
      + wheel(x + 0.85, y + 0.3) + wheel(x + 0.85, y + 1.3);
    cars.forEach((c, i) => {
      const at = i === 0 ? [7.9, 5.7] : [8.3, 7.5];
      svg += `<g opacity="${c.connected === false ? 0.4 : 1}">${car(at[0], at[1], i === 0 ? C.ev : "#7489DB")}</g>`;
    });

    // Etiketter
    // Lille batteri-ikon med ladeniveau (SoC) og procent
    const socPill = (x, y, soc, w = 22) => {
      const v = Math.max(0, Math.min(100, soc));
      const col = v < 20 ? "#C2416B" : v < 50 ? C.amber : C.green;
      return `<g class="soc" transform="translate(${x},${y})">
        <rect x="0" y="-5" width="${w}" height="10" rx="2.6" class="soc-shell"/>
        <rect x="${w + 0.8}" y="-2.2" width="2" height="4.4" rx="0.8" class="soc-nub"/>
        <rect x="1.6" y="-3.4" width="${((w - 3.2) * v / 100).toFixed(1)}" height="6.8" rx="1.6" fill="${col}"/>
        <text x="${w + 6}" y="3.6" class="soc-t">${nf(v, 0)} %</text></g>`;
    };
    const textW = (t) => String(t).length * 6.1;   // ca. bredde af lbl-v-tekst
    let labels = label(1.7, 7.7, 3.9, "Net", `${kwTxt(g)} ${g >= 0.05 ? "køb" : g <= -0.05 ? "salg" : ""}`, C.grid);
    if (f.has_solar) labels += label(2.6, 4.2, 3.9, "Sol", kwTxt(f.solar_kw), "#D99A0B", -6, "end");
    labels += label(5.0, 7.8, -0.9, "Huset", kwTxt(f.house_kw), C.green);
    if (f.has_battery) {
      const bk = f.battery_kw;
      const dir = bk == null ? "" : bk >= 0.05 ? "afgiver" : bk <= -0.05 ? "lader" : "";
      labels += label(8.1, 3.0, 3.0, "Batteri", bk == null ? "" : `${kwTxt(bk)} ${dir}`, C.blue);
      if (f.battery_soc != null) {
        const [sx, sy] = P(8.75, 3.1, 1.0);
        labels += socPill(sx + 10, sy, f.battery_soc, 24);
      }
    }
    cars.forEach((c, i) => {
      const at = i === 0 ? [8.75, 6.5, 0.2] : [9.15, 8.3, 0.2];
      const off = c.connected === false;
      // Ikke tilsluttet: kort tekst i titlen, så batteriniveauet står lige under navnet
      const val = off ? (c.soc == null ? "ikke tilsluttet" : "") : c.kw == null ? "" : kwTxt(c.kw);
      let l = label(...at, off && c.soc != null ? `${c.name} · ikke tilsluttet` : c.name, val, C.ev, 14, "start");
      if (c.soc != null) {
        const [sx, sy] = P(...at);
        l += socPill(sx + 14 + (val ? textW(val) + 6 : 0), sy + 5, c.soc);
      }
      labels += off ? `<g opacity="0.5">${l}</g>` : l;
    });

    // Vejret lige nu øverst til venstre
    const w = f.weather || {};
    let wxAria = "";
    if (w.condition || w.temperature != null) {
      const cond = w.condition === "sunny" && w.night ? "clear-night" : w.condition;
      const name = WEATHER_DA[cond] || "";
      svg += this._weatherIcon(cond, -158, -20);
      labels += `<g class="lbl" style="text-anchor:start" transform="translate(-140,-21)">
        ${w.temperature != null ? `<text y="3" class="wx-temp">${nf(w.temperature, 1)} °C</text>` : ""}
        <text y="${w.temperature != null ? 15 : 3}" class="lbl-t">${esc(name || "Ude")}</text></g>`;
      wxAria = `, ude ${w.temperature != null ? `${nf(w.temperature, 1)} grader` : ""} ${name}`;
    }

    return `<div class="scene" role="img" aria-label="Energiflow: huset bruger ${kwTxt(f.house_kw)}${wxAria}">
      <svg viewBox="-176 -34 360 236" preserveAspectRatio="xMidYMid meet">${svg}${labels}</svg></div>`;
  }

  /** Lille vejrikon (sol, måne, skyer, regn, sne, torden, tåge, blæst) centreret i (x, y). */
  _weatherIcon(cond, x, y) {
    const amber = "#F7B21B", blue = "#3A8DDE";
    const sun = (sx, sy, r) => {
      let rays = "";
      for (let i = 0; i < 8; i++) {
        const a = (i * Math.PI) / 4, c = Math.cos(a), s = Math.sin(a);
        rays += `<line x1="${(sx + c * (r + 2.5)).toFixed(1)}" y1="${(sy + s * (r + 2.5)).toFixed(1)}" x2="${(sx + c * (r + 5.5)).toFixed(1)}" y2="${(sy + s * (r + 5.5)).toFixed(1)}"/>`;
      }
      return `<g class="wx-rays" stroke="${amber}" stroke-width="1.8" stroke-linecap="round">${rays}</g><circle cx="${sx}" cy="${sy}" r="${r}" fill="${amber}"/>`;
    };
    const moon = (mx, my) => `<path d="M${mx + 3},${my - 8} A8.5,8.5 0 1 0 ${mx + 7},${my + 5} A7,7 0 0 1 ${mx + 3},${my - 8} Z" fill="#F2D27A"/>`;
    const cloud = (cx, cy, k = 1) => `<path class="wx-cloud" transform="translate(${cx},${cy}) scale(${k})"
      d="M-11,6 L10,6 A5.5,5.5 0 0 0 10,-5 A7.5,7.5 0 0 0 -4,-7 A6.5,6.5 0 0 0 -11,6 Z"/>`;
    const drops = (n, color = blue) => {
      let d = "";
      for (let i = 0; i < n; i++) {
        const dx = -7 + (i * 14) / Math.max(n - 1, 1);
        d += `<line class="wx-drop" style="animation-delay:${(i * 0.37).toFixed(2)}s" x1="${x + dx}" y1="${y + 9}" x2="${x + dx - 1.5}" y2="${y + 13}" stroke="${color}" stroke-width="1.8" stroke-linecap="round"/>`;
      }
      return d;
    };
    // Snefnug som små stjerner, hagl som runde korn
    const flakes = (xs, hail = false) => xs.map((dx, i) => {
      const fx = x + dx, fy = y + 11, r = 2.4;
      const shape = hail
        ? `<circle cx="${fx}" cy="${fy}" r="1.8" fill="#FFFFFF" stroke="#8FA2B4" stroke-width=".8"/>`
        : `<g stroke="#7FA6C9" stroke-width="1.1" stroke-linecap="round"><line x1="${fx - r}" y1="${fy}" x2="${fx + r}" y2="${fy}"/>
            <line x1="${fx - r / 2}" y1="${fy - r * 0.87}" x2="${fx + r / 2}" y2="${fy + r * 0.87}"/><line x1="${fx - r / 2}" y1="${fy + r * 0.87}" x2="${fx + r / 2}" y2="${fy - r * 0.87}"/></g>`;
      return `<g class="wx-flake" style="animation-delay:${(i * 0.8).toFixed(2)}s">${shape}</g>`;
    }).join("");
    const bolt = `<path d="M${x + 1},${y + 5} L${x - 3},${y + 12} L${x},${y + 12} L${x - 2},${y + 18} L${x + 4},${y + 9} L${x + 1},${y + 9} L${x + 3},${y + 5} Z" fill="${amber}"/>`;
    const wind = (wx, wy) => `<g fill="none" stroke="#8C9AA8" stroke-width="1.6" stroke-linecap="round">
      <path d="M${wx - 10},${wy - 3} H${wx + 5} a3,3 0 1 0 -3,-3"/><path d="M${wx - 10},${wy + 2} H${wx + 8} a3,3 0 1 1 -3,3"/></g>`;
    switch (cond) {
      case "sunny": return sun(x, y, 6);
      case "clear-night": return moon(x - 2, y);
      case "partlycloudy": return sun(x - 5, y - 6, 5) + cloud(x + 4, y + 3, 0.72);
      case "cloudy": return cloud(x + 4, y - 3, 0.7) + cloud(x - 1, y + 1);
      case "rainy": return cloud(x, y - 1) + drops(3);
      case "pouring": return cloud(x, y - 1) + drops(5);
      case "snowy": return cloud(x, y - 1) + flakes([-7, 0, 7]);
      case "snowy-rainy": return cloud(x, y - 1) + flakes([-6, 6]) + `<line class="wx-drop" x1="${x}" y1="${y + 9}" x2="${x - 1.5}" y2="${y + 13}" stroke="${blue}" stroke-width="1.8" stroke-linecap="round"/>`;
      case "hail": return cloud(x, y - 1) + flakes([-7, -2, 3, 8], true);
      case "lightning": return cloud(x, y - 1) + bolt;
      case "lightning-rainy": return cloud(x, y - 1) + bolt + drops(2);
      case "fog": return `<g stroke="#9AA7B4" stroke-width="2" stroke-linecap="round"><line x1="${x - 10}" y1="${y - 5}" x2="${x + 10}" y2="${y - 5}"/><line x1="${x - 7}" y1="${y}" x2="${x + 12}" y2="${y}"/><line x1="${x - 12}" y1="${y + 5}" x2="${x + 7}" y2="${y + 5}"/></g>`;
      case "windy": return wind(x, y);
      case "windy-variant": return cloud(x + 3, y - 4, 0.8) + wind(x - 1, y + 6);
      case "exceptional": return `<path d="M${x},${y - 9} L${x + 10},${y + 8} L${x - 10},${y + 8} Z" fill="${amber}"/><text x="${x}" y="${y + 6}" text-anchor="middle" font-size="11" font-weight="700" fill="#3B2A00">!</text>`;
      default: return `<g stroke="#8C9AA8" stroke-width="1.6" fill="none"><circle cx="${x}" cy="${y}" r="7"/></g>`;
    }
  }

  _powerIcon(x, y, r, color, glow) {
    const gap = 0.6;
    const a0 = -Math.PI / 2 + gap, a1 = -Math.PI / 2 - gap + 2 * Math.PI;
    const p = (a) => `${(x + r * Math.cos(a)).toFixed(1)},${(y + r * Math.sin(a)).toFixed(1)}`;
    const ring = `<path d="M${p(a0)} A${r},${r} 0 1 1 ${p(a1)}" fill="none" stroke="${color}" stroke-width="${(r / 3.4).toFixed(1)}" stroke-linecap="round"/>
      <line x1="${x}" y1="${(y - r * 1.15).toFixed(1)}" x2="${x}" y2="${(y - r * 0.15).toFixed(1)}" stroke="${color}" stroke-width="${(r / 3.4).toFixed(1)}" stroke-linecap="round"/>`;
    return glow ? `<g><g filter="url(#sh-glow)" opacity=".7">${ring}</g>${ring}</g>` : ring;
  }

  _connectedPicker() {
    const pre = `select.${this._config.prefix}_connected_car`;
    return Object.keys(this._hass.states)
      .filter((id) => id === pre || id.startsWith(`${pre}_`))
      .map((id) => {
        const st = this._hass.states[id];
        const a = st.attributes;
        const chips = (a.options || []).map((o) => `<button class="chip ${o === st.state ? "on" : ""}${o === "Ingen bil" ? " none" : ""}" data-action="select"
            data-entity="${id}" data-option="${esc(o)}" aria-pressed="${o === st.state}">${esc(o)}</button>`).join("");
        const note = a.none_connected ? (a.detected_automatically ? "Ingen bil er tilsluttet" : "Vælg den bil, der sidder i laderen") : a.detected_automatically ? "Registreret automatisk fra bilen" : "Vælg den bil, der sidder i laderen";
        return `<div class="picker"><div class="picker-head"><ha-icon icon="mdi:ev-plug-type2"></ha-icon><span>Tilsluttet bil</span>
          <span class="picker-note">${note}</span></div><div class="chips">${chips}</div></div>`;
      }).join("");
  }

  _switchRow(it) {
    const st = this._st("switch", it.sw);
    const on = st.state === "on";
    return `
      <div class="step">
        <span class="step-lbl">${esc(it.label)}${it.note ? `<span class="win-note">${esc(it.note)}</span>` : ""}</span>
        ${this._toggle(st.entity_id, on, it.label)}
      </div>`;
  }

  _window(fromKey, toKey) {
    const a = this._st("number", fromKey), b = this._st("number", toKey);
    const fa = parseFloat(a.state), fb = parseFloat(b.state);
    const all = fb - fa >= 24 || fa === fb;
    const p2 = (v) => String(Math.round(v)).padStart(2, "0");
    const mini = (st, v) => `
      <span class="mini">
        <button data-action="step" data-entity="${st.entity_id}" data-dir="-1" aria-label="Tidligere">−</button>
        <span class="mini-val">${p2(v)}</span>
        <button data-action="step" data-entity="${st.entity_id}" data-dir="1" aria-label="Senere">+</button>
      </span>`;
    return `
      <div class="step win">
        <span class="step-lbl">Tidsrum<span class="win-note">${all ? "Hele døgnet" : fa > fb ? "Hen over midnat" : `${Math.round(fb - fa)} timer`}</span></span>
        <span class="win-ctl">${mini(a, fa)}<span class="dash">–</span>${mini(b, fb)}</span>
      </div>`;
  }

  /** Batteriets reserve: forventet husforbrug til strømmen bliver billig igen, og hvad der må sælges. */
  _reserveBox() {
    const r = this._st("sensor", "battery_reserve");
    if (!r) return "";
    const a = r.attributes || {};
    if (!a.profile_ready) {
      return `<div class="cmp wait">SmartHus lærer husets forbrug. Reserven bruges, når der er mindst 2 dages historik (har nu ${nf(a.learned_days || 0, 0)}).</div>`;
    }
    const prof = a.profile_today || [];
    const max = Math.max(...prof, 0.01);
    const nowH = new Date().getHours();
    const bars = prof.map((v, i) => `<i title="kl. ${String(i).padStart(2, "0")}: ${nf(v, 2)} kWh" style="height:${Math.max((v / max) * 100, 3)}%" class="${i === nowH ? "now" : i < nowH ? "past" : ""}"></i>`).join("");
    const until = a.until ? new Date(a.until) : null;
    const untilTxt = until ? `${until.getDate() !== new Date().getDate() ? "i morgen " : ""}kl. ${hh(until)}` : "";
    const bat = a.battery_kwh, sell = a.sellable_kwh ?? 0;
    const floor = a.min_level_kwh || 0, need = a.reserve_kwh || 0;
    const have = bat != null ? Math.min(need, Math.max(bat - floor, 0)) : 0;
    const missing = bat != null ? Math.max(need - have, 0) : 0;
    const total = Math.max((bat ?? 0) + missing, floor + need, 0.01);
    const seg = (v, c, cls = "") => (v > 0.005 ? `<i class="${cls}" style="width:${Math.min(v / total, 1) * 100}%;--c:${c};background:${c}"></i>` : "");
    // Forventet salg: kommende afladningsperioder og salgsprisen i dem
    const sale = this._salePeriod("battery_discharge_now");
    const charge = this._salePeriod("battery_charge_now");
    const leg = (c, t, cls = "") => `<span><i class="${cls}" style="--c:${c};background:${c}"></i>${t}</span>`;
    let result;
    if (sell > 0.05) {
      result = `Batteriet har ${nf(bat, 1)} kWh · må sælge <b>${nf(sell, 1)} kWh</b>${sale ? ` ${esc(sale.when)}` : " i de dyre timer"}`;
      if (sale) result += `<div class="res-sale">Forventet salgspris ca. <b>${nf(sale.avgSale)} kr/kWh</b> → ca. <b>${nf(sell * sale.avgSale, 0)} kr</b></div>`;
    } else {
      result = `Batteriet har ${nf(bat, 1)} kWh · sælger ikke – strømmen skal bruges i huset`;
      if (missing > 0.05) result += `<div class="res-sale">Mangler <b>${nf(missing, 1)} kWh</b> af reserven${charge ? ` – lades ${esc(charge.when)} til ca. ${nf(charge.avgBuy)} kr/kWh` : ""}</div>`;
    }
    const ss = a.sell_solar;
    let solarLine = "";
    if (ss) {
      const why = ss.active
        ? `Salg giver ${nf(ss.sale_now)} kr/kWh, og det koster kun ${nf(ss.charge_price_later)} kr/kWh (plus tab) at lade batteriet fra nettet senere.`
        : !ss.house_covered
          ? "Batteriet skal først have reserven til huset."
          : `Salg giver kun ${nf(ss.sale_now)} kr/kWh, mens det koster ${nf(ss.charge_price_later)} kr/kWh (plus tab) at lade fra nettet senere – så solstrømmen er mere værd i batteriet.`;
      solarLine = `<div class="res-solar ${ss.active ? "on" : ""}"><b>${ss.active ? "Sælger solstrømmen nu" : "Gemmer solstrømmen i batteriet"}</b> · ${why}</div>`;
    }
    const gc = a.grid_charge;
    if (gc) {
      const txt = gc.sell_solar_better
        ? `<b>Lader fuldt fra nettet</b> · solstrømmen kan sælges dyrere midt på dagen (${nf(gc.sale_midday)} kr/kWh).`
        : gc.skipping
          ? `<b>Lader ikke fra nettet</b> · solen forventes at give ca. ${nf(gc.solar_surplus_kwh, 0)} kWh overskud, og batteriet har nok til huset, til solen tager over.`
          : `<b>Lader kun til ${nf(gc.target_soc, 0)} % fra nettet</b> · solen forventes at fylde resten (ca. ${nf(gc.solar_surplus_kwh, 0)} kWh overskud).`;
      solarLine += `<div class="res-solar on">${txt}</div>`;
    }
    return `
      <div class="cmp">
        <div class="cmp-title">Reserve til huset frem til ${untilTxt}</div>
        <div class="res-line">Forventet forbrug <b>${nf(a.expected_house_kwh, 1)} kWh</b>${a.expected_solar_kwh > 0.05 ? ` − sol <b>${nf(a.expected_solar_kwh, 1)} kWh</b>` : ""} → gem <b>${nf(need, 1)} kWh</b></div>
        ${bat != null ? `
        <div class="res-bar">${seg(floor, COLORS.mid)}${seg(have, COLORS.battery_charge)}${seg(missing, COLORS.battery_charge, "miss")}${seg(sell, COLORS.battery_discharge)}</div>
        <div class="res-leg">${leg(COLORS.mid, "Laveste niveau")}${leg(COLORS.battery_charge, "Reserve")}${missing > 0.05 ? leg(COLORS.battery_charge, "Mangler", "miss") : ""}${leg(COLORS.battery_discharge, `Kan sælges${sell > 0.05 ? "" : " (0 kWh)"}`)}</div>
        <div class="cmp-result">${result}</div>${solarLine}` :
        `<div class="cmp-sub" style="margin-left:0">Vælg en sensor for batteriniveau for at se, hvor meget der må sælges.</div>`}
        <div class="prof-title">Husets forbrug pr. time (lært af ${nf(a.learned_days, 0)} dage)</div>
        <div class="prof">${bars}</div>
        <div class="prof-ticks"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div>
      </div>`;
  }

  /** Batteriets handel: hvad salget i de dyre timer gav, efter hvad strømmen kostede. */
  _tradeBox() {
    const tab = ["today", "month", "total"].includes(this._tradeTab) ? this._tradeTab : "today";
    const st = this._st("sensor", `battery_trade_${tab}`);
    if (!st) return "";
    const a = st.attributes || {};
    const tabs = [["today", "I dag"], ["month", "Måned"], ["total", "I alt"]]
      .map(([id, l]) => `<button class="tab ${id === tab ? "on" : ""}" data-action="trade-tab" data-tab="${id}" aria-pressed="${id === tab}">${l}</button>`).join("");
    const cost = (a.cost_grid || 0) + (a.cost_solar || 0);
    const profit = a.profit || 0;
    const sold = a.sold_kwh || 0;
    const row = (l, v, cls = "") => `<div class="tr-row ${cls}"><span>${l}</span><b>${v}</b></div>`;
    const body = sold < 0.05
      ? `<div class="cmp-sub" style="margin-left:0">Batteriet har ikke solgt strøm ${tab === "today" ? "i dag" : tab === "month" ? "denne måned" : "endnu"}.${a.house_kwh > 0.05 ? ` Det har leveret ${nf(a.house_kwh, 1)} kWh til huset (værdi ${nf(a.house_value, 0)} kr).` : ""}</div>`
      : `${row(`Solgt ${nf(sold, 1)} kWh til nettet`, `+${nf(a.income)} kr`)}
         ${a.cost_grid > 0.005 ? row("Købt fra nettet til opladning", `−${nf(a.cost_grid)} kr`, "neg") : ""}
         ${a.cost_solar > 0.005 ? row("Solstrøm der kunne være solgt", `−${nf(a.cost_solar)} kr`, "neg") : ""}
         ${row("Tjent ved salg", `${profit >= 0 ? "+" : "−"}${nf(Math.abs(profit))} kr`, `sum ${profit >= 0 ? "pos" : "neg"}`)}
         <div class="cmp-sub" style="margin-left:0">Snit ${nf(a.income / sold)} kr/kWh solgt mod ${nf(cost / sold)} kr/kWh i indkøb (inkl. ca. 10 % tab).${a.house_kwh > 0.05 ? ` Derudover ${nf(a.house_kwh, 1)} kWh brugt i huset.` : ""}</div>`;
    return `
      <div class="cmp trade">
        <div class="tr-head"><span class="cmp-title" style="margin:0">Batteriets salg</span><span class="tabs" role="group" aria-label="Periode">${tabs}</span></div>
        ${body}
        ${a.avg_cost_in_battery != null ? `<div class="cmp-sub" style="margin-left:0">Strømmen i batteriet nu har kostet ca. ${nf(a.avg_cost_in_battery)} kr/kWh.</div>` : ""}
      </div>`;
  }

  /** Kommende perioder for en plan (fx afladning) med gennemsnitlig salgs- og købspris. */
  _salePeriod(key) {
    const all = (this._st("binary_sensor", key)?.attributes?.slots || []).map((t) => new Date(t).getTime())
      .filter((t) => t + 900e3 > Date.now()).sort((x, y) => x - y);
    if (!all.length) return null;
    // Kun den næste sammenhængende periode – det er den, der gælder frem til næste opladning
    const slots = [all[0]];
    for (const t of all.slice(1)) { if (t - slots[slots.length - 1] <= 900e3) slots.push(t); else break; }
    const ps = this._st("sensor", "current_price")?.attributes || {};
    const byStart = new Map([...(ps.today || []), ...(ps.tomorrow || [])].map((p) => [new Date(p.start).getTime(), p]));
    const hits = slots.map((t) => byStart.get(t) || byStart.get(t - (t % 3600e3))).filter(Boolean);
    if (!hits.length) return null;
    const avg = (f) => hits.reduce((x, p) => x + (p[f] ?? 0), 0) / hits.length;
    const first = upcoming(slots.map((t) => new Date(t).toISOString()))[0];
    const when = first ? (first.active ? `nu–${first.text.split("–")[1]}` : first.text) : "";
    return { when, avgSale: avg("sale"), avgBuy: avg("price") };
  }

  _evCompare(cid) {
    const s = this._st("sensor", `${cid}_smart_saving`);
    if (!s) return "";
    const a = s.attributes;
    if (a.target_reached) {
      return `<div class="cmp done">Bilen har nået målet på ${nf(a.target_soc, 0)} %. Der lades ikke mere.</div>`;
    }
    if (a.short_of_time) {
      const ready = a.ready_at ? new Date(a.ready_at) : null;
      const mins = ready ? Math.max(0, Math.round((ready.getTime() - Date.now()) / 60000)) : null;
      const left = mins == null ? "" : mins < 90 ? `${mins} min` : `${nf(mins / 60, 1)} timer`;
      return `<div class="cmp warn-box">
        <div class="cmp-title">Ikke tid nok til ${nf(a.target_soc, 0)} %</div>
        <div class="cmp-result">Der er kun ${left} til kl. ${ready ? hh(ready) : ""}. Bilen når ca. <b>${nf(a.soc_at_ready, 0)} %</b> (fra ${nf(a.soc, 0)} %)${this._st("switch", `control_${cid}`)?.state === "on" ? ", så der lades med det samme" : ""}.</div>
        <div class="cmp-sub" style="margin-left:0">Ryk "Klar kl." senere, hvis bilen skal nå ${nf(a.target_soc, 0)} % – så vælges de billigste timer igen.</div>
      </div>`;
    }
    if (a.cost_now == null || a.cost_planned == null) {
      return `<div class="cmp wait">Prisen for opladningen beregnes, når morgendagens elpriser er kendt (normalt ca. kl. 13).</div>`;
    }
    const maxc = Math.max(a.cost_now, a.cost_planned, 0.01);
    const save = a.cost_now - a.cost_planned;
    const when = this._periods(`${cid}_charge_now`).join(", ");
    const row = (label, cost, price, color) => `
      <div class="cmp-row">
        <span class="cmp-lbl">${label}</span>
        <span class="cmp-bar"><i style="width:${Math.max((cost / maxc) * 100, 2)}%;background:${color}"></i></span>
        <span class="cmp-val">${nf(cost)} kr</span>
      </div>
      <div class="cmp-sub">${nf(price)} kr/kWh i snit</div>`;
    return `
      <div class="cmp">
        <div class="cmp-title">${nf(a.energy_kwh, 1)} kWh${a.soc != null ? ` fra ${nf(a.soc, 0)} % til ${nf(a.target_soc, 0)} %` : ""}</div>
        ${row("Lad nu", a.cost_now, a.price_now, COLORS.expensive)}
        ${row("Billigste timer", a.cost_planned, a.price_planned, COLORS.cheap)}
        <div class="cmp-result">${save > 0.005
          ? `Du sparer <b>${nf(save)} kr</b> ved at lade ${esc(when || "i de billigste timer")}`
          : "Det er lige så billigt at lade nu"}</div>
        ${a.limited_by_power ? `<div class="cmp-warn">Den samlede ladeeffekt er begrænset, så der er ikke plads til hele opladningen inden klar-tidspunktet. Hæv grænsen eller klar-tidspunktet.</div>` : ""}
      </div>`;
  }

  _stepper(key) {
    const s = this._st("number", key);
    const v = parseFloat(s.state);
    const unit = s.attributes.unit_of_measurement || "";
    const step = s.attributes.step || 1;
    const dec = step < 0.1 ? 2 : step < 1 ? (step % 0.5 === 0 ? 1 : 2) : 0;
    const trim = (x) => Number(x.toFixed(2)).toLocaleString("da-DK", { maximumFractionDigits: 2 });
    const shown =
      unit === "kl." ? `kl. ${String(v).padStart(2, "0")}` :
      unit === "h" ? `${trim(v)} t` :
      `${nf(v, dec)} ${esc(unit)}`;
    return `
      <div class="step">
        <span class="step-lbl">${esc(this._label(key))}${key === "ev_max_total_power" ? `<span class="win-note">0 = ingen grænse</span>` : key === "hotwater_surplus_kw" ? `<span class="win-note">0 = fra · eksport lige nu ${nf(this._st("binary_sensor", "hotwater_now")?.attributes?.export_kw, 1)} kW</span>` : ""}</span>
        <span class="step-ctl">
          <button data-action="step" data-entity="${s.entity_id}" data-dir="-1" aria-label="Mindre ${esc(this._label(key))}">−</button>
          <span class="step-val">${shown}</span>
          <button data-action="step" data-entity="${s.entity_id}" data-dir="1" aria-label="Mere ${esc(this._label(key))}">+</button>
        </span>
      </div>`;
  }

  _toggle(entity, on, label) {
    return `<button class="tgl ${on ? "on" : ""}" role="switch" aria-checked="${on}" aria-label="${esc(label)}"
      data-action="toggle" data-entity="${entity}"><span></span></button>`;
  }

  // --- Handlinger -------------------------------------------------------
  _onClick(ev) {
    const el = ev.composedPath().find((n) => n.dataset && n.dataset.action);
    if (!el) return;
    const { action, entity } = el.dataset;
    if (action === "toggle") {
      this._hass.callService("switch", "toggle", { entity_id: entity });
    } else if (action === "panel") {
      const id = el.dataset.panel;
      this._panel = this._panel === id ? null : id;
      this._panelAnim = !!this._panel;
      this._render();
      if (this._panel) requestAnimationFrame(() => this.shadowRoot.querySelector(".panel")?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
    } else if (action === "close-panel") {
      this._panel = null;
      this._render();
      requestAnimationFrame(() => this.shadowRoot.querySelector(".dock")?.scrollIntoView({ block: "nearest", behavior: "smooth" }));
    } else if (action === "expand") {
      const id = el.dataset.mod;
      this._closed.has(id) ? this._closed.delete(id) : this._closed.add(id);
      this._render();
    } else if (action === "trade-tab") {
      this._tradeTab = el.dataset.tab;
      this._render();
    } else if (action === "solar-tab") {
      this._solarTab = el.dataset.tab;
      this._solarSel = null;
      try { localStorage.setItem("smarthus-card-solar-tab", this._solarTab); } catch (e) { /* ingen lagring */ }
      this._render();
    } else if (action === "solar-bar") {
      this._solarSel = this._solarSel === el.dataset.k ? null : el.dataset.k;
      this._render();
    } else if (action === "bar") {
      const i = parseInt(el.dataset.i, 10);
      this._sel = this._sel === i ? null : i;
      this._render();
    } else if (action === "step") {
      const s = this._hass.states[entity];
      const step = s.attributes.step || 1;
      const min = s.attributes.min ?? -Infinity;
      const max = s.attributes.max ?? Infinity;
      let v = parseFloat(s.state) + step * parseInt(el.dataset.dir, 10);
      v = Math.min(max, Math.max(min, Math.round(v / step) * step));
      this._hass.callService("number", "set_value", { entity_id: entity, value: Number(v.toFixed(3)) });
    } else if (action === "select") {
      this._hass.callService("select", "select_option", { entity_id: entity, option: el.dataset.option });
    } else if (action === "more") {
      this.dispatchEvent(new CustomEvent("hass-more-info", { bubbles: true, composed: true, detail: { entityId: entity } }));
    }
  }

  // --- Stil -------------------------------------------------------------
  _css() {
    return `<style>
      :host { --sh-track: color-mix(in srgb, var(--primary-text-color) 7%, transparent);
              --sh-line: var(--divider-color, rgba(127,127,127,.2)); }
      ha-card { display: block; padding: 16px 0 6px; overflow: hidden; color: var(--primary-text-color); container-type: inline-size; }
      .top { display: grid; grid-template-columns: minmax(0, 1fr); grid-template-areas: "head" "scene" "solar" "strip"; }
      .top > .strip { grid-area: strip; }
      .top > .scene { grid-area: scene; }
      .top > .head { grid-area: head; }
      .top > .solar { grid-area: solar; }
      .sec-title { display: flex; align-items: center; gap: 6px; margin-top: 14px; font-size: 12px; font-weight: 600;
                   letter-spacing: .04em; text-transform: uppercase; color: var(--secondary-text-color); }
      .sec-title ha-icon { --mdc-icon-size: 18px; color: ${COLORS.solar_sold}; }
      .parts { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
      .part { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px 5px 8px; border-radius: 999px;
              background: var(--sh-track); font-size: 12px; }
      .part ha-icon { --mdc-icon-size: 16px; color: var(--secondary-text-color); }
      .p-lbl { color: var(--secondary-text-color); }
      .p-val { font-weight: 600; font-variant-numeric: tabular-nums; }
      .p-val.neg { color: ${COLORS.expensive}; }

      .solar { padding: 4px 16px 14px; border-top: 1px solid var(--sh-line); }
      .solar-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
      .solar-head .sec-title { margin-top: 10px; }
      .tabs { display: inline-flex; margin-top: 10px; padding: 2px; border-radius: 999px; background: var(--sh-track); }
      .tab { padding: 4px 11px; border-radius: 999px; font-size: 12px; color: var(--secondary-text-color); }
      .tab.on { background: var(--card-background-color, #fff); color: var(--primary-text-color); font-weight: 600; box-shadow: 0 1px 2px rgba(0,0,0,.15); }
      .solar-big { display: flex; align-items: baseline; flex-wrap: wrap; gap: 0 6px; margin-top: 8px; text-align: left; }
      .solar-big .value { font-size: 36px; line-height: 1.05; font-weight: 700; font-variant-numeric: tabular-nums; letter-spacing: -.02em; }
      .solar-big .unit { font-size: 14px; color: var(--secondary-text-color); }
      .big-lbl { flex-basis: 100%; font-size: 12px; color: var(--secondary-text-color); }
      .kpis { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin-top: 12px; border-top: 1px solid var(--sh-line); }
      .kpi { display: flex; flex-direction: column; align-items: flex-start; padding: 10px 0 0; text-align: left; min-width: 0; }
      .kpi + .kpi { padding-left: 12px; border-left: 1px solid var(--sh-line); }
      .k-lbl { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--secondary-text-color); }
      .k-lbl i, .lg-dot { display: inline-block; width: 9px; height: 9px; border-radius: 2px; }
      .lg-dot { margin: 0 4px 0 8px; vertical-align: -1px; }
      .lg-dot:first-child { margin-left: 0; }
      .k-val { font-size: 17px; font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
      .k-sub { font-size: 11px; color: var(--secondary-text-color); }
      .roi { display: block; width: 100%; text-align: left; margin-top: 12px; padding: 10px 12px; border-radius: 12px; background: rgba(63,168,143,.10); border: 1px solid rgba(63,168,143,.25); color: inherit; font: inherit; cursor: pointer; }
      .roi-top { display: flex; justify-content: space-between; align-items: baseline; }
      .roi-lbl { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 600; }
      .roi-lbl ha-icon { --mdc-icon-size: 18px; color: ${COLORS.cheap}; }
      .roi-val { font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; color: ${COLORS.cheap}; }
      .roi-bar { height: 8px; border-radius: 4px; background: rgba(127,127,127,.18); overflow: hidden; margin-top: 8px; }
      .roi-bar i { display: block; height: 100%; border-radius: 4px; background: linear-gradient(90deg, ${COLORS.cheap}, #7BC043); }
      .roi-sub { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; font-size: 11px; color: var(--secondary-text-color); margin-top: 6px; }
      .share { display: flex; height: 8px; border-radius: 4px; overflow: hidden; margin-top: 14px; gap: 2px; }
      .share i { display: block; height: 100%; }
      .share-lbl { display: flex; justify-content: space-between; font-size: 11px; color: var(--secondary-text-color); margin-top: 4px; }
      .schart { margin-top: 14px; }
      .fc { margin-top: 14px; }
      .grid-title { margin-top: 14px; }
      .fc-title { font-size: 12px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; color: var(--secondary-text-color); }
      .fc-kpis { margin-top: 6px; }
      .fc-chart { margin-top: 10px; }
      .fc-chart svg { display: block; width: 100%; height: 56px; }
      .fc-now { stroke: var(--primary-text-color); stroke-width: 1; stroke-dasharray: 2 2; vector-effect: non-scaling-stroke; opacity: .6; }
      .schart svg { display: block; width: 100%; height: 80px; }
      .sbar { cursor: pointer; }
      .sbar:not(.sel) rect + rect { opacity: .85; }
      .sbar.sel rect + rect { stroke: var(--primary-text-color); stroke-width: 1; vector-effect: non-scaling-stroke; }
      .muted { color: var(--secondary-text-color); }
      .ticks.c span { transform: translateX(-50%); white-space: nowrap; }
      .solar-empty { margin-top: 12px; padding: 12px; border-radius: 10px; background: var(--sh-track); font-size: 13px; color: var(--secondary-text-color); }

      /* Fuld bredde: pris og besparelse ved siden af solcellerne, moduler i kolonner */
      @container (min-width: 760px) {
        .top { grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr); grid-template-areas: "scene scene" "head solar" "strip strip"; }
        .top.no-solar { grid-template-areas: "head scene" "strip strip"; align-items: center; }
        .top.no-scene { grid-template-areas: "head solar" "strip strip"; }
        .top.no-solar > .scene { border-left: 1px solid var(--sh-line); padding-left: 8px; }
        .top > .solar { border-top: 0; border-left: 1px solid var(--sh-line); padding-top: 0; padding-left: 20px; }
        .top > .solar .solar-head .sec-title, .top > .solar .tabs { margin-top: 0; }
        .legend { grid-template-columns: repeat(3, minmax(0, 1fr)); column-gap: 24px; }
        .lg { grid-template-columns: 10px minmax(0, 1fr); }
        .lg-val { grid-column: 2; text-align: left; font-size: 12px; margin-top: -4px; }
        .panel-mods.multi { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .panel-mods.multi .mod:nth-child(odd) { border-right: 1px solid var(--sh-line); }
        .panel .mod-body { columns: 2; column-gap: 32px; }
        .panel-mods.multi .mod-body { columns: 1; }
      }
      @container (min-width: 900px) {
        /* Prisgrafen rykker op under pris og energiflow, ved siden af solcellerne */
        .top { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1.2fr); grid-template-rows: auto 1fr;
               grid-template-areas: "head scene solar" "strip strip solar"; align-items: start; }
        .top > .solar { align-self: stretch; }
        .top > .strip { align-self: start; }
        .top.no-solar { grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr); grid-template-areas: "head scene" "strip strip"; }
        .top.no-scene { grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr); grid-template-areas: "head solar" "strip solar"; }
        .top > .scene { padding: 0 8px; border-left: 1px solid var(--sh-line); }
        .top > .scene svg { max-height: 250px; }
      }
      /* Tablet (900–1199 px): tre kolonner, men pris over teksten og mere plads til huset */
      @container (min-width: 900px) and (max-width: 1199px) {
        .top { grid-template-columns: minmax(0, .9fr) minmax(0, 1.2fr) minmax(0, 1fr); }
        .top > .scene svg { max-height: 280px; }
        .head .now { flex-direction: column; align-items: flex-start; gap: 4px; }
        .legend { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .legend .lg-val { text-align: left; }
        .clock .clock-date.long { display: none; }
        .clock .clock-date.short { display: block; }
        .savings .s-val { font-size: 16px; white-space: nowrap; }
      }
      button { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
      button:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; border-radius: 6px; }
      .empty { padding: 16px; color: var(--secondary-text-color); }
      .head { padding: 0 16px 12px; }
      .title-row { display: flex; align-items: center; justify-content: space-between; }
      .brand { display: flex; align-items: center; gap: 10px; }
      .logo { width: 34px; height: 34px; object-fit: contain; }
      .title { font-size: 15px; font-weight: 600; letter-spacing: .01em; color: var(--secondary-text-color); }
      .now { display: flex; align-items: flex-end; gap: 14px; margin-top: 6px; }
      .title-end { display: flex; align-items: center; gap: 14px; min-width: 0; }
      .clock { display: flex; flex-direction: column; align-items: flex-end; text-align: right; min-width: 0; }
      .clock-time { font-size: 20px; line-height: 1.1; font-weight: 600; font-variant-numeric: tabular-nums; }
      .clock-date.short { display: none; }
      .clock-date { font-size: 12px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
      .price { display: flex; align-items: baseline; gap: 6px; }
      .price .value { font-size: 44px; line-height: 1; font-weight: 700; font-variant-numeric: tabular-nums; letter-spacing: -.02em; }
      .price .unit { font-size: 14px; color: var(--secondary-text-color); }
      .now-meta { padding-bottom: 3px; }
      .level-text { font-size: 14px; font-weight: 600; }
      .level-cheap .level-text { color: ${COLORS.cheap}; }
      .level-expensive .level-text { color: ${COLORS.expensive}; }
      .sub { font-size: 12px; color: var(--secondary-text-color); }
      .status { margin-top: 10px; font-size: 14px; cursor: pointer; }
      .savings { display: grid; grid-template-columns: repeat(3, 1fr); margin-top: 12px; border-top: 1px solid var(--sh-line); }
      .saving { display: flex; flex-direction: column; align-items: flex-start; padding: 10px 0 0; text-align: left; }
      .saving + .saving { padding-left: 12px; border-left: 1px solid var(--sh-line); }
      .s-val { font-size: 17px; font-weight: 600; font-variant-numeric: tabular-nums; }
      .s-lbl { font-size: 12px; color: var(--secondary-text-color); }

      .strip { padding: 12px 16px 4px; border-top: 1px solid var(--sh-line); }
      .strip.empty { color: var(--secondary-text-color); padding: 16px; }
      .strip-info { font-size: 13px; color: var(--secondary-text-color); min-height: 18px; margin-bottom: 8px; }
      .strip-info b { color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
      .chart { position: relative; padding-top: 16px; }
      .bars { display: block; width: 100%; height: 110px; }
      .bar { cursor: pointer; }
      .bar.sel { stroke: var(--primary-text-color); stroke-width: 1; vector-effect: non-scaling-stroke; }
      .nowline { stroke: var(--primary-text-color); stroke-width: 1.5; vector-effect: non-scaling-stroke; }
      .midline { stroke: var(--sh-line); stroke-width: 1; stroke-dasharray: 3 3; vector-effect: non-scaling-stroke; }
      .daylbl { position: absolute; top: 0; font-size: 11px; color: var(--secondary-text-color); padding-left: 4px; }
      .daylbl.first { left: 0; padding-left: 0; }
      .lanes { display: block; width: 100%; margin-top: 6px; }
      .ticks { position: relative; height: 16px; margin-top: 4px; font-size: 11px; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
      .ticks span { position: absolute; transform: translateX(-2px); }
      .legend { display: grid; gap: 4px; margin: 6px 0 8px; }
      .lg { display: grid; grid-template-columns: 10px auto 1fr; align-items: center; gap: 8px; font-size: 13px; }
      .lg.off { opacity: .5; }
      .lg i { width: 10px; height: 10px; border-radius: 2px; }
      .lg-val { color: var(--secondary-text-color); text-align: right; font-variant-numeric: tabular-nums; }

      .modules { border-top: 1px solid var(--sh-line); }
      /* Knapper for områderne */
      .dock { display: grid; grid-template-columns: repeat(auto-fit, minmax(64px, 1fr)); gap: 8px; padding: 14px 16px; border-top: 1px solid var(--sh-line); }
      .tile { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 10px 4px 8px; border-radius: 14px; min-width: 0;
              transition: background .15s, box-shadow .15s; }
      .tile:hover { background: var(--sh-track); }
      .tile:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
      .tile-ic { position: relative; display: grid; place-items: center; width: 52px; height: 52px; border-radius: 16px;
                 background: color-mix(in srgb, #1E9E5C 12%, transparent); box-shadow: inset 0 0 0 1px color-mix(in srgb, #1E9E5C 30%, transparent); }
      .tile-dot { position: absolute; top: -3px; right: -3px; width: 12px; height: 12px; border-radius: 50%;
                  background: var(--secondary-text-color); opacity: .5; box-shadow: 0 0 0 2px var(--card-background-color, #fff); }
      .tile.on .tile-dot { background: #1E9E5C; opacity: 1; }
      .tile.sel { background: color-mix(in srgb, #1E9E5C 10%, transparent); box-shadow: inset 0 0 0 1.5px #1E9E5C; }
      .tile.sel .tile-ic { background: #1E9E5C; --sh-ic: #FFFFFF; box-shadow: none; }
      .tile-name { font-size: 13px; font-weight: 600; max-width: 100%; text-align: center; line-height: 1.25; overflow-wrap: anywhere; }
      .tile-line { font-size: 11.5px; color: var(--secondary-text-color); max-width: 100%; text-align: center; line-height: 1.3;
                   display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
      @container (max-width: 520px) { .tile-line { display: none; } .tile-ic { width: 46px; height: 46px; } .tile-name { font-size: 12px; } .dock { gap: 4px; padding: 12px 10px; } }
      /* Indstillingspanel */
      section.panel { margin: 0 16px 12px; border-radius: 16px; box-shadow: inset 0 0 0 1px var(--sh-line); overflow: hidden; }
      section.panel.anim { animation: sh-panel .18s ease-out; }
      @keyframes sh-panel { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: none; } }
      @media (prefers-reduced-motion: reduce) { section.panel { animation: none; } }
      .panel-head { display: flex; align-items: center; gap: 12px; padding: 12px 12px 12px 16px; background: color-mix(in srgb, #1E9E5C 8%, transparent); border-bottom: 1px solid var(--sh-line); }
      .panel-ic { display: grid; place-items: center; width: 40px; height: 40px; border-radius: 12px; background: #1E9E5C; --sh-ic: #FFFFFF; }
      .panel-ic svg { width: 26px; height: 26px; }
      .panel-title { flex: 1; display: flex; flex-direction: column; font-size: 16px; font-weight: 700; }
      .panel-title span { font-size: 12px; font-weight: 400; color: var(--secondary-text-color); }
      .panel-close { display: inline-flex; align-items: center; gap: 6px; padding: 8px 14px; border-radius: 20px; font-size: 14px; font-weight: 600;
                     box-shadow: inset 0 0 0 1px var(--sh-line); background: var(--card-background-color, #fff); }
      .panel-close:hover { background: var(--sh-track); }
      .panel .mod { border-bottom: 1px solid var(--sh-line); }
      .panel .mod:last-child { border-bottom: 0; }
      .panel .mod-main { padding-left: 16px; }
      .panel .mod-body { padding-left: 16px; }
      .panel .picker { border-bottom: 1px solid var(--sh-line); }
      .panel .mod-body > * { break-inside: avoid; }
      .ver { padding: 8px 16px 4px; font-size: 11px; color: var(--secondary-text-color); text-align: right; opacity: .7; }
      .mod { border-bottom: 1px solid var(--sh-line); }
      .mod:last-child { border-bottom: 0; }
      .mod-head { display: flex; align-items: center; gap: 8px; padding: 0 16px 0 0; }
      .mod-main { flex: 1; display: flex; align-items: center; gap: 14px; padding: 12px 0 12px 16px; text-align: left; min-width: 0; }
      .mod-main ha-icon { color: var(--secondary-text-color); --mdc-icon-size: 22px; }
      .mod.on .mod-main > ha-icon:first-child { color: var(--primary-color); }
      .mod-text { display: flex; flex-direction: column; min-width: 0; flex: 1; }
      .mod-name { font-size: 15px; font-weight: 600; }
      .mod-line { font-size: 13px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .chev-lbl { font-size: 12px; color: var(--secondary-text-color); white-space: nowrap; }
      .chev { transition: transform .2s; --mdc-icon-size: 20px; }
      .mod.open .chev { transform: rotate(180deg); }
      .mod-body { padding: 0 16px 14px 52px; }

      .tgl { width: 40px; height: 24px; border-radius: 12px; background: var(--sh-track); position: relative; flex: none;
             box-shadow: inset 0 0 0 1px var(--sh-line); transition: background .2s; }
      .tgl span { position: absolute; top: 3px; left: 3px; width: 18px; height: 18px; border-radius: 50%;
                  background: var(--card-background-color, #fff); box-shadow: 0 1px 2px rgba(0,0,0,.3); transition: transform .2s; }
      .tgl.on { background: var(--primary-color); box-shadow: none; }
      .tgl.on span { transform: translateX(16px); }

      .cmp { padding: 12px; border-radius: 10px; background: var(--sh-track); margin-bottom: 12px; font-size: 13px; }
      .cmp.done, .cmp.wait { color: var(--secondary-text-color); }
      .cmp-title { font-weight: 600; margin-bottom: 8px; }
      .cmp-row { display: grid; grid-template-columns: 96px 1fr auto; align-items: center; gap: 10px; }
      .cmp-bar { height: 10px; border-radius: 5px; background: color-mix(in srgb, var(--primary-text-color) 6%, transparent); overflow: hidden; }
      .cmp-bar i { display: block; height: 100%; border-radius: 5px; }
      .cmp-val { font-variant-numeric: tabular-nums; font-weight: 600; min-width: 64px; text-align: right; }
      .cmp-sub { margin: 1px 0 8px 106px; font-size: 11px; color: var(--secondary-text-color); }
      .cmp-result { margin-top: 2px; font-size: 14px; }
      .cmp.warn-box { box-shadow: inset 3px 0 0 ${COLORS.expensive}; }
      .cmp.warn-box .cmp-title { color: ${COLORS.expensive}; }
      .cmp-warn { margin-top: 8px; font-size: 12px; color: ${COLORS.expensive}; }
      .cmp-result b { color: ${COLORS.cheap}; font-variant-numeric: tabular-nums; }
      .res-line { font-size: 13px; }
      .res-line b { font-variant-numeric: tabular-nums; }
      .res-bar { display: flex; gap: 2px; height: 10px; border-radius: 5px; overflow: hidden; margin: 10px 0 4px;
                 background: color-mix(in srgb, var(--primary-text-color) 6%, transparent); }
      .res-bar i { display: block; height: 100%; }
      .res-bar i.miss, .res-leg i.miss { background: repeating-linear-gradient(135deg, var(--c) 0 3px, transparent 3px 6px) !important; opacity: .7; }
      .tr-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
      .tr-head .tabs { margin-top: 0; }
      .tr-row { display: flex; justify-content: space-between; gap: 10px; padding: 3px 0; font-size: 13px; }
      .tr-row b { font-variant-numeric: tabular-nums; }
      .tr-row.neg b { color: var(--secondary-text-color); font-weight: 500; }
      .tr-row.sum { border-top: 1px solid var(--sh-line); margin-top: 4px; padding-top: 6px; font-size: 14px; }
      .tr-row.sum.pos b { color: ${COLORS.cheap}; }
      .tr-row.sum.neg b { color: ${COLORS.expensive}; font-weight: 700; }
      .res-sale { margin-top: 4px; font-size: 13px; color: var(--secondary-text-color); }
      .res-sale b { color: var(--primary-text-color); font-variant-numeric: tabular-nums; }
      .res-solar { margin-top: 10px; padding: 8px 10px; border-radius: 8px; font-size: 12.5px; line-height: 1.45; color: var(--secondary-text-color);
                   background: color-mix(in srgb, ${COLORS.solar_sold || "#E0A21B"} 10%, transparent); }
      .res-solar b { color: var(--primary-text-color); }
      .res-solar.on { box-shadow: inset 3px 0 0 ${COLORS.solar_sold || "#E0A21B"}; }
      .res-leg { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 11px; color: var(--secondary-text-color); margin-bottom: 6px; }
      .res-leg i { display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; }
      .prof-title { margin-top: 12px; font-size: 12px; color: var(--secondary-text-color); }
      .prof { display: flex; align-items: flex-end; gap: 2px; height: 44px; margin-top: 6px; }
      .prof i { flex: 1; border-radius: 2px 2px 0 0; background: ${COLORS.battery_charge}; opacity: .8; }
      .prof i.past { opacity: .3; }
      .prof i.now { opacity: 1; outline: 1px solid var(--primary-text-color); }
      .prof-ticks { display: flex; justify-content: space-between; font-size: 10px; color: var(--secondary-text-color); margin-top: 2px; }

      .grp { padding-top: 6px; }
      .grp + .grp { margin-top: 6px; border-top: 1px dashed var(--sh-line); }
      .grp-title { font-size: 12px; font-weight: 600; color: var(--secondary-text-color); padding: 6px 0 0; }
      .water { padding: 4px 0 10px; }
      .water-row { display: flex; justify-content: space-between; align-items: baseline; font-size: 14px; }
      .water-row b { font-size: 18px; font-variant-numeric: tabular-nums; }
      .water-bar { height: 8px; border-radius: 4px; background: var(--sh-track); margin-top: 6px; overflow: hidden; }
      .water-bar i { display: block; height: 100%; border-radius: 4px; background: ${COLORS.hotwater || "#2BA3C9"}; }
      .water-sub { margin-top: 4px; font-size: 12px; color: var(--secondary-text-color); }
      .scene { padding: 6px 8px 0; }
      .scene svg { display: block; width: 100%; height: auto; max-height: 300px; overflow: visible; }
      .scene .fl-base { fill: none; stroke-width: 2.2; opacity: .18; stroke-linecap: round; }
      .scene .fl-dash { fill: none; stroke-width: 2.6; stroke-dasharray: 2 9; stroke-linecap: round; animation: sh-flow linear infinite; }
      .scene .lbl text { paint-order: stroke; stroke: var(--card-background-color, #fff); stroke-width: 3px; }
      .scene .lbl-t { font-size: 8.5px; fill: var(--secondary-text-color); }
      .scene .soc-shell { fill: var(--card-background-color, #fff); stroke: #8C9AA8; stroke-width: 1.2; }
      .scene .soc-nub { fill: #8C9AA8; }
      .scene .soc-t { font-size: 10px; font-weight: 700; fill: var(--primary-text-color); font-variant-numeric: tabular-nums;
                      paint-order: stroke; stroke: var(--card-background-color, #fff); stroke-width: 3px; }
      .scene .lbl-v { font-size: 10.5px; font-weight: 700; font-variant-numeric: tabular-nums; }
      .scene .panel.on { filter: drop-shadow(0 0 2.5px rgba(247,178,27,.7)); }
      .scene .bolt.on { filter: drop-shadow(0 0 3px rgba(247,178,27,.9)); }
      .scene .wifi path { opacity: .35; }
      .scene .wifi.on path { animation: sh-wifi 2.4s ease-in-out infinite; }
      .scene .wifi.on path:nth-child(2) { animation-delay: .3s; }
      .scene .wifi.on path:nth-child(3) { animation-delay: .6s; }
      @keyframes sh-wifi { 0%, 100% { opacity: .25; } 40% { opacity: 1; } }
      .scene .wx-rays { animation: sh-spin 24s linear infinite; transform-box: fill-box; transform-origin: center; }
      .scene .wx-drop { animation: sh-drop 1.2s linear infinite; }
      .scene .wx-flake { animation: sh-drop 2.6s linear infinite; }
      .scene .wx-cloud { fill: #EEF2F5; stroke: #B9C4CE; stroke-width: 1; }
      ha-card.dark .scene .wx-cloud { fill: #C9D2DA; stroke: #8996A3; }
      .scene .wx-temp { font-size: 15px; font-weight: 700; fill: var(--primary-text-color); font-variant-numeric: tabular-nums; }
      @keyframes sh-spin { to { transform: rotate(360deg); } }
      @keyframes sh-drop { 0% { transform: translateY(-3px); opacity: 0; } 30% { opacity: 1; } 100% { transform: translateY(7px); opacity: 0; } }
      .scene .steam path { animation: sh-steam 2.4s ease-in-out infinite; }
      @keyframes sh-flow { to { stroke-dashoffset: -22; } }
      @keyframes sh-steam { 0%, 100% { opacity: .2; } 50% { opacity: .9; } }
      @media (prefers-reduced-motion: reduce) { .scene .fl-dash, .scene .steam path, .scene .wifi.on path, .scene .wx-rays, .scene .wx-drop, .scene .wx-flake { animation: none; } }
      .picker { padding: 12px 16px; border-bottom: 1px solid var(--sh-line); }
      .picker-head { display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 600; }
      .picker-head ha-icon { --mdc-icon-size: 20px; color: var(--secondary-text-color); }
      .picker-note { margin-left: auto; font-size: 12px; font-weight: 400; color: var(--secondary-text-color); }
      .chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
      .chip { padding: 7px 14px; border-radius: 18px; font-size: 14px; box-shadow: inset 0 0 0 1px var(--sh-line); }
      .chip.on { background: var(--primary-color); color: var(--text-primary-color, #fff); box-shadow: none; }
      .hint { font-size: 13px; color: var(--secondary-text-color); }
      .win-note { display: block; font-size: 11px; color: var(--secondary-text-color); }
      .win-ctl { display: flex; align-items: center; gap: 2px; }
      .dash { color: var(--secondary-text-color); padding: 0 2px; }
      .mini { display: flex; align-items: center; }
      .mini button { width: 28px; height: 28px; border-radius: 50%; font-size: 16px; line-height: 1; box-shadow: inset 0 0 0 1px var(--sh-line); }
      .mini button:active { background: var(--sh-track); }
      .mini-val { min-width: 26px; text-align: center; font-weight: 600; font-variant-numeric: tabular-nums; }
      @media (max-width: 420px) { .mod-body { padding-left: 16px; } }
      .step { display: flex; justify-content: space-between; align-items: center; min-height: 40px; font-size: 14px; }
      .step-ctl { display: flex; align-items: center; gap: 4px; }
      .step-ctl button { width: 32px; height: 32px; border-radius: 50%; font-size: 18px; line-height: 1;
                         box-shadow: inset 0 0 0 1px var(--sh-line); }
      .step-ctl button:active { background: var(--sh-track); }
      .step-val { min-width: 76px; text-align: center; font-variant-numeric: tabular-nums; font-weight: 600; }
      .action { margin-top: 10px; width: 100%; display: flex; align-items: center; justify-content: center; gap: 8px;
                padding: 10px; border-radius: 10px; font-weight: 600; box-shadow: inset 0 0 0 1px var(--primary-color); color: var(--primary-color); }
      .action.active { background: var(--primary-color); color: var(--text-primary-color, #fff); }
      .action ha-icon { --mdc-icon-size: 18px; }
      @media (prefers-reduced-motion: reduce) { .chev, .tgl, .tgl span { transition: none; } }
    </style>`;
  }
}

if (!customElements.get("smarthus-card")) {
  customElements.define("smarthus-card", SmartHusCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "smarthus-card",
    name: "SmartHus",
    description: "Elpris, plan, besparelser og styring af batteri, elbil, varmt vand og varme.",
    preview: false,
    documentationURL: "https://github.com/SmartHus-DK/Smarthus.dk",
  });
  console.info(`%c SMARTHUS-CARD %c v${SMARTHUS_CARD_VERSION} `, "background:#3FA88F;color:#fff;border-radius:3px 0 0 3px", "background:#333;color:#fff;border-radius:0 3px 3px 0");
}

/*
 * SmartHus-kort til Home Assistant
 * Følger med SmartHus-integrationen og registreres automatisk.
 * Brug:  type: custom:smarthus-card
 */
const SMARTHUS_CARD_VERSION = "1.7.0";

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
      { title: "Grænser", items: ["battery_min_spread", "battery_min_soc", "battery_max_soc"] },
    ],
  },
  {
    id: "hotwater", name: "Varmt vand", icon: "mdi:water-boiler", sw: "control_hotwater",
    groups: [
      { title: null, items: ["hotwater_hours", ["hotwater_from", "hotwater_to"]] },
      { title: "Temperatur", items: ["hotwater_boost_temp", "hotwater_normal_temp"] },
    ],
  },
  {
    id: "heating", name: "Varme", icon: "mdi:heat-pump", sw: "control_heating",
    groups: [
      { title: null, items: ["heating_base_temp"] },
      { title: "Ekstra varme i billige timer", items: ["heating_boost_degrees", "heating_boost_hours", ["heating_boost_from", "heating_boost_to"]] },
      { title: "Spar i dyre timer", items: ["heating_reduce_degrees", "heating_reduce_hours", ["heating_reduce_from", "heating_reduce_to"]] },
    ],
  },
  { id: "solar", name: "Solceller", icon: "mdi:solar-power", sw: "control_solar", groups: [] },
];

const CAR_COLORS = [COLORS.ev, COLORS.ev2, COLORS.ev3];

/** Et felt pr. elbil. Bilens id er fx "ev" eller "ev_tesla". */
function carModule(cid, name, first) {
  const groups = [
    { title: null, items: [`${cid}_target_soc`, `${cid}_ready_hour`, `${cid}_hours`] },
    { title: "Bil og lader", items: [`${cid}_battery_capacity`, `${cid}_charge_power`] },
  ];
  if (first) groups.push({ title: "Alle elbiler", items: ["ev_max_total_power"] });
  return { id: cid, car: true, name, icon: "mdi:car-electric", sw: `control_${cid}`, groups };
}

const EV_LABELS = {
  target_soc: "Lad til",
  ready_hour: "Klar kl.",
  charge_power: "Ladeeffekt",
  battery_capacity: "Batteristørrelse",
  hours: "Timer at lade",
};

const NUMBER_LABELS = {
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
  hotwater_hours: "Timer at opvarme",
  hotwater_boost_temp: "Opvarm til",
  hotwater_normal_temp: "Normal temperatur",
  heating_base_temp: "Grundtemperatur",
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
      }));
  }
  _forceKey(cid) {
    return cid === "ev" ? "ev_force" : `${cid}_force`;
  }
  _modules() {
    const cars = this._cars().map((c, i) => carModule(c.cid, c.name, i === 0));
    return [...cars, ...MODULES].filter((m) => this._st("switch", m.sw));
  }
  _label(key) {
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
    if (!priceState) {
      this.shadowRoot.innerHTML = `<ha-card><div class="empty">Kunne ikke finde SmartHus-integrationen. Tjek at den er installeret, eller angiv <code>prefix</code> i kortets opsætning.</div></ha-card>${this._css()}`;
      return;
    }
    this.shadowRoot.innerHTML = `
      ${this._css()}
      <ha-card>
        <div class="top">
          ${this._renderHeader()}
          ${this._renderSolar()}
        </div>
        ${this._renderStrip()}
        <div class="modules">
          ${this._modules().map((m) => this._renderModule(m)).join("")}
        </div>
      </ha-card>`;
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
          ${master ? this._toggle(master.entity_id, masterOn, masterOn ? "Styring til" : "Styring fra") : ""}
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
    if (!st) return "";
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
        <div class="kpis">
          ${this._kpi("Produceret", `${nf(produced, produced >= 100 ? 0 : 1)} kWh`, null, `solar_produced_${tab}`)}
          ${this._kpi("Sparet", `${nf(a.self_value, 0)} kr`, `${nf(a.self_kwh, 0)} kWh brugt selv`, `solar_saved_${tab}`, COLORS.solar_self)}
          ${this._kpi("Tjent ved salg", noExport ? "–" : `${nf(a.export_income, 0)} kr`, noExport ? "Kræver eksportmåler" : `${nf(a.export_kwh, 0)} kWh solgt`, `solar_sold_${tab}`, COLORS.solar_sold)}
        </div>
        ${produced > 0 && !noExport ? `
        <div class="share" aria-label="Egetforbrug ${Math.round(selfShare * 100)} procent">
          <i style="width:${selfShare * 100}%;background:${COLORS.solar_self}"></i><i style="width:${(1 - selfShare) * 100}%;background:${COLORS.solar_sold}"></i>
        </div>
        <div class="share-lbl"><span>${Math.round(selfShare * 100)} % brugt selv</span><span>${Math.round((1 - selfShare) * 100)} % solgt</span></div>` : ""}
        ${this._solarChart(tab)}
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
    const carLanes = this._cars().map((c) => ({ key: `${c.cid}_charge_now`, label: `${c.name} lader`, color: c.color, module: `control_${c.cid}` }));
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

  _renderModule(m) {
    const sw = this._st("switch", m.sw);
    const on = sw.state === "on";
    const open = !this._closed.has(m.id);
    const line = this._moduleLine(m, on);
    const body = open ? this._moduleBody(m) : "";
    return `
      <section class="mod ${open ? "open" : ""} ${on ? "on" : ""}">
        <div class="mod-head">
          <button class="mod-main" data-action="expand" data-mod="${m.id}" aria-expanded="${open}">
            <ha-icon icon="${m.icon}"></ha-icon>
            <span class="mod-text"><span class="mod-name">${m.name}</span><span class="mod-line">${esc(line)}</span></span>
            <span class="chev-lbl">${open ? "Skjul" : "Indstillinger"}</span><ha-icon class="chev" icon="mdi:chevron-down"></ha-icon>
          </button>
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
      if (isOn(k)) return `${prefix}Lader nu${a.soc != null ? ` · ${nf(a.soc, 0)} %` : ""}`;
      const n = next(k);
      return `${prefix}${n ? `Lader ${n}` : "Venter på priser"}${a.soc != null ? ` · ${nf(a.soc, 0)} % af ${nf(a.target_soc, 0)} %` : ""}`;
    }
    switch (m.id) {
      case "battery":
        if (isOn("battery_charge_now")) return `${prefix}Lader fra nettet`;
        if (isOn("battery_discharge_now")) return `${prefix}Bruger batteriet`;
        return `${prefix}${next("battery_charge_now") ? `Lader ${next("battery_charge_now")}` : "Normal drift"}`;
      case "hotwater":
        return `${prefix}${isOn("hotwater_now") ? "Opvarmer nu" : next("hotwater_now") ? `Opvarmer ${next("hotwater_now")}` : "Ingen plan"}`;
      case "heating":
        if (isOn("heating_boost_now")) return `${prefix}Ekstra varme nu`;
        if (isOn("heating_reduce_now")) return `${prefix}Sparer på varmen nu`;
        return `${prefix}${next("heating_boost_now") ? `Ekstra varme ${next("heating_boost_now")}` : "Normal drift"}`;
      case "solar":
        return `${prefix}${isOn("solar_curtail_now") ? "Begrænser eksport (negativ pris)" : "Normal eksport"}`;
    }
    return "";
  }

  _moduleBody(m) {
    let extra = "";
    if (m.car) extra = this._evCompare(m.id);
    if (m.id === "solar") extra = `<div class="hint">Når salgsprisen er negativ, begrænses eksporten automatisk. Der er ingen indstillinger.</div>`;
    const evHasSoc = m.car && this._st("sensor", `${m.id}_smart_saving`)?.attributes?.soc != null;
    const hide = (k) =>
      (m.car && evHasSoc && k === `${m.id}_hours`) ||
      (m.car && !evHasSoc && [`${m.id}_target_soc`, `${m.id}_battery_capacity`].includes(k));
    const groups = m.groups
      .map((g) => {
        const rows = g.items
          .map((it) => {
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

  _evCompare(cid) {
    const s = this._st("sensor", `${cid}_smart_saving`);
    if (!s) return "";
    const a = s.attributes;
    if (a.target_reached) {
      return `<div class="cmp done">Bilen har nået målet på ${nf(a.target_soc, 0)} %. Der lades ikke mere.</div>`;
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
        <span class="step-lbl">${esc(this._label(key))}${key === "ev_max_total_power" ? `<span class="win-note">0 = ingen grænse</span>` : ""}</span>
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
    } else if (action === "expand") {
      const id = el.dataset.mod;
      this._closed.has(id) ? this._closed.delete(id) : this._closed.add(id);
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
      .top { display: grid; grid-template-columns: minmax(0, 1fr); }
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
      .share { display: flex; height: 8px; border-radius: 4px; overflow: hidden; margin-top: 14px; gap: 2px; }
      .share i { display: block; height: 100%; }
      .share-lbl { display: flex; justify-content: space-between; font-size: 11px; color: var(--secondary-text-color); margin-top: 4px; }
      .schart { margin-top: 14px; }
      .schart svg { display: block; width: 100%; height: 80px; }
      .sbar { cursor: pointer; }
      .sbar:not(.sel) rect + rect { opacity: .85; }
      .sbar.sel rect + rect { stroke: var(--primary-text-color); stroke-width: 1; vector-effect: non-scaling-stroke; }
      .muted { color: var(--secondary-text-color); }
      .ticks.c span { transform: translateX(-50%); white-space: nowrap; }
      .solar-empty { margin-top: 12px; padding: 12px; border-radius: 10px; background: var(--sh-track); font-size: 13px; color: var(--secondary-text-color); }

      /* Fuld bredde: pris og besparelse ved siden af solcellerne, moduler i kolonner */
      @container (min-width: 760px) {
        .top { grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr); }
        .top > .solar { border-top: 0; border-left: 1px solid var(--sh-line); padding-top: 0; padding-left: 20px; }
        .top > .solar .solar-head .sec-title, .top > .solar .tabs { margin-top: 0; }
        .legend { grid-template-columns: repeat(3, minmax(0, 1fr)); column-gap: 24px; }
        .lg { grid-template-columns: 10px minmax(0, 1fr); }
        .lg-val { grid-column: 2; text-align: left; font-size: 12px; margin-top: -4px; }
        .modules { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .mod { border-bottom: 1px solid var(--sh-line); }
        .mod:nth-child(odd) { border-right: 1px solid var(--sh-line); }
        .mod:last-child { border-bottom: 1px solid var(--sh-line); }
      }
      @container (min-width: 1200px) {
        .modules { grid-template-columns: repeat(3, minmax(0, 1fr)); }
        .mod:nth-child(odd) { border-right: 0; }
        .mod:not(:nth-child(3n)) { border-right: 1px solid var(--sh-line); }
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
      .cmp-warn { margin-top: 8px; font-size: 12px; color: ${COLORS.expensive}; }
      .cmp-result b { color: ${COLORS.cheap}; font-variant-numeric: tabular-nums; }

      .grp { padding-top: 6px; }
      .grp + .grp { margin-top: 6px; border-top: 1px dashed var(--sh-line); }
      .grp-title { font-size: 12px; font-weight: 600; color: var(--secondary-text-color); padding: 6px 0 0; }
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

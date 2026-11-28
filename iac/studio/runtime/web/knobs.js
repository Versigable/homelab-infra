// studio-knobs: the numbers of a prototype, on sliders.
//
//   import { knobs } from 'studio-knobs';
//   const k = knobs({ speed: [6, 0, 20], jump: [9, 0, 30, 0.5], gravity: 25 });
//   ... use k.speed, k.jump, k.gravity every frame: they change as the sliders move.
//
// Each entry is [value, min, max, step] or just a value (its range is then 0 to twice it).
// The panel sits in a corner, folds away, and shows the tuning as one line of text
// (speed=7&jump=9.5) that a person can copy into the issue. The same text after # in the
// page's address sets the knobs at the start: the portal puts a pasted tuning there.
// A prototype runs shut in a frame with no storage and no network, so nothing is saved.

function fromHash() {
  const out = {};
  for (const part of decodeURIComponent(location.hash.slice(1)).split(/[&,]/)) {
    const [name, raw] = part.split('=');
    const v = Number(raw);
    if (name && raw !== undefined && raw !== '' && Number.isFinite(v)) out[name] = v;
  }
  return out;
}

function el(tag, style, text) {
  const e = document.createElement(tag);
  if (style) e.style.cssText = style;
  if (text !== undefined) e.textContent = text;
  return e;
}

export function knobs(spec, { title = 'Knobs' } = {}) {
  const given = fromHash();
  const values = {};
  const defs = {};
  for (const [name, d] of Object.entries(spec)) {
    const [value, min, max, step] = Array.isArray(d) ? d : [d];
    const lo = min ?? Math.min(0, value * 2);
    const hi = max ?? (value === 0 ? 1 : Math.max(0, value * 2));
    defs[name] = { value, min: lo, max: hi, step: step ?? (hi - lo) / 200 };
    const g = given[name];
    values[name] = g === undefined ? value : Math.min(hi, Math.max(lo, g));
  }

  const panel = el('div', 'position:fixed;top:8px;right:8px;z-index:9999;background:rgba(16,20,24,.88);color:#e6e6e6;' +
    'font:12px system-ui,sans-serif;border:1px solid #2a323c;border-radius:6px;padding:6px 8px;max-width:260px;' +
    'max-height:calc(100vh - 16px);overflow:auto;user-select:none');
  const head = el('div', 'cursor:pointer;font-weight:600', '▾ ' + title);
  const body = el('div', 'margin-top:6px');
  const code = el('input', 'width:100%;box-sizing:border-box;margin-top:6px;background:#0b0e11;color:#9aa4ae;' +
    'border:1px solid #2a323c;border-radius:4px;font:11px ui-monospace,monospace;padding:3px');
  code.readOnly = true;
  code.title = 'This tuning, as text. Copy it into the issue.';
  code.addEventListener('focus', () => code.select());
  const rows = {};

  const fmt = (v) => String(Math.round(v * 1000) / 1000);
  const refresh = () => {
    code.value = Object.keys(defs).map((n) => `${n}=${fmt(values[n])}`).join('&');
    for (const [n, r] of Object.entries(rows)) {
      r.slider.value = values[n];
      r.shown.textContent = fmt(values[n]);
    }
  };

  for (const [name, d] of Object.entries(defs)) {
    const row = el('label', 'display:grid;grid-template-columns:1fr auto;gap:2px 8px;margin:4px 0');
    const shown = el('span', 'color:#7cc4ff;font-variant-numeric:tabular-nums');
    const slider = el('input', 'grid-column:1 / 3;width:100%;margin:0');
    slider.type = 'range';
    slider.min = d.min;
    slider.max = d.max;
    slider.step = d.step;
    slider.addEventListener('input', () => {
      values[name] = Number(slider.value);
      refresh();
    });
    // The game's keys shouldn't move a slider, and a slider shouldn't keep the keyboard.
    slider.addEventListener('keydown', (e) => e.preventDefault());
    slider.addEventListener('pointerup', () => slider.blur());
    row.append(el('span', '', name), shown, slider);
    body.append(row);
    rows[name] = { slider, shown };
  }

  const reset = el('button', 'margin-top:4px;font:inherit;background:#26323d;color:#e6e6e6;border:1px solid #4a5a6a;' +
    'border-radius:4px;padding:1px 8px;cursor:pointer', 'Reset');
  reset.addEventListener('click', () => {
    for (const [n, d] of Object.entries(defs)) values[n] = d.value;
    refresh();
    reset.blur();
  });
  body.append(reset, code);
  head.addEventListener('click', () => {
    const open = body.style.display !== 'none';
    body.style.display = open ? 'none' : '';
    head.textContent = (open ? '▸ ' : '▾ ') + title;
  });
  panel.append(head, body);
  // Clicks on the panel aren't the game's.
  for (const ev of ['pointerdown', 'mousedown', 'click', 'wheel']) panel.addEventListener(ev, (e) => e.stopPropagation());
  const mount = () => document.body.append(panel);
  if (document.body) mount(); else addEventListener('DOMContentLoaded', mount);
  refresh();
  return values;
}

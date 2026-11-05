// Small hand-written SVG charts (no chart library needed).
import { esc, fmt } from './util.js';

export function barChart({
  labels, values, color = 'var(--accent)', highlight = [], height = 150, unit = '',
  refValue = null, refLabel = '', digits = 1, muted = [],
}) {
  const w = 600;
  const top = 22;
  const bottom = 26;
  const h = height;
  const max = Math.max(...values.filter((v) => v != null), refValue ?? 0) * 1.12 || 1;
  const n = values.length;
  const slot = w / n;
  const bw = Math.min(46, slot * 0.62);
  const y = (v) => top + (h - top - bottom) * (1 - v / max);
  const bars = values.map((v, i) => {
    const x = i * slot + (slot - bw) / 2;
    if (v == null) {
      return `<text x="${x + bw / 2}" y="${h - bottom - 4}" class="c-val" text-anchor="middle">–</text>`;
    }
    const fill = highlight.includes(i) ? 'var(--warn)' : muted.includes(i) ? 'var(--line-strong)' : color;
    return `<rect x="${x}" y="${y(v)}" width="${bw}" height="${h - bottom - y(v)}" rx="3" fill="${fill}"><title>${esc(labels[i])}: ${fmt(v, digits)} ${esc(unit)}</title></rect>
      <text x="${x + bw / 2}" y="${y(v) - 5}" class="c-val" text-anchor="middle">${fmt(v, digits)}</text>`;
  }).join('');
  const xl = labels.map((l, i) => `<text x="${i * slot + slot / 2}" y="${h - 8}" class="c-lab" text-anchor="middle">${esc(l)}</text>`).join('');
  const ref = refValue == null ? '' : `<line x1="0" x2="${w}" y1="${y(refValue)}" y2="${y(refValue)}" class="c-ref"/>
    <text x="4" y="${y(refValue) + 14}" class="c-ref-lab" text-anchor="start">${esc(refLabel)} ${fmt(refValue, digits)}</text>`;
  return `<svg class="chart" viewBox="0 0 ${w} ${h}" style="aspect-ratio:${w} / ${h}" role="img" preserveAspectRatio="none">${bars}${ref}${xl}</svg>`;
}

export function progressBar(done, total, color = 'var(--accent)') {
  const pct = total ? Math.max(0, Math.min(100, (done / total) * 100)) : 0;
  return `<div class="pbar"><div class="pbar-fill" style="width:${pct}%;background:${color}"></div></div>`;
}

export function stackedBar(parts) {
  return `<div class="sbar">${parts.map(([value, color, label]) => (
    `<div style="width:${value * 100}%;background:${color}" title="${esc(label)} ${fmt(value * 100, 0)}%"></div>`
  )).join('')}</div>`;
}

// Horizontal bars as HTML (labels wrap nicely, works at any width). rows: [[label, value], ...]
export function hbarChart(rows, { color = 'var(--accent)', colors = null, digits = 0 } = {}) {
  if (!rows.length) return '<p class="empty">No data in this range.</p>';
  const max = Math.max(...rows.map(([, v]) => v), 1);
  return `<div class="hbars">${rows.map(([label, v], i) => `<div class="hbar"><span class="hbar-l">${esc(label)}</span>
    <span class="hbar-t"><span class="hbar-f" style="width:${(v / max) * 100}%;background:${colors ? colors[i] : color}"></span></span>
    <span class="hbar-v">${fmt(v, digits)}</span></div>`).join('')}</div>`;
}

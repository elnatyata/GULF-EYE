// Shared helpers: labels, formatting, dates, geometry.
export const VESSEL_TYPES = {
  cargo: { name: 'Cargo', color: '#5B9BD5' },
  tanker: { name: 'Tanker', color: '#B58CE0' },
  fishing: { name: 'Fishing', color: '#3FBF9A' },
  supply: { name: 'Supply', color: '#E8B04A' },
  tug: { name: 'Tug', color: '#9AA7B5' },
  passenger: { name: 'Passenger / ferry', color: '#F472B6' },
};

export const OP_LABELS = {
  patrol: 'Offshore patrol',
  rhib: 'RHIB interception',
  air: 'Air surveillance',
  sar: 'SAR small-boat detection',
};

export const TYPE_LABELS = {
  dark_vessel: 'Dark vessel',
  ais_gap: 'AIS gap',
  sts: 'Possible transfer',
  report: 'Field report',
  dispatch: 'Dispatch',
  resolve: 'Resolved',
  refuel: 'Refuel',
};

export const RISK = {
  critical: { label: 'Critical', color: '#FF4D5A' },
  high: { label: 'High', color: '#F0556A' },
  medium: { label: 'Medium', color: '#F5A524' },
  low: { label: 'Low', color: '#8A97A6' },
};

export const SOURCES = {
  ais: { label: 'AIS', title: 'AIS positions (Global Fishing Watch / AISStream via Neptune-AIS). Synthetic in this demo.' },
  sar: { label: 'SAR', title: 'Sentinel-1 radar detections (YOLOv8 vessel detector). Simulated output in this demo.' },
  captured: { label: 'Captured', title: 'Logged in this app, by text or voice' },
  forecast: { label: 'Forecast', title: 'Open-Meteo wind and wave forecast (snapshot)' },
  simulated: { label: 'Simulated', title: 'Simulated for the demo' },
  rule: { label: 'Rule of thumb', title: 'Threshold set by the team, to be confirmed by operators' },
  reference: { label: 'Reference', title: 'Regional baseline (simulated by the team)' },
};

export const typeName = (t) => VESSEL_TYPES[t]?.name ?? t;
export const typeColor = (t) => VESSEL_TYPES[t]?.color ?? '#9AA7B5';

export function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
  ));
}

export function fmt(value, digits = 1) {
  if (value == null || Number.isNaN(Number(value))) return '–';
  return Number(value).toLocaleString('en-GB', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtQty(value, unit) {
  if (value == null) return '–';
  if (unit === 'L' && Math.abs(value) >= 10000) return `${fmt(value / 1000, 1)} kL`;
  return `${fmt(value, 0)} ${unit}`;
}

export function badge(source) {
  const s = SOURCES[source];
  if (!s) return '';
  return `<span class="badge badge-${source}" title="${esc(s.title)}">${esc(s.label)}</span>`;
}
export const badges = (list) => list.map(badge).join('');

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

export const parseDate = (iso) => new Date(iso.length === 10 ? `${iso}T12:00:00Z` : iso);
export const isoDate = (d) => d.toISOString().slice(0, 10);
export const addDays = (iso, n) => isoDate(new Date(parseDate(iso).getTime() + n * 86400000));
export const daysBetween = (a, b) => Math.round((parseDate(b) - parseDate(a)) / 86400000);
export const monthName = (m) => MONTHS[m - 1];
export const minutesBetween = (a, b) => (parseDate(b) - parseDate(a)) / 60000;
export const addMinutes = (iso, m) => new Date(parseDate(iso).getTime() + m * 60000).toISOString();

export function fmtDate(iso, withYear = false) {
  const d = parseDate(iso);
  return `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}${withYear ? ` ${d.getUTCFullYear()}` : ''}`;
}
export function fmtTime(iso) {
  const d = parseDate(iso);
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`;
}
export const fmtDateTime = (iso) => `${fmtDate(iso)} ${fmtTime(iso)} UTC`;
export function fmtDay(iso) {
  const d = parseDate(iso);
  return `${WEEKDAYS[d.getUTCDay()]} ${d.getUTCDate()}`;
}
export function fmtLongDate(iso) {
  const d = parseDate(iso);
  const long = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  return `${long[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}
export function fmtDuration(hours) {
  if (hours == null) return '–';
  const h = Math.floor(hours);
  const m = Math.round((hours - h) * 60);
  if (h === 0) return `${m} min`;
  return m === 60 ? `${h + 1} h` : `${h} h ${String(m).padStart(2, '0')}`;
}
export function fmtDateRuns(isos) {
  if (!isos.length) return 'none';
  const runs = [];
  let start = isos[0];
  let prev = isos[0];
  for (const iso of isos.slice(1)) {
    if (daysBetween(prev, iso) === 1) { prev = iso; continue; }
    runs.push([start, prev]);
    start = iso;
    prev = iso;
  }
  runs.push([start, prev]);
  const month = (iso) => MONTHS[parseDate(iso).getUTCMonth()];
  const sameMonth = runs.every(([a, b]) => month(a) === month(runs[0][0]) && month(b) === month(runs[0][0]));
  const day = (iso) => parseDate(iso).getUTCDate();
  const part = ([a, b]) => {
    if (sameMonth) return a === b ? `${day(a)}` : `${day(a)}–${day(b)}`;
    return a === b ? fmtDate(a) : `${fmtDate(a)}–${fmtDate(b)}`;
  };
  return sameMonth ? `${runs.map(part).join(', ')} ${month(runs[0][0])}` : runs.map(part).join(', ');
}

export const sum = (xs) => xs.reduce((a, b) => a + (b ?? 0), 0);
export const mean = (xs) => {
  const v = xs.filter((x) => x != null);
  return v.length ? sum(v) / v.length : null;
};

// ---- geometry (lat/lon in degrees, distances in km or nautical miles)
const KM_PER_DEG = 111.32;
export const KM_PER_NM = 1.852;
export function haversineKm(lat1, lon1, lat2, lon2) {
  const r = (x) => (x * Math.PI) / 180;
  const a = Math.sin(r(lat2 - lat1) / 2) ** 2 + Math.cos(r(lat1)) * Math.cos(r(lat2)) * Math.sin(r(lon2 - lon1) / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(a));
}
export const nmBetween = (a, b) => haversineKm(a.lat, a.lon, b.lat, b.lon) / KM_PER_NM;
export function moveBy(lat, lon, bearingDeg, km) {
  const b = (bearingDeg * Math.PI) / 180;
  return {
    lat: lat + (km * Math.cos(b)) / KM_PER_DEG,
    lon: lon + (km * Math.sin(b)) / (KM_PER_DEG * Math.cos((lat * Math.PI) / 180)),
  };
}
export function pointInPoly(lon, lat, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i, i += 1) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if ((yi > lat) !== (yj > lat) && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
export function fmtCoord(lat, lon) {
  return `${fmt(Math.abs(lat), 2)}°${lat >= 0 ? 'N' : 'S'} ${fmt(Math.abs(lon), 2)}°${lon >= 0 ? 'E' : 'W'}`;
}

export function weatherIcon(code) {
  if (code === 0) return { icon: '☀️', text: 'Clear' };
  if (code <= 2) return { icon: '🌤️', text: 'Partly cloudy' };
  if (code === 3) return { icon: '☁️', text: 'Overcast' };
  if (code === 45 || code === 48) return { icon: '🌫️', text: 'Fog' };
  if (code >= 51 && code <= 57) return { icon: '🌦️', text: 'Drizzle' };
  if (code >= 61 && code <= 67) return { icon: '🌧️', text: 'Rain' };
  if (code >= 80 && code <= 82) return { icon: '🌦️', text: 'Showers' };
  if (code >= 95) return { icon: '⛈️', text: 'Thunderstorm' };
  return { icon: '🌥️', text: 'Cloudy' };
}

export const isAlertId = (id) => /^[DGR]-/.test(id || '');
export const shortMmsi = (id) => `…${String(id).slice(-4)}`;

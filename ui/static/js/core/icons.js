// core/icons.js — the inline SVG icon set (builder B). 24x24 grid, stroke=currentColor.
// Icons are static data built with createElementNS; unknown names render a circle.

const SVG_NS = 'http://www.w3.org/2000/svg';

// Each icon: list of path strings or [tag, attrs] tuples.
const ICONS = {
  plus: ['M12 5v14M5 12h14'],
  chat: ['M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z'],
  bolt: ['M13 2 3 14h9l-1 8 10-12h-9l1-8z'],
  trash: ['M3 6h18M8 6V4h8v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6'],
  edit: ['M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z'],
  copy: [['rect', { x: 9, y: 9, width: 13, height: 13, rx: 2 }], 'M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1'],
  download: ['M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3'],
  upload: ['M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12'],
  settings: [['circle', { cx: 12, cy: 12, r: 3 }], 'M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z'],
  stats: ['M3 3v18h18', 'M7 15l4-4 3 3 5-6'],
  templates: [['rect', { x: 3, y: 3, width: 7, height: 7, rx: 1.5 }], ['rect', { x: 14, y: 3, width: 7, height: 7, rx: 1.5 }], ['rect', { x: 3, y: 14, width: 7, height: 7, rx: 1.5 }], ['rect', { x: 14, y: 14, width: 7, height: 7, rx: 1.5 }]],
  batch: ['M12 2 2 7l10 5 10-5-10-5z', 'M2 17l10 5 10-5', 'M2 12l10 5 10-5'],
  compare: ['M9 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h4', 'M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4', 'M12 2v20'],
  inspect: ['M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2', 'M7 9h10M7 12h6M7 15h8'],
  image: [['rect', { x: 3, y: 3, width: 18, height: 18, rx: 2 }], ['circle', { cx: 8.5, cy: 8.5, r: 1.5 }], 'M21 15l-5-5L5 21'],
  send: ['M12 19V5', 'M5 12l7-7 7 7'],
  stop: [['rect', { x: 6, y: 6, width: 12, height: 12, rx: 2, fill: 'currentColor' }]],
  sun: [['circle', { cx: 12, cy: 12, r: 4 }], 'M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41'],
  moon: ['M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z'],
  monitor: [['rect', { x: 2, y: 3, width: 20, height: 14, rx: 2 }], 'M8 21h8M12 17v4'],
  check: ['M20 6 9 17l-5-5'],
  x: ['M18 6 6 18M6 6l12 12'],
  alert: ['M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z', 'M12 9v4M12 17h.01'],
  refresh: ['M23 4v6h-6M1 20v-6h6', 'M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15'],
  'chevron-down': ['M6 9l6 6 6-6'],
  'chevron-right': ['M9 18l6-6-6-6'],
  'chevron-up': ['M18 15l-6-6-6 6'],
  menu: ['M3 12h18M3 6h18M3 18h18'],
  search: [['circle', { cx: 11, cy: 11, r: 7.5 }], 'M21 21l-4.35-4.35'],
  pin: ['M12 17v5', 'M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z'],
  code: ['M16 18l6-6-6-6M8 6l-6 6 6 6'],
  json: ['M8 3H7a2 2 0 0 0-2 2v5a2 2 0 0 1-2 2 2 2 0 0 1 2 2v5a2 2 0 0 0 2 2h1', 'M16 21h1a2 2 0 0 0 2-2v-5a2 2 0 0 1 2-2 2 2 0 0 1-2-2V5a2 2 0 0 0-2-2h-1'],
  play: ['M6 4l14 8-14 8z'],
  sparkles: ['M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z', 'M19 16l.7 1.8 1.8.7-1.8.7L19 21l-.7-1.8-1.8-.7 1.8-.7z'],
  clock: [['circle', { cx: 12, cy: 12, r: 9.5 }], 'M12 6.5V12l3.5 2'],
  cpu: [['rect', { x: 4, y: 4, width: 16, height: 16, rx: 2 }], ['rect', { x: 9, y: 9, width: 6, height: 6 }], 'M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3'],
  eye: ['M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z', ['circle', { cx: 12, cy: 12, r: 3 }]],
  'eye-off': ['M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19M14.12 14.12a3 3 0 1 1-4.24-4.24', 'M1 1l22 22'],
  link: ['M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71', 'M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71'],
  grip: [['circle', { cx: 9, cy: 5, r: 1 }], ['circle', { cx: 9, cy: 12, r: 1 }], ['circle', { cx: 9, cy: 19, r: 1 }], ['circle', { cx: 15, cy: 5, r: 1 }], ['circle', { cx: 15, cy: 12, r: 1 }], ['circle', { cx: 15, cy: 19, r: 1 }]],
  circle: [['circle', { cx: 12, cy: 12, r: 9 }]],
  // B extras
  more: [['circle', { cx: 5, cy: 12, r: 1.2, fill: 'currentColor' }], ['circle', { cx: 12, cy: 12, r: 1.2, fill: 'currentColor' }], ['circle', { cx: 19, cy: 12, r: 1.2, fill: 'currentColor' }]],
  sidebar: [['rect', { x: 3, y: 3, width: 18, height: 18, rx: 2 }], 'M9 3v18'],
  paperclip: ['M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48'],
  info: [['circle', { cx: 12, cy: 12, r: 9.5 }], 'M12 16v-4M12 8h.01'],
  sigma: ['M18 7V4H6l6 8-6 8h12v-3'],
  keyboard: [['rect', { x: 2, y: 5, width: 20, height: 14, rx: 2 }], 'M6 9h.01M10 9h.01M14 9h.01M18 9h.01M6 13h.01M18 13h.01M8 16h8M10 13h4'],
  'corner-down-right': ['M15 10l5 5-5 5', 'M4 4v7a4 4 0 0 0 4 4h12'],
  scale: ['M12 3v18M7 21h10M4 7h16', 'M6 7l-3 7a3.2 3.2 0 0 0 6 0zM18 7l-3 7a3.2 3.2 0 0 0 6 0z'],
  terminal: ['M4 17l6-6-6-6M12 19h8'],
  duplicate: [['rect', { x: 8, y: 8, width: 13, height: 13, rx: 2 }], 'M4 16V5a2 2 0 0 1 2-2h11', 'M14.5 11.5v6M11.5 14.5h6'],
  logo: [['circle', { cx: 12, cy: 12, r: 9.5 }], 'M8.5 12.5l2.5 2.5 5-6'],
  hash: ['M4 9h16M4 15h16M10 3 8 21M16 3l-2 18'],
  thumbsup: ['M7 10v12M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88z'],
};

export function icon(name, size = 16) {
  const def = ICONS[name] || ICONS.circle;
  const el = document.createElementNS(SVG_NS, 'svg');
  el.setAttribute('viewBox', '0 0 24 24');
  el.setAttribute('width', String(size));
  el.setAttribute('height', String(size));
  el.setAttribute('fill', 'none');
  el.setAttribute('stroke', 'currentColor');
  el.setAttribute('stroke-width', '1.8');
  el.setAttribute('stroke-linecap', 'round');
  el.setAttribute('stroke-linejoin', 'round');
  el.setAttribute('aria-hidden', 'true');
  el.setAttribute('class', `icon icon-${name}`);
  for (const part of def) {
    let child;
    if (typeof part === 'string') {
      child = document.createElementNS(SVG_NS, 'path');
      child.setAttribute('d', part);
    } else {
      const [tag, attrs] = part;
      child = document.createElementNS(SVG_NS, tag);
      for (const [k, v] of Object.entries(attrs)) child.setAttribute(k, String(v));
    }
    el.appendChild(child);
  }
  return el;
}

export const ICON_NAMES = Object.keys(ICONS);

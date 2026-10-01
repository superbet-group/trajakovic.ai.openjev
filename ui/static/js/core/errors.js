// core/errors.js — error cards for NormalizedError and the upstream-down banner (builder B).
// Each card: status badge, title, message, error_type, request id, hint, Raw disclosure,
// kind-specific extras (422 loc list, retry-after countdown with one auto-retry) and actions.

import { h, clear, copyText } from '/js/core/dom.js';
import { icon } from '/js/core/icons.js';
import { emit } from '/js/core/bus.js';
import { toast } from '/js/core/toast.js';
import { getSettings } from '/js/core/store.js';
import { getCachedConfig, nextHealthCheckAt, checkHealthNow } from '/js/core/api.js';
import { fmtBytes, shortId } from '/js/core/format.js';

function cmdChip(cmd) {
  return h('button', { class: 'cmd-chip mono', title: 'Copy command', onClick: (ev) => { ev.stopPropagation(); copyText(cmd); } },
    icon('terminal', 13), h('span', {}, cmd), icon('copy', 12));
}

function locPath(loc) {
  const parts = (loc || []).filter((x, i) => !(i === 0 && x === 'body'));
  return parts.map(String).join(' › ') || '(body)';
}

export function renderError(error, { onRetry, onFix, onEdit, autoRetry, requestBytes } = {}) {
  const e = error || { kind: 'unknown', title: 'Unknown error', message: '' };
  const settings = getSettings();
  const config = getCachedConfig();
  const warnKinds = new Set(['rate_limited', 'overloaded', 'auth', 'forbidden', 'client', 'aborted']);
  const card = h('div', { class: ['err-card', `err-${e.kind}`, warnKinds.has(e.kind) && 'err-warn'], role: 'alert' });
  const actions = h('div', { class: 'err-actions row' });

  card.appendChild(h('div', { class: 'err-head' },
    h('span', { class: 'badge err-badge mono' }, e.status ? String(e.status) : e.kind),
    h('span', { class: 'err-title' }, e.title || 'Error'),
    h('span', { class: 'spacer' }),
    e.requestId ? h('button', { class: 'err-rid mono', title: `Copy ${e.requestId}`, onClick: () => copyText(e.requestId) }, shortId(e.requestId, 10), icon('copy', 12)) : null));

  if (e.message && e.kind !== 'validation') card.appendChild(h('div', { class: 'err-msg' }, e.message));
  if (e.errorType) card.appendChild(h('div', { class: 'err-type mono faint' }, `error_type: ${e.errorType}`));

  // kind-specific
  if (e.kind === 'validation' && Array.isArray(e.details)) {
    card.appendChild(h('ul', { class: 'err-locs' }, e.details.map((d) => h('li', {},
      h('span', { class: 'mono err-loc' }, locPath(d.loc)),
      h('span', { class: 'err-locmsg' }, d.msg || ''),
      d.type ? h('span', { class: 'mono faint err-loctype' }, d.type) : null))));
    if (onFix) actions.appendChild(h('button', { class: 'btn sm primary', onClick: () => onFix(e) }, icon('edit', 14), 'Fix in editor'));
  }
  if (e.kind === 'bad_request' && onEdit) actions.appendChild(h('button', { class: 'btn sm primary', onClick: () => onEdit(e) }, icon('edit', 14), 'Edit & re-ask'));
  if (e.kind === 'auth' || e.kind === 'forbidden') {
    card.appendChild(h('div', { class: 'err-extra' },
      h('p', {}, 'The UI proxy sends OPENJEV_API_KEY. Start it with the server\'s key:'),
      cmdChip('OPENJEV_API_KEY=… mise run ui'),
      h('div', { class: 'err-kv mono' },
        h('span', {}, `proxy auth configured: ${config?.authConfigured ? 'yes' : 'no'}`),
        h('span', { class: settings.authOverride ? 'warn-text' : '' }, `auth override: ${settings.authOverride ? 'active' : 'off'}`))));
    actions.appendChild(h('button', { class: 'btn sm', onClick: () => emit('oj:open-settings', { section: 'auth' }) }, icon('settings', 14), 'Open settings'));
  }
  if (e.kind === 'too_large') {
    card.appendChild(h('div', { class: 'err-extra' }, requestBytes ? `Body is ${fmtBytes(requestBytes)}; remove or downscale images.` : 'Remove or downscale images.'));
  }
  if (e.kind === 'unavailable') card.appendChild(h('div', { class: 'err-extra' }, 'The inference backend (or a routed model\'s container) is down.'));
  if (e.kind === 'upstream_down') {
    const hints = config?.hints || {};
    card.appendChild(h('div', { class: 'err-extra' },
      h('p', {}, `OpenJev is not reachable at ${config?.openjevUrl || 'http://127.0.0.1:8080'}.`),
      h('div', { class: 'row wrap' }, cmdChip(hints.start || 'mise run startOpenJev'), cmdChip(hints.logs || 'mise run logsOpenJev')),
      h('p', { class: 'faint' }, 'First start loads the model (~16 GB), may take a minute.')));
  }
  if (e.kind === 'timeout') card.appendChild(h('div', { class: 'err-extra' }, 'No answer within 900 s.'));
  if (e.kind === 'network') card.appendChild(h('div', { class: 'err-extra' }, 'The UI server itself is unreachable. Is ', h('code', {}, 'mise run ui'), ' still running?'));

  let timer = null;
  if ((e.kind === 'rate_limited' || e.kind === 'overloaded') && onRetry) {
    const auto = autoRetry ?? settings.autoRetryOverloaded;
    let left = Math.max(1, Math.ceil(e.retryAfter ?? 1));
    const cd = h('span', { class: 'mono err-countdown' });
    const paint = () => { cd.textContent = auto ? `auto-retry in ${left}s` : `retry-after ${left}s`; };
    paint();
    card.appendChild(h('div', { class: 'err-extra row' }, icon('clock', 14), cd));
    let seen = false;
    timer = setInterval(() => {
      if (card.isConnected) seen = true;
      else if (seen) { clearInterval(timer); return; }
      left -= 1;
      if (left <= 0) {
        clearInterval(timer);
        cd.textContent = auto ? 'retrying…' : 'ready to retry';
        if (auto && card.isConnected) onRetry(e, { auto: true });
        return;
      }
      paint();
    }, 1000);
    actions.appendChild(h('button', { class: 'btn sm primary', onClick: () => { clearInterval(timer); onRetry(e); } }, icon('refresh', 14), 'Retry now'));
  } else if (onRetry && ['unavailable', 'upstream_down', 'timeout', 'network', 'unknown', 'aborted', 'auth', 'forbidden'].includes(e.kind)) {
    actions.appendChild(h('button', { class: 'btn sm', onClick: () => onRetry(e) }, icon('refresh', 14), 'Retry'));
  }

  if (e.hint && !['upstream_down', 'auth', 'forbidden', 'network', 'unavailable', 'timeout'].includes(e.kind)) {
    card.appendChild(h('div', { class: 'err-hint' }, icon('info', 13), h('span', {}, e.hint)));
  }
  if (e.raw) {
    const det = h('details', { class: 'err-raw', open: settings.showRawByDefault ? true : null },
      h('summary', {}, 'Raw'),
      h('pre', { class: 'mono' }, e.raw));
    card.appendChild(det);
  }
  if (actions.children.length) card.appendChild(actions);
  return card;
}

// ------------------------------------------------------------------ banner
let wasDown = false;
let bannerTimer = null;

export function renderUpstreamBanner(health) {
  const el = document.getElementById('banner');
  if (!el) return;
  clearInterval(bannerTimer);
  if (!health || health.ok) {
    if (wasDown && health?.ok) toast('OpenJev is back', { kind: 'ok' });
    wasDown = false;
    el.hidden = true;
    clear(el);
    el.className = 'banner';
    return;
  }
  wasDown = true;
  const config = getCachedConfig();
  const hints = config?.hints || {};
  const url = health.upstream?.url || config?.openjevUrl || 'http://127.0.0.1:8080';
  const isAuth = health.upstream?.errorType === 'auth';
  clear(el);
  el.className = `banner ${isAuth ? 'banner-warn' : 'banner-err'}`;
  const countdown = h('span', { class: 'mono banner-cd faint' });
  const tick = () => {
    const at = nextHealthCheckAt();
    const s = at ? Math.max(0, Math.ceil((at - Date.now()) / 1000)) : null;
    countdown.textContent = s === null ? '' : `next check in ${s}s`;
  };
  tick();
  bannerTimer = setInterval(tick, 500);
  const retry = h('button', { class: 'btn sm', onClick: async () => { retry.disabled = true; await checkHealthNow(); retry.disabled = false; } }, icon('refresh', 13), 'Retry now');

  let msg, extra = null;
  if (health.proxyDown) {
    msg = 'UI server unreachable';
    extra = h('span', { class: 'muted' }, 'Is mise run ui still running?');
  } else if (isAuth) {
    msg = `OpenJev rejected the proxy's credentials: ${health.upstream?.error || 'auth error'}`;
    extra = h('span', { class: 'row wrap' }, h('span', { class: 'muted' }, 'Start the UI with the server\'s key:'), cmdChip('OPENJEV_API_KEY=… mise run ui'));
  } else {
    msg = `OpenJev is not reachable at ${url}`;
    extra = h('span', { class: 'row wrap' },
      cmdChip(hints.start || 'mise run startOpenJev'),
      cmdChip(hints.logs || 'mise run logsOpenJev'),
      h('span', { class: 'faint' }, 'first start loads the model (~16 GB), may take a minute'));
  }
  el.append(
    h('span', { class: 'banner-icon' }, icon('alert', 16)),
    h('span', { class: 'banner-msg' }, msg),
    extra,
    h('span', { class: 'spacer' }),
    countdown,
    retry);
  el.hidden = false;
}

// Image attach handling for C's own panes (batch): File → ImageRef, re-encoding images over
// 5 MB to JPEG q0.9 at ≤2048px, plus a drop/paste/pick zone. The thread composer (B) has its own.

import { el } from '/js/jev/util.js';
import { uid, fmtBytes } from '/js/core/format.js';
import { icon } from '/js/core/icons.js';
import { toast } from '/js/core/toast.js';
import { IMAGE_TOKENS_EST } from '/js/core/metrics.js';

export const IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
export const MAX_IMAGES = 8;
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

function readDataUrl(file) {
  return new Promise((res, rej) => { const r = new FileReader(); r.onload = () => res(r.result); r.onerror = () => rej(r.error); r.readAsDataURL(file); });
}
function loadImg(src) {
  return new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => rej(new Error('image decode failed')); i.src = src; });
}
const dataUrlBytes = (u) => Math.round((u.length - u.indexOf(',') - 1) * 3 / 4);

/** File → ImageRef {id, name, type, bytes, width, height, dataUrl, reencoded?}. Throws on unsupported types. */
export async function fileToImageRef(file) {
  if (!IMAGE_TYPES.includes(file.type)) throw new Error(`${file.name || 'file'}: ${file.type || 'unknown type'} is not supported (JPEG, PNG, WebP, GIF)`);
  let dataUrl = await readDataUrl(file);
  let img = await loadImg(dataUrl);
  let type = file.type, bytes = file.size, reencoded = false;
  if (bytes > MAX_IMAGE_BYTES) {
    const scale = Math.min(1, 2048 / Math.max(img.naturalWidth, img.naturalHeight));
    const c = document.createElement('canvas');
    c.width = Math.round(img.naturalWidth * scale); c.height = Math.round(img.naturalHeight * scale);
    c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
    dataUrl = c.toDataURL('image/jpeg', 0.9);
    type = 'image/jpeg'; bytes = dataUrlBytes(dataUrl); reencoded = true;
    img = await loadImg(dataUrl);
  }
  return { id: uid('img'), name: file.name || 'pasted-image', type, bytes, width: img.naturalWidth, height: img.naturalHeight, dataUrl, reencoded };
}

/**
 * A drop/paste/pick zone with a thumbnail tray.
 * opts: {images, onChange(images), label}
 * → {element, get(), set(images), destroy()}
 */
export function imageZone({ images = [], onChange, label = 'Images for every state' } = {}) {
  let list = images.slice();
  const tray = el('div', { class: 'batch-tray' });
  const input = el('input', { type: 'file', accept: IMAGE_TYPES.join(','), multiple: true, hidden: true, onchange: (e) => add([...e.target.files]).then(() => { input.value = ''; }) });
  const zone = el('div', { class: 'batch-drop', tabindex: '0', title: 'drop, paste or click to attach images' },
    el('div', { class: 'row' }, icon('image', 16), el('span', {}, label), el('span', { class: 'spacer' }),
      el('button', { class: 'btn sm ghost', type: 'button', onclick: () => input.click() }, icon('upload', 14), ' attach')),
    tray, input);
  const changed = () => { draw(); if (onChange) onChange(list.slice()); };
  async function add(files) {
    for (const f of files) {
      if (list.length >= MAX_IMAGES) { toast(`At most ${MAX_IMAGES} images per request`, { kind: 'warn' }); break; }
      try {
        const ref = await fileToImageRef(f);
        if (ref.reencoded) toast(`${ref.name} was over 5 MB: re-encoded to JPEG q0.9, ${ref.width}×${ref.height}, ${fmtBytes(ref.bytes)}`, { kind: 'info', timeout: 5000 });
        list.push(ref);
      } catch (e) { toast(e.message || String(e), { kind: 'err' }); }
    }
    changed();
  }
  function draw() {
    tray.textContent = '';
    if (!list.length) { tray.appendChild(el('span', { class: 'faint' }, 'none · drop, paste or attach (the same images go with every state)')); return; }
    list.forEach((im, i) => tray.appendChild(el('div', { class: 'batch-thumb', title: `${im.name} ${im.width}×${im.height}` },
      im.dataUrl ? el('img', { src: im.dataUrl, alt: im.name }) : el('span', { class: 'faint' }, 'stripped'),
      el('span', { class: 'mono faint' }, `${fmtBytes(im.bytes)} · ~${IMAGE_TOKENS_EST} tok`),
      el('button', { class: 'icon-btn', type: 'button', title: 'remove', onclick: () => { list.splice(i, 1); changed(); } }, icon('x', 12)))));
  }
  const onDrop = (e) => { e.preventDefault(); zone.classList.remove('over'); add([...(e.dataTransfer?.files || [])]); };
  const onOver = (e) => { if ([...(e.dataTransfer?.types || [])].includes('Files')) { e.preventDefault(); zone.classList.add('over'); } };
  const onLeave = () => zone.classList.remove('over');
  const onPaste = (e) => {
    const files = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith('image/'));
    if (files.length) { e.preventDefault(); add(files); }
  };
  zone.addEventListener('drop', onDrop);
  zone.addEventListener('dragover', onOver);
  zone.addEventListener('dragleave', onLeave);
  zone.addEventListener('paste', onPaste);
  draw();
  return { element: zone, get: () => list.slice(), set(v) { list = (v || []).slice(); draw(); }, add, destroy() { zone.remove(); } };
}

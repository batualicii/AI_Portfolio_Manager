// Arayüz katmanı: araç çubuğu, parça listesi, durum mesajları, kısayollar.
import { METALS, swatchCss } from './materials.js';

const $ = (id) => document.getElementById(id);

const EYE_OPEN = '<svg viewBox="0 0 24 24" class="icon"><path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="2.6"/></svg>';
const EYE_OFF  = '<svg viewBox="0 0 24 24" class="icon"><path d="M4 4l16 16"/><path d="M9.6 6.1A10.9 10.9 0 0 1 12 5.5c6.4 0 10 6.5 10 6.5a18 18 0 0 1-3.6 4.3M6.4 8A17.7 17.7 0 0 0 2 12s3.6 6.5 10 6.5c1 0 1.9-.2 2.7-.4"/></svg>';

export class UI {
  constructor({ assembly, actions }) {
    this.assembly = assembly;
    this.actions = actions;
    this.el = {
      list: $('part-list'), count: $('part-count'), moved: $('moved-note'),
      explode: $('explode'), explodeVal: $('explode-val'), metal: $('metal'),
      undo: $('btn-undo'), reset: $('btn-reset'), status: $('status'),
      statusText: $('status-text'), dropzone: $('dropzone'), empty: $('empty'),
      fileInput: $('file-input'), help: $('help'),
    };

    for (const m of METALS) {
      const opt = document.createElement('option');
      opt.value = m.key; opt.textContent = m.label;
      this.el.metal.appendChild(opt);
    }

    this._bindToolbar();
    this._bindDragAndDrop();
    this._bindKeyboard();
  }

  _bindToolbar() {
    $('btn-reset').addEventListener('click', () => this.actions.reset());
    $('btn-undo').addEventListener('click', () => this.actions.undo());
    $('btn-fit').addEventListener('click', () => this.actions.fit());
    $('btn-shot').addEventListener('click', () => this.actions.screenshot());
    $('btn-help').addEventListener('click', () => this.el.help.showModal());
    $('btn-add').addEventListener('click', () => this.el.fileInput.click());
    $('btn-axis').addEventListener('click', () => {
      const axis = this.actions.toggleAxis();
      document.getElementById('axis-label').textContent = axis === 'z' ? 'Z↑' : 'Y↑';
    });
    $('btn-show-all').addEventListener('click', () => {
      for (const p of this.assembly.parts) p.visible = true;
      this.refresh();
    });

    this.el.fileInput.addEventListener('change', (e) => {
      if (e.target.files?.length) this.actions.loadFiles([...e.target.files]);
      e.target.value = '';
    });

    this.el.explode.addEventListener('input', (e) => {
      const v = Number(e.target.value) / 100;
      this.el.explodeVal.textContent = `${e.target.value}%`;
      this.actions.explode(v);
    });

    this.el.metal.addEventListener('change', (e) => this.actions.metal(e.target.value));
  }

  _bindDragAndDrop() {
    let depth = 0;
    const show = (on) => this.el.dropzone.classList.toggle('hidden', !on);
    window.addEventListener('dragenter', (e) => { e.preventDefault(); depth++; show(true); });
    window.addEventListener('dragover', (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
    window.addEventListener('dragleave', (e) => { e.preventDefault(); if (--depth <= 0) { depth = 0; show(false); } });
    window.addEventListener('drop', (e) => {
      e.preventDefault(); depth = 0; show(false);
      const files = [...(e.dataTransfer?.files ?? [])];
      if (files.length) this.actions.loadFiles(files);
    });
  }

  _bindKeyboard() {
    window.addEventListener('keydown', (e) => {
      const tag = e.target?.tagName;
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || this.el.help.open) return;
      const key = e.key.toLowerCase();
      if ((e.metaKey || e.ctrlKey) && key === 'z') { e.preventDefault(); this.actions.undo(); return; }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (key === 'r') { e.preventDefault(); this.actions.reset(); }
      else if (key === 'f') { e.preventDefault(); this.actions.fit(); }
      else if (key === 'h') {
        const sel = this.assembly.selected;
        if (sel) { sel.visible = !sel.visible; this.refresh(); }
      } else if (e.key === 'Escape') this.assembly.select(null);
    });
  }

  setStatus(text) {
    this.el.status.classList.toggle('hidden', !text);
    if (text) this.el.statusText.textContent = text;
  }

  showEmpty(on) { this.el.empty.classList.toggle('hidden', !on); }

  setExplodeValue(v) {
    const pct = Math.round(v * 100);
    this.el.explode.value = String(pct);
    this.el.explodeVal.textContent = `${pct}%`;
  }

  refresh() {
    const parts = this.assembly.parts;
    this.el.count.textContent = String(parts.length);
    this.el.undo.disabled = !this.assembly.canUndo;

    const moved = this.assembly.movedCount;
    this.el.moved.textContent = moved ? `${moved} parça taşındı` : '';
    this.el.reset.classList.toggle('btn-accent', moved > 0 || this.assembly.explode > 0);

    const sel = this.assembly.selected;
    this.el.metal.value = sel ? sel.materialKey : (parts[0]?.materialKey ?? 'celik');
    this.el.metal.title = sel ? `“${sel.name}” parçasına uygulanır` : 'Tüm parçalara uygulanır';

    this.el.list.replaceChildren(...parts.map((p) => this._row(p)));
    this.showEmpty(parts.length === 0);
  }

  _row(part) {
    const li = document.createElement('li');
    li.className = [
      part === this.assembly.selected ? 'selected' : '',
      part.visible ? '' : 'hidden-part',
      part.moved ? 'moved' : '',
    ].filter(Boolean).join(' ');
    li.title = part.name;

    const sw = document.createElement('span');
    sw.className = 'swatch';
    sw.style.background = swatchCss(part.materialKey, part.stepColor);

    const name = document.createElement('span');
    name.className = 'pname';
    name.textContent = part.name;

    const dot = document.createElement('span');
    dot.className = 'moved-dot';
    dot.title = 'Bu parça taşındı';

    const eye = document.createElement('button');
    eye.className = 'eye';
    eye.innerHTML = part.visible ? EYE_OPEN : EYE_OFF;
    eye.title = part.visible ? 'Gizle' : 'Göster';
    eye.addEventListener('click', (e) => {
      e.stopPropagation();
      part.visible = !part.visible;
      this.refresh();
    });

    li.append(sw, name, dot, eye);
    li.addEventListener('click', () => this.assembly.select(part === this.assembly.selected ? null : part));
    li.addEventListener('dblclick', () => this.actions.focus(part));
    return li;
  }
}

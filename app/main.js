// Giriş noktası: sahneyi kurar, modelleri yükler, arayüzü bağlar.
import * as THREE from 'three';
import { Viewer } from './viewer.js';
import { Assembly, updateTweens, activeTweenCount } from './parts.js';
import { loadStep, baseName } from './stepLoader.js';
import { DragController } from './interactions.js';
import { UI } from './ui.js';

const viewer = new Viewer(document.getElementById('viewport'));
const assembly = new Assembly(viewer.world);
viewer.onFrame((t) => {
  updateTweens(t);
  const sel = assembly.selected;
  if (sel?.visible && sel.group.children.length) {
    // Ucuz yol: parçanın hazır yerel kutusunu dünya matrisiyle dönüştür
    viewer.selectionBox.box.copy(sel.localBox).applyMatrix4(sel.group.matrixWorld)
      .expandByScalar((viewer.radius ?? 1) * 0.015);
    viewer.selectionBox.visible = !viewer.selectionBox.box.isEmpty();
  } else {
    viewer.selectionBox.visible = false;
  }
});

/** Parça paneli modeli örtmesin diye kadrajda sol pay bırakılır. */
const framePad = () => (window.innerWidth > 760 && assembly.parts.length ? 236 : 0);

const ui = new UI({ assembly, actions: {} });
assembly.onChange = () => ui.refresh();

const drag = new DragController({ viewer, assembly, onChange: () => ui.refresh() });

/** Tarayıcının durum mesajını boyayabilmesi için bir kare bekler (WASM okuması ana iş parçacığını kilitler). */
const paint = () => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 0)));

function naturalSort(a, b) {
  return String(a).localeCompare(String(b), 'tr', { numeric: true, sensitivity: 'base' });
}

async function addStepFiles(sources) {
  // sources: [{ name, arrayBuffer() }] — File nesnesi ya da {name, url}
  const errors = [];
  let loaded = 0;
  const list = [...sources].sort((a, b) => naturalSort(a.name, b.name));

  for (const [i, src] of list.entries()) {
    ui.setStatus(`${baseName(src.name)} okunuyor… (${i + 1}/${list.length})`);
    await paint();
    try {
      const buffer = src.url ? await (await fetch(src.url)).arrayBuffer() : await src.arrayBuffer();
      const { group, stepColor } = await loadStep(buffer, src.name);
      assembly.add(group, stepColor);
      loaded++;
    } catch (err) {
      console.error(err);
      errors.push(err.message ?? String(err));
    }
  }

  if (loaded) {
    assembly.finalizeLayout();
    const box = assembly.worldBounds();
    viewer.layoutForBounds(box);
    viewer.fit(box, { animate: false, padLeftPx: framePad() });
  }

  ui.refresh();
  ui.setStatus(errors.length ? `Hata: ${errors[0]}` : null);
  if (errors.length) setTimeout(() => ui.setStatus(null), 9000);
  return loaded;
}

const actions = {
  loadFiles: (files) => addStepFiles(files.filter((f) => /\.(step|stp)$/i.test(f.name))),

  reset: () => {
    assembly.resetAll({ onExplodeStep: (v) => ui.setExplodeValue(v) });
    assembly.select(null);
    ui.refresh();
  },

  undo: () => { assembly.undo(); ui.refresh(); },

  fit: () => viewer.fit(assembly.worldBounds(), { padLeftPx: framePad() }),

  focus: (part) => viewer.fit(new THREE.Box3().setFromObject(part.group), { offset: 1.9 }),

  toggleAxis: () => {
    viewer.setUpAxis(viewer.upAxis === 'z' ? 'y' : 'z');
    const box = assembly.worldBounds();
    viewer.layoutForBounds(box);
    viewer.fit(box, { padLeftPx: framePad() });
    return viewer.upAxis;
  },

  explode: (v) => {
    assembly.setExplode(v);
    if (v > 0) viewer.ensureVisible(assembly.worldBounds());   // taşan parçaları kadraja al
  },

  metal: (key) => {
    const targets = assembly.selected ? [assembly.selected] : assembly.parts;
    for (const p of targets) p.setMetal(key);
    ui.refresh();
  },

  screenshot: () => {
    const a = document.createElement('a');
    a.href = viewer.screenshot();
    a.download = `step-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.png`;
    a.click();
  },
};
Object.assign(ui.actions, actions);

// --- Açılışta sunucudaki modelleri yükle -------------------------------------
(async function boot() {
  ui.refresh();
  try {
    const res = await fetch('api/models', { cache: 'no-store' });
    if (res.ok) {
      const data = await res.json();
      if (data.files?.length) {
        ui.setStatus(`${data.files.length} dosya bulundu — ${data.dir}`);
        await addStepFiles(data.files);
        return;
      }
    }
  } catch { /* sunucu yok: yalnızca sürükle-bırak */ }
  ui.setStatus(null);
  ui.showEmpty(true);
})();

// Otomatik testler ve konsoldan müdahale için
window.__viewer = window.__musashi = {   // __musashi: eski testlerle uyum
  viewer, assembly, ui, drag, THREE,
  state: () => ({
    parts: assembly.parts.map((p) => ({
      name: p.name, moved: p.moved, visible: p.visible, metal: p.materialKey,
      offset: p.userOffset.toArray(), position: p.group.position.toArray(),
    })),
    explode: assembly.explode,
    canUndo: assembly.canUndo,
    animating: activeTweenCount() > 0,
  }),
};

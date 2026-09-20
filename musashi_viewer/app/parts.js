// Parça kaydı: her parçanın ilk (ev) konumu burada saklanır — "geri" tuşunun
// dayandığı tek gerçek kaynak budur. Konum = ev + elle taşıma + patlatma.
import * as THREE from 'three';
import { makeMaterial, DEFAULT_METAL } from './materials.js';

const ZERO = new THREE.Vector3();
const tweens = [];

export function addTween(duration, onUpdate, onDone) {
  const t = { start: performance.now(), duration: Math.max(duration, 1), onUpdate, onDone, dead: false };
  tweens.push(t);
  return t;
}

export function activeTweenCount() { return tweens.filter((t) => !t.dead).length; }

export function updateTweens(now = performance.now()) {
  for (let i = tweens.length - 1; i >= 0; i--) {
    const t = tweens[i];
    if (t.dead) { tweens.splice(i, 1); continue; }
    const k = Math.min((now - t.start) / t.duration, 1);
    const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2; // easeInOutCubic
    t.onUpdate(e, k);
    if (k >= 1) { tweens.splice(i, 1); t.onDone?.(); }
  }
}

let nextId = 1;

export class Part {
  constructor(group, stepColor) {
    this.id = nextId++;
    this.group = group;
    this.name = group.name || `Parça ${this.id}`;
    this.stepColor = stepColor ?? null;
    this.home = group.position.clone();      // ilk konum — asla değişmez
    this.userOffset = new THREE.Vector3();   // elle sürükleme
    this.explodeDir = new THREE.Vector3();   // patlatma yönü (birim)
    this.explodeAmount = 0;                  // 0..1 (montajdan gelir)
    this.explodeScale = 1;
    this.materialKey = DEFAULT_METAL;
    this.highlight = 'none';
    this._tween = null;
    this.localCenter = new THREE.Vector3();
    this.setMetal(DEFAULT_METAL);
  }

  get visible() { return this.group.visible; }
  set visible(v) { this.group.visible = v; }
  get moved() { return this.userOffset.lengthSq() > 1e-9; }

  eachMesh(fn) { this.group.traverse((o) => { if (o.isMesh) fn(o); }); }

  /** Parçanın kendi uzayındaki sınır kutusu — bir kez hesaplanır, seçim çerçevesi bunu kullanır. */
  get localBox() {
    if (!this._localBox) {
      this._localBox = new THREE.Box3();
      this.eachMesh((mesh) => {
        if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox();
        mesh.updateMatrix();
        this._localBox.union(mesh.geometry.boundingBox.clone().applyMatrix4(mesh.matrix));
      });
    }
    return this._localBox;
  }

  setMetal(key) {
    this.materialKey = key;
    this.eachMesh((mesh) => {
      mesh.material?.dispose?.();
      mesh.material = makeMaterial(key, mesh.userData.stepColor ?? this.stepColor);
    });
    this.applyHighlight();
  }

  setHighlight(mode) {
    if (this.highlight === mode) return;
    this.highlight = mode;
    this.applyHighlight();
  }

  applyHighlight() {
    // Metalde güçlü emissive rengi bozar; seçim asıl olarak çerçeveyle belli edilir.
    const spec = this.highlight === 'select' ? [0x2a5f92, 0.10]
               : this.highlight === 'hover'  ? [0x2a5f92, 0.06]
               : [0x000000, 0];
    this.eachMesh((mesh) => {
      if (!mesh.material?.emissive) return;
      mesh.material.emissive.setHex(spec[0]);
      mesh.material.emissiveIntensity = spec[1];
    });
  }

  updatePosition() {
    this.group.position.copy(this.home).add(this.userOffset)
      .addScaledVector(this.explodeDir, this.explodeAmount * this.explodeScale);
  }

  cancelTween() { if (this._tween) { this._tween.dead = true; this._tween = null; } }

  /** userOffset'i hedefe yumuşak animasyonla taşır. */
  moveOffsetTo(target, duration = 620, onDone) {
    this.cancelTween();
    const from = this.userOffset.clone();
    if (from.distanceToSquared(target) < 1e-12) { this.userOffset.copy(target); this.updatePosition(); onDone?.(); return; }
    this._tween = addTween(duration, (e) => {
      this.userOffset.lerpVectors(from, target, e);
      this.updatePosition();
    }, () => { this._tween = null; onDone?.(); });
  }

  /** Sürükleme sırasında anlık konum (animasyonsuz). */
  setOffset(v) { this.cancelTween(); this.userOffset.copy(v); this.updatePosition(); }
}

export class Assembly {
  constructor(world) {
    this.world = world;            // parçaların eklendiği THREE.Group
    this.parts = [];
    this.selected = null;
    this.undoStack = [];
    this.explode = 0;
    this.onChange = () => {};
  }

  get movedCount() { return this.parts.filter((p) => p.moved).length; }
  get canUndo() { return this.undoStack.length > 0; }

  add(group, stepColor) {
    const part = new Part(group, stepColor);
    group.userData.__part = part;          // ışın testinde parçaya geri dönebilmek için
    this.world.add(group);
    this.parts.push(part);
    return part;
  }

  clear() {
    for (const p of this.parts) {
      p.cancelTween();
      p.eachMesh((m) => { m.geometry.dispose(); m.material?.dispose?.(); });
      this.world.remove(p.group);
    }
    this.parts = [];
    this.undoStack = [];
    this.selected = null;
    this.explode = 0;
  }

  /** Parça merkezleri, patlatma yönleri ve ölçek — yükleme bitince bir kez çağrılır. */
  finalizeLayout() {
    if (!this.parts.length) return;
    this.world.updateMatrixWorld(true);
    const box = new THREE.Box3();
    const centers = new Map();
    for (const p of this.parts) {
      const b = new THREE.Box3().setFromObject(p.group);
      // Dünya uzayından world grubunun yerel uzayına çevir: konumlar orada tutuluyor.
      centers.set(p, this.world.worldToLocal(b.getCenter(new THREE.Vector3())));
      box.union(b);
    }
    const center = this.world.worldToLocal(box.getCenter(new THREE.Vector3()));
    const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1e-4);

    this.parts.forEach((p, i) => {
      p.localCenter.copy(centers.get(p));
      const dir = centers.get(p).clone().sub(center);
      if (dir.lengthSq() < (radius * 1e-3) ** 2) {
        // Tam merkezdeki parça: yönü deterministik olarak çember üzerine dağıt
        const a = (i / this.parts.length) * Math.PI * 2;
        dir.set(Math.cos(a), Math.sin(a), 0.35);
      }
      p.explodeDir.copy(dir).normalize();
      p.explodeScale = radius * 0.85;
      p.updatePosition();
    });

    this.homeBox = box;
    this.homeCenter = center;
    this.homeRadius = radius;
  }

  setExplode(amount01) {
    this.explode = amount01;
    for (const p of this.parts) { p.explodeAmount = amount01; p.updatePosition(); }
    this.onChange();
  }

  /** Sürükleme bitince çağrılır; geri alma yığınına kaydeder. */
  pushMove(part, fromOffset) {
    if (fromOffset.distanceToSquared(part.userOffset) < 1e-9) return;
    this.undoStack.push({ part, from: fromOffset.clone(), to: part.userOffset.clone() });
    if (this.undoStack.length > 100) this.undoStack.shift();
    this.onChange();
  }

  undo() {
    const last = this.undoStack.pop();
    if (!last) return false;
    last.part.moveOffsetTo(last.from, 420, () => this.onChange());
    this.onChange();
    return true;
  }

  /** ↺ — her şey eski yerine: elle taşımalar sıfırlanır, patlatma kapanır. */
  resetAll({ animate = true, duration = 700, onExplodeStep } = {}) {
    const startExplode = this.explode;
    for (const p of this.parts) {
      if (animate) p.moveOffsetTo(ZERO, duration, () => this.onChange());
      else { p.setOffset(ZERO); }
    }
    if (animate && startExplode > 0) {
      addTween(duration, (e) => {
        const v = startExplode * (1 - e);
        this.explode = v;
        for (const p of this.parts) { p.explodeAmount = v; p.updatePosition(); }
        onExplodeStep?.(v);
      }, () => { this.explode = 0; onExplodeStep?.(0); this.onChange(); });
    } else if (!animate) {
      this.explode = 0;
      for (const p of this.parts) { p.explodeAmount = 0; p.updatePosition(); }
      onExplodeStep?.(0);
    }
    this.undoStack = [];
    this.onChange();
  }

  select(part) {
    if (this.selected === part) return;
    this.selected?.setHighlight('none');
    this.selected = part ?? null;
    this.selected?.setHighlight('select');
    this.onChange();
  }

  worldBounds() {
    const box = new THREE.Box3();
    for (const p of this.parts) if (p.visible) box.expandByObject(p.group);
    return box.isEmpty() ? new THREE.Box3(new THREE.Vector3(-1, -1, -1), new THREE.Vector3(1, 1, 1)) : box;
  }
}

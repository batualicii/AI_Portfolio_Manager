// "Elle tutma": ekrandan ışın gönderip parçayı bul, kameraya paralel bir düzlem
// üzerinde sürükle. Sürükleme sırasında yörünge kontrolü devre dışı kalır.
import * as THREE from 'three';

const DRAG_THRESHOLD_PX = 4;

export class DragController {
  constructor({ viewer, assembly, onChange }) {
    this.viewer = viewer;
    this.assembly = assembly;
    this.onChange = onChange ?? (() => {});
    this.enabled = true;

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.plane = new THREE.Plane();
    this.hit = new THREE.Vector3();

    this.hovered = null;
    this.drag = null;
    this._downAt = null;
    this._needsHoverCheck = false;

    const el = viewer.renderer.domElement;
    el.addEventListener('pointerdown', (e) => this._onDown(e));
    el.addEventListener('pointermove', (e) => this._onMove(e));
    el.addEventListener('pointerup', (e) => this._onUp(e));
    el.addEventListener('pointercancel', () => this._endDrag(true));
    el.addEventListener('dblclick', (e) => this._onDoubleClick(e));
    window.addEventListener('keydown', (e) => this._onKey(e));
    window.addEventListener('keyup', (e) => this._onKey(e));
  }

  _setPointer(event) {
    const r = this.viewer.renderer.domElement.getBoundingClientRect();
    this.pointer.set(
      ((event.clientX - r.left) / r.width) * 2 - 1,
      -((event.clientY - r.top) / r.height) * 2 + 1
    );
    this.raycaster.setFromCamera(this.pointer, this.viewer.camera);
  }

  _pick() {
    const targets = this.assembly.parts.filter((p) => p.visible).map((p) => p.group);
    if (!targets.length) return null;
    const hits = this.raycaster.intersectObjects(targets, true);
    if (!hits.length) return null;
    const hit = hits[0];
    let node = hit.object;
    while (node && !node.userData.__part) node = node.parent;
    return node?.userData.__part ? { part: node.userData.__part, point: hit.point.clone() } : null;
  }

  _onDown(event) {
    if (!this.enabled || event.button !== 0) return;
    this._downAt = { x: event.clientX, y: event.clientY, moved: false };
    this._setPointer(event);
    const picked = this._pick();
    if (!picked) return;

    const { part, point } = picked;
    this.assembly.select(part);

    const normal = this.viewer.camera.getWorldDirection(new THREE.Vector3()).negate();
    this.plane.setFromNormalAndCoplanarPoint(normal, point);

    this.drag = {
      part,
      startOffset: part.userOffset.clone(),
      undoFrom: part.userOffset.clone(),
      grabLocal: this.viewer.world.worldToLocal(point.clone()),
      axisLock: event.shiftKey,
    };
    this.viewer.controls.enabled = false;
    this.viewer.renderer.domElement.setPointerCapture?.(event.pointerId);
    this.viewer.renderer.domElement.style.cursor = 'grabbing';
    this.onChange();
  }

  _onMove(event) {
    if (!this.enabled) return;
    if (this._downAt) {
      const dx = event.clientX - this._downAt.x, dy = event.clientY - this._downAt.y;
      if (dx * dx + dy * dy > DRAG_THRESHOLD_PX * DRAG_THRESHOLD_PX) this._downAt.moved = true;
    }

    if (this.drag) {
      this._setPointer(event);
      if (!this.raycaster.ray.intersectPlane(this.plane, this.hit)) return;
      const local = this.viewer.world.worldToLocal(this.hit.clone());
      const delta = local.sub(this.drag.grabLocal);
      if (event.shiftKey) {
        const ax = Math.abs(delta.x), ay = Math.abs(delta.y), az = Math.abs(delta.z);
        if (ax >= ay && ax >= az) delta.set(delta.x, 0, 0);
        else if (ay >= az) delta.set(0, delta.y, 0);
        else delta.set(0, 0, delta.z);
      }
      this.drag.part.setOffset(this.drag.startOffset.clone().add(delta));
      this.onChange();
      return;
    }

    // Sürükleme yokken: üzerine gelinen parçayı vurgula.
    // Işın testi kare başına bir kez — çok üçgenli modellerde fareyi yormasın.
    this._hoverEvent = event;
    if (this._hoverScheduled) return;
    this._hoverScheduled = true;
    requestAnimationFrame(() => { this._hoverScheduled = false; this._updateHover(); });
  }

  _updateHover() {
    const event = this._hoverEvent;
    if (!event || this.drag) return;
    this._setPointer(event);
    const picked = this._pick();
    const part = picked?.part ?? null;
    if (part !== this.hovered) {
      if (this.hovered && this.hovered !== this.assembly.selected) this.hovered.setHighlight('none');
      this.hovered = part;
      if (part && part !== this.assembly.selected) part.setHighlight('hover');
    }
    this.viewer.renderer.domElement.style.cursor = part ? 'grab' : 'default';
  }

  _onUp(event) {
    if (this.drag) { this._endDrag(false); }
    else if (this._downAt && !this._downAt.moved && event.button === 0) {
      this._setPointer(event);
      if (!this._pick()) this.assembly.select(null);   // boşluğa tıklama: seçimi bırak
    }
    this._downAt = null;
  }

  _endDrag(cancelled) {
    const drag = this.drag;
    this.drag = null;
    this.viewer.controls.enabled = true;
    this.viewer.renderer.domElement.style.cursor = 'default';
    if (!drag) return;
    if (cancelled) drag.part.setOffset(drag.startOffset);
    else this.assembly.pushMove(drag.part, drag.undoFrom);
    this.onChange();
  }

  _onDoubleClick(event) {
    this._setPointer(event);
    const picked = this._pick();
    if (!picked) return;
    const box = new THREE.Box3().setFromObject(picked.part.group);
    this.viewer.fit(box, { offset: 1.9 });
  }

  _onKey(event) {
    if (event.key === 'Escape' && this.drag) this._endDrag(true);
  }
}

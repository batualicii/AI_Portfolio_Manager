// Sahne kurulumu: render motoru, kamera, kontroller, zemin, ortam aydınlatması.
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';
import { createStudioEnvironment, createBackdrop } from './studio.js';

const V = new THREE.Vector3();

function radialAlphaTexture(size = 512) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const ctx = c.getContext('2d');
  const g = ctx.createRadialGradient(size / 2, size / 2, size * 0.05, size / 2, size / 2, size * 0.5);
  g.addColorStop(0.0, '#ffffff');
  g.addColorStop(0.55, '#8a8a8a');
  g.addColorStop(1.0, '#000000');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.NoColorSpace;
  return tex;
}

export class Viewer {
  constructor(container) {
    this.container = container;

    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.setSize(container.clientWidth, container.clientHeight);
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.0;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    container.appendChild(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.scene.environment = createStudioEnvironment(this.renderer);
    this.scene.environmentIntensity = 1.0;

    this.camera = new THREE.PerspectiveCamera(42, this._aspect(), 0.1, 10000);
    this.camera.position.set(3, 2, 4);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.screenSpacePanning = true;
    this.controls.mouseButtons = {
      LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN,
    };

    // STEP dosyaları genelde Z-yukarı; three.js Y-yukarı. Tüm model bu grubun içinde durur.
    this.world = new THREE.Group();
    this.upAxis = 'z';
    this.world.rotation.x = -Math.PI / 2;
    this.scene.add(this.world);

    // Yön ışığı yalnızca gölge için; asıl aydınlatma ortam haritasından gelir.
    this.sun = new THREE.DirectionalLight(0xffffff, 0.55);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(2048, 2048);
    this.sun.shadow.bias = -0.0008;
    this.scene.add(this.sun, this.sun.target);

    this.floor = new THREE.Mesh(
      new THREE.CircleGeometry(1, 96),
      new THREE.MeshStandardMaterial({
        color: 0x0d1014, roughness: 0.92, metalness: 0.0,
        transparent: true, alphaMap: radialAlphaTexture(), depthWrite: false,
      })
    );
    this.floor.rotation.x = -Math.PI / 2;
    this.floor.receiveShadow = true;
    this.scene.add(this.floor);

    this.grid = new THREE.GridHelper(1, 40, 0x2a3138, 0x1a2027);
    this.grid.material.transparent = true;
    this.grid.material.opacity = 0.5;
    this.scene.add(this.grid);

    this.backdrop = createBackdrop(1);
    this.scene.add(this.backdrop);

    // Seçili parçanın çerçevesi — malzemeye dokunmadan seçimi belli eder
    this.selectionBox = new THREE.Box3Helper(new THREE.Box3(), 0xc8a45c);
    this.selectionBox.material.fog = false;
    this.selectionBox.material.transparent = true;
    this.selectionBox.material.opacity = 0.85;
    this.selectionBox.visible = false;
    this.scene.add(this.selectionBox);

    this._onResize = () => this.resize();
    window.addEventListener('resize', this._onResize);

    this._frameCallbacks = [];
    this.renderer.setAnimationLoop((t) => this._tick(t));
  }

  _aspect() {
    return Math.max(this.container.clientWidth, 1) / Math.max(this.container.clientHeight, 1);
  }

  onFrame(fn) { this._frameCallbacks.push(fn); }

  _tick(time) {
    for (const fn of this._frameCallbacks) fn(time);
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  resize() {
    const w = this.container.clientWidth, h = this.container.clientHeight;
    this.renderer.setSize(w, h);
    this.camera.aspect = this._aspect();
    this.camera.updateProjectionMatrix();
  }

  /** Modelin ölçüsüne göre zemin, gölge, ızgara, sis ve kamera kırpma düzlemlerini ayarlar. */
  layoutForBounds(box) {
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const radius = Math.max(size.length() / 2, 1e-3);
    this.radius = radius;
    this.center = center;

    this.floor.position.set(center.x, box.min.y - radius * 0.02, center.z);
    this.floor.scale.setScalar(radius * 6);
    this.grid.position.copy(this.floor.position).setY(this.floor.position.y + radius * 0.001);
    this.grid.scale.setScalar(radius * 24);

    this.backdrop.scale.setScalar(radius * 60);
    this.backdrop.position.copy(center);
    this.scene.fog = new THREE.Fog(0x0b0d10, radius * 4, radius * 16);

    this.sun.position.copy(center).add(V.set(-0.6, 1.25, 0.8).multiplyScalar(radius * 4));
    this.sun.target.position.copy(center);
    const s = this.sun.shadow.camera;
    s.left = -radius * 2.2; s.right = radius * 2.2;
    s.top = radius * 2.2; s.bottom = -radius * 2.2;
    s.near = radius * 0.5; s.far = radius * 10;
    s.updateProjectionMatrix();
  }

  /** Modeli ekrana sığdırır. */
  fit(box, { offset = 1.35, animate = true, padLeftPx = 0 } = {}) {
    const center = box.getCenter(new THREE.Vector3());
    const distance = this.distanceFor(box, offset);

    const dir = new THREE.Vector3(0.85, 0.5, 1).normalize();
    const to = center.clone().addScaledVector(dir, distance);

    // Sol paneldeki payı telafi et: model görünür alanın ortasında kalsın
    const target = center.clone();
    if (padLeftPx > 0 && this.container.clientWidth > 0) {
      const visibleH = 2 * Math.tan((this.camera.fov * Math.PI) / 360) * distance;
      const visibleW = visibleH * this.camera.aspect;
      const shift = (visibleW * (padLeftPx / 2)) / this.container.clientWidth;
      const backward = new THREE.Vector3().subVectors(to, center).normalize();
      const right = new THREE.Vector3().crossVectors(this.camera.up, backward).normalize();
      // Kamerayı sola kaydırmak modeli ekranda sağa taşır (panelin yanına)
      target.addScaledVector(right, -shift);
      to.addScaledVector(right, -shift);
    }

    this.camera.near = Math.max(distance / 500, 1e-3);
    this.camera.far = distance * 200;
    this.camera.updateProjectionMatrix();
    this.controls.minDistance = distance / 40;
    this.controls.maxDistance = distance * 12;

    if (animate) this._flyTo(to, target, 620);
    else { this.camera.position.copy(to); this.controls.target.copy(target); }
  }

  /** Verilen kutuyu kadraja sığdırmak için gereken kamera mesafesi. */
  distanceFor(box, offset = 1.35) {
    const size = box.getSize(new THREE.Vector3());
    const maxSize = Math.max(size.x, size.y, size.z, 1e-3);
    const fitHeight = maxSize / (2 * Math.atan((Math.PI * this.camera.fov) / 360));
    return offset * Math.max(fitHeight, fitHeight / this.camera.aspect);
  }

  /**
   * Kutu kadraja sığmıyorsa kamerayı YALNIZCA geriye alır (yakınlaştırmaz).
   * Patlatma sırasında parçaların ekrandan taşmasını engeller, kullanıcının
   * kendi yakınlaştırmasını bozmaz.
   */
  ensureVisible(box, offset = 1.25) {
    const needed = this.distanceFor(box, offset);
    const dir = new THREE.Vector3().subVectors(this.camera.position, this.controls.target);
    const current = dir.length();
    if (current < 1e-9 || needed <= current) return;
    this.camera.position.copy(this.controls.target).addScaledVector(dir.normalize(), needed);
    this.controls.maxDistance = Math.max(this.controls.maxDistance, needed * 1.5);
    this.camera.far = Math.max(this.camera.far, needed * 20);
    this.camera.updateProjectionMatrix();
  }

  _flyTo(position, target, duration) {
    const p0 = this.camera.position.clone(), t0 = this.controls.target.clone();
    const start = performance.now();
    const step = (now) => {
      const k = Math.min((now - start) / duration, 1);
      const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      this.camera.position.lerpVectors(p0, position, e);
      this.controls.target.lerpVectors(t0, target, e);
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  setExposure(v) { this.renderer.toneMappingExposure = v; }

  /** 'z' → STEP'in Z-yukarı düzeni (varsayılan), 'y' → model koordinatları olduğu gibi. */
  setUpAxis(axis) {
    this.upAxis = axis;
    this.world.rotation.x = axis === 'z' ? -Math.PI / 2 : 0;
    this.world.updateMatrixWorld(true);
  }

  screenshot() {
    this.renderer.render(this.scene, this.camera);
    return this.renderer.domElement.toDataURL('image/png');
  }
}

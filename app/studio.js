// Prosedürel stüdyo ortamı: metalin "gerçek" görünmesi, yansıttığı ortama bağlıdır.
// Burada karanlık bir stüdyo + birkaç parlak softbox kurup PMREM ile ortam haritasına çeviriyoruz.
// (Harici HDR dosyası yok — internetsiz de çalışır.)
import * as THREE from 'three';

function areaLight(intensity) {
  const m = new THREE.MeshBasicMaterial();
  m.color.setScalar(intensity);   // >1 değerler HalfFloat hedefte korunur
  return m;
}

function box(scene, mat, w, h, d, pos, rot) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), mat);
  mesh.position.set(pos[0], pos[1], pos[2]);
  if (rot) mesh.rotation.set(rot[0], rot[1], rot[2]);
  scene.add(mesh);
  return mesh;
}

export function createStudioEnvironment(renderer) {
  const pmrem = new THREE.PMREMGenerator(renderer);
  const env = new THREE.Scene();

  // Oda: koyu gri kabuk (içten görünür)
  const shell = new THREE.Mesh(
    new THREE.BoxGeometry(30, 18, 30),
    new THREE.MeshStandardMaterial({ side: THREE.BackSide, roughness: 1, metalness: 0, color: 0x14171c })
  );
  env.add(shell);

  // Tavan dolgusu — genel aydınlığı verir
  box(env, areaLight(2.2), 26, 0.2, 26, [0, 8.6, 0]);
  // Zemin sekmesi — alttan hafif dolgu, parçanın karnını karanlıkta bırakmaz
  box(env, areaLight(0.9), 26, 0.2, 26, [0, -8.6, 0]);

  // Ana softbox (ön-üst sol): metalde uzun, yumuşak parlama çizgisi
  box(env, areaLight(26), 9, 0.3, 5, [-3.5, 7.2, 4.5], [0, 0, Math.PI * 0.12]);
  // Yan dolgu (sağ): karşı kenarı okunur kılar
  box(env, areaLight(9), 0.3, 7, 9, [9.5, 2.0, -1.0]);
  // Arka rim (kontur) şeridi: siluet kenarlarını çizer
  box(env, areaLight(16), 12, 3.2, 0.3, [1.5, 3.5, -9.5]);
  // Ön alt vurgu: yüzeylerde ikinci bir yansıma katmanı
  box(env, areaLight(4.5), 8, 2.2, 0.3, [-1.0, -2.0, 9.5]);
  // Sol dar şerit: keskin, ince yansıma (bıçak sırtı gibi yüzeylerde belirginleşir)
  box(env, areaLight(20), 0.3, 5, 2.2, [-9.5, 4.0, 1.5]);

  const target = pmrem.fromScene(env, 0.02);

  env.traverse((o) => { if (o.isMesh) { o.geometry.dispose(); o.material.dispose(); } });
  pmrem.dispose();
  return target.texture;
}

// Arka plan: dikey degrade (düz renkten çok daha derin bir stüdyo hissi verir)
export function createBackdrop(radius) {
  const geo = new THREE.SphereGeometry(radius, 32, 24);
  const mat = new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false,
    uniforms: {
      topColor:    { value: new THREE.Color(0x1a1f26) },
      bottomColor: { value: new THREE.Color(0x05070a) },
    },
    vertexShader: `
      varying vec3 vPos;
      void main(){ vPos = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
    fragmentShader: `
      uniform vec3 topColor; uniform vec3 bottomColor; varying vec3 vPos;
      void main(){
        float t = clamp(normalize(vPos).y * 0.5 + 0.5, 0.0, 1.0);
        gl_FragColor = vec4(mix(bottomColor, topColor, pow(t, 1.35)), 1.0);
        #include <colorspace_fragment>
      }`,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.name = 'backdrop';
  return mesh;
}

// STEP okuma: occt-import-js (OpenCascade'in WebAssembly derlemesi) dosyayı
// üçgen ağlara çevirir, biz de bunu three.js geometrisine dönüştürürüz.
import * as THREE from 'three';

export const QUALITY = {
  hizli:  { linearDeflection: 0.004,  angularDeflection: 0.60 },
  normal: { linearDeflection: 0.0012, angularDeflection: 0.35 },
  yuksek: { linearDeflection: 0.0004, angularDeflection: 0.20 },
};

let occtPromise = null;

export function occtReady() {
  if (!occtPromise) {
    if (typeof occtimportjs === 'undefined') {
      return Promise.reject(new Error(
        'STEP okuyucu yüklenemedi. Sayfayı "baslat.command" ile açın — ' +
        'index.html dosyasını doğrudan çift tıklayarak açmak tarayıcı güvenliği nedeniyle çalışmaz.'
      ));
    }
    occtPromise = occtimportjs();
  }
  return occtPromise;
}

export function baseName(fileName) {
  return String(fileName).replace(/^.*[\\/]/, '').replace(/\.[^.]+$/, '');
}

function toGeometry(mesh) {
  const geo = new THREE.BufferGeometry();
  const pos = mesh.attributes?.position?.array;
  if (!pos || !pos.length) return null;
  geo.setAttribute('position', new THREE.Float32BufferAttribute(Float32Array.from(pos), 3));

  const nrm = mesh.attributes?.normal?.array;
  if (nrm && nrm.length === pos.length) {
    geo.setAttribute('normal', new THREE.Float32BufferAttribute(Float32Array.from(nrm), 3));
  }
  const idx = mesh.index?.array;
  if (idx && idx.length) geo.setIndex(new THREE.Uint32BufferAttribute(Uint32Array.from(idx), 1));
  if (!geo.getAttribute('normal')) geo.computeVertexNormals();
  geo.computeBoundingBox();
  geo.computeBoundingSphere();
  return geo;
}

/**
 * Bir STEP dosyasını okur ve tek bir THREE.Group döndürür (dosya = parça).
 * @returns {{group: THREE.Group, stepColor: THREE.Color|null, triangles: number}}
 */
const AUTO_FAST_BYTES = 25 * 1024 * 1024;   // bu boyutun üstünde kaba üçgenleme

export async function loadStep(buffer, fileName, quality = 'auto') {
  const occt = await occtReady();
  if (quality === 'auto') {
    quality = buffer.byteLength > AUTO_FAST_BYTES ? 'hizli' : 'normal';
    if (quality === 'hizli') {
      console.info(`${fileName}: dosya büyük (${(buffer.byteLength / 1048576).toFixed(0)} MB) — hızlı üçgenleme kullanılıyor.`);
    }
  }
  const params = { linearUnit: 'millimeter', linearDeflectionType: 'bounding_box_ratio', ...(QUALITY[quality] ?? QUALITY.normal) };

  let result;
  try {
    result = occt.ReadStepFile(new Uint8Array(buffer), params);
  } catch (err) {
    throw new Error(`${fileName}: STEP çözümlenemedi (${err?.message ?? err})`);
  }
  if (!result || !result.success) throw new Error(`${fileName}: geçerli bir STEP dosyası değil ya da okunamadı.`);

  const group = new THREE.Group();
  group.name = baseName(fileName);
  let triangles = 0;
  let stepColor = null;

  for (const m of result.meshes ?? []) {
    const geo = toGeometry(m);
    if (!geo) continue;
    const mesh = new THREE.Mesh(geo);
    mesh.name = m.name || group.name;
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    if (Array.isArray(m.color) && m.color.length >= 3) {
      const c = new THREE.Color().setRGB(m.color[0], m.color[1], m.color[2], THREE.SRGBColorSpace);
      mesh.userData.stepColor = c;
      stepColor ??= c;
    }
    triangles += geo.index ? geo.index.count / 3 : geo.getAttribute('position').count / 3;
    group.add(mesh);
  }

  if (!group.children.length) throw new Error(`${fileName}: dosyada görüntülenebilir katı/yüzey bulunamadı.`);
  return { group, stepColor, triangles: Math.round(triangles) };
}

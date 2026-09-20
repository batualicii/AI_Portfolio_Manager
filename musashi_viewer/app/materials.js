// Metal ön ayarları. Metalde renk = yansıma rengidir; asıl gerçekçilik
// metalness=1 + doğru roughness + ortam haritasından gelir.
import * as THREE from 'three';

export const METALS = [
  { key: 'celik',      label: 'Çelik',             color: 0x8d9095, roughness: 0.28 },
  { key: 'paslanmaz',  label: 'Paslanmaz (parlak)', color: 0xc3c7cd, roughness: 0.13 },
  { key: 'krom',       label: 'Krom',              color: 0xdfe3e8, roughness: 0.045 },
  { key: 'dokum',      label: 'Döküm çelik (mat)',  color: 0x74787e, roughness: 0.55 },
  { key: 'karartilmis',label: 'Kararmış çelik',     color: 0x3b3f45, roughness: 0.34 },
  { key: 'aluminyum',  label: 'Alüminyum',         color: 0xb6bbc1, roughness: 0.38 },
  { key: 'titanyum',   label: 'Titanyum',          color: 0x94969c, roughness: 0.44 },
  { key: 'pirinc',     label: 'Pirinç',            color: 0xd2ae62, roughness: 0.24 },
  { key: 'bakir',      label: 'Bakır',             color: 0xc17a4c, roughness: 0.22 },
  { key: 'altin',      label: 'Altın',             color: 0xdca63f, roughness: 0.16 },
  { key: 'step',       label: 'Dosyadaki renk',    color: 0x9aa0a6, roughness: 0.30, useStepColor: true },
];

export const METAL_BY_KEY = new Map(METALS.map((m) => [m.key, m]));
export const DEFAULT_METAL = 'celik';

// Parça listesindeki renk kutucuğu için
export function swatchCss(key, stepColor) {
  const def = METAL_BY_KEY.get(key) ?? METAL_BY_KEY.get(DEFAULT_METAL);
  const c = def.useStepColor && stepColor ? stepColor : new THREE.Color(def.color);
  return `#${c.getHexString()}`;
}

export function makeMaterial(key, stepColor) {
  const def = METAL_BY_KEY.get(key) ?? METAL_BY_KEY.get(DEFAULT_METAL);
  const color = def.useStepColor && stepColor ? stepColor.clone() : new THREE.Color(def.color);
  return new THREE.MeshPhysicalMaterial({
    color,
    metalness: def.metalness ?? 1.0,
    roughness: def.roughness,
    envMapIntensity: def.env ?? 1.25,
    clearcoat: def.clearcoat ?? 0,
    side: THREE.FrontSide,
  });
}

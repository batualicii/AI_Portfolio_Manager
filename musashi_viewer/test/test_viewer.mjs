import { chromium } from 'playwright';

const URL = process.env.VIEWER_URL || 'http://127.0.0.1:8791/';
const OUT = process.env.OUT_DIR || '.';
const log = (...a) => console.log(...a);

/** Animasyonlar bitene kadar bekler — sabit sleep yerine (yazılımsal render yavaş olabilir). */
const settle = async (page, timeout = 120000) => {
  await page.waitForFunction(() => window.__musashi && !window.__musashi.state().animating, null, { timeout });
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
};
let failures = 0;
const check = (name, ok, detail = '') => {
  log(`${ok ? '  ✓' : '  ✗'} ${name}${detail ? ' — ' + detail : ''}`);
  if (!ok) failures++;
};


/** Verilen parçanın o ANKİ ekran konumlarını hesaplar (patlatmadan sonra da doğru olsun diye). */
async function screenPoints(page, partIndex) {
  return page.evaluate((idx) => {
    const { viewer, assembly, THREE } = window.__musashi;
    const part = assembly.parts[idx];
    const mesh = part.group.children[0];
    const pos = mesh.geometry.getAttribute('position');
    const rect = viewer.renderer.domElement.getBoundingClientRect();
    viewer.scene.updateMatrixWorld(true);
    const out = [];
    for (let i = 0; i < pos.count; i += Math.max(1, Math.floor(pos.count / 60))) {
      const v = new THREE.Vector3().fromBufferAttribute(pos, i);
      mesh.localToWorld(v).project(viewer.camera);
      const x = rect.left + (v.x * 0.5 + 0.5) * rect.width;
      const y = rect.top + (-v.y * 0.5 + 0.5) * rect.height;
      if (x > 4 && y > 4 && x < rect.width - 4 && y < rect.height - 4) out.push({ x, y });
    }
    return { candidates: out, name: part.name };
  }, partIndex);
}

/** Bir parçayı fareyle tutup sürükler; tutulan parçanın adını döndürür. */
async function grabAndDrag(page, partIndex, dx, dy) {
  const { candidates } = await screenPoints(page, partIndex);
  for (const pt of candidates) {
    await page.mouse.move(pt.x, pt.y);
    await page.mouse.down();
    const grabbed = await page.evaluate(() => window.__musashi.assembly.selected?.name ?? null);
    if (grabbed) {
      for (let i = 1; i <= 12; i++) await page.mouse.move(pt.x + (dx * i) / 12, pt.y + (dy * i) / 12);
      await page.mouse.up();
      return grabbed;
    }
    await page.mouse.up();
  }
  return null;
}

const browser = await chromium.launch({
  ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
  args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--use-gl=angle', '--disable-lcd-text'],
});
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });

const consoleErrors = [];
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + e.message));

log('\n1) Sayfa açılıyor: ' + URL);
await page.goto(URL, { waitUntil: 'domcontentloaded' });

log('2) Modellerin yüklenmesi bekleniyor…');
await page.waitForFunction(() => window.__musashi?.state().parts.length >= 5, null, { timeout: 180000 });
const loaded = await page.evaluate(() => window.__musashi.state());
check('5 STEP dosyası yüklendi', loaded.parts.length === 5, loaded.parts.map(p => p.name).join(', '));

// WebGL gerçekten çiziyor mu? (tuvalin boş olmadığını piksel okuyarak doğrula)
const pixels = await page.evaluate(() => {
  const v = window.__musashi.viewer;
  v.renderer.render(v.scene, v.camera);
  const gl = v.renderer.getContext();
  const buf = new Uint8Array(4 * 64 * 64);
  gl.readPixels(gl.drawingBufferWidth / 2 - 32, gl.drawingBufferHeight / 2 - 32, 64, 64, gl.RGBA, gl.UNSIGNED_BYTE, buf);
  let min = 255, max = 0;
  for (let i = 0; i < buf.length; i += 4) { const l = buf[i]; if (l < min) min = l; if (l > max) max = l; }
  return { min, max };
});
check('WebGL sahnesi çiziliyor (piksel çeşitliliği var)', pixels.max - pixels.min > 12, `min=${pixels.min} max=${pixels.max}`);

await settle(page);
await page.screenshot({ path: `${OUT}/01-montaj.png` });

// --- sürükleme testi --------------------------------------------------------
log('3) Parça sürükleme testi…');
const grabbed = await grabAndDrag(page, 2, 170, -70);
check('Fareyle bir parça tutuldu', !!grabbed, grabbed ?? 'hiçbir parça seçilemedi');

const afterDrag = await page.evaluate(() => window.__musashi.state());
const movedParts = afterDrag.parts.filter(p => p.moved);
check('Sürüklenen parça yer değiştirdi', movedParts.length === 1,
      movedParts.map(p => `${p.name} → [${p.offset.map(n => n.toFixed(1)).join(', ')}]`).join(' | '));
check('Diğer parçalar yerinde kaldı', afterDrag.parts.filter(p => !p.moved).length === 4);
check('Geri alma yığını doldu', afterDrag.canUndo === true);
await page.screenshot({ path: `${OUT}/02-parca-tasindi.png` });

// --- geri al (⌘Z) -----------------------------------------------------------
log('4) Son hareketi geri alma (⌘Z) testi…');
await page.click('#btn-undo');
await settle(page);
const afterUndo = await page.evaluate(() => window.__musashi.state());
check('Geri al: parça ilk konumuna döndü', afterUndo.parts.every(p => !p.moved),
      afterUndo.parts.filter(p => p.moved).map(p => p.name).join(', ') || 'tüm parçalar evinde');

// --- patlatma + elle taşıma + ↺ geri ---------------------------------------
log('5) Patlatma + ↺ geri testi…');
await page.evaluate(() => {
  const s = document.getElementById('explode');
  s.value = '65';
  s.dispatchEvent(new Event('input', { bubbles: true }));
});
await settle(page);
const exploded = await page.evaluate(() => window.__musashi.state());
check('Patlatma parçaları ayırdı', Math.abs(exploded.explode - 0.65) < 1e-6 &&
      exploded.parts.every(p => p.position.some(v => Math.abs(v) > 1e-6)), `explode=${exploded.explode}`);
await page.screenshot({ path: `${OUT}/03-patlatma.png` });

// patlatma açıkken bir parçayı da elle taşı
const grabbed2 = await grabAndDrag(page, 0, -150, 90);
check('Patlatma açıkken de parça elle taşınabildi', !!grabbed2, grabbed2 ?? 'tutulamadı');

const beforeReset = await page.evaluate(() => window.__musashi.state());
log(`   (geri öncesi: ${beforeReset.parts.filter(p => p.moved).length} parça elle taşınmış, patlatma ${Math.round(beforeReset.explode * 100)}%)`);

await page.click('#btn-reset');
await settle(page);
const afterReset = await page.evaluate(() => window.__musashi.state());
check('↺ Geri: elle taşımalar sıfırlandı', afterReset.parts.every(p => !p.moved));
check('↺ Geri: patlatma kapandı', afterReset.explode === 0);
check('↺ Geri: tüm parçalar ilk konumunda', afterReset.parts.every(p => p.position.every(v => Math.abs(v) < 1e-6)),
      afterReset.parts.map(p => `${p.name}:[${p.position.map(n => n.toFixed(2)).join(',')}]`).join(' '));
check('↺ Geri: slider da sıfıra döndü', await page.inputValue('#explode') === '0');
await page.screenshot({ path: `${OUT}/04-geri-sonrasi.png` });

// --- malzeme değişimi -------------------------------------------------------
log('6) Metal değiştirme testi…');
await page.selectOption('#metal', 'pirinc');
await settle(page);
const metalState = await page.evaluate(() => window.__musashi.state());
check('Metal tüm parçalara uygulandı', metalState.parts.every(p => p.metal === 'pirinc'));
await page.screenshot({ path: `${OUT}/05-pirinc.png` });
await page.selectOption('#metal', 'celik');
await settle(page);

// --- yukarı ekseni düğmesi ---------------------------------------------------
log('7) Z↑/Y↑ eksen düğmesi testi…');
const axisBefore = await page.evaluate(() => window.__musashi.viewer.world.rotation.x);
await page.click('#btn-axis');
await settle(page);
const axisAfter = await page.evaluate(() => ({
  rot: window.__musashi.viewer.world.rotation.x,
  label: document.getElementById('axis-label').textContent,
  axis: window.__musashi.viewer.upAxis,
}));
check('Eksen düğmesi modeli çeviriyor', Math.abs(axisBefore + Math.PI / 2) < 1e-6 && axisAfter.rot === 0 && axisAfter.label === 'Y↑');

// eksen değişikken de sürükleme + geri çalışmalı (sürükleme matematiği yerel uzayda)
const grabbed3 = await grabAndDrag(page, 2, 140, 60);
const afterAxisDrag = await page.evaluate(() => window.__musashi.state());
check('Eksen değişikken sürükleme çalışıyor', !!grabbed3 && afterAxisDrag.parts.some(p => p.moved), grabbed3 ?? 'tutulamadı');
await page.screenshot({ path: `${OUT}/06-eksen.png` });
await page.click('#btn-reset');
await settle(page);
await page.click('#btn-axis');
await settle(page);
check('Eksen geri alındı', await page.evaluate(() => window.__musashi.viewer.upAxis) === 'z');

// --- konsol hataları --------------------------------------------------------
const realErrors = consoleErrors.filter(e => !/favicon|DevTools|Automatic fallback to software/i.test(e));
check('Konsolda hata yok', realErrors.length === 0, realErrors.slice(0, 3).join(' | '));

await browser.close();
log(`\n${failures === 0 ? '✅ TÜM TESTLER GEÇTİ' : `❌ ${failures} test BAŞARISIZ`}\n`);
process.exit(failures ? 1 : 0);

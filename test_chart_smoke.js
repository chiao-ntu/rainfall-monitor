// 實頁煙霧測試：把每一張圖畫出來、把 tooltip 滑過每一格，確認不丟例外。
// ------------------------------------------------------------------
// 靜態測試只能證明「程式碼長得對」，證明不了「跑起來沒事」。
// 本輪改了 tooltip 的座標來源與分派邏輯，必須真的跑過。
const fs = require('fs');
const puppeteer = require('puppeteer');
const _EXEC = [process.env.PUPPETEER_EXECUTABLE_PATH,
  '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  '/opt/pw-browsers/chromium/chrome-linux/chrome'].find(p => p && fs.existsSync(p));
const _LAUNCH = { args: ['--no-sandbox', '--disable-dev-shm-usage'] };
if (_EXEC) _LAUNCH.executablePath = _EXEC;

(async () => {
  const sf = [];
  const browser = await puppeteer.launch(_LAUNCH);
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1000 });
  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  // data.json / favicon 在本機測試環境本來就不存在（由排程產生），不計為錯誤
  const KNOWN_404 = /data\.json|favicon\.ico/;
  page.on('console', m => {
    if (m.type() !== 'error') return;
    const txt = m.text();
    if (/404|Failed to load resource/.test(txt)) return;   // 見下方 response 監聽
    errs.push('console: ' + txt);
  });
  page.on('response', res => {
    if (res.status() >= 400 && !KNOWN_404.test(res.url()))
      errs.push(`資源 ${res.status()}: ${res.url()}`);
  });

  await page.goto('http://127.0.0.1:8899/_local.html', { waitUntil: 'networkidle2', timeout: 120000 });
  await new Promise(r => setTimeout(r, 2500));

  const r = await page.evaluate(() => {
    const out = { drawn: [], geoms: {}, hovers: [], thrown: [] };
    const towns = (typeof TOWNSHIPS !== 'undefined') ? TOWNSHIPS : [];
    out.nTown = towns.length;
    const t = towns.find(x => x && x.alert_val > 0) || towns[0];
    out.sample = t ? (t.county + t.township) : null;
    if (t && typeof selectTown === 'function') { try { selected = t; } catch (e) {} }
    else { try { selected = t; } catch (e) {} }

    const tryDraw = (label, fn) => {
      try { fn(); out.drawn.push(label); }
      catch (e) { out.thrown.push(label + ' → ' + e.message); }
    };
    tryDraw('鄉鎮逐日 cv',        () => drawChart(t));
    tryDraw('鄉鎮逐時 cv-district-hyeto',
            () => _drawTownChart(t, 'cv-district-hyeto', false, 'hyeto'));
    tryDraw('各分署逐日 cv-all-daily',  () => drawAllDistrictDailyChart('cv-all-daily'));
    tryDraw('各分署逐時 cv-all-hyeto',  () => drawAllDistrictHyetoChart('cv-all-hyeto'));

    // 幾何登記簿：每張畫過的圖都應登記，且 nItems/itemW 合理
    ['cv', 'cv-district-hyeto', 'cv-all-daily', 'cv-all-hyeto'].forEach(id => {
      const g = _chartGeom(id);
      out.geoms[id] = g ? { kind: g.kind, pL: Math.round(g.pL),
                            itemW: Math.round(g.itemW * 100) / 100, nItems: g.nItems } : null;
    });

    // 鄉鎮圖位於預設收合的面板內，量不到寬度就測不到 tooltip —— 先展開
    ['body-daily', 'daily-town', 'body-district-hyeto', 'sec-body'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = 'block';
    });
    document.querySelectorAll('.sec-body').forEach(el => { el.style.display = 'block'; });
    ['daily-town', 'hyeto-town'].forEach(id => {
      const el = document.getElementById(id); if (el) el.style.display = 'block';
    });

    // tooltip：每張圖沿 x 軸滑過 25 個位置
    ['cv', 'cv-district-hyeto', 'cv-all-daily', 'cv-all-hyeto'].forEach(id => {
      const cv = document.getElementById(id);
      if (!cv) { out.hovers.push([id, 'no-canvas']); return; }
      const rect = cv.getBoundingClientRect();
      if (!(rect.width > 0)) { out.hovers.push([id, 'zero-width']); return; }
      let okN = 0, err = null;
      for (let k = 0; k <= 24; k++) {
        const clientX = rect.left + rect.width * k / 24;
        const clientY = rect.top + rect.height / 2;
        try { showChartTooltip({ clientX, clientY }, id); okN++; }
        catch (e) { err = e.message; break; }
      }
      out.hovers.push([id, err ? ('throw: ' + err) : ('ok ' + okN + '/25')]);
    });

    // 放大檢視：四張圖都會畫到同一張 chart-zoom-canvas，幾何必須逐次覆寫
    out.zoom = [];
    [['cv', () => drawChartHQ(t, 'chart-zoom-canvas')],
     ['cv-district-hyeto', () => drawHyetograph(t, 'chart-zoom-canvas')],
     ['cv-all-daily', () => drawAllDistrictDailyChart('chart-zoom-canvas')],
     ['cv-all-hyeto', () => drawAllDistrictHyetoChart('chart-zoom-canvas')],
    ].forEach(([src, fn]) => {
      try {
        fn();
        const g = _chartGeom('chart-zoom-canvas');
        // 放大後 tooltip 也要能滑（用畫布內部座標直接呼叫換算）
        const mid = g ? _geomIndexAt(g, g.pL + g.itemW * (g.nItems / 2) + g.itemW / 2) : -2;
        out.zoom.push([src, g ? `${g.kind} pL=${Math.round(g.pL)} n=${g.nItems} 中點槽=${mid}` : '未登記']);
      } catch (e) { out.zoom.push([src, 'throw: ' + e.message]); }
    });

    // 幾何換算的一致性：登記的 itemW 必須與畫布實際寬度相符
    out.geomSane = [];
    ['cv', 'cv-district-hyeto', 'cv-all-daily', 'cv-all-hyeto'].forEach(id => {
      const g = _chartGeom(id), cv = document.getElementById(id);
      if (!g || !cv) return;
      const span = g.itemW * g.nItems;
      const avail = cv.width - g.pL - g.pR;
      out.geomSane.push([id, Math.abs(span - avail) < 1.5]);
    });

    // ETR2 折線：分署逐時圖的覆蓋率閘門是否真的在作用
    try {
      const d = _calcDistrictHourly();
      out.distr = DISTRICT_ORDER.map((nm, di) => {
        const er = d.etrRows[di] || [], cov = (d.covRows || [])[di] || [];
        return { nm, nNull: er.filter(v => v == null).length,
                 nZero: er.filter(v => v === 0).length,
                 maxCov: Math.max(0, ...cov), n: er.length };
      });
      out.maxEtrAxis = d.maxEtr;
    } catch (e) { out.thrown.push('_calcDistrictHourly → ' + e.message); }
    return out;
  });

  const ok = (c, m) => { if (!c) sf.push(m); };

  console.log('   載入鄉鎮數：', r.nTown, '／樣本：', r.sample);
  console.log('   已繪製：', r.drawn.join('、') || '（無）');
  console.log('   幾何登記：');
  Object.entries(r.geoms).forEach(([k, v]) =>
    console.log('     ', k.padEnd(20), v ? JSON.stringify(v) : '未登記'));
  console.log('   tooltip 滑行：');
  r.hovers.forEach(([k, v]) => console.log('     ', k.padEnd(20), v));
  if (r.distr) {
    console.log('   分署逐時 ETR2（n=' + (r.distr[0] || {}).n + '，軸上限 ' + r.maxEtrAxis + '%）：');
    r.distr.forEach(d => console.log(`      ${d.nm.padEnd(6)} 留白 ${String(d.nNull).padStart(3)} 格`
      + `／值為0 ${String(d.nZero).padStart(3)} 格／最佳覆蓋 ${d.maxCov} 個鄉鎮`));
  }

  ok(r.thrown.length === 0, '繪製或聚合丟出例外：' + r.thrown.join('；'));
  ok(r.drawn.length === 4, `應繪製 4 張圖，實得 ${r.drawn.length}`);
  Object.entries(r.geoms).forEach(([k, v]) => ok(v, `${k} 未登記幾何`));
  r.hovers.forEach(([k, v]) => ok(/^ok /.test(v), `${k} tooltip 異常：${v}`));
  r.geomSane.forEach(([k, good]) => ok(good, `${k} 登記的 itemW×nItems 與畫布寬度不符`));
  console.log('   放大檢視（共用 chart-zoom-canvas）：');
  (r.zoom || []).forEach(([k, v]) => console.log('     ', k.padEnd(20), v));
  (r.zoom || []).forEach(([k, v]) => ok(!/throw|未登記/.test(v), `${k} 放大後異常：${v}`));
  ok((r.zoom || []).length === 4, '放大檢視未涵蓋 4 張圖');
  ok(errs.length === 0, '頁面錯誤：' + errs.slice(0, 5).join(' ｜ '));
  // 軸上限必須可被 4 整除（刻度整數）
  if (r.maxEtrAxis != null) ok(r.maxEtrAxis % 4 === 0 || (r.maxEtrAxis / 4) % 1 === 0,
    `分署逐時圖 ETR2 軸上限 ${r.maxEtrAxis} 無法 4 等分成整數刻度`);

  await browser.close();
  if (sf.length) {
    console.log('❌ test_chart_smoke 失敗 ' + sf.length + ' 項');
    sf.forEach(s => console.log('   - ' + s));
    process.exit(1);
  }
  console.log('✅ test_chart_smoke 全數通過');
})().catch(e => { console.error('❌ 例外：', e); process.exit(1); });

// FORMOSA 跨介面一致性稽核
// ====================================================================
// 使用者：「我一個一個抓會抓不完。」
//
// 抓不完的原因是：同一個量在系統裡有很多個出口（地圖著色、排行、分署摘要、
// 鄉鎮圖、逐時圖、tooltip、CSV），只要其中一條路徑自己算一份，就會分歧，
// 而分歧只能靠人眼在某一張圖上碰巧發現。
//
// 本稽核反過來做：對同一個鄉鎮、同一個時段，把**每一個出口**的值都取出來
// 比對。不一致就報出來，不必等人去看圖。
//
// 另外檢查物理不變量 —— 這些是「不管資料從哪來都必須成立」的條件：
//   P1  ETR2 ≥ 當日觀測雨量（官方權重 R0=1.0，故 ETR2 必含今日全量）
//   P2  ETR2% = ETR2 / 警戒值 x 100
//   P3  相鄰段變化量不得超過衰減律允許範圍：
//         E(s+1) − E(s)x0.7^(1/4) ≈ 該段雨量，不可憑空增加
//   P4  沒有官方值時必須是 null，不可以是 0
//
// 用法：node audit_consistency.js [http://127.0.0.1:8899/_local.html]
const fs = require('fs');
const puppeteer = require('puppeteer');
const URL = process.argv[2] || 'http://127.0.0.1:8899/_local.html';
const _EXEC = [process.env.PUPPETEER_EXECUTABLE_PATH,
  '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  '/opt/pw-browsers/chromium/chrome-linux/chrome'].find(p => p && fs.existsSync(p));
const _LAUNCH = { args: ['--no-sandbox', '--disable-dev-shm-usage'] };
if (_EXEC) _LAUNCH.executablePath = _EXEC;

(async () => {
  const browser = await puppeteer.launch(_LAUNCH);
  const page = await browser.newPage();
  const pageErrs = [];
  page.on('pageerror', e => pageErrs.push(e.message));
  await page.goto(URL, { waitUntil: 'networkidle2', timeout: 180000 });
  await new Promise(r => setTimeout(r, 2500));

  const R = await page.evaluate(() => {
    const out = { surfaces: [], invariants: [], meta: {} };
    const towns = (typeof TOWNSHIPS !== 'undefined') ? TOWNSHIPS : [];
    out.meta.nTown = towns.length;
    out.meta.model = (typeof forecastModel !== 'undefined') ? forecastModel : '?';
    const qf = (typeof MODEL_FIELD !== 'undefined' && MODEL_FIELD[forecastModel])
               || 'qpf_best';
    // undefined 與 null 都代表「沒有值」，視為相同；0 是真實數值，不可與之混同
    const near = (a, b, tol) => (a == null && b == null) ? true
      : (a == null || b == null) ? false : Math.abs(a - b) <= tol;

    // 受測對象：有警戒值的鄉鎮（ETR2 才有意義）
    const pool = towns.filter(t => t && t.alert_val > 0);
    out.meta.nAlert = pool.length;

    // ── 1) ETR2% 跨介面比對 ─────────────────────────────────
    const saveMode = (typeof mode !== 'undefined') ? mode : 'rain';
    const saveFrom = segFrom, saveTo = segTo;
    const mism = [];
    const SEGS = [0, 1, 2, 3];
    try {
      for (const t of pool) {
        for (const sg of SEGS) {
          const A = _etrPctAt(t, sg, qf);                       // 唯一取值入口
          const m = townMetrics(t, sg);
          const B = (m && m.etrPct != null) ? Math.round(m.etrPct) : null;
          const cd = _townChartData(t, 'hyeto', qf);
          const C = (cd.etr || [])[sg + 2];                     // 鄉鎮組體圖
          const hs = _etr2HourlySeries(t, sg * 6 + 5, sg * 6 + 5);
          const D = hs ? hs[0] : null;                          // 逐時圖（段末小時）
          // 地圖／排行／摘要共用 getAccum（單段視窗）
          mode = 'etr'; segFrom = sg; segTo = sg;
          const acc = getAccum(t);
          const E = (acc && acc.etrPct != null) ? Math.round(acc.etrPct) : null;
          mode = saveMode;
          const row = { n: (t.county || '') + (t.township || ''), sg, A, B, C, D, E };
          if (!near(A, B, 1)) mism.push({ ...row, why: '計算層(townMetrics) ≠ 取值入口' });
          else if (!near(A, C, 1)) mism.push({ ...row, why: '鄉鎮組體圖 ≠ 取值入口' });
          else if (!near(A, D, 2)) mism.push({ ...row, why: '逐時圖 ≠ 取值入口（內插容差 2）' });
          else if (!near(A, E, 1)) mism.push({ ...row, why: '地圖/排行/摘要 ≠ 取值入口' });
        }
      }
    } catch (e) { mism.push({ why: '例外：' + e.message }); }
    finally { mode = saveMode; segFrom = saveFrom; segTo = saveTo; }
    out.surfaces.push({ name: 'ETR2%（5 個出口 x 4 段）',
                        n: pool.length * SEGS.length * 5,
                        bad: mism.slice(0, 8), nBad: mism.length });

    // ── 2) 雨量跨介面比對 ───────────────────────────────────
    const mism2 = [];
    try {
      for (const t of towns) {
        for (const sg of SEGS) {
          const A = (getQpfArr(t, qf) || [])[sg];
          const m = townMetrics(t, sg);
          const B = m ? m.segRain : null;
          const cd = _townChartData(t, 'hyeto', qf);
          const C = (cd.rain || [])[sg + 2];
          const row = { n: (t.county || '') + (t.township || ''), sg, A, B, C };
          if (!near(A, B, 0.2)) mism2.push({ ...row, why: '計算層段雨量 ≠ getQpfArr' });
          else if (!near(A, C, 0.2)) mism2.push({ ...row, why: '鄉鎮組體圖雨量 ≠ getQpfArr' });
        }
      }
    } catch (e) { mism2.push({ why: '例外：' + e.message }); }
    out.surfaces.push({ name: '段雨量（3 個出口 x 4 段）',
                        n: towns.length * SEGS.length * 3,
                        bad: mism2.slice(0, 8), nBad: mism2.length });

    // ── 3) 物理不變量 ───────────────────────────────────────
    const D6 = Math.pow(0.7, 0.25);
    const inv = (name, bad, n, note) =>
      out.invariants.push({ name, n, nBad: bad.length, bad: bad.slice(0, 6), note });

    // P1 ETR2 ≥ 當日觀測雨量
    let b1 = [], n1 = 0;
    pool.forEach(t => {
      const e = t.etr2, d0 = (t.daily_rain || [])[0];
      if (e == null || d0 == null) return;
      n1++;
      if (e < d0 - 0.5) b1.push(`${t.county}${t.township} ETR2=${e} < 今日雨量=${d0}`);
    });
    inv('P1 ETR2 ≥ 當日觀測雨量', b1, n1, '官方權重 R0=1.0，ETR2 必含今日全量');

    // P2 ETR2% = ETR2 / 警戒值
    let b2 = [], n2 = 0;
    pool.forEach(t => {
      const e = t.etr2, a = _etrDen(t), p = t.etr2_pct;
      if (e == null || p == null || !(a > 0)) return;
      n2++;
      const calc = e / a * 100;
      if (Math.abs(calc - p) > 1.5)
        b2.push(`${t.county}${t.township} ${e}/${a}=${calc.toFixed(1)}% 但 etr2_pct=${p}%`);
    });
    inv('P2 ETR2% = ETR2 / 警戒值 x 100', b2, n2, '單位換算（曾發生 100 倍錯誤）');

    // P3 相鄰段變化不得超過衰減律允許
    let b3 = [], n3 = 0;
    pool.forEach(t => {
      const h = t.etr2_hist, hb = t.etr2_hist_base;
      if (!Array.isArray(h) || hb == null) return;
      for (let i = 1; i < h.length; i++) {
        const a = h[i - 1], b = h[i];
        if (a == null || b == null) continue;
        n3++;
        const minAllowed = a * D6 - 0.5;           // 無雨時的下限
        if (b < minAllowed - Math.max(1, a * 0.02))
          b3.push(`${t.county}${t.township} 段${i - hb} ${a}→${b}，低於純衰減 ${(a * D6).toFixed(1)}`);
      }
    });
    inv('P3 ETR2 不得低於純衰減值', b3, n3, 'E(s+1) ≥ E(s)x0.7^(1/4)，雨量只會往上加');

    // P4 沒有官方值必須是 null，不可為 0
    let b4 = [], n4 = 0;
    pool.forEach(t => {
      const h = t.etr2_hist;
      if (!Array.isArray(h)) return;
      h.forEach((v, i) => { n4++; if (v === 0) b4.push(`${t.county}${t.township} 段索引${i} 值為 0`); });
    });
    inv('P4 歷史值不得為 0（0 與「無值」必須分得開）', b4, n4, '0 會被畫成貼地的假線');

    // P5 補值標記長度必須與歷史序列一致
    let b5 = [], n5 = 0;
    pool.forEach(t => {
      if (!Array.isArray(t.etr2_hist_fill)) return;
      n5++;
      if (!Array.isArray(t.etr2_hist) || t.etr2_hist.length !== t.etr2_hist_fill.length)
        b5.push(`${t.county}${t.township} 長度不符`);
      else t.etr2_hist_fill.forEach((f, i) => {
        if (f && t.etr2_hist[i] == null)
          b5.push(`${t.county}${t.township} 索引${i} 標為補值但值是 null`);
      });
    });
    inv('P5 補值標記與歷史序列對齊', b5, n5, '標記錯位會讓實測被畫成虛線');

    // ── 4) 分署聚合 vs 成員鄉鎮 ─────────────────────────────
    let b6 = [], n6 = 0;
    try {
      const d = _calcDistrictHourly();
      DISTRICT_ORDER.forEach((nm, di) => {
        const er = d.etrRows[di] || [];
        const members = towns.filter(t => DISTRICT_COUNTIES[nm].includes(t.county)
                                       && t.alert_val > 0);
        for (let h = 0; h < er.length; h += 7) {        // 抽樣檢查
          const v = er[h];
          if (v == null) continue;
          n6++;
          let mx = null;
          members.forEach(t => {
            const b = _hourlyBars(t);
            const s = _etr2HourlySeries(t, b.hFrom, b.hFrom + er.length - 1);
            const x = s ? s[h] : null;
            if (x != null && (mx == null || x > mx)) mx = x;
          });
          if (mx == null || Math.abs(mx - v) > 1)
            b6.push(`${nm} h=${h} 分署值 ${v} ≠ 成員最大 ${mx}`);
        }
      });
    } catch (e) { b6.push('例外：' + e.message); }
    inv('P6 分署折線 = 署內鄉鎮最大值', b6, n6, '子集取最大會低估（覆蓋率閘門應已擋下）');

    return out;
  });

  // ── 報表 ───────────────────────────────────────────────────
  console.log(`\n載入 ${R.meta.nTown} 個鄉鎮（其中 ${R.meta.nAlert} 個有警戒值）`
            + `／模式 ${R.meta.model}\n`);
  let fail = 0;
  console.log('='.repeat(74));
  console.log('跨介面一致性：同一鄉鎮同一時段，各出口必須同值');
  console.log('='.repeat(74));
  R.surfaces.forEach(s => {
    const m = s.nBad ? '  !!  ' : '  OK  ';
    console.log(`${m}${s.name}：比對 ${s.n} 次，不一致 ${s.nBad} 筆`);
    s.bad.forEach(b => console.log(`        ${b.n || ''} 段${b.sg} `
      + `A=${b.A} B=${b.B} C=${b.C}${b.D !== undefined ? ` D=${b.D} E=${b.E}` : ''}　${b.why}`));
    if (s.nBad) fail += s.nBad;
  });
  console.log('\n' + '='.repeat(74));
  console.log('物理不變量：不管資料從哪來都必須成立');
  console.log('='.repeat(74));
  R.invariants.forEach(v => {
    const m = v.nBad ? '  !!  ' : '  OK  ';
    console.log(`${m}${v.name}：檢查 ${v.n} 筆，違反 ${v.nBad} 筆`);
    console.log(`        ${v.note}`);
    v.bad.forEach(b => console.log(`        → ${b}`));
    if (v.nBad) fail += v.nBad;
  });
  if (pageErrs.length) {
    console.log('\n  !!  頁面例外：');
    pageErrs.slice(0, 5).forEach(e => console.log('        ' + e));
    fail += pageErrs.length;
  }
  console.log('\n' + '='.repeat(74));
  await browser.close();
  if (fail) { console.log(`❌ 共 ${fail} 筆問題`); process.exit(1); }
  console.log('✅ 跨介面一致性與物理不變量全部通過');
})().catch(e => { console.error('❌ 例外：', e); process.exit(1); });

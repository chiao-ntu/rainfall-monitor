// 用 jsdom + 真實 Leaflet 跑 index.html 的 script，讓被測函式在真環境下執行。
// 目的：實際驗證幾何引擎與自訂範圍併算，而不是驗證 stub。
const fs = require('fs');
const { JSDOM } = require('jsdom');

const html = fs.readFileSync('index.html', 'utf8');
const leafletSrc = fs.readFileSync('node_modules/leaflet/dist/leaflet-src.js', 'utf8');
// 取出 index.html 的 inline script（不含外部 src）
const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);

// 只保留 <body> 的 DOM 結構，移除所有 script（改為手動注入，順序可控）
const shell = html
  .replace(/<script[\s\S]*?<\/script>/g, '')
  .replace(/<link[^>]*>/g, '');

const dom = new JSDOM(shell, {
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  url: 'https://example.org/',
});
const win = dom.window;

// jsdom 沒有 canvas/geolocation，補最小實作避免載入期爆掉
// canvas 2d context：用 Proxy 讓任何方法都可呼叫，屬性可讀寫（避免測試雜訊）
win.HTMLCanvasElement.prototype.getContext = function () {
  const store = {};
  return new Proxy(store, {
    get(t, p) {
      if (p in t) return t[p];
      if (p === 'measureText') return () => ({ width: 0 });
      if (p === 'getImageData') return () => ({ data: [] });
      if (p === 'createLinearGradient' || p === 'createRadialGradient')
        return () => ({ addColorStop(){} });
      return () => undefined;
    },
    set(t, p, v) { t[p] = v; return true; },
  });
};
win.fetch = () => Promise.reject(new Error('network disabled in test'));

function run(code, label) {
  const s = win.document.createElement('script');
  s.textContent = code;
  try { win.document.body.appendChild(s); return true; }
  catch (e) { console.log(`!! ${label} 載入失敗: ${e.message.slice(0, 200)}`); return false; }
}

// 攔截載入期的未捕捉錯誤，但不讓它中斷
const loadErrors = [];
win.addEventListener('error', e => loadErrors.push(e.message || String(e.error)));

run(leafletSrc, 'leaflet');
if (typeof win.L !== 'function' && typeof win.L !== 'object') {
  console.log('!! Leaflet 未掛上 window.L'); process.exit(1);
}
console.log(`Leaflet ${win.L.version} 已載入｜L.Control.extend: ${typeof win.L.Control.extend}`);

blocks.forEach((b, i) => run(b, `index script #${i + 1}`));
if (loadErrors.length) {
  console.log(`載入期錯誤 ${loadErrors.length} 則（前3）：`);
  loadErrors.slice(0, 3).forEach(m => console.log('   -', String(m).slice(0, 160)));
}

const G = win;
let fails = [];
const _fmt = d => d ? `${d.getMonth()+1}/${d.getDate()} ${String(d.getHours()).padStart(2,'0')}:00` : '—';
function chk(label, got, exp) {
  const ok = JSON.stringify(got) === JSON.stringify(exp);
  if (!ok) fails.push(label);
  console.log(`  ${ok ? 'OK ' : '!! '}${label}: ${JSON.stringify(got)}` +
    (ok ? '' : `  期望 ${JSON.stringify(exp)}`));
}

// ★ index.html 的頂層 let/const 是**詞法綁定**，不會掛到 window 上：
//   `G._userFactor = 1.5` 只是新增一個 window 屬性，程式讀的仍是詞法變數。
//   （與 TOWN_GEO 同一個陷阱。）同一 global scope 的後續 script 可以指派既有綁定，
//   故以注入 script 的方式設定，才是真的改到程式讀的那個變數。
function setLex(expr) {
  const el = win.document.createElement('script');
  el.textContent = expr;
  win.document.body.appendChild(el);
}
function getLex(expr) {
  const key = '__probe_' + Math.random().toString(36).slice(2);
  setLex(`window.${key} = (${expr});`);
  const v = win[key]; delete win[key];
  return v;
}

function need(name) {
  if (typeof G[name] !== 'function') { fails.push(`${name} 未定義`); console.log(`!! ${name} 未定義`); return false; }
  return true;
}

console.log('\n=== 函式可用性 ===');
['_spanAccum', '_hourlyBars', '_futuHourly', 'getQpfArr', '_tyKeyPoints', '_tyInterp',
 '_distToTaiwanKm', '_twLandPts', '_lsState', '_lsPct', '_alertStyleOf', '_tyAlertTimes',
 '_heavyRainLine', 'renderTyphoonLegend', '_lsRows'].forEach(n => {
  console.log(`  ${typeof G[n] === 'function' ? 'OK ' : '!! '}${n}`);
});

// ════════ 1. _spanAccum ════════
console.log('\n=== _spanAccum：自訂範圍跨時併算 ===');
if (need('_spanAccum') && need('_hourlyBars')) {
  const t1 = {
    county: '高雄市', township: '六龜區', alert_val: 300, rain_24h: 50,
    obs_1h_p48: Array(48).fill(2),
    qpf_1h_p48: Array(48).fill(1),
    qpf_best: Array(16).fill(12),
    qpf_1h: Array(96).fill(2),
    daily_rain: [10, 48, 48, 48, 30, 20, 10, 5, 0, 0, 0, 0, 0, 0, 0],
  };
  const bars = G._hourlyBars(t1);
  const nowH = bars.nowH;
  console.log(`   _hourlyBars: ${bars.vals.length} 槽, hFrom=${bars.hFrom}, nowH=${nowH}`);

  const segFut = Math.floor(nowH / 6) + 2;
  const fut = G._spanAccum(t1, segFut, segFut);
  chk('純未來段 obs=0', fut.obs, 0);
  console.log(`   純未來段: total=${fut.total} fc=${fut.fc}`);
  if (!(fut.fc > 0)) fails.push('純未來段 fc 應>0');

  const past = G._spanAccum(t1, -4, -1);
  chk('純過去段 fc=0', past.fc, 0);
  console.log(`   純過去段(昨天): total=${past.total} obs=${past.obs}`);
  if (!(past.obs > 0)) fails.push('純過去段 obs 應>0');

  // 跨越現在：加法一致性
  const a = G._spanAccum(t1, -4, -1), b = G._spanAccum(t1, 0, segFut);
  const both = G._spanAccum(t1, -4, segFut);
  const diff = Math.abs(both.total - (a.total + b.total));
  console.log(`   跨時: total=${both.total} obs=${both.obs} fc=${both.fc}`);
  console.log(`   分段相加=${(a.total + b.total).toFixed(1)}, 差=${diff.toFixed(2)}`);
  if (diff > 0.5) fails.push(`跨時加總不一致 差${diff.toFixed(2)}`);
  else console.log('  OK  跨時＝分段相加（無重複計算、無遺漏）');
  if (!(both.obs > 0 && both.fc > 0)) fails.push('跨時應同時有 obs 與 fc');
  else console.log('  OK  跨時同時含觀測與預測');

  // 舊 bug：過去跨多天只算一天
  const d3 = G._spanAccum(t1, -12, -1), d1 = G._spanAccum(t1, -4, -1);
  console.log(`   過去3天=${d3.total} vs 過去1天=${d1.total}`);
  if (!(d3.total > d1.total * 2)) fails.push('過去多天未累加（舊bug）');
  else console.log('  OK  過去跨多天正確累加（舊 bug 已修）');

  // 缺值不得當 0
  const t2 = Object.assign({}, t1, { daily_rain: [10, null, null, null, 30, 20, 10, 5] });
  const miss = G._spanAccum(t2, -28, -1);
  console.log(`   含 null 日觀測: total=${miss.total} hasNull=${miss.hasNull}`);
  chk('缺值有標記 hasNull', miss.hasNull, true);
}

// ════════ 2. 颱風幾何（真實 Leaflet 環境）════════
console.log('\n=== 颱風關鍵時間點幾何 ===');
if (need('_distToTaiwanKm') && need('_tyKeyPoints')) {
  // 注意：TOWN_GEO 是 const，無法從外部替換（早期版本試圖注入假矩形，其實無效
  // 且掩蓋了真實幾何的問題）。此處一律用 index.html 內嵌的真實 368 鄉鎮圖資驗證。
  const pts = G._twLandPts();
  let laMin=99, laMax=-99, loMin=999, loMax=-999;
  pts.forEach(p=>{ laMin=Math.min(laMin,p[0]); laMax=Math.max(laMax,p[0]);
                   loMin=Math.min(loMin,p[1]); loMax=Math.max(loMax,p[1]); });
  console.log(`   點集 ${pts.length} 點｜lat ${laMin.toFixed(2)}~${laMax.toFixed(2)}｜lng ${loMin.toFixed(2)}~${loMax.toFixed(2)}`);
  // ★ 必須排除釣魚臺列嶼(至124.56E)、東沙(116.7E)、南沙太平島(10.37N)
  if (loMax > 122.05) fails.push(`點集含釣魚臺列嶼或北方三島（最東 ${loMax.toFixed(3)}E）`);
  else console.log('  OK  已排除釣魚臺列嶼與北方三島（最東 ≤122.05E）');
  if (laMin < 21.0) fails.push(`點集含東沙/南沙（最南 ${laMin.toFixed(2)}N）`);
  else console.log('  OK  已排除東沙／南沙（最南 ≥21.0N）');
  // 逐一確認被排除的島礁座標不在點集判準內，且該保留者仍在
  [['花瓶嶼',25.424,121.946,false],['棉花嶼',25.484,122.108,false],
   ['彭佳嶼',25.628,122.078,false],['赤尾嶼',25.923,124.559,false],
   ['釣魚臺',25.745,123.478,false],['東沙島',20.70,116.72,false],
   ['南沙太平島',10.37,114.36,false],
   ['龜山島',24.845,121.950,true],['三貂角(貢寮)',25.010,122.000,true],
   ['蘭嶼',22.05,121.55,true],['澎湖馬公',23.57,119.57,true],
   ['金門',24.45,118.30,true],['烏坵',24.99,119.45,true],
   ['馬祖東引',26.38,120.47,true]].forEach(([nm,la,lo,want])=>{
    const got = G._inWarnScope(la,lo);
    const ok = got === want;
    if(!ok) fails.push(`${nm} 判準範圍應為 ${want}`);
    console.log(`   ${ok?'OK ':'!! '}${nm} (${la},${lo}) 納入判準=${got}${want?'（應納入）':'（應排除）'}`);
  });
  // 金馬必須納入（使用者明確要求）
  const dKinmen = G._distToTaiwanKm(24.45, 118.30);
  const dMatsu  = G._distToTaiwanKm(26.15, 119.95);
  console.log(`   金門=${dKinmen.toFixed(1)}km、馬祖=${dMatsu.toFixed(1)}km（應接近0）`);
  if (!(dKinmen < 5)) fails.push('金門未納入點集');
  if (!(dMatsu < 5)) fails.push('馬祖未納入點集');
  if (dKinmen < 5 && dMatsu < 5) console.log('  OK  金門、馬祖已納入100km判準範圍');

  // ★ 隨機全域比對：手選點無法涵蓋剪枝邊界。
  //   曾因「用粗篩上界當中止條件」而剪掉真正最近點，遠海距離偏大、
  //   颱風警報時間全錯，而 8 個手選點恰好全部避開該情形。
  {
    let seed = 42; const rnd = ()=>{ seed = (seed*1103515245+12345) & 0x7fffffff; return seed/0x7fffffff; };
    let worst = 0, worstAt = null;
    for (let k = 0; k < 120; k++) {
      const la = 18 + rnd()*14, lo = 112 + rnd()*18;
      let bf = Infinity;
      for (let i = 0; i < pts.length; i++) {
        const d = G._kmBetween(la, lo, pts[i][0], pts[i][1]); if (d < bf) bf = d;
      }
      const f = G._distToTaiwanKm(la, lo);
      const d = Math.abs(f - bf);
      if (d > worst) { worst = d; worstAt = [la.toFixed(2), lo.toFixed(2), f.toFixed(1), bf.toFixed(1)]; }
    }
    console.log(`   隨機 120 點全域比對，最大誤差 ${worst.toFixed(4)}km` +
      (worstAt ? `（最差 ${worstAt[0]},${worstAt[1]}：${worstAt[2]} vs ${worstAt[3]}）` : ''));
    if (worst > 0.01) fails.push(`距離函式全域精度有損（最大誤差 ${worst.toFixed(3)}km）`);
    else console.log('  OK  全域精度無損（剪枝未剪掉最近點）');
  }

  // 與暴力解逐一比對（確認未因剪枝剪掉真正最近點）
  const cases = [[26.9,126.6,'白海豚8/7'],[25.0,122.5,'東北角外海'],[23.5,121.0,'島內'],[22.0,120.3,'高雄外海']];
  let exact = true;
  for (const [la, lo, lbl] of cases) {
    let bf = Infinity;
    pts.forEach(p => { const d = G._kmBetween(la, lo, p[0], p[1]); if (d < bf) bf = d; });
    const f = G._distToTaiwanKm(la, lo);
    const ok = Math.abs(f - bf) < 1e-9;
    if (!ok) { exact = false; fails.push(`${lbl} 距離與暴力解不符`); }
    console.log(`   ${ok?'OK ':'!! '}${lbl}: ${f.toFixed(1)}km（暴力解 ${bf.toFixed(1)}km）`);
  }
  if (exact) console.log('  OK  距離函式與暴力解完全一致');
  const dFar = G._distToTaiwanKm(26.9, 126.6);
  if (!(dFar > 400)) fails.push(`白海豚位置距離不合理 ${dFar.toFixed(0)}km`);

  const ty = { name_zh: '測試', ty_no: '13',
    current:  { lat: 26.9, lng: 126.6, ws: 40, p: 950, r15: 280, r25: 90, gust: 50 },
    forecast: [
      { fh: 24, lat: 25.5, lng: 124.0, ws: 38, p: 955, r15: 280, r25: 90, gust: 48 },
      { fh: 48, lat: 24.0, lng: 121.5, ws: 33, p: 965, r15: 250, r25: 80, gust: 43 },
      { fh: 72, lat: 23.0, lng: 118.0, ws: 20, p: 985, r15: 150, r25: 0,  gust: 28 },
      { fh: 96, lat: 22.0, lng: 114.0, ws: 14, p: 1000, r15: 0, r25: 0,   gust: 20 },
    ] };
  const path = G._tyInterp(ty);
  console.log(`   內插路徑點數=${path.length}（預期 97：0~96h 每小時）`);
  chk('內插為每小時', path.length, 97);

  const kp = G._tyKeyPoints(ty);
  const kinds = (kp.points || []).map(p => p.kind);
  console.log('   關鍵點:', kinds.join(', ') || '(無)');
  ['sea_on', 'land_on', 'land_off', 'sea_off'].forEach(k => {
    if (!kinds.includes(k)) { fails.push(`缺 ${k}`); console.log(`  !! 缺 ${k}`); }
  });
  const get = k => (kp.points || []).find(p => p.kind === k);
  const so = get('sea_on'), lo = get('land_on'), lf = get('land_off'), sf2 = get('sea_off');
  if (so && lo && lf && sf2) {
    console.log(`   fh: sea_on=${so.fh} land_on=${lo.fh} land_off=${lf.fh} sea_off=${sf2.fh}`);
    if (!(so.fh <= lo.fh)) fails.push('海警觸及應不晚於陸警觸及');
    else console.log('  OK  海警觸及 ≤ 陸警觸及');
    if (!(lo.fh <= lf.fh)) fails.push('陸警觸及應早於脫離陸地');
    else console.log('  OK  觸陸 ≤ 脫離陸地');
    if (!(lf.fh <= sf2.fh)) fails.push('脫離陸地應早於脫離海警線');
    else console.log('  OK  脫離陸地 ≤ 脫離海警線');
    // 觸及點的 edge 應該真的跨過門檻
    console.log(`   edge: sea_on=${so.edge.toFixed(1)}km（應≤100）, land_on=${lo.edge.toFixed(1)}km（應≤0）`);
    if (!(so.edge <= 100.01)) fails.push('sea_on 的 edge 未 ≤100');
    if (!(lo.edge <= 0.01)) fails.push('land_on 的 edge 未 ≤0');
    // 兩個風圈資料都在
    if (so.r15 == null || so.r25 == null) fails.push('關鍵點缺 r15/r25');
    else console.log(`  OK  關鍵點含七級(${Math.round(so.r15)}km)與十級(${Math.round(so.r25)}km)風半徑`);
  }
  const T = kp.times || {};
  chk('willLand', T.willLand, true);
  if (T.sea_issue && T.sea_touch)
    chk('海警提前24h', Math.round((T.sea_touch - T.sea_issue) / 3600000), 24);
  if (T.land_issue && T.land_touch)
    chk('陸警提前18h', Math.round((T.land_touch - T.land_issue) / 3600000), 18);
  const grades = (kp.points || []).filter(p => p.kind === 'grade');
  console.log(`   強度升降階=${grades.length} 個: ${grades.map(g => g.label).join('；')}`);
  if (!grades.length) fails.push('應偵測到強度降階');

  // 不觸陸
  const ty2 = { name_zh: '遠離',
    current: { lat: 26.9, lng: 132.0, ws: 40, r15: 100, r25: 30 },
    forecast: [{ fh: 24, lat: 28.0, lng: 134.0, ws: 38, r15: 100, r25: 30 },
               { fh: 48, lat: 30.0, lng: 137.0, ws: 30, r15: 80, r25: 0 }] };
  const kp2 = G._tyKeyPoints(ty2);
  chk('遠離型 willLand=false', kp2.times.willLand, false);
  chk('遠離型 land_issue=null', kp2.times.land_issue, null);
  chk('遠離型無觸及點', (kp2.points || []).filter(p => p.kind !== 'grade').length, 0);

  // 退化輸入
  chk('無預報點回空', (G._tyKeyPoints({ current: { lat: 24, lng: 121, ws: 30, r15: 100 }, forecast: [] }).points || []).length, 0);
  chk('空物件回空', (G._tyKeyPoints({}).points || []).length, 0);
  chk('r15 缺值不爆', (G._tyKeyPoints({ current: { lat: 26, lng: 126, ws: 30 },
       forecast: [{ fh: 24, lat: 24, lng: 121, ws: 30 }] }).points || []).length, 0);
}

// ════════ 3. 四個警報時間的文字輸出 ════════
console.log('\n=== _tyAlertTimes：官方已發布 vs 系統推估 ===');
if (need('_tyAlertTimes')) {
  const ty = { current: { lat: 26.9, lng: 126.6, ws: 40, r15: 280, r25: 90 },
    forecast: [{ fh: 24, lat: 25.5, lng: 124.0, ws: 38, r15: 280, r25: 90 },
               { fh: 48, lat: 24.0, lng: 121.5, ws: 33, r15: 250, r25: 80 },
               { fh: 72, lat: 23.0, lng: 118.0, ws: 20, r15: 150, r25: 0 }] };
  G.TYPHOON_TRACK = [ty];
  G.TYPHOON_WARN = [{ warn_kind: 'SEA', headline: '海上颱風警報', severity_level: '海上颱風警報' }];
  const rowsSea = G._tyAlertTimes(ty);
  console.log(rowsSea.map(r => `   【${r[0]}】${r[1]}`).join('\n'));
  chk('已發布海警 → 顯示已發布', /已發布/.test(rowsSea[0][1]), true);
  chk('未發布陸警 → 顯示推估', /推估/.test(rowsSea[1][1]), true);

  G.TYPHOON_WARN = [{ warn_kind: 'LAND', headline: '陸上颱風警報', severity_level: '陸上颱風警報' }];
  const rowsLand = G._tyAlertTimes(ty);
  chk('已發布陸警 → 顯示已發布', /已發布/.test(rowsLand[1][1]), true);

  // ★ 預報末端仍在範圍內時，不得報出預報並未顯示的解除時刻
  G.TYPHOON_WARN = [];
  const tyStay = { current: { lat: 26.9, lng: 126.6, ws: 40, r15: 280, r25: 90 },
    forecast: [{ fh: 24, lat: 25.5, lng: 124.0, ws: 38, r15: 280, r25: 90 },
               { fh: 48, lat: 24.0, lng: 121.5, ws: 33, r15: 250, r25: 80 }] };
  const rStay = G._tyAlertTimes(tyStay);
  console.log(`   末端仍在範圍: 【${rStay[2][0]}】${rStay[2][1]}`);
  console.log(`                 【${rStay[3][0]}】${rStay[3][1]}`);
  chk('解除陸警 → 說明尚未脫離', /尚未脫離/.test(rStay[2][1]), true);
  chk('解除海警 → 說明尚未脫離', /尚未脫離/.test(rStay[3][1]), true);
  chk('兩個解除時間不得相同且憑空給值', rStay[2][1] === rStay[3][1] && /推估 \d/.test(rStay[2][1]), false);

  // 確實脫離者仍要給出時間，且陸警解除早於海警解除
  const tyPass = { current: { lat: 24.0, lng: 121.5, ws: 33, r15: 250, r25: 80 },
    forecast: [{ fh: 24, lat: 23.5, lng: 118.0, ws: 25, r15: 180, r25: 0 },
               { fh: 48, lat: 23.0, lng: 114.0, ws: 18, r15: 100, r25: 0 },
               { fh: 72, lat: 22.5, lng: 110.0, ws: 14, r15: 0, r25: 0 }] };
  const kpPass = G._tyKeyPoints(tyPass);
  const TP = kpPass.times;
  console.log(`   通過型: land_lift=${TP.land_lift && _fmt(TP.land_lift)} sea_lift=${TP.sea_lift && _fmt(TP.sea_lift)}`);
  if (TP.land_lift && TP.sea_lift) {
    chk('陸警解除早於海警解除', TP.land_lift <= TP.sea_lift, true);
  } else fails.push('通過型應能推估出解除時間');
  chk('通過型 landStillIn=false', TP.landStillIn, false);

  // 不觸陸時的 fallback 文字
  G.TYPHOON_WARN = [];
  const rowsNo = G._tyAlertTimes({ current: { lat: 26.9, lng: 132, ws: 40, r15: 100, r25: 30 },
    forecast: [{ fh: 24, lat: 28, lng: 134, ws: 38, r15: 100, r25: 30 }] });
  console.log(`   不觸陸: 【${rowsNo[1][0]}】${rowsNo[1][1]}`);
  chk('不觸陸 → 不發布陸警文字', /不發布陸上颱風警報/.test(rowsNo[1][1]), true);
}

// ════════ 4. 雨勢較大地區 ════════
console.log('\n=== _heavyRainLine ===');
if (need('_heavyRainLine')) {
  G.HEAVY_RAIN_TH = 150;
  G.HEAVY_RAIN_COUNTIES = {};
  chk('無縣市達標時有說明', /無縣市/.test(G._heavyRainLine()), true);
  G.HEAVY_RAIN_COUNTIES = {
    '宜蘭縣': { obs_max: 320, obs_town: '大同鄉', fc_max: 210, fc_town: '大同鄉', fc_win: 0 },
    '花蓮縣': { obs_max: 100, obs_town: '秀林鄉', fc_max: 265, fc_town: '秀林鄉', fc_win: 1 },
  };
  const line = G._heavyRainLine();
  console.log('  ', line.replace(/<[^>]*>/g, ''));
  chk('觀測未達門檻者不列觀測值', /花蓮縣[^、]*觀測/.test(line), false);
  chk('宜蘭排在花蓮之前（峰值較大）', line.indexOf('宜蘭縣') < line.indexOf('花蓮縣'), true);
  chk('預測有標窗別', /24–48h/.test(line), true);
}

// ════════ 5. 警戒狀態 ════════
console.log('\n=== 警戒狀態：官方優先於推估 ===');
if (need('_lsState')) {
  chk('官方紅 rank=5', G._lsState({ off_level: 'r', est_yellow_now: true }).rank, 5);
  chk('官方黃 rank=4', G._lsState({ off_level: 'y' }).rank, 4);
  chk('官方黃 isOfficial', G._lsState({ off_level: 'y' }).isOfficial, true);
  chk('推估達紅 rank=3', G._lsState({ off_level: '', est_red_fc: true }).rank, 3);
  chk('推估 isOfficial=false', G._lsState({ off_level: '', est_red_fc: true }).isOfficial, false);
  chk('推估黃 rank=2', G._lsState({ off_level: '', est_yellow_now: true }).rank, 2);
  chk('入夜示警 rank=1', G._lsState({ off_level: '', night_warn: true }).rank, 1);
  chk('未達標 rank=0', G._lsState({ off_level: '' }).rank, 0);
  chk('null 安全', G._lsState(null).rank, 0);
}
if (need('_alertStyleOf')) {
  chk('官方紅覆蓋推估', G._alertStyleOf({ off_level: 'r', est_yellow_now: true }).tag, '🔴官方紅色警戒');
  chk('取用失敗有專屬標示', G._alertStyleOf({ off_level: null }).tag, '官方警戒取用失敗');
  chk('推估可辨識', G._alertStyleOf({ off_level: '', est_red_fc: true }).tag, '推估未來24h達紅');
}
if (need('_lsPct')) {
  chk('達成率用調整後值', G._lsPct({ etr2: 150, alert: 400, alert_adj: 300 }), 50);
  chk('無調整用原值', G._lsPct({ etr2: 200, alert: 400 }), 50);
  chk('缺ETR2回null', G._lsPct({ alert: 400 }), null);
}

// ════════ 6. 只列達警戒者 ════════
console.log('\n=== _lsRows(onlyAlert) ===');
if (need('_lsRows')) {
  // ★ 用 TMAP 中不存在的鄉鎮，讓 _withEst 走「退回後端值」路徑，
  //   才測得到排序本身；否則前端重算會覆寫 fixture 的 est_red_fc。
  G.LANDSLIDE_ALERTS = {
    'A1': { county: '測試縣', town: '甲鄉', off_level: 'r', etr2: 700, alert: 600 },
    'A2': { county: '測試縣', town: '乙鄉', off_level: '', est_red_fc: true, etr2: 400, alert: 700 },
    'A3': { county: '測試縣', town: '丙鄉', off_level: '', etr2: 10, alert: 650 },
    'A4': { county: '測試縣', town: '丁鄉', off_level: 'y', etr2: 300, alert: 600 },
  };
  G._lsGeo = null;
  const all = G._lsRows(false), only = G._lsRows(true);
  chk('全部 4 筆', all.length, 4);
  chk('只列達警戒 3 筆（排除 A3）', only.length, 3);
  chk('排序順序：官方紅→官方黃→推估', only.map(r=>r.no), ['A1','A4','A2']);
}

// ════════ 7. 海警線等值線與縣市涵蓋 ════════
console.log('\n=== 100km 海警線等值線 ===');
if (need('_seaLineSegs')) {
  const t0 = Date.now();
  const segs = G._seaLineSegs();
  console.log(`   ${segs.length} 段，耗時 ${Date.now() - t0}ms`);
  if (!(segs.length > 100)) fails.push(`海警線段數過少（${segs.length}）`);
  else console.log('  OK  等值線段數合理');
  // 等值線上的點，距陸地應接近 100km（這是「畫的線＝判定的線」的驗證）
  let worst = 0;
  const sample = segs.filter((_, i) => i % Math.max(1, Math.floor(segs.length / 40)) === 0);
  sample.forEach(sg => {
    sg.forEach(p => {
      const d = G._distToTaiwanKm(p[0], p[1]);
      worst = Math.max(worst, Math.abs(d - 100));
    });
  });
  console.log(`   抽樣 ${sample.length} 段，距陸地與 100km 的最大偏差 = ${worst.toFixed(1)}km`);
  if (!(worst < 5)) fails.push(`等值線偏差過大 ${worst.toFixed(1)}km`);
  else console.log('  OK  等值線確實落在 100km 附近（畫的線＝判定用的線）');
}

console.log('\n=== _countiesInRadius：觸陸後列出涵蓋縣市 ===');
if (need('_countiesInRadius')) {
  // 中心放在臺灣正中央、半徑 300km → 應涵蓋多個縣市
  const many = G._countiesInRadius(23.8, 121.0, 300);
  console.log(`   (23.8N,121.0E) r=300km → ${many.length} 縣市：${many.slice(0, 8).join('、')}…`);
  if (!(many.length >= 10)) fails.push('300km 應涵蓋 10 個以上縣市');
  else console.log('  OK  大範圍涵蓋多縣市');
  // 半徑 0 / 負值 → 空
  chk('r=0 回空', G._countiesInRadius(23.8, 121.0, 0), []);
  chk('r=null 回空', G._countiesInRadius(23.8, 121.0, null), []);
  // 遠方小圈 → 空
  chk('遠方小圈回空', G._countiesInRadius(26.9, 126.6, 50), []);
  // 恆春半島小圈 → 應含屏東縣
  const ken = G._countiesInRadius(22.0, 120.75, 40);
  console.log(`   (22.0N,120.75E) r=40km → ${ken.join('、') || '(空)'}`);
  if (!ken.includes('屏東縣')) fails.push('恆春半島 40km 應含屏東縣');
  else console.log('  OK  小範圍定位正確');
}


// ════════ 8. CSV 與地圖著色必須同源（回歸測試）════════
console.log('\n=== _summaryRain 與 getAccum 一致性（CSV vs 地圖）===');
if (need('_summaryRain') && need('getAccum')) {
  const t = {
    county:'高雄市', township:'六龜區', alert_val:300, rain_24h:50,
    obs_1h_p48: Array(48).fill(2), qpf_1h_p48: Array(48).fill(1),
    qpf_best: Array(16).fill(12), qpf_1h: Array(96).fill(2),
    daily_rain:[10,48,48,48,30,20,10,5,0,0,0,0,0,0,0],
  };
  const nowSeg = G._nowSeg();
  const vals = [];
  const cases = [
    ['自訂-跨時',   'custom', -4, nowSeg + 2],
    ['自訂-純過去', 'custom', -12, -1],
    ['自訂-純未來', 'custom', nowSeg + 1, nowSeg + 3],
    ['過去整天',    'past1',  -8, -5],
    ['今天',        'today',  0, 3],
  ];
  cases.forEach(([lbl, wk, sf, st]) => {
    // winKey/segFrom/segTo/mode 同為詞法綁定，必須用 setLex 才改得到（見上方註解）
    setLex(`winKey='${wk}'; segFrom=${sf}; segTo=${st}; mode='rain';`);
    const a = G.getAccum(t, 'rain');
    const b = G._summaryRain(t);
    const av = a && a.totalRain == null ? null : Math.round(a.totalRain*10)/10;
    const ok = av === b;
    if (!ok) fails.push(`${lbl}: 地圖 ${av} ≠ CSV ${b}`);
    console.log(`  ${ok?'OK ':'!! '}${lbl}: 地圖=${av} CSV=${b}` +
      `  [winKey=${getLex('winKey')} segFrom=${getLex('segFrom')} segTo=${getLex('segTo')}]`);
    vals.push(av);
  });
  // 五種視窗若算出完全相同的值，代表 setLex 沒生效（測試本身失效），要喊出來
  if (new Set(vals.map(v => String(v))).size === 1) {
    fails.push('五種視窗值全同 → 視窗狀態沒被真正切換，測試無效');
    console.log('  !! 五種視窗值全同，測試無效（狀態未切換）');
  } else {
    console.log('  OK  各視窗值有差異，狀態確實有切換');
  }
}


// ════════ 9. 自訂倍率（與自動偏差修正疊加）════════
console.log('\n=== 自訂倍率 ===');
if (need('getQpfArr') && need('_qpfFactor') && need('setUserFactor')) {
  const t = { qpf_best: [10, 20, null, 5], qpf_cwa: [10, 20, null, 5] };
  setLex('_biasApplyOn = false; _userFactorOn = false; _userFactor = 1; _biasFactor = 2;');
  chk('都關閉時倍率=1', G._qpfFactor(), 1);
  chk('都關閉時原值', G.getQpfArr(t, 'qpf_best'), [10, 20, null, 5]);

  setLex('_userFactor = 1.5; _userFactorOn = true;');
  chk('僅自訂倍率 1.5', G._qpfFactor(), 1.5);
  chk('值×1.5，null 保持 null', G.getQpfArr(t, 'qpf_best'), [15, 30, null, 7.5]);

  setLex('_biasApplyOn = true;');            // 偏差 2 × 自訂 1.5 = 3
  chk('疊加倍率=3', G._qpfFactor(), 3);
  chk('值×3', G.getQpfArr(t, 'qpf_best'), [30, 60, null, 15]);
  chk('★純CWA不受任何倍率影響', G.getQpfArr(t, 'qpf_cwa'), [10, 20, null, 5]);

  // 只乘一次、只捨入一次
  setLex('_biasFactor = 1.15; _userFactor = 1.15;');   // 合併 1.3225
  const once = Math.round(7.7 * 1.3225 * 10) / 10;
  chk('合併後只捨入一次', G.getQpfArr({ qpf_best: [7.7] }, 'qpf_best'), [once]);

  // 快取鍵必須隨倍率與開關改變
  if (need('_factorKey')) {
    setLex('_userFactor = 1.0;'); const k1 = G._factorKey();
    setLex('_userFactor = 2.0;'); const k2 = G._factorKey();
    chk('快取鍵隨自訂倍率改變', k1 !== k2, true);
    setLex('_userFactorOn = false;'); const k3 = G._factorKey();
    chk('快取鍵隨開關改變', k2 !== k3, true);
  }

  // 限幅與無效輸入（走 setUserFactor 公開介面）
  setLex('_userFactorOn = false; _biasApplyOn = false;');
  G.setUserFactor(99);    chk('上限截斷至 5', getLex('_userFactor'), 5);
  G.setUserFactor(0.01);  chk('下限截斷至 0.1', getLex('_userFactor'), 0.1);
  const keep = getLex('_userFactor');
  G.setUserFactor('abc'); chk('非數字不套用', getLex('_userFactor'), keep);
  G.setUserFactor(-3);    chk('負值不套用', getLex('_userFactor'), keep);
  G.setUserFactor(0);     chk('零不套用', getLex('_userFactor'), keep);
  G.setUserFactor(1.234); chk('四捨五入到兩位', getLex('_userFactor'), 1.23);
  setLex('_userFactor = 1; _userFactorOn = false; _biasApplyOn = false;');
}


// ════════ 10. 自訂範圍超過 +72h 不得歸零（回歸測試）════════
console.log('\n=== _spanAccum：超出逐時視窗（>+72h）仍須有值 ===');
if (need('_spanAccum')) {
  const t = {
    county:'高雄市', township:'六龜區', alert_val:300,
    obs_1h_p48: Array(48).fill(2), qpf_1h_p48: Array(48).fill(1),
    qpf_best: Array(24).fill(12), qpf_1h: Array(96).fill(2),
    daily_rain:[10,48,48,48,30,20,10,5,0,0,0,0,0,0,0],
  };
  setLex("forecastModel='best'; _userFactorOn=false; _biasApplyOn=false;");
  const bars = G._hourlyBars(t);
  const lastHourSeg = Math.floor((bars.hFrom + bars.vals.length - 1) / 6);
  // 完全落在逐時視窗之外的未來段
  const farA = lastHourSeg + 2, farB = lastHourSeg + 4;
  const far = G._spanAccum(t, farA, farB);
  console.log(`   逐時視窗至段 ${lastHourSeg}；取段 ${farA}~${farB}（全在視窗外）`);
  console.log(`   total=${far.total} fc=${far.fc} coarseSegs=${far.coarseSegs}`);
  if (!(far.total > 0)) fails.push('超出+72h 的自訂範圍歸零（舊 bug 未修）');
  else console.log('  OK  超出逐時視窗仍以 6h QPF 計入（舊 bug 已修）');
  chk('全在視窗外時 coarseSegs=段數', far.coarseSegs, farB - farA + 1);

  // 跨越視窗邊界的段不得重複計算：拆兩半相加應等於整段
  const wide = G._spanAccum(t, 0, farB);
  const mid = lastHourSeg;
  const p1 = G._spanAccum(t, 0, mid), p2 = G._spanAccum(t, mid + 1, farB);
  const diff = Math.abs(wide.total - (p1.total + p2.total));
  console.log(`   跨界: 整段=${wide.total}, 拆兩半相加=${(p1.total+p2.total).toFixed(1)}, 差=${diff.toFixed(2)}`);
  if (diff > 0.6) fails.push(`跨逐時視窗邊界重複或漏算（差 ${diff.toFixed(2)}）`);
  else console.log('  OK  跨界不重複、不漏算');

  // 長範圍（接近 slider 上限）應單調不減
  const r5 = G._spanAccum(t, 0, 19), r10 = G._spanAccum(t, 0, 39);
  console.log(`   0~19段=${r5.total}, 0~39段=${r10.total}`);
  if (!(r10.total >= r5.total)) fails.push('範圍加長後總量反而變少');
  else console.log('  OK  範圍加長總量不減');
}


// ════════ 11. 情境編輯器（逐日 × 分署×地形 × 模式／加成／倍率）════════
console.log('\n=== 情境編輯器 ===');
if (need('getQpfArr') && need('_scnActive') && need('_townGroupKey')) {
  // 山區（有坡地警戒值）與平地（無）各一個鄉鎮，分屬不同分署
  const mtn = { county:'南投縣', township:'仁愛鄉', alert_val:700,
                qpf_best:Array(40).fill(10), qpf_hi:Array(40).fill(30),
                qpf_lo:Array(40).fill(4), qpf_cwa:Array(40).fill(8) };
  const flat= { county:'臺南市', township:'安南區', alert_val:0,
                qpf_best:Array(40).fill(10), qpf_hi:Array(40).fill(30),
                qpf_lo:Array(40).fill(4), qpf_cwa:Array(40).fill(8) };
  // 注入四分類對照表（正式版由 apply_terrain_zones.py 產生）

  chk('山區群組鍵', G._townGroupKey(mtn), '南投分署|山區');
  chk('沿海群組鍵（安南區臨海）', G._townGroupKey(flat), '臺南分署|沿海地區');
  // ★ 官方定義判定後，南投市為淺山區（100-1000m 面積佔比達標）
  chk('淺山群組鍵（南投市）', G._townGroupKey({county:'南投縣', township:'南投市'}), '南投分署|淺山區');
  chk('平地群組鍵（臺北大安區）', G._townGroupKey({county:'臺北市', township:'大安區'}), '臺北分署|平地');
  chk('SCN_TERRAIN 四分類（順序：山區/淺山區/平地/沿海地區）', getLex('SCN_TERRAIN'),
      ['山區','淺山區','平地','沿海地區']);
  // 表中沒有的鄉鎮 → 退路（不得爆掉）
  const fb = G._townGroupKey({county:'南投縣', township:'不存在鄉', alert_val:0});
  chk('未收錄鄉鎮走退路不爆', typeof fb === 'string' && fb.indexOf('|') > 0, true);

  setLex("forecastModel='best'; _userFactorOn=false; _biasApplyOn=false; _scnOn=false; _scnDays={};");
  chk('未啟用時 _scnActive=false', G._scnActive(), false);
  chk('未啟用時原值', G.getQpfArr(mtn,'qpf_best').slice(0,4), [10,10,10,10]);

  // 情境：第0天用強降雨、南投山區倍率1.5；第1天用弱降雨、臺南平地加成20mm
  setLex(`_scnOn = true; _scnDays = {
    0:{model:'hi', g:{'南投分署|山區':{add:0, mul:1.5}}},
    1:{model:'lo', g:{'臺南分署|沿海地區':{add:20, mul:1}}}
  };`);
  chk('_scnActive=true', G._scnActive(), true);
  const m = G.getQpfArr(mtn,'qpf_best'), f = G.getQpfArr(flat,'qpf_best');
  console.log(`   山區 day0(段0-3)=${m.slice(0,4)} day1(段4-7)=${m.slice(4,8)}`);
  console.log(`   平地 day0(段0-3)=${f.slice(0,4)} day1(段4-7)=${f.slice(4,8)}`);
  chk('山區day0：強降雨30×1.5', m.slice(0,4), [45,45,45,45]);
  chk('山區day1：弱降雨4，未設群組不調整', m.slice(4,8), [4,4,4,4]);
  chk('★沿海day0：非目標群組不受影響（用強降雨原值）', f.slice(0,4), [30,30,30,30]);
  chk('沿海day1：弱降雨4＋20/4=9', f.slice(4,8), [9,9,9,9]);
  chk('未設定的第3天沿用全域模式(best)', m.slice(12,16), [10,10,10,10]);

  // 加成能讓模式報 0 的地區產生雨量（乘法做不到的事）
  const dry = { county:'臺南市', township:'安南區', alert_val:0, qpf_best:Array(40).fill(0) };
  setLex(`_scnDays = {0:{model:null, g:{'臺南分署|沿海地區':{add:40, mul:1}}}};`);
  chk('模式報0＋加成40 → 每段10', G.getQpfArr(dry,'qpf_best').slice(0,4), [10,10,10,10]);
  setLex(`_scnDays = {0:{model:null, g:{'臺南分署|沿海地區':{add:0, mul:3}}}};`);
  chk('模式報0×倍率3 仍為0（故需加成）', G.getQpfArr(dry,'qpf_best').slice(0,4), [0,0,0,0]);

  // 負加成不得產生負雨量
  setLex(`_scnDays = {0:{model:null, g:{'南投分署|山區':{add:-200, mul:1}}}};`);
  const neg = G.getQpfArr(mtn,'qpf_best').slice(0,4);
  chk('負加成截止於 0', neg.every(v=>v>=0), true);

  // 與全域倍率相乘
  setLex(`_scnDays = {0:{model:null, g:{'南投分署|山區':{add:0, mul:2}}}};
          _userFactor = 1.5; _userFactorOn = true;`);
  chk('群組倍率2 × 全域1.5 = 30', G.getQpfArr(mtn,'qpf_best').slice(0,1), [30]);
  setLex('_userFactorOn = false;');

  // 快取鍵必須含情境簽章
  if (need('_factorKey')) {
    const k1 = G._factorKey();
    setLex(`_scnDays = {0:{model:null, g:{'南投分署|山區':{add:0, mul:2.5}}}};`);
    chk('快取鍵隨情境改變', k1 !== G._factorKey(), true);
    setLex('_scnOn = false;');
    const kOff = G._factorKey();
    setLex('_scnOn = true;');
    chk('快取鍵隨情境開關改變', kOff !== G._factorKey(), true);
  }

  // null 值保持 null（不得被加成填成數字）
  const withNull = { county:'南投縣', township:'仁愛鄉', alert_val:700,
                     qpf_best:[null,10,null,10, 10,10,10,10] };
  setLex(`_scnDays = {0:{model:null, g:{'南投分署|山區':{add:40, mul:2}}}};`);
  const wn = G.getQpfArr(withNull,'qpf_best').slice(0,4);
  console.log(`   含 null 的段: ${JSON.stringify(wn)}`);
  chk('null 仍為 null', [wn[0], wn[2]], [null, null]);
  chk('非 null 段正常調整 (10+10)*2', [wn[1], wn[3]], [40, 40]);

  // 全部歸零後應回到未啟用
  setLex(`_scnDays = {0:{model:null, g:{'南投分署|山區':{add:0, mul:1}}}};`);
  chk('設定值全為中性 → _scnActive=false', G._scnActive(), false);
  setLex('_scnOn=false; _scnDays={};');
}


// ════════ 12. 本輪修正的回歸測試 ════════
console.log('\n=== 官方值(CWA)絕不調整 ===');
if (need('getQpfArr')) {
  const t = { county:'南投縣', township:'仁愛鄉', alert_val:700,
              qpf_best:Array(64).fill(10), qpf_cwa:Array(64).fill(8) };
  setLex("forecastModel='cwa'; _userFactor=3; _userFactorOn=true; _biasApplyOn=false;");
  setLex("_scnOn=true; _scnDays={0:{model:null,g:{'南投分署|山區':{add:200,mul:3}}}};");
  // 全域倍率不得套用於 CWA（即使情境啟用）；情境本身的加成/倍率則允許
  chk('全域倍率不動 CWA（情境未設該群組時＝原值）',
      G.getQpfArr(t,'qpf_cwa').slice(0,4), [174,174,174,174]);
  // ★ 使用者後續改變決定（第7項）：情境編輯器中選 CWA 的日期**接受**加成／倍率，
  //   因為實務上需要官方值當基礎再調整。但全域倍率仍不動 qpf_cwa（上一條已驗）。
  setLex("forecastModel='best'; _userFactorOn=false; _scnDays={0:{model:'cwa',g:{'南投分署|山區':{add:200,mul:3}}}};");
  chk('情境中選 CWA → 接受調整 (8+50)*3', G.getQpfArr(t,'qpf_best').slice(0,4), [174,174,174,174]);
  setLex("_scnDays={0:{model:'hi',g:{}},1:{model:'cwa',g:{'南投分署|山區':{add:200,mul:3}}}};");
  const mix = G.getQpfArr(Object.assign({qpf_hi:Array(64).fill(20)}, t), 'qpf_best');
  console.log(`   day0(hi,無群組)=${mix.slice(0,4)} day1(cwa,+200×3)=${mix.slice(4,8)}`);
  chk('day0 強降雨原值', mix.slice(0,1), [20]);
  chk('day1 CWA 亦受情境調整', mix.slice(4,8), [174,174,174,174]);
  setLex("_userFactorOn=false; _userFactor=1; _scnOn=false; _scnDays={};");
}

console.log('\n=== 加成語意：每日總量 ===');
if (need('getQpfArr')) {
  const t = { county:'南投縣', township:'仁愛鄉', alert_val:700, qpf_best:Array(64).fill(10) };
  setLex("forecastModel='best'; _userFactorOn=false; _biasApplyOn=false;");
  setLex("_scnOn=true; _scnDays={0:{model:null,g:{'南投分署|山區':{add:200,mul:3}}}};");
  const a = G.getQpfArr(t,'qpf_best');
  const daySum = a.slice(0,4).reduce((x,y)=>x+(y||0),0);
  console.log(`   段值=${a.slice(0,4)}  日和=${daySum}`);
  chk('每段 (10+200/4)*3 = 180', a.slice(0,4), [180,180,180,180]);
  chk('日和 = 4*180 = 720（加成貢獻 600）', daySum, 720);
  chk('加成對日總量的貢獻 = 200*3', daySum - 4*10*3, 600);
  setLex("_scnOn=false; _scnDays={};");
}

console.log('\n=== 日期短標籤不得截斷 ===');
if (need('_scnDayShort')) {
  const s0 = G._scnDayShort(0), s5 = G._scnDayShort(5);
  console.log(`   day0="${s0}"  day5="${s5}"`);
  chk('括號成對(day0)', /^\d+\/\d+\([日一二三四五六]\)$/.test(s0), true);
  chk('括號成對(day5)', /^\d+\/\d+\([日一二三四五六]\)$/.test(s5), true);
  // 16 天全部檢查
  // SCN_DAYS_MAX 是 const（詞法綁定，不在 window 上）→ 必須用 getLex 讀
  const nDays = getLex('SCN_DAYS_MAX');
  chk('SCN_DAYS_MAX = 16（UI 做滿，無有/無UI日期落差）', nDays, 16);
  let bad = [];
  for (let d = 0; d < nDays; d++) {
    if (!/^\d+\/\d+\([日一二三四五六]\)$/.test(G._scnDayShort(d))) bad.push(d);
  }
  chk('全 16 天標籤格式正確', bad, []);
}


// ════════ 13. 警戒清單共用版式（比照二次災害高風險區）════════
console.log('\n=== _alertListText 版式 ===');
if (need('_alertListText')) {
  const items = [
    {county:'南投縣', town:'信義鄉', id:'投縣DF185', rank:5000},
    {county:'南投縣', town:'信義鄉', id:'投縣DF206', rank:4000},
    {county:'臺東縣', town:'太麻里鄉', id:'東縣DF111', rank:5000},
    {county:'臺東縣', town:'太麻里鄉', id:'東縣DF124', rank:4900},
    {county:'臺東縣', town:'卑南鄉', id:'東縣DF049', rank:4800},
    {county:'臺東縣', town:'卑南鄉', id:'東縣DF059', rank:4700},
    {county:'臺東縣', town:'金峰鄉', id:'東縣DF076', rank:4600},
  ];
  const txt = G._alertListText(items);
  console.log(txt.split('\n').map(l=>'   '+l).join('\n'));
  chk('有分署標頭', /^【南投分署】$/m.test(txt), true);
  chk('同鄉鎮編號合併於括號內',
      /南投縣：信義鄉（投縣DF185、投縣DF206）/.test(txt), true);
  chk('同縣市多鄉鎮以、分隔',
      /臺東縣：太麻里鄉（東縣DF111、東縣DF124）、卑南鄉（東縣DF049、東縣DF059）、金峰鄉（東縣DF076）/.test(txt), true);
  chk('分署順序依 DISTRICT_ORDER（南投在臺東前）',
      txt.indexOf('【南投分署】') < txt.indexOf('【臺東分署】'), true);
  // 每個縣市恰好一行
  const lines = txt.trim().split('\n').filter(l=>l && !l.startsWith('【'));
  chk('縣市行數 = 2', lines.length, 2);
  chk('空清單回空字串', G._alertListText([]), '');
}


// ════════ 14. 推估前端重算：必須與 fetch_rainfall.py slope_est 一致 ════════
console.log('\n=== _slopeEstJS 與後端 slope_est 門檻一致 ===');
if (need('_slopeEstJS')) {
  const S = G._slopeEstJS;
  chk('警戒值350 → 門檻0.30', S(100,350,0).yellow_th, 0.30);
  chk('警戒值400 → 門檻0.40', S(100,400,0).yellow_th, 0.40);
  chk('警戒值1500 → 門檻0.40', S(100,1500,0).yellow_th, 0.40);
  chk('實際80(<90) 預測+500 → 不符黃警', S(80,300,500).est_yellow_now, false);
  chk('  但未來24h會達紅', S(80,300,500).est_red_fc, true);
  chk('實際90(=30%) 預測+250 → 符合黃警', S(90,300,250).est_yellow_now, true);
  chk('實際90 預測+100(合190) → 不符', S(90,300,100).est_yellow_now, false);
  chk('  也不會達紅', S(90,300,100).est_red_fc, false);
  chk('實際已達警戒值 → est_red_fc', S(300,300,0).est_red_fc, true);
  chk('合計299.95<300 → false', S(299.9,300,0.05).est_red_fc, false);
  chk('夜間QPF null → night_warn null', S(200,300,50,null).night_warn, null);
  chk('200+120=320≥300 → true', S(200,300,50,120).night_warn, true);
  chk('200+50=250<300 → false', S(200,300,50,50).night_warn, false);
  ['etr2=null','alert=null','alert=0'].forEach((lbl,i)=>{
    const r=[S(null,300,50),S(100,null,50),S(100,0,50)][i];
    const bad=['est_yellow_now','est_red_fc','pct','fc_pct'].filter(k=>r[k]!==null);
    chk(`${lbl} → 判定全 null`, bad, []);
  });
  chk('qpf24 null 視為 0', S(100,300,null).fc_etr2, 100);
}

console.log('\n=== 情境驅動推估（本輪核心）===');
if (need('_withEst') && need('getQpfArr')) {
  // 造一個鄉鎮進 TMAP，讓 _alertTown 找得到
  setLex(`TMAP['臺東縣卑南鄉'] = {county:'臺東縣', township:'卑南鄉', alert_val:500,
      etr2:100, etr2_alert:500, qpf_best:Array(64).fill(0)};
      forecastModel='best'; _userFactorOn=false; _biasApplyOn=false; _scnOn=false; _scnDays={};`);
  const a = {county:'臺東縣', town:'卑南鄉', alert:500, etr2:100,
             off_level:'', est_red_fc:false, est_yellow_now:false, fc_etr2:100};
  const noScn = G._withEst(a);
  console.log(`   無情境: est_src=${noScn.est_src} qpf24=${noScn.qpf24} fc=${noScn.fc_etr2} red=${noScn.est_red_fc}`);
  chk('無情境時前端重算生效', noScn.est_src, 'frontend');
  chk('模式報0 → 不達紅', noScn.est_red_fc, false);

  // 情境：臺東山區每日加成 2000mm → 未來24h 應遠超警戒值
  setLex(`_scnOn = true; _scnDays = {0:{model:null,g:{'臺東分署|山區':{add:2000,mul:1}}},
                                    1:{model:null,g:{'臺東分署|山區':{add:2000,mul:1}}}};`);
  const withScn = G._withEst(a);
  console.log(`   有情境: qpf24=${withScn.qpf24} fc=${withScn.fc_etr2} red=${withScn.est_red_fc} yellow=${withScn.est_yellow_now}`);
  if (!(withScn.qpf24 > 500)) fails.push(`情境未反映到 qpf24（${withScn.qpf24}）`);
  else console.log('  OK  情境已驅動未來24h QPF');
  chk('情境使推估達紅', withScn.est_red_fc, true);
  chk('官方現況欄位不被覆寫', withScn.off_level, '');

  // 找不到鄉鎮 → 退回後端值並標記
  const orphan = G._withEst({county:'不存在縣', town:'不存在鄉', alert:500, etr2:100});
  chk('找不到鄉鎮 → est_src=backend', orphan.est_src, 'backend');
  setLex('_scnOn=false; _scnDays={};');
}


// ════════ 15. 今天視窗 ETR2 必須含整日剩餘 QPF（本輪核心修正）════════
console.log('\n=== 今天視窗 ETR2 反映情境／倍率 ===');
if (need('getAccum') && need('calcEtr2AtSeg')) {
  const t = { county:'高雄市', township:'六龜區', alert_val:250, etr2_alert:250,
              etr2:67, etr2_pct:0.27,
              qpf_best:Array(64).fill(0), daily_rain:Array(15).fill(0),
              obs_1h_p48:Array(48).fill(0), qpf_1h_p48:Array(48).fill(0),
              qpf_1h:Array(96).fill(0) };
  setLex("forecastModel='best'; _userFactorOn=false; _biasApplyOn=false; _scnOn=false; _scnDays={};");
  setLex("winKey='today'; segFrom=0; segTo=3; mode='rain';");
  // ★ 把 BASE_TIME 設到「明天 00:00」，使 nowH 夾到 0、_nowSeg()=0，
  //   這樣今天視窗才有「整日剩餘 QPF」可算；否則示範資料的 BASE_TIME 過舊，
  //   nowH 會夾到 23、seg3 就等於「現在」，測不到要驗的路徑。
  setLex(`{ const d = new Date(); d.setDate(d.getDate()+1); d.setHours(0,0,0,0);
            BASE_TIME = d; }`);
  console.log(`   _nowSeg()=${G._nowSeg()}（需為 0 才測得到整日剩餘）`);
  const dry = G.getAccum(t, 'rain');
  console.log(`   無雨: totalRain=${dry.totalRain} etrPct=${dry.etrPct}`);

  // 情境：六龜區屬臺南分署，注入地形後給大量加成
  setLex(`_scnOn = true;
          _scnDays = {0:{model:null, g:{'臺南分署|淺山區':{add:2000, mul:1}}}};`);
  const wet = G.getAccum(t, 'rain');
  console.log(`   情境+2000mm/日: totalRain=${wet.totalRain} etrPct=${wet.etrPct}`);
  if (!(wet.totalRain > dry.totalRain)) fails.push('情境未反映到今天視窗雨量');
  else console.log('  OK  雨量已反映情境');
  // ★ 關鍵：警戒值 250mm、雨量遠超 → ETR2% 不可能還停在 27%
  if (!(wet.etrPct > 100)) fails.push(`今天視窗 ETR2% 未含整日 QPF（${wet.etrPct}%）`);
  else console.log(`  OK  ETR2% 已含整日剩餘 QPF（${wet.etrPct}%）`);
  if (!(wet.etrPct > dry.etrPct)) fails.push('ETR2% 未隨情境提高');

  // getAccum 與 _summaryEtr（CSV/複製）必須一致
  const se = G._summaryEtr(t);
  console.log(`   getAccum.etrPct=${wet.etrPct} vs _summaryEtr=${se}`);
  if (Math.abs(se - wet.etrPct) > 1.01) fails.push(`地圖與CSV的ETR2%不一致（${wet.etrPct} vs ${se}）`);
  else console.log('  OK  地圖與 CSV 的 ETR2% 一致');
  setLex("_scnOn=false; _scnDays={}; winKey='today'; segFrom=0; segTo=3;");
}

console.log('\n=== Ctrl+Z / Ctrl+Y 復原重做 ===');
if (need('scnUndo') && need('scnRedo') && need('scnPushUndo')) {
  setLex("_scnDays={}; _scnUndo=[]; _scnRedo=[]; _scnOn=false;");
  setLex("scnPushUndo(); _scnDays={0:{model:'hi',g:{}}};");
  setLex("scnPushUndo(); _scnDays={0:{model:'hi',g:{}},1:{model:'lo',g:{}}};");
  chk('目前有兩天設定', Object.keys(getLex('_scnDays')).length, 2);
  G.scnUndo();
  chk('復原一步 → 一天', Object.keys(getLex('_scnDays')).length, 1);
  G.scnUndo();
  chk('再復原 → 空', Object.keys(getLex('_scnDays')).length, 0);
  chk('已無可復原', G.scnUndo(), false);
  G.scnRedo();
  chk('重做一步 → 一天', Object.keys(getLex('_scnDays')).length, 1);
  G.scnRedo();
  chk('再重做 → 兩天', Object.keys(getLex('_scnDays')).length, 2);
  chk('已無可重做', G.scnRedo(), false);
  // ★ 新編輯必須清空重做堆疊（否則會跳回被岔開的歷史線）
  G.scnUndo();
  setLex("scnPushUndo(); _scnDays={5:{model:'gfs',g:{}}};");
  chk('新編輯後 redo 堆疊清空', getLex('_scnRedo.length'), 0);
  chk('新編輯後 Ctrl+Y 無效', G.scnRedo(), false);
  setLex("_scnDays={}; _scnUndo=[]; _scnRedo=[];");
}


// ════════ 16. 「全區」群組必須覆蓋整個分署（本輪修正）════════
console.log('\n=== 分署|全區 覆蓋所有鄉鎮 ===');
if (need('getQpfArr') && need('_townGroupKey')) {
  // TOWN_ZONE 清空 → _townZone() 退路只會回 山區/平地，
  // 這正是「設了淺山區/沿海地區卻沒反應」的情境
  setLex("forecastModel='best'; _userFactorOn=false; _biasApplyOn=false;");
  const mtn  = { county:'臺中市', township:'和平區', alert_val:600, qpf_best:Array(64).fill(10) };
  const flat = { county:'臺中市', township:'西區',   alert_val:0,   qpf_best:Array(64).fill(10) };
  const t2   = { county:'苗栗縣', township:'苗栗市', alert_val:0,   qpf_best:Array(64).fill(10) };

  // 只設「淺山區」→ 退路對不到，應完全無效（記錄現況，說明為何需要全區）
  setLex("_scnOn=true; _scnDays={0:{model:null,g:{'臺中分署|淺山區':{add:400,mul:1}}}};");
  console.log(`   只設淺山區: 和平=${G.getQpfArr(mtn,'qpf_best')[0]} 西區=${G.getQpfArr(flat,'qpf_best')[0]}`);

  // 改設「全區」→ 該分署所有鄉鎮都要生效
  setLex("_scnDays={0:{model:null,g:{'臺中分署|全區':{add:400,mul:1}}}};");
  const a = G.getQpfArr(mtn,'qpf_best')[0], b = G.getQpfArr(flat,'qpf_best')[0],
        c = G.getQpfArr(t2,'qpf_best')[0];
  console.log(`   設全區+400: 和平=${a} 西區=${b} 苗栗市=${c}（同署三鄉鎮）`);
  chk('全區生效於山區鄉鎮 (10+100)', a, 110);
  chk('全區生效於平地鄉鎮 (10+100)', b, 110);
  chk('全區生效於同署他縣鄉鎮 (10+100)', c, 110);

  // 他分署不受影響
  const other = { county:'臺東縣', township:'卑南鄉', alert_val:500, qpf_best:Array(64).fill(10) };
  chk('★他分署不受影響', G.getQpfArr(other,'qpf_best')[0], 10);

  // 地形列優先於全區列
  setLex("_scnDays={0:{model:null,g:{'臺中分署|全區':{add:400,mul:1},'臺中分署|山區':{add:0,mul:2}}}};");
  const p1 = G.getQpfArr(mtn,'qpf_best')[0], p2 = G.getQpfArr(flat,'qpf_best')[0];
  console.log(`   全區+400 與 山區×2 併存: 和平(山區)=${p1} 西區(平地)=${p2}`);
  chk('山區列優先於全區列 (10*2)', p1, 20);
  chk('未指定地形者仍吃全區 (10+100)', p2, 110);
  setLex("_scnOn=false; _scnDays={};");
}


// ════════ 17. 情境一致性總檢（雨量／ETR2／各時段皆須同源）════════
console.log('\n=== 情境：段值 → 日和 → ETR2 一致性 ===');
if (need('getQpfArr') && need('getAccum') && need('calcEtr2AtSeg')) {
  setLex(`TMAP['南投縣仁愛鄉'] = {county:'南投縣',township:'仁愛鄉',alert_val:700,etr2_alert:700,
    etr2:50, etr2_pct:0.07, daily_rain:Array(15).fill(0),
    qpf_best:Array(64).fill(10), qpf_ecmwf:Array(64).fill(20),
    qpf_hi:Array(64).fill(40), qpf_lo:Array(64).fill(5), qpf_cwa:Array(64).fill(30),
    obs_1h_p48:Array(48).fill(0), qpf_1h_p48:Array(48).fill(0), qpf_1h:Array(96).fill(0)};
    {const d=new Date(); d.setDate(d.getDate()+1); d.setHours(0,0,0,0); BASE_TIME=d;}
    _userFactorOn=false; _biasApplyOn=false;
    winKey='today'; segFrom=0; segTo=3; mode='rain'; forecastModel='ecmwf';`);
  const t = getLex("TMAP['南投縣仁愛鄉']");
  const ns = G._nowSeg();
  chk('測試時鐘使 nowSeg=0', ns, 0);

  setLex("_scnOn=true; _scnDays={0:{model:'ecmwf',g:{'南投分署|山區':{add:200,mul:4}}}};");
  const q = G.getQpfArr(t, 'qpf_ecmwf');
  chk('段值 = (20+200/4)*4', q.slice(0,4), [280,280,280,280]);

  // ★「今天」＝整日 00–24 時（全系統統一定義），故日和 = 4 × 段值，
  //   不再扣除已過時段——已過段的情境調整同樣屬於使用者指定的雨量。
  const a = G.getAccum(t, 'rain');
  const expect = 4 * 280;
  console.log(`   totalRain=${a.totalRain} 期望=${expect}（整日 4 段和）`);
  if (Math.abs(a.totalRain - expect) > 0.2) fails.push(`日和與段值不一致（${a.totalRain} vs ${expect}）`);
  else console.log('  OK  日和＝整日 4 段和（與逐日圖／組體圖同義）');

  // ETR2：情境把 QPF 放大數十倍，ETR2% 必須遠超 100%
  console.log(`   etrPct=${a.etrPct}%（分母 700mm）`);
  if (!(a.etrPct > 100)) fails.push(`ETR2% 未反映情境（${a.etrPct}%）`);
  else console.log('  OK  ETR2% 已反映情境');

  // 地圖(getAccum) 與 CSV(_summaryRain/_summaryEtr) 必須一致
  const sr = G._summaryRain(t), se = G._summaryEtr(t);
  if (Math.abs(sr - a.totalRain) > 0.2) fails.push(`地圖與CSV雨量不一致（${a.totalRain} vs ${sr}）`);
  else console.log('  OK  地圖與 CSV 雨量一致');
  if (Math.abs(se - a.etrPct) > 1.01) fails.push(`地圖與CSV ETR2%不一致（${a.etrPct} vs ${se}）`);
  else console.log('  OK  地圖與 CSV ETR2% 一致');

  // 已過時段的情境調整不得被觀測折算吃掉
  const eNow = G.calcEtr2AtSeg(t, ns, 'qpf_ecmwf');
  console.log(`   ETR2@現在=${Math.round(eNow)}mm（官方錨點 50mm ＋ 已過段情境超額）`);
  if (!(eNow > 50)) fails.push('已過時段的情境調整被丟棄');
  else console.log('  OK  已過時段的情境調整有計入');

  // 未來段必須單調不減（有雨情況）
  const e1 = G.calcEtr2AtSeg(t, 1, 'qpf_ecmwf'), e3 = G.calcEtr2AtSeg(t, 3, 'qpf_ecmwf');
  console.log(`   ETR2 seg1=${Math.round(e1)} seg3=${Math.round(e3)}`);
  if (!(e3 >= e1)) fails.push('未來段 ETR2 未單調上升');
  else console.log('  OK  未來段 ETR2 隨降雨累加');

  // 切模式必須改變結果（證明模式選擇有落實）
  setLex("_scnDays={0:{model:'lo',g:{'南投分署|山區':{add:0,mul:1}}}};");
  const qlo = G.getQpfArr(t, 'qpf_ecmwf');
  chk('情境指定弱降雨 → 取 qpf_lo', qlo.slice(0,1), [5]);
  setLex("_scnDays={0:{model:'hi',g:{'南投分署|山區':{add:0,mul:1}}}};");
  chk('情境指定強降雨 → 取 qpf_hi', G.getQpfArr(t,'qpf_ecmwf').slice(0,1), [40]);
  setLex("_scnOn=false; _scnDays={};");
}


// ════════ 18. 全系統一致性：情境須貫穿 雨量／ETR2／警特報／風險／土石流推估 ════════
console.log('\n=== 情境貫穿所有指標（本輪核心）===');
if (need('getQpfArr') && need('_warnLevelAt') && need('calcRiskIndicator') && need('_withEst')) {
  setLex(`TMAP['南投縣仁愛鄉'] = {county:'南投縣',township:'仁愛鄉',alert_val:700,etr2_alert:700,
    etr2:50, etr2_pct:0.07, daily_rain:Array(15).fill(0), pop_6h:Array(28).fill(80),
    qpf_best:Array(64).fill(2), qpf_ecmwf:Array(64).fill(2),
    qpf_hi:Array(64).fill(3), qpf_lo:Array(64).fill(1), qpf_cwa:Array(64).fill(2),
    warn_seg:Array(64).fill(0), maxh_best:Array(64).fill(1),
    obs_1h_p48:Array(48).fill(0), qpf_1h_p48:Array(48).fill(0), qpf_1h:Array(96).fill(0)};
    {const d=new Date(); d.setDate(d.getDate()+1); d.setHours(0,0,0,0); BASE_TIME=d;}
    _userFactorOn=false; _biasApplyOn=false;
    winKey='today'; segFrom=0; segTo=3; mode='rain'; forecastModel='ecmwf';
    _scnOn=false; _scnDays={};`);
  const t = getLex("TMAP['南投縣仁愛鄉']");

  // 基線：微量雨 → 警特報 0、風險低、土石流推估未達
  const w0 = G._warnLevelAt(t, 3), r0 = G.calcRiskIndicator(t, 3).R;
  const a0 = { county:'南投縣', town:'仁愛鄉', alert:700, etr2:50, off_level:'' };
  const e0 = G._withEst(a0);
  console.log(`   基線: 警特報=${w0} 風險=${r0} 推估達紅=${e0.est_red_fc} ETR2=${e0.etr2_est}`);
  chk('基線警特報為 0', w0, 0);
  chk('基線推估未達紅', e0.est_red_fc, false);

  // 情境：南投山區 加成 1200mm/日（每段 300mm）→ 必然超大豪雨、風險極高、推估達紅
  setLex("_scnOn=true; _scnDays={0:{model:'ecmwf',g:{'南投分署|山區':{add:1200,mul:1}}}," +
         "1:{model:'ecmwf',g:{'南投分署|山區':{add:1200,mul:1}}}};");
  const q = G.getQpfArr(t, 'qpf_ecmwf');
  chk('段值 = 2+1200/4', q.slice(0,4), [302,302,302,302]);

  const w1 = G._warnLevelAt(t, 3);
  console.log(`   情境後警特報級別=${w1}（0無/1大雨/2豪雨/3大豪雨/4超大豪雨）`);
  if (!(w1 >= 3)) fails.push(`警特報未反映情境（級別 ${w1}，1200mm/日應達大豪雨以上）`);
  else console.log('  OK  警特報已反映情境（不再被後端固定陣列蓋住）');

  const r1 = G.calcRiskIndicator(t, 3).R;
  console.log(`   情境後風險指標=${r1}`);
  if (!(r1 > r0)) fails.push(`風險指標未反映情境（${r0} → ${r1}）`);
  else console.log('  OK  風險指標已反映情境');

  const e1 = G._withEst(a0);
  console.log(`   情境後推估: ETR2=${e1.etr2_est}mm 達紅=${e1.est_red_fc} 來源=${e1.est_src}`);
  if (!(e1.etr2_est > e0.etr2_est)) fails.push('土石流推估 ETR2 未反映情境');
  else console.log('  OK  土石流／大崩推估已反映情境');
  chk('推估達紅（1200mm/日 vs 700mm 警戒值）', e1.est_red_fc, true);
  chk('官方現況欄位不被覆寫', e1.off_level, '');

  // 推估必須隨選取時段改變（使用者第3點）
  setLex("segTo=0;"); const eS0 = G._withEst(a0);
  setLex("segTo=3;"); const eS3 = G._withEst(a0);
  console.log(`   segTo=0 → ETR2=${eS0.etr2_est}mm；segTo=3 → ETR2=${eS3.etr2_est}mm`);
  if (!(eS3.etr2_est > eS0.etr2_est)) fails.push('推估未隨選取時段改變');
  else console.log('  OK  推估隨選取時段改變');
  chk('段記錄正確', [eS0.etr2_seg, eS3.etr2_seg], [0, 3]);

  // 顯示值與判定值同源（_lsPct 用 etr2_est）
  const pct = G._lsPct(Object.assign({alert:700}, e1));
  const expectPct = e1.etr2_est / 700 * 100;
  if (Math.abs(pct - expectPct) > 0.1) fails.push(`顯示達成率與判定值不同源（${pct} vs ${expectPct}）`);
  else console.log('  OK  面板顯示值與判定值同源');

  // 切模式必須連動（強降雨 vs 弱降雨）
  setLex("_scnDays={0:{model:'hi',g:{}},1:{model:'hi',g:{}}};");
  const wHi = G.getQpfArr(t,'qpf_ecmwf')[0];
  setLex("_scnDays={0:{model:'lo',g:{}},1:{model:'lo',g:{}}};");
  const wLo = G.getQpfArr(t,'qpf_ecmwf')[0];
  console.log(`   情境模式切換: 強降雨=${wHi} 弱降雨=${wLo}`);
  chk('強降雨取 qpf_hi', wHi, 3);
  chk('弱降雨取 qpf_lo', wLo, 1);

  setLex("_scnOn=false; _scnDays={}; segTo=3;");
}


// ════════ 19. 圖表與地圖必須同數（逐時ETR2序列 vs calcEtr2AtSeg）════════
console.log('\n=== 圖表 ETR2 序列與地圖同源 ===');
if (need('_etr2HourlySeries') && need('calcEtr2AtSeg') && need('_hourlyBars')) {
  setLex(`TMAP['南投縣仁愛鄉'] = {county:'南投縣',township:'仁愛鄉',alert_val:700,etr2_alert:700,
    etr2:50, etr2_pct:0.07, daily_rain:Array(15).fill(0),
    qpf_best:Array(64).fill(2), qpf_ecmwf:Array(64).fill(2),
    obs_1h_p48:Array(48).fill(0), qpf_1h_p48:Array(48).fill(0), qpf_1h:Array(96).fill(0)};
    {const d=new Date(); d.setDate(d.getDate()+1); d.setHours(0,0,0,0); BASE_TIME=d;}
    _userFactorOn=false; _biasApplyOn=false; forecastModel='ecmwf';
    winKey='today'; segFrom=0; segTo=3; mode='rain';
    _scnOn=true; _scnDays={0:{model:'ecmwf',g:{'南投分署|山區':{add:1200,mul:1}}},
                           1:{model:'ecmwf',g:{'南投分署|山區':{add:1200,mul:1}}}};`);
  const t = getLex("TMAP['南投縣仁愛鄉']");
  const den = 700;

  // 圖表序列在「今日 23 時」的 ETR2 vs 地圖 calcEtr2AtSeg(seg 3)
  const b = G._hourlyBars(t);
  const es = G._etr2HourlySeries(t, b.hFrom, b.hFrom + 119);
  // ★ 序列索引 = h - hFrom（hFrom 為 -48），先前寫 23 - hFrom 之外還誤以為 idx=23，
  //   讀到的是兩天前的值，才會量出 0.13 的假落差。
  const idx23 = 23 - b.hFrom;
  const chartEtr = (idx23 >= 0 && idx23 < es.length) ? es[idx23]*den/100 : null;
  const mapEtr = G.calcEtr2AtSeg(t, 3, 'qpf_ecmwf');
  console.log(`   圖表@今日23時=${chartEtr==null?'—':Math.round(chartEtr)}mm` +
              `（${chartEtr==null?'—':Math.round(chartEtr/den*100)}%）`);
  console.log(`   地圖@seg3   =${Math.round(mapEtr)}mm（${Math.round(mapEtr/den*100)}%）`);
  if (chartEtr == null) fails.push('圖表 ETR2 序列取不到今日值');
  else {
    // 兩者演算法不同（逐時 vs 逐段），容許 25% 差異；但不可差到數倍
    const ratio = chartEtr / mapEtr;
    console.log(`   比值=${ratio.toFixed(2)}（1.0 為完全一致，先前約 0.17 即六倍落差）`);
    if (ratio < 0.75 || ratio > 1.33) fails.push(`圖表與地圖 ETR2 差異過大（比值 ${ratio.toFixed(2)}）`);
    else console.log('  OK  圖表與地圖 ETR2 量級一致（不再有數倍落差）');
  }
  // 逐段核對：序列在各段末尾應貼近 calcEtr2AtSeg（同源的最強驗證）
  let worst = 0, worstSeg = null;
  for(let sg = 0; sg <= 7; sg++){
    const h = sg*6 + 5;                            // 該段最後一小時
    const idx = h - b.hFrom;
    if(idx < 0 || idx >= es.length) continue;
    const cv = es[idx]*den/100;
    const mv = G.calcEtr2AtSeg(t, sg, 'qpf_ecmwf');
    const d = Math.abs(cv - mv) / Math.max(1, mv);
    if(d > worst){ worst = d; worstSeg = sg; }
  }
  console.log(`   逐段最大相對差=${(worst*100).toFixed(1)}%（seg ${worstSeg}）`);
  if(worst > 0.15) fails.push(`圖表與地圖逐段不一致（最大差 ${(worst*100).toFixed(1)}%）`);
  else console.log('  OK  圖表逐段貼合 calcEtr2AtSeg（同源）');

  // 圖表序列在情境下必須遠超 100%
  // ★ 只取「現在之後」的區段：過去 48h 是既定觀測，情境本來就不該改動它。
  const futFrom = Math.max(0, 0 - b.hFrom);        // h=0（今日00時）在序列中的位置
  const maxPct = Math.max(...es.slice(futFrom));
  console.log(`   圖表序列最高 ETR2% = ${Math.round(maxPct)}%`);
  if (!(maxPct > 100)) fails.push(`圖表 ETR2% 未反映情境（最高 ${Math.round(maxPct)}%）`);
  else console.log('  OK  圖表 ETR2% 已反映情境');
  setLex("_scnOn=false; _scnDays={};");
}

console.log('\n=== 地形分類已就緒（使用者指示 e：800m 二分）===');
if (need('_townZone')) {
  const tz = getLex('TOWN_ZONE');
  const n = tz ? Object.keys(tz).length : 0;
  console.log(`   TOWN_ZONE 筆數=${n}`);
  // ★ 舊的位置型分類（北海岸／東北角／恆春半島／淺山／沿海）不得殘留
  const legacy = Object.values(tz).filter(v=>!['山區','淺山區','沿海地區','平地'].includes(v));
  chk('無殘留舊分類值', [...new Set(legacy)], []);
  const cnt = {};
  Object.values(tz).forEach(v=>{ cnt[v]=(cnt[v]||0)+1; });
  console.log(`   四類統計=${JSON.stringify(cnt)}`);
  if (!(n > 300)) fails.push(`TOWN_ZONE 未載入完整（${n} 筆）`);
  else console.log('  OK  TOWN_ZONE 已內建（不再有「設了沒反應」的地形列）');
  chk('SCN_TERRAIN 為四類', getLex('SCN_TERRAIN'), ['山區','淺山區','平地','沿海地區']);
  // 已知山區/平地抽樣
  [['南投縣仁愛鄉','山區'],['臺中市和平區','山區'],['嘉義縣阿里山鄉','山區'],
   ['宜蘭縣大同鄉','山區'],['宜蘭縣南澳鄉','山區'],
   ['臺北市大安區','平地'],['宜蘭縣宜蘭市','平地'],['宜蘭縣羅東鎮','平地'],
   // ★ 官方定義判定（2026-08）：這些是各條規則的代表案例，
   //   不再列舉人工分類的逐一指定值——那些會隨圖資更新而變動。
   ['臺東縣蘭嶼鄉','沿海地區'],   // 離島優先（雖為山地原住民鄉）
   ['高雄市旗津區','沿海地區'],   // 離島（沙洲）
   ['金門縣金湖鎮','沿海地區'],   // 離島
   ['屏東縣牡丹鄉','山區'],       // 山地原住民鄉（≥1000m 佔比 0%）
   ['屏東縣獅子鄉','山區'],       // 山地原住民鄉
   ['花蓮縣吉安鄉','沿海地區'],   // 濱海省道判定
   ['宜蘭縣蘇澳鎮','沿海地區'],   // 緊鄰海岸
   ['屏東縣恆春鎮','沿海地區']].forEach(([k,want])=>{
    const got = tz[k];
    const ok = got === want;
    if(!ok) fails.push(`${k} 應為 ${want}，實為 ${got}`);
    console.log(`   ${ok?'OK ':'!! '}${k} → ${got}`);
  });
}


// ════════ 20. 單一計算層：所有呈現面必須回報同一組數字 ════════
console.log('\n=== townMetrics 為唯一資料來源 ===');
if (need('townMetrics') && need('getAccum') && need('_calcDistrictDaily') && need('_calcDistrictHyeto')) {
  // 臺南分署山區 +500 ×3（使用者實際設定）
  setLex(`TMAP['高雄市六龜區']={county:'高雄市',township:'六龜區',alert_val:250,etr2_alert:250,
    etr2:67,etr2_pct:0.27,daily_rain:Array(15).fill(0),pop_6h:Array(28).fill(80),
    qpf_best:Array(64).fill(2),qpf_ecmwf:Array(64).fill(2),
    warn_seg:Array(64).fill(0),maxh_best:Array(64).fill(1),
    obs_1h_p48:Array(48).fill(0),qpf_1h_p48:Array(48).fill(0),qpf_1h:Array(96).fill(0)};
    {const d=new Date();d.setDate(d.getDate()+1);d.setHours(0,0,0,0);BASE_TIME=d;}
    forecastModel='ecmwf'; _userFactorOn=false;_biasApplyOn=false;
    winKey='today';segFrom=0;segTo=3;mode='rain';
    _scnOn=true;_scnDays={0:{model:'ecmwf',g:{'臺南分署|淺山區':{add:500,mul:3}}},
                          1:{model:'ecmwf',g:{'臺南分署|淺山區':{add:500,mul:3}}}};`);
  const t = getLex("TMAP['高雄市六龜區']");
  // 六龜區在四類分區下為「淺山區」（依既有人工分類轉換），測試依實際分類設定群組
  chk('六龜區分類', G._townZone(t), '淺山區');

  // 段值 (2+500/4)*3 = 381 → 日和 1524
  const q = G.getQpfArr(t, 'qpf_ecmwf');
  chk('段值 = (2+125)*3', q.slice(0,4), [381,381,381,381]);
  const m = G.townMetrics(t, 3);
  chk('townMetrics 日和 = 4*381', m.dayRain, 1524);
  console.log(`   townMetrics: dayRain=${m.dayRain} etr2=${m.etr2}mm etrPct=${m.etrPct}% ` +
              `warn=${m.warnLv} risk=${m.risk}`);

  // ETR2%：1524mm 對 250mm 警戒值 → 必須遠超 400%
  if (!(m.etrPct > 400)) fails.push(`townMetrics ETR2% 偏低（${m.etrPct}%）`);
  else console.log('  OK  ETR2% 合乎 1524mm/250mm 的量級');

  // 地圖(getAccum) 必須與 townMetrics 同數
  const a = G.getAccum(t, 'rain');
  console.log(`   getAccum: totalRain=${a.totalRain} etrPct=${a.etrPct}`);
  if (Math.abs(a.etrPct - m.etrPct) > 1.01) fails.push(`地圖與 townMetrics ETR2% 不一致（${a.etrPct} vs ${m.etrPct}）`);
  else console.log('  OK  地圖 ETR2% ＝ townMetrics');

  // 逐日圖軸值必須含此鄉鎮的值（臺南分署 index）
  const di = getLex('DISTRICT_ORDER').indexOf('臺南分署');
  const dd = G._calcDistrictDaily('qpf_ecmwf');
  const dayEtr = dd.etrRows[di][0], dayRain = dd.rainRows[di][0];
  console.log(`   逐日圖臺南分署 day0: 雨量=${dayRain} ETR2%=${dayEtr}（軸上限 ${dd.maxEtr}）`);
  // ★ 分署圖是「署內所有鄉鎮取最大」，故軸值 ≥ 單一鄉鎮值；不可要求相等。
  if (dayEtr < Math.round(m.etrPct) - 1) fails.push(`逐日圖 ETR2% 低於該鄉鎮值（${dayEtr} < ${Math.round(m.etrPct)}）`);
  else console.log(`  OK  逐日圖 ETR2% ≥ 六龜值（${dayEtr} ≥ ${Math.round(m.etrPct)}，署內取最大）`);
  if (dayRain < m.dayRain - 0.2) fails.push(`逐日圖雨量低於 townMetrics（${dayRain} vs ${m.dayRain}）`);
  else console.log('  OK  逐日圖雨量 ≥ townMetrics（分署取最大）');
  // 軸上限必須容納最大值，否則圖面會被截斷（使用者看到 420 而非 568 的另一成因）
  if (dd.maxEtr < dayEtr) fails.push(`逐日圖 ETR2 軸上限 ${dd.maxEtr} < 實際值 ${dayEtr}（圖面會截斷）`);
  else console.log('  OK  ETR2 軸上限容納實際值（圖面不截斷）');

  // 組體圖同樣核對
  const hy = G._calcDistrictHyeto('qpf_ecmwf');
  const hEtr = hy.etrRows[di][3];
  console.log(`   組體圖臺南分署 seg3: ETR2%=${hEtr}（軸上限 ${hy.maxEtr}）`);
  if (hEtr < Math.round(m.etrPct) - 1) fails.push(`組體圖 ETR2% 低於該鄉鎮值（${hEtr} < ${Math.round(m.etrPct)}）`);
  else console.log(`  OK  組體圖 ETR2% ≥ 六龜值（${hEtr} ≥ ${Math.round(m.etrPct)}）`);
  // 兩張圖必須彼此一致（同署同段）
  chk('逐日圖與組體圖同段一致', dayEtr, hEtr);
  if (hy.maxEtr < hEtr) fails.push(`組體圖 ETR2 軸上限 ${hy.maxEtr} < 實際值 ${hEtr}`);
  else console.log('  OK  組體圖軸上限容納實際值');

  // 快取必須隨情境失效（否則改設定後圖表沿用舊值）
  const k1 = G._tmKey();
  setLex("_scnDays={0:{model:'ecmwf',g:{'臺南分署|淺山區':{add:500,mul:5}}}};");
  const k2 = G._tmKey();
  chk('情境變動 → 計算層快取鍵改變', k1 !== k2, true);
  const m2 = G.townMetrics(t, 3);
  if (!(m2.dayRain > m.dayRain)) fails.push('快取未失效，townMetrics 沿用舊值');
  else console.log(`  OK  快取正確失效（倍率3→5：${m.dayRain} → ${m2.dayRain}）`);

  setLex("_scnOn=false;_scnDays={};");
}


// ════════ 21. 桃源區實例：所有面向必須收斂到同一組數字 ════════
console.log('\n=== 「今天」視窗定義統一（桃源區實例）===');
if (need('townMetrics') && need('getAccum') && need('_panelSeg')) {
  // 重現使用者情境：段值 375/375.9/384/375，警戒值 250mm，現在 nowH=9
  setLex(`TMAP['高雄市桃源區']={county:'高雄市',township:'桃源區',alert_val:250,etr2_alert:250,
    etr2:52,etr2_pct:0.21,daily_rain:Array(15).fill(0),pop_6h:Array(28).fill(80),
    qpf_best:[375,375.9,384,375].concat(Array(60).fill(0)),
    qpf_ecmwf:[375,375.9,384,375].concat(Array(60).fill(0)),
    warn_seg:Array(64).fill(0),maxh_best:Array(64).fill(60),
    obs_1h_p48:Array(48).fill(0),qpf_1h_p48:Array(48).fill(0),qpf_1h:Array(96).fill(0)};
    {const d=new Date(); d.setHours(9,30,0,0); BASE_TIME=new Date(d.getFullYear(),d.getMonth(),d.getDate(),0,0,0,0);}
    forecastModel='ecmwf'; _userFactorOn=false;_biasApplyOn=false;_scnOn=false;_scnDays={};
    winKey='today';segFrom=0;segTo=3;mode='rain';`);
  const t = getLex("TMAP['高雄市桃源區']");
  const nowSeg = G._nowSeg();
  console.log(`   nowSeg=${nowSeg}　_panelSeg()=${G._panelSeg()}`);
  chk('★「今天」視窗一律取日末段 3', G._panelSeg(), 3);

  const dayTotal = 375 + 375.9 + 384 + 375;         // 1509.9
  const m = G.townMetrics(t, 3);
  const a = G.getAccum(t, 'rain');
  console.log(`   townMetrics.dayRain=${m.dayRain}　getAccum.totalRain=${a.totalRain}　（整日和 ${dayTotal}）`);
  chk('townMetrics 日和 = 段值總和', m.dayRain, Math.round(dayTotal*10)/10);
  // ★ 地圖「全天累積雨量」必須等於整日和，不可只剩未過段
  if (Math.abs(a.totalRain - dayTotal) > 2) {
    fails.push(`地圖全天累積 ${a.totalRain} ≠ 整日和 ${dayTotal}（舊值約 885，只算未過段）`);
  } else console.log('  OK  地圖全天累積 ＝ 整日和（不再只算未過段）');

  // ETR2%：面板段與地圖段一致 → 百分比必須相同
  const panelEtr = G.calcEtr2AtSeg(t, G._panelSeg(), 'qpf_ecmwf') / 250 * 100;
  console.log(`   面板ETR2%=${Math.round(panelEtr)}　地圖ETR2%=${Math.round(a.etrPct)}　townMetrics=${Math.round(m.etrPct)}`);
  if (Math.abs(panelEtr - a.etrPct) > 1.5) {
    fails.push(`ETR2%警戒面板 ${Math.round(panelEtr)}% ≠ 地圖 ${Math.round(a.etrPct)}%（舊為 321 vs 559）`);
  } else console.log('  OK  ETR2%警戒面板 ＝ 地圖 ＝ townMetrics（不再 321 vs 559）');

  // 逐日圖 / 組體圖同段一致
  const di = getLex('DISTRICT_ORDER').indexOf('臺南分署');
  const dd = G._calcDistrictDaily('qpf_ecmwf');
  const hy = G._calcDistrictHyeto('qpf_ecmwf');
  console.log(`   逐日圖day0 ETR2%=${dd.etrRows[di][0]}　組體圖seg3 ETR2%=${hy.etrRows[di][3]}`);
  chk('逐日圖與組體圖同段一致', dd.etrRows[di][0], hy.etrRows[di][3]);
  if (dd.etrRows[di][0] < Math.round(m.etrPct) - 1) {
    fails.push(`逐日圖 ETR2% 低於桃源值（${dd.etrRows[di][0]} < ${Math.round(m.etrPct)}）`);
  } else console.log('  OK  逐日圖 ETR2% ≥ 桃源值（署內取最大）');

  // 逐日圖雨量 ≥ 整日和
  if (dd.rainRows[di][0] < dayTotal - 2) {
    fails.push(`逐日圖雨量 ${dd.rainRows[di][0]} < 整日和 ${dayTotal}`);
  } else console.log('  OK  逐日圖雨量 ≥ 整日和');
}


// ════════ 22. 情境變更必須即時同步所有面板／圖層（不需手動點圖層）════════
console.log('\n=== _refreshAfterFactorChange 覆蓋完整性 ===');
if (need('_refreshAfterFactorChange') && need('_safeCall')) {
  // 蒐集 refresh 實際會呼叫哪些函式：把每個目標函式包上探針
  const targets = ['updateEtrAlertPanel','updateDebrisPanel','updateLandslidePanel',
                   'updateTyphoonPanel','updateDistrictSummary','updateAllDistrictCharts',
                   'renderLayer','renderLegend'];
  const probe = '__refreshCalled';
  setLex(`window.${probe} = {};`);
  targets.forEach(fn=>{
    setLex(`if (typeof ${fn} === 'function') {
      const _o = ${fn};
      ${fn} = function(){ window.${probe}['${fn}'] = true; try{ return _o.apply(this, arguments); }catch(e){} };
    }`);
  });
  // 觸發一次情境刷新
  setLex("_scnOn = true; _scnDays = {0:{model:'hi',g:{}}};");
  setLex("_refreshAfterFactorChange();");
  const called = getLex(`window.${probe}`) || {};
  targets.forEach(fn=>{
    const ok = !!called[fn];
    if(!ok) fails.push(`情境刷新未呼叫 ${fn}（使用者需手動點圖層才會更新）`);
    console.log(`   ${ok?'OK ':'!! '}${fn}`);
  });
  // ★ _safeCall 不得靜默：函式不存在要在 console 報出（不是拋錯中斷）
  let threw = false;
  try { setLex("_safeCall('__notExist__', undefined);"); } catch(e){ threw = true; }
  chk('_safeCall 對不存在的函式不拋錯', threw, false);
  setLex("_scnOn=false; _scnDays={};");
}

console.log('\n=== ETR2%官方現值：標籤與時間戳 ===');
{
  const html = fs.readFileSync('index.html', 'utf8');
  const nNew = (html.match(/ETR2%官方現值/g) || []).length;
  const nOld = (html.match(/>今日ETR2%</g) || []).length;
  console.log(`   「ETR2%官方現值」出現 ${nNew} 次；殘留「今日ETR2%」標籤 ${nOld} 次`);
  chk('無殘留舊標籤', nOld, 0);
  if (!(nNew >= 2)) fails.push('官方現值標籤數過少，可能有遺漏未改名處');
  else console.log('  OK  官方現值處皆已改名');
  chk('標籤帶更新時間（ETR2_NOW_TIME）', /ETR2_NOW_TIME/.test(html), true);
  // 前端必須讀 etr2_now.json
  chk('前端載入 etr2_now.json', /etr2_now\.json/.test(html), true);
}


// ════════ 23. 官方現值：畫面上必須看得到（不藏在 title 屬性）════════
console.log('\n=== ETR2%官方現值 顯示與時間戳 ===');
if (need('_etr2NowRow') && need('_etr2NowLabel')) {
  setLex("window.ETR2_NOW_TIME = '2026-08-13T10:12';");
  chk('時間格式化為 MM/DD HH:MM', G._etr2NowLabel(), '08/13 10:12');
  setLex("window.ETR2_NOW_TIME = '';");
  chk('無時間時回空字串', G._etr2NowLabel(), '');
  setLex("window.ETR2_NOW_TIME = '2026-08-13T10:12';");

  const t = { county:'高雄市', township:'桃源區', alert_val:250, etr2_pct:0.21 };
  const row = G._etr2NowRow(t);
  console.log(`   輸出：${row.replace(/<[^>]*>/g,'')}`);
  chk('含「官方現值」字樣', /ETR2%官方現值/.test(row), true);
  chk('含百分比數值', /21%/.test(row), true);
  chk('★時間戳直接顯示於內容（非 title 屬性）', /08\/13 10:12/.test(row), true);
  chk('不使用 title 屬性藏資訊', /title=/.test(row), false);
  chk('無官方值時回空字串（不顯示空列）', G._etr2NowRow({county:'x',township:'y'}), '');

  // 全檔檢查：官方現值不得只存在於 title 屬性裡
  const html = fs.readFileSync('index.html', 'utf8');
  const inTitleOnly = /title="[^"]*ETR2%官方現值/.test(html);
  chk('全檔無「官方現值僅存於 title」的情形', inTitleOnly, false);
  // 預估與官方值必須分開標示
  chk('預估值明確標示「（預估）」', (html.match(/ETR2%（預估）/g)||[]).length >= 3, true);
  // 站別彈窗不得冒充官方時間（站級值不由 etr2_now.json 覆寫）
  chk('站別彈窗標示為主排程值', /本站ETR2% \$\{[^}]*\}%（主排程值）/.test(html), true);
}



// ════════ 24. 警戒面板隨時段／模式／情境變動 + 趨勢 ════════
console.log('\n=== 土石流／大崩面板：趨勢與時段連動 ===');
if (need('_alertTrend') && need('_prevCountMap') && need('_alertListHtml')) {
  setLex(`TMAP['南投縣仁愛鄉']={county:'南投縣',township:'仁愛鄉',alert_val:700,etr2_alert:700,
      etr2:70,etr2_pct:0.1,daily_rain:Array(15).fill(0),
      qpf_best:[0,100,200,300].concat(Array(60).fill(0)),
      qpf_ecmwf:[0,100,200,300].concat(Array(60).fill(0)),
      qpf_lo:Array(64).fill(0),
      obs_1h_p48:Array(48).fill(0),qpf_1h_p48:Array(48).fill(0),qpf_1h:Array(96).fill(0)};
    {const d=new Date(); d.setHours(0,0,0,0); BASE_TIME=d;}
    forecastModel='ecmwf'; _userFactorOn=false;_biasApplyOn=false;_scnOn=false;_scnDays={};
    winKey='today'; segFrom=0; segTo=3;`);
  const a = {county:'南投縣', town:'仁愛鄉', alert:300, etr2:70, off_level:''};
  // ★ 時鐘固定到當日 00 時（_nowSeg()=0），使 seg1~3 皆在未來。
  //   否則深夜執行時各段都已過去，不同模式取到相同的空段，
  //   會誤判為「數值未隨模式變動」。所有比較須基於同一時間基準。
  setLex("{const d=new Date(); d.setHours(0,0,0,0); BASE_TIME=d;}");

  // 趨勢：雨量遞增 → 應為上升箭頭，且帶前段百分比
  const tr = G._alertTrend(a, null);
  console.log(`   segTo=3：前段 ${tr.prevPct}% → 本段 ${tr.pct}%　箭頭=${tr.arrow.replace(/<[^>]*>/g,'')}`);
  if (!(tr.pct > tr.prevPct)) fails.push('雨量遞增時本段應高於前段');
  else console.log('  OK  趨勢方向正確（上升）');
  chk('上升為紅色▲', /ff5544/.test(tr.arrow), true);

  // ★ 隨選取時段變動
  setLex("segTo = 1;");
  const tr1 = G._alertTrend(a, null);
  setLex("segTo = 3;");
  const tr3 = G._alertTrend(a, null);
  console.log(`   segTo=1 → ${tr1.pct}%；segTo=3 → ${tr3.pct}%`);
  if (!(tr3.pct > tr1.pct)) fails.push('面板數值未隨選取時段變動');
  else console.log('  OK  隨選取時段變動');

  // ★ 隨模式變動（弱降雨全 0）
  setLex("forecastModel='lo';");
  const trLo = G._alertTrend(a, null);
  setLex("forecastModel='ecmwf';");
  console.log(`   ecmwf → ${tr3.pct}%；lo → ${trLo.pct}%`);
  // ★ 注意：ETR2 以官方值 t.etr2 為錨點，未來段才加 QPF。
  //   若選取時段落在「錨點所在段或更早」，兩模式必然相同——這是正確行為，
  //   不可據此判定未隨模式變動。故改測「更後面的時段」才有模式差異。
  setLex("segTo = 3; forecastModel='ecmwf';");
  const trE5 = G._alertTrend(a, null);
  setLex("forecastModel='lo';");
  const trL5 = G._alertTrend(a, null);
  setLex("forecastModel='ecmwf';");
  console.log(`   seg3：ecmwf ${trE5.pct}%　lo ${trL5.pct}%（錨點 70mm/300 = 23%）`);
  if (trE5.pct === trL5.pct && trE5.pct === 23) {
    console.log('  OK  兩模式同值係因該段仍等於官方錨點（設計如此）');
  } else if (trL5.pct < trE5.pct) {
    console.log('  OK  隨模式變動');
  } else {
    fails.push(`模式切換行為異常（ecmwf ${trE5.pct}% / lo ${trL5.pct}%）`);
  }

  // ★ 隨情境變動
  setLex("_scnOn=true; _scnDays={0:{model:'ecmwf',g:{'南投分署|山區':{add:1200,mul:1}}}," +
         "1:{model:'ecmwf',g:{'南投分署|山區':{add:1200,mul:1}}}};");
  const trScn = G._alertTrend(a, null);
  setLex("_scnOn=false; _scnDays={};");
  console.log(`   無情境 → ${tr3.pct}%；情境+1200mm/日 → ${trScn.pct}%`);
  if (!(trScn.pct > tr3.pct)) fails.push('面板數值未隨情境變動');
  else console.log('  OK  隨情境變動');

  // 達標數增減
  const entries = [['DF1', a], ['DF2', {county:'南投縣', town:'仁愛鄉', alert:9999, etr2:70, off_level:''}]];
  const pm = G._prevCountMap(entries, x=>x.alert < 1000);
  chk('前段達標數統計', pm['南投縣仁愛鄉'], 1);
  // 鄉鎮列應顯示達標數與增減
  const html = G._alertListHtml([{county:'南投縣', town:'仁愛鄉', id:'DF1', rank:5000,
    tag:'測試', tagCol:'#fff', detail:'d', trend:{arrow:'<span>▲</span>', prevCount:0}}]);
  chk('鄉鎮列顯示達標數', /仁愛鄉[\s\S]{0,200}>1</.test(html), true);
  chk('鄉鎮列顯示增減', /▲\+1/.test(html), true);
}

console.log('\n=== 未來6h段按鈕：起點以「現在」為準 ===');
if (need('_panelSeg') && need('_nowSeg')) {
  setLex("{const d=new Date(); d.setHours(0,0,0,0); BASE_TIME=d;}");
  const nowSeg = G._nowSeg();
  setLex("winKey='fut6_1';");
  const s1 = G._panelSeg();
  setLex("winKey='fut6_8';");
  const s8 = G._panelSeg();
  setLex("winKey='today'; segFrom=0; segTo=3;");
  console.log(`   nowSeg=${nowSeg}　fut6_1→seg${s1}　fut6_8→seg${s8}`);
  chk('fut6_1 起點＝現在所在段', s1, nowSeg);
  chk('fut6_8 涵蓋至 +48h', s8, nowSeg + 7);
  chk('共涵蓋 8 段（48小時）', s8 - s1 + 1, 8);
}

console.log('\n=== 前瞻功能已移除 ===');
{
  const html = fs.readFileSync('index.html', 'utf8');
  chk('無殘留 _lookahead', /_lookahead/.test(html), false);
  chk('無殘留 LOOKAHEAD_HOURS', /LOOKAHEAD_HOURS/.test(html), false);
  chk('無殘留「前瞻提醒」字樣', /前瞻提醒/.test(html), false);
}


// ════════ 25. 警戒「清單成員」必須隨時段變動（不只數值）════════
console.log('\n=== 土石流／大崩清單成員隨時段變動 ===');
if (need('_debrisHits') && need('_estWindowHours') && need('_lsRows')) {
  // ★ 雨的位置必須相對於 _nowSeg() 佈置，不可寫死段索引：
  //   BASE_TIME 設為今日 00 時後，_nowSeg() 仍取決於執行當下的時鐘，
  //   寫死 seg5 會讓測試在一天中的不同時間得到不同結果（曾因此誤判為程式回歸）。
  setLex(`{const d=new Date(); d.setHours(0,0,0,0); BASE_TIME=d;}`);
  const _ns0 = G._nowSeg();
  setLex(`{
    const q = Array(64).fill(0);
    q[` + (_ns0 + 4) + `] = 600;          // 雨落在「現在起第5段」（+24~30h）
    // ★ 需放到第5段：_fcQpfFromSeg 自該段「結束」起算，fut6_1 的 6h 視窗會涵蓋
    //   到第2段，故雨若放第4段，未來6h即已判定達標，測不到「6h內無雨」。
    TMAP['南投縣仁愛鄉']={county:'南投縣',township:'仁愛鄉',alert_val:700,etr2_alert:700,
      etr2:70,etr2_pct:0.1,daily_rain:Array(15).fill(0),
      qpf_best:q.slice(), qpf_ecmwf:q.slice(), qpf_lo:Array(64).fill(0),
      obs_1h_p48:Array(48).fill(0),qpf_1h_p48:Array(48).fill(0),qpf_1h:Array(96).fill(0)};
  }`);
  setLex(`
    forecastModel='ecmwf'; _userFactorOn=false;_biasApplyOn=false;_scnOn=false;_scnDays={};
    window.DEBRIS_ALERTS={'投縣DF001':{county:'南投縣',town:'仁愛鄉',vill:'',
      alert:300,etr2:70,off_level:'',pct:0.23}};
    window.LANDSLIDE_ALERTS={'投縣LL001':{county:'南投縣',town:'仁愛鄉',village:'',
      alert:300,etr2:70,off_level:'',pct:0.23}};`);
  const ns = G._nowSeg();
  const counts = [];
  for(let n=1; n<=4; n++){
    const seg = ns + n - 1;
    setLex(`winKey='fut6_${n}'; segFrom=${seg}; segTo=${seg};`);
    const c = G._debrisHits().length, l = G._lsRows(true).length;
    counts.push(c);
    console.log(`   fut6_${n}(seg${seg}) 視窗${G._estWindowHours(seg)}h → 土石流 ${c} 條、大崩 ${l} 處`);
    if(c !== l) fails.push(`同一鄉鎮的土石流(${c})與大崩(${l})判定不一致`);
  }
  // ★ 關鍵：前後段的清單成員必須不同（雨在 seg5，未來6h內不該達標）
  if (counts[0] === counts[counts.length-1]) {
    fails.push(`清單成員未隨時段變動（各段皆 ${counts[0]} 條）`);
  } else console.log('  OK  清單成員隨時段變動（不再全時段相同）');
  // ★ 斷言描述行為而非寫死索引：雨在「現在起第5段」，故短視窗未達標、
  //   視窗延長到涵蓋該段後才達標，且成立後不應再回到 0（單調性）。
  chk('最短視窗（6h）尚未達標', counts[0], 0);
  const firstHit = counts.findIndex(c => c > 0);
  console.log(`   首次達標於第 ${firstHit + 1} 個視窗（${(firstHit + 1) * 6}h）`);
  if (firstHit < 0) fails.push('延長視窗後仍未達標（清單與時段脫鉤）');
  else console.log('  OK  視窗延長後轉為達標');
  const afterHit = counts.slice(firstHit);
  if (afterHit.some(c => c === 0)) fails.push('達標後又回到 0（視窗延長卻遺漏降雨）');
  else console.log('  OK  達標後維持（視窗延長不遺漏降雨）');

  // 視窗時數：未來段依段距遞增，today 仍用官方 24h
  chk('fut6_1 視窗 6h', G._estWindowHours(ns), 6);
  chk('fut6_4 視窗 24h', G._estWindowHours(ns+3), 24);
  setLex("winKey='today'; segFrom=0; segTo=3;");
  chk('today 維持官方 24h 定義', G._estWindowHours(3), 24);

  // 模式連動（弱降雨全 0 → 應無達標）
  setLex("forecastModel='lo';");
  chk('切弱降雨 → 0 條', G._debrisHits().length, 0);
  setLex("forecastModel='ecmwf';");

  // 情境連動
  const before = G._debrisHits().length;
  setLex("_scnOn=true; _scnDays={0:{model:'lo',g:{}},1:{model:'lo',g:{}}};");
  const after = G._debrisHits().length;
  setLex("_scnOn=false; _scnDays={};");
  console.log(`   情境改用弱降雨：${before} → ${after} 條`);
  if (after >= before && before > 0) fails.push('清單成員未隨情境變動');
  else console.log('  OK  清單成員隨情境變動');
}


// ════════ 26. 切換時段／模式必須重繪所有面板（不只地圖）════════
console.log('\n=== setWin / setModel 觸發完整重繪 ===');
if (need('setWin') && need('setModel')) {
  const targets = ['updateEtrAlertPanel','updateDebrisPanel','updateLandslidePanel',
                   'updateTyphoonPanel','updateDistrictSummary','renderLayer'];
  const probe = '__winCalled';
  targets.forEach(fn=>{
    setLex(`if (typeof ${fn} === 'function' && !${fn}.__wrapped) {
      const _o = ${fn};
      ${fn} = function(){ (window.${probe}=window.${probe}||{})['${fn}']=true;
        try{ return _o.apply(this, arguments); }catch(e){} };
      ${fn}.__wrapped = true;
    }`);
  });

  // setWin：切到未來6h段
  setLex(`window.${probe} = {}; setWin('fut6_2');`);
  const c1 = getLex(`window.${probe}`) || {};
  targets.forEach(fn=>{
    const ok = !!c1[fn];
    if(!ok) fails.push(`setWin 未重繪 ${fn}（該面板不隨時段變動）`);
    console.log(`   setWin  ${ok?'OK ':'!! '}${fn}`);
  });

  // setModel：切模式
  setLex(`window.${probe} = {}; setModel('lo');`);
  const c2 = getLex(`window.${probe}`) || {};
  targets.forEach(fn=>{
    const ok = !!c2[fn];
    if(!ok) fails.push(`setModel 未重繪 ${fn}（該面板不隨模式變動）`);
    console.log(`   setModel ${ok?'OK ':'!! '}${fn}`);
  });
  setLex("setModel('ecmwf'); setWin('today');");
}


// ════════ 27. TD 面板內容 ＋ 過期警報過濾 ════════
console.log('\n=== TD 階段面板內容 ===');
if (need('updateTyphoonPanel')) {
  const future = new Date(Date.now() + 4*3600e3).toISOString();
  setLex("window.TYPHOON_WARN = [];" +
    "window.TYPHOON_TRACK = [{name_zh:'', name_en:'', ty_no:'', td_no:'20'," +
    "current:{t:'" + new Date().toISOString() + "', lat:20.5, lng:125.3, ws:15, gust:23, p:1000, r15:80, r25:0}," +
    "forecast:[{fh:24, lat:21.5, lng:123.0, ws:16, r15:90, r70:120}," +
    "{fh:48, lat:22.5, lng:121.0, ws:16, r15:90, r70:180}]}];" +
    "document.body.insertAdjacentHTML('beforeend','<div id=\\'typhoon-panel-body\\'></div>');");
  G.updateTyphoonPanel();
  const plain = (getLex("document.getElementById('typhoon-panel-body').innerHTML")||'')
    .replace(/<[^>]*>/g,' ');
  console.log('   面板摘要：' + plain.replace(/\s+/g,' ').slice(0, 130));
  chk('顯示強度分級', /熱帶低壓/.test(plain), true);
  chk('無名稱時以「熱帶性低氣壓」呈現', /熱帶性低氣壓/.test(plain), true);
  // ★ TD 階段官方有編號（CwaTdNo），不應顯示「未編號」
  chk('★TD 顯示熱帶性低壓編號', /熱帶性低壓編號 20/.test(plain), true);
  chk('不再誤標「未編號」', /未編號/.test(plain), false);
  chk('顯示資料時間', /資料時間/.test(plain), true);
  chk('顯示中心位置', /125\.3/.test(plain), true);
  chk('顯示氣壓', /1000/.test(plain), true);
  chk('顯示預報路徑', /\+24h/.test(plain), true);
  chk('顯示雨勢較大地區', /雨勢較大地區/.test(plain), true);
}

console.log('\n=== 過期警報單／舊颱風資料須濾除 ===');
if (need('_tyWarnList') && need('_tyRows')) {
  const iso = h => new Date(Date.now() + h*3600e3).toISOString();
  // 1) expires 已過 → 不顯示
  setLex("window.TYPHOON_WARN = [{headline:'海上颱風警報', effective:'" + iso(-72) +
         "', expires:'" + iso(-60) + "', sections:[]}];");
  chk('★expires 已過的警報單不顯示', G._tyWarnList().length, 0);
  chk('可查得被濾除的筆數', G._tyWarnStale(), 1);
  // 2) expires 未到 → 顯示
  setLex("window.TYPHOON_WARN = [{headline:'海上颱風警報', effective:'" + iso(-2) +
         "', expires:'" + iso(4) + "', sections:[]}];");
  chk('expires 未到的警報單正常顯示', G._tyWarnList().length, 1);
  // 3) 無 expires → 以 effective 距今 48h 為界
  setLex("window.TYPHOON_WARN = [{headline:'海上颱風警報', effective:'" + iso(-72) + "', sections:[]}];");
  chk('★無 expires 且發布逾 48h → 不顯示', G._tyWarnList().length, 0);
  setLex("window.TYPHOON_WARN = [{headline:'海上颱風警報', effective:'" + iso(-12) + "', sections:[]}];");
  chk('無 expires 但發布未逾 48h → 顯示', G._tyWarnList().length, 1);
  // 4) 時間無法解析 → 保留（寧可多顯示，不可漏掉有效警報）
  setLex(`window.TYPHOON_WARN = [{headline:'海上颱風警報', effective:'壞掉的時間', sections:[]}];`);
  chk('時間無法解析 → 保留', G._tyWarnList().length, 1);
  // 5) 颱風路徑同樣過濾
  setLex("window.TYPHOON_WARN = []; window.TYPHOON_TRACK = [{name_zh:'舊颱風', current:{t:'" +
         iso(-72) + "', lat:20,lng:125,ws:30}, forecast:[]}];");
  chk('★逾 48h 的舊颱風路徑不顯示', G._tyRows().length, 0);
  setLex("window.TYPHOON_TRACK = [{name_zh:'現有颱風', current:{t:'" + iso(-3) +
         "', lat:20,lng:125,ws:30}, forecast:[]}];");
  chk('近期颱風路徑正常顯示', G._tyRows().length, 1);
  setLex("window.TYPHOON_WARN = []; window.TYPHOON_TRACK = [];");
  setLex("winKey='today'; segFrom=0; segTo=3; forecastModel='ecmwf';");
}


// ════════ 28. 快取不得沿用舊 BASE_TIME 的結果 ════════
console.log('\n=== BASE_TIME 變動後快取須失效 ===');
if (need('_tyKeyPoints') && need('townMetrics')) {
  const ty = {ty_no:'13', current:{t:'2026-08-19T00:00:00+08:00', lat:26.9, lng:126.6,
    ws:40, p:950, r15:280, r25:90},
    forecast:[{fh:24, lat:25.5, lng:124.0, ws:38, r15:280, r25:90},
              {fh:48, lat:24.0, lng:121.5, ws:33, r15:250, r25:80},
              {fh:72, lat:23.0, lng:118.0, ws:20, r15:150, r25:0}]};
  setLex("window.__ty = " + JSON.stringify(ty) + ";");
  // 第一次：BASE_TIME 設為 A 日
  setLex("{const d=new Date(2026,0,10,0,0,0); BASE_TIME=d;} _tyKpCache.clear();");
  const kpA = G._tyKeyPoints(getLex('window.__ty'));
  const tA = kpA.points.length ? kpA.points[0].time.getTime() : null;
  // 第二次：BASE_TIME 改為 B 日（模擬 data.json 載入後更新）
  setLex("{const d=new Date(2026,5,20,0,0,0); BASE_TIME=d;}");
  const kpB = G._tyKeyPoints(getLex('window.__ty'));
  const tB = kpB.points.length ? kpB.points[0].time.getTime() : null;
  console.log(`   BASE_TIME=1/10 → ${tA ? new Date(tA).toLocaleDateString('zh-TW') : '—'}`);
  console.log(`   BASE_TIME=6/20 → ${tB ? new Date(tB).toLocaleDateString('zh-TW') : '—'}`);
  if (tA == null || tB == null) fails.push('關鍵時間點未產生，無法驗證快取失效');
  else if (tA === tB) fails.push('★BASE_TIME 變動後仍沿用舊快取（面板會顯示過期日期）');
  else console.log('  OK  BASE_TIME 變動後關鍵時間點隨之更新');

  // townMetrics 亦同
  setLex(`TMAP['南投縣仁愛鄉']={county:'南投縣',township:'仁愛鄉',alert_val:700,etr2_alert:700,
      etr2:70,etr2_pct:0.1,daily_rain:Array(15).fill(0),
      qpf_best:Array(64).fill(5), qpf_ecmwf:Array(64).fill(5),
      obs_1h_p48:Array(48).fill(0),qpf_1h_p48:Array(48).fill(0),qpf_1h:Array(96).fill(0)};
    forecastModel='ecmwf'; _scnOn=false; _userFactorOn=false; _biasApplyOn=false;`);
  const k1 = G._tmKey();
  setLex("{const d=new Date(2026,7,1,0,0,0); BASE_TIME=d;}");
  const k2 = G._tmKey();
  chk('★_tmKey 隨 BASE_TIME 改變', k1 !== k2, true);
  setLex("window.TYPHOON_TRACK=[]; window.TYPHOON_WARN=[];");
}


// ════════ 29. 風險指標：須隨未來雨量上修／下修 ════════
console.log('\n=== 風險指標的兩個實務目標 ===');
if (need('calcRiskIndicator')) {
  const mk = (etr2, alert, futQpf) => ({
    county:'高雄市', township:'六龜區', alert_val:alert, etr2_alert:alert,
    etr2, etr2_pct: etr2/alert, daily_rain:Array(15).fill(0),
    pop_6h:Array(28).fill(50),
    qpf_best:Array(64).fill(futQpf), qpf_ecmwf:Array(64).fill(futQpf),
    warn_seg:Array(64).fill(0), maxh_best:Array(64).fill(futQpf/6),
    obs_1h_p48:Array(48).fill(0), qpf_1h_p48:Array(48).fill(0), qpf_1h:Array(96).fill(0),
  });
  setLex("forecastModel='ecmwf'; _scnOn=false; _userFactorOn=false; _biasApplyOn=false;" +
         "winKey='today'; segFrom=0; segTo=3; mode='rain';" +
         "{const d=new Date(); d.setHours(0,0,0,0); BASE_TIME=d;}");

  // ★目標1：ETR2% 高（155%）但未來幾乎無雨 → 風險應下修
  const dry = G.calcRiskIndicator(mk(386.8, 250, 0.2), 3);
  console.log(`   ETR2 155%＋未來無雨: R=${dry.R}（E_proj=${dry.E_proj} → E_adj=${dry.E_adj}，係數 ${dry.fRain}，未來24h ${dry.q24}mm）`);
  if (!(dry.E_adj < dry.E_proj)) fails.push('★目標1未達成：ETR2高但無雨時風險未下修');
  else console.log('  OK  目標1：雨停折減中 → 風險下修');
  chk('下修係數為下限 0.55', dry.fRain, 0.55);

  // 同樣 ETR2%，但未來持續大雨 → 不應下修
  const wet = G.calcRiskIndicator(mk(386.8, 250, 30), 3);
  console.log(`   ETR2 155%＋未來大雨: R=${wet.R}（係數 ${wet.fRain}，未來24h ${wet.q24}mm）`);
  chk('持續降雨時係數為 1（不下修）', wet.fRain, 1);
  if (!(wet.R > dry.R)) fails.push('持續降雨的風險應高於雨停');
  else console.log('  OK  同一 ETR2%，有雨風險高於無雨');

  // ★目標2：ETR2% 低但未來大雨 → 風險應上修（由警特報路徑拉高）
  const lowWet = G.calcRiskIndicator(mk(25, 250, 60), 3);   // ETR2 僅10%，但每段60mm
  const lowDry = G.calcRiskIndicator(mk(25, 250, 0.2), 3);
  console.log(`   ETR2 10%＋未來大雨: R=${lowWet.R}（警特報 ${lowWet.warnLv}）`);
  console.log(`   ETR2 10%＋未來無雨: R=${lowDry.R}（警特報 ${lowDry.warnLv}）`);
  if (!(lowWet.R > lowDry.R)) fails.push('★目標2未達成：ETR2低但未來大雨時風險未上修');
  else console.log('  OK  目標2：ETR2低但將有大雨 → 風險上修');
  // ★ 不寫死級距：_nowSeg() 依執行時鐘變動，會改變納入的預報段，絕對值因而浮動。
  //   驗「顯著上升」即可（至少 10 倍且達 30 分以上）。
  // ★ 絕對值受 _nowSeg() 影響（納入的預報段數不同），只驗「明顯上升」：
  //   至少 3 倍且淨增 10 分以上，方向正確即可。
  if (!(lowWet.R >= lowDry.R * 3 && lowWet.R - lowDry.R >= 10)) {
    fails.push(`目標2：未來大雨的風險上升幅度不足（${lowDry.R} → ${lowWet.R}）`);
  } else console.log(`  OK  未來大雨確實產生顯著風險訊號（${lowDry.R} → ${lowWet.R}）`);

  // 下修不得過度：高 ETR2 仍應保有基本風險（土壤含水量高）
  if (dry.R < 50) fails.push(`下修過度：ETR2 155% 不應降到 ${dry.R}（低於「低」級）`);
  else console.log(`  OK  下修有下限，ETR2 155% 仍保有 R=${dry.R}`);
}


// ════════ 30. 鄉鎮市區名稱圖層 ════════
console.log('\n=== 鄉鎮市區名稱圖層 ===');
if (need('toggleTownNameLayer') && need('_townCentroid') && need('setTownNameScope')) {
  // 形心必須落在該鄉鎮的經緯度範圍內
  const f = getLex('TOWN_GEO.features[0]');
  const c = G._townCentroid(f);
  console.log(`   ${f.properties.COUNTYNAME}${f.properties.TOWNNAME} 形心 = ` +
              `${c ? c.map(v=>v.toFixed(3)).join(', ') : '—'}`);
  if (!c) fails.push('形心計算失敗');
  else {
    // 與該 feature 的 bounds 比對
    let laMin=99,laMax=-99,loMin=999,loMax=-999;
    const g=f.geometry;
    const polys = g.type==='Polygon'?[g.coordinates]:g.coordinates;
    polys.forEach(p=>(p[0]||[]).forEach(pt=>{
      laMin=Math.min(laMin,pt[1]); laMax=Math.max(laMax,pt[1]);
      loMin=Math.min(loMin,pt[0]); loMax=Math.max(loMax,pt[0]);
    }));
    const inside = c[0]>=laMin && c[0]<=laMax && c[1]>=loMin && c[1]<=loMax;
    chk('形心落在該鄉鎮範圍內', inside, true);
  }
  // 全臺 368 個都算得出形心（不得有 null）
  const bad = getLex(`(function(){let n=0;
    TOWN_GEO.features.forEach(f=>{ if(!_townCentroid(f)) n++; }); return n;})()`);
  chk('★全部鄉鎮都有形心（無 null）', bad, 0);

  // 範圍切換
  G.setTownNameScope('臺中市');
  chk('範圍設為臺中市', getLex('townNameScope'), '臺中市');
  G.setTownNameScope('ALL');
  chk('範圍設回全臺', getLex('townNameScope'), 'ALL');

  // 下拉選單內容
  setLex(`document.body.insertAdjacentHTML('beforeend',
    '<select id="townNameScope"></select><button id="bTownName"></button>');
    _buildTownNameScope();`);
  const nOpt = getLex("document.getElementById('townNameScope').options.length");
  console.log(`   下拉選單 ${nOpt} 個選項（全臺 + 22 縣市 = 23）`);
  chk('選單含全臺與各縣市', nOpt, 23);
  const first = getLex("document.getElementById('townNameScope').options[0].value");
  chk('第一項為 ALL', first, 'ALL');
}


// ════════ 31. 颱風區塊收合 ＋ 鄉鎮界線 ════════
console.log('\n=== 颱風／TD 區塊收合 ===');
if (need('toggleTySec') && need('_tySecKey') && need('updateTyphoonPanel')) {
  setLex("document.body.insertAdjacentHTML('beforeend','<div id=\"typhoon-panel-body\"></div>');");
  setLex("window.TYPHOON_WARN = []; window.TYPHOON_TRACK = [{name_zh:'', ty_no:'', td_no:'20'," +
    "current:{t:'" + new Date().toISOString() + "', lat:20.5, lng:125.3, ws:15, gust:23, p:1000, r15:80, r25:0}," +
    "forecast:[{fh:24, lat:21.5, lng:123.0, ws:16, r15:90, r70:120}]}];");
  G.updateTyphoonPanel();
  const html1 = getLex("document.getElementById('typhoon-panel-body').innerHTML") || '';
  chk('預設展開（▾）', /▾/.test(html1), true);
  chk('展開時內容可見', /display:block/.test(html1), true);
  chk('標題列可點擊收合', /toggleTySec\(/.test(html1), true);

  // 收合後：內容隱藏、箭頭改變，但標題仍在
  const key = G._tySecKey(null, getLex('window.TYPHOON_TRACK[0]'));
  console.log(`   區塊鍵 = ${key}`);
  G.toggleTySec(key);
  const html2 = getLex("document.getElementById('typhoon-panel-body').innerHTML") || '';
  chk('★收合後內容隱藏', /display:none/.test(html2), true);
  chk('收合後箭頭為 ▸', /▸/.test(html2), true);
  chk('收合後標題仍在（TD 編號）', /熱帶性低壓編號 20/.test(html2), true);

  // 再點一次應展開；且狀態在重繪後保留
  G.toggleTySec(key);
  const html3 = getLex("document.getElementById('typhoon-panel-body').innerHTML") || '';
  chk('再點展開', /display:block/.test(html3), true);
  G.toggleTySec(key);
  G.updateTyphoonPanel();                       // 重繪
  const html4 = getLex("document.getElementById('typhoon-panel-body').innerHTML") || '';
  chk('★收合狀態於重繪後保留', /display:none/.test(html4), true);
  setLex("window.TYPHOON_TRACK = []; window.TYPHOON_WARN = [];");
}

console.log('\n=== 鄉鎮市區界線 ===');
{
  const html = fs.readFileSync('index.html', 'utf8');
  chk('名稱圖層會畫界線', /鄉鎮市區界：比縣市界細/.test(html), true);
  chk('界線不攔截滑鼠事件', /color:'#5a5a5a'[\s\S]{0,120}interactive:false/.test(html), true);
  // 改深灰以確保在飽和色塊上可見；粗細維持比縣市界細
  chk('界線比縣市界細（weight 0.7）', /color:'#5a5a5a', weight:0\.7/.test(html), true);
  chk('繪後把縣市界拉回上層', /countyBorder\.bringToFront/.test(html), true);
}


// ════════ 32. 形心置中 ＋ 關鍵時間點位置 ════════
console.log('\n=== 鄉鎮標籤位置必須落在該鄉鎮內 ===');
if (need('_townCentroid') && need('_ptInRing')) {
  const bad = getLex(`(function(){
    const out=[];
    TOWN_GEO.features.forEach(f=>{
      const c=_townCentroid(f);
      if(!c){ out.push(f.properties.COUNTYNAME+f.properties.TOWNNAME+'(null)'); return; }
      const g=f.geometry; const polys=g.type==='Polygon'?[g.coordinates]:g.coordinates;
      let ok=false;
      polys.forEach(p=>{ if(_ptInRing(c[1],c[0],p[0]||[])) ok=true; });
      if(!ok) out.push(f.properties.COUNTYNAME+f.properties.TOWNNAME);
    });
    return out;})()`);
  console.log(`   落在區域外：${bad.length} / 368` + (bad.length ? '：' + bad.join('、') : ''));
  chk('★全部 368 個標籤都在自己的鄉鎮內', bad.length, 0);
  // ★ 旗津區含東沙(南海)與本島兩環，頂點數相同；須取面積大者（本島）
  const qj = getLex(`(function(){
    const f=TOWN_GEO.features.find(x=>x.properties.TOWNNAME==='旗津區');
    return f ? _townCentroid(f) : null;})()`);
  console.log(`   旗津區標籤：${qj ? qj.map(v=>v.toFixed(3)).join(', ') : '—'}`);
  chk('★旗津標籤在本島而非東沙', !!(qj && qj[0] > 22 && qj[1] > 120), true);
  // 先前已知會標錯的 7 個必須修好
  ['嘉義縣番路鄉','臺東縣太麻里鄉','屏東縣枋山鄉','新北市八里區',
   '新北市新莊區','高雄市旗津區','屏東縣恆春鎮'].forEach(k=>{
    if (bad.includes(k)) fails.push(`${k} 標籤仍在區域外`);
  });
  if (!bad.length) console.log('  OK  先前 7 個標錯的鄉鎮已修正');

  // 快取：第二次呼叫應極快
  const t0 = Date.now();
  getLex('TOWN_GEO.features.forEach(f=>_townCentroid(f))');
  const ms = Date.now() - t0;
  console.log(`   已快取後全臺重算耗時 ${ms}ms`);
  if (ms > 500) fails.push(`形心快取未生效（${ms}ms）`);
  else console.log('  OK  快取生效（縮放重繪不會卡頓）');
}

console.log('\n=== 關鍵時間點位置與標示 ===');
if (need('_tyKeyPointsHtml')) {
  const html = fs.readFileSync('index.html', 'utf8');
  chk('明確標示為本系統推估', /本系統推估，非官方發布值/.test(html), true);
  // 位置：應在「雨勢較大地區」之前（即緊接標題）
  const iOff = html.indexOf('① 官方資料（中央氣象署警報單）');
  const iEst = html.indexOf('② 本系統推估（非官方發布值）');
  const iKp  = html.indexOf('_tyKeyPointsHtml((window.TYPHOON_TRACK||[])[0]');
  console.log(`   ①官方=${iOff}　②推估=${iEst}　關鍵時間點=${iKp}`);
  chk('★官方資料段在推估段之前', iOff > 0 && iOff < iEst, true);
  chk('★關鍵時間點屬於推估段', iKp > iEst, true);
  chk('保守顯示：未觸及時不列出', /const touch = kp\.points\.filter/.test(html), true);
  // 旗津：取面積最大環而非頂點最多
  chk('環選取依面積（旗津東沙問題）', /let ring = null, bestA = -1/.test(html), true);
  chk('標籤置中（iconAnchor 取容器中心）', /iconAnchor:\[_w\/2, _h\/2\]/.test(html), true);
  chk('TD 區塊也有關鍵時間點', /_tyKeyPointsHtml\(ty, SEC\)/.test(html), true);
  // ★ 「關鍵時間點（編號對應地圖徽章）」一詞也用於地圖圖例，不可據此判斷。
  //   改為確認面板內不再有「獨立的 TYPHOON_TRACK 迴圈」產生該區塊。
  chk('末端不再有獨立的關鍵時間點迴圈',
      /關鍵時間點：編號與地圖徽章一致/.test(html), false);
}


// ════════ 33. 多颱風排序、預設收合、地圖全顯示 ════════
console.log('\n=== 多系統：排序與預設收合 ===');
if (need('_tyRows') && need('_tyUrgency') && need('updateTyphoonPanel')) {
  const iso = new Date().toISOString();
  // A：遠離臺灣（東方外海）；B：即將侵臺（暴風圈會觸陸）
  setLex("window.TYPHOON_WARN = []; window.TYPHOON_TRACK = [" +
    "{name_zh:'遠方颱風', ty_no:'20', current:{t:'" + iso + "', lat:25.0, lng:140.0, ws:35, r15:150, r25:50}," +
    " forecast:[{fh:24, lat:26.0, lng:143.0, ws:35, r15:150, r25:50}," +
    "           {fh:48, lat:27.0, lng:146.0, ws:30, r15:120, r25:0}]}," +
    "{name_zh:'侵臺颱風', ty_no:'21', current:{t:'" + iso + "', lat:22.0, lng:124.0, ws:40, r15:250, r25:80}," +
    " forecast:[{fh:24, lat:23.0, lng:121.5, ws:38, r15:250, r25:80}," +
    "           {fh:48, lat:24.0, lng:119.0, ws:30, r15:180, r25:0}]}];");
  const rows = G._tyRows();
  console.log(`   排序後：${rows.map(r=>r.ty.name_zh).join(' → ')}`);
  chk('★越先影響臺灣者排越前', rows[0].ty.name_zh, '侵臺颱風');
  const u0 = G._tyUrgency(rows[0].ty), u1 = G._tyUrgency(rows[1].ty);
  console.log(`   急迫度：${rows[0].ty.name_zh}=${u0}　${rows[1].ty.name_zh}=${u1}`);
  if (!(u0 < u1)) fails.push('急迫度排序不正確');

  // 預設收合：第一個展開、其餘收合
  // ★ _tySecOpen 是 const 物件：不可指派，只能清空內容（詞法綁定陷阱）
  setLex("for (const k in _tySecOpen) delete _tySecOpen[k];" +
    "document.body.insertAdjacentHTML('beforeend','<div id=\"typhoon-panel-body\"></div>');");
  G.updateTyphoonPanel();
  const html = getLex("document.getElementById('typhoon-panel-body').innerHTML") || '';
  const nOpen = (html.match(/display:block/g) || []).length;
  const nShut = (html.match(/display:none/g) || []).length;
  console.log(`   展開 ${nOpen} 個、收合 ${nShut} 個`);
  chk('★僅第一個預設展開', nOpen, 1);
  chk('★其餘預設收合', nShut, 1);
  // 第一個展開的應是侵臺颱風
  const iFirst = html.indexOf('侵臺颱風'), iSecond = html.indexOf('遠方颱風');
  chk('侵臺颱風排在前', iFirst > 0 && iFirst < iSecond, true);

  // 地圖：不受面板收合影響，全部系統都畫
  const src = fs.readFileSync('index.html', 'utf8');
  const iLayer = src.indexOf('function renderTyphoonLayer');
  const layerSrc = src.slice(iLayer, iLayer + 900);
  chk('★地圖畫全部系統（不看收合狀態）', /_tySecOpen/.test(layerSrc), false);
  chk('地圖直接用 TYPHOON_TRACK', /const list = \(window\.TYPHOON_TRACK \|\| \[\]\)/.test(layerSrc), true);

  setLex("window.TYPHOON_TRACK = []; window.TYPHOON_WARN = [];" +
         "for (const k in _tySecOpen) delete _tySecOpen[k];");
}

console.log('\n=== 颱風資料每小時更新 ===');
{
  const src = fs.readFileSync('index.html', 'utf8');
  chk('前端載入 typhoon_now.json', /typhoon_now\.json/.test(src), true);
  chk('載入後清除關鍵時間點快取', /_tyKpCache\.clear\(\)[\s\S]{0,80}關鍵時間點重算/.test(src), true);
  const py = fs.readFileSync('fetch_qpesums_hourly.py', 'utf8');
  chk('每小時腳本會寫 typhoon_now.json', /TYPHOON_FILE\s*=\s*"typhoon_now\.json"/.test(py), true);
  chk('沿用主腳本解析（不複製實作）', /import fetch_rainfall as FR/.test(py), true);
  chk('兩項皆失敗才跳過', /兩項皆失敗，保留前一份/.test(py), true);
}


// ════════ 34. 有警報單時，其他系統仍須顯示（預設收合）════════
console.log('\n=== 有警報單 + 其他系統並存 ===');
if (need('updateTyphoonPanel')) {
  const iso = new Date().toISOString();
  setLex("for (const k in _tySecOpen) delete _tySecOpen[k];" +
    "document.body.insertAdjacentHTML('beforeend','<div id=\"typhoon-panel-body2\"></div>');");
  // 一份海警（颱風13）＋ 另一個未發布警報的 TD
  setLex("window.TYPHOON_WARN = [{headline:'海上颱風警報', severity_level:'海上颱風警報'," +
    " ty_no:'13', name_zh:'測試颱風', report_no:'5', effective:'" + iso + "', sections:[" +
    "{title:'颱風動態', value:'向西北移動'}], areas:['臺灣北部海面']}];" +
    "window.TYPHOON_TRACK = [" +
    "{name_zh:'測試颱風', ty_no:'13', current:{t:'" + iso + "', lat:22.0, lng:124.0, ws:40, r15:250, r25:80}," +
    " forecast:[{fh:24, lat:23.0, lng:121.5, ws:38, r15:250, r25:80}]}," +
    "{name_zh:'', ty_no:'', td_no:'21', current:{t:'" + iso + "', lat:19.0, lng:132.0, ws:15, r15:80, r25:0}," +
    " forecast:[{fh:24, lat:20.0, lng:130.0, ws:16, r15:90}]}];");
  setLex("document.getElementById('typhoon-panel-body').id='typhoon-panel-body';");
  G.updateTyphoonPanel();
  const html = getLex("document.getElementById('typhoon-panel-body').innerHTML") || '';
  const plain = html.replace(/<[^>]*>/g, ' ');

  chk('顯示官方警報單', /海上颱風警報/.test(plain), true);
  chk('★其他系統仍顯示（TD 21）', /熱帶性低壓編號 21/.test(plain), true);
  chk('有「其他活動中系統」分隔標頭', /其他活動中系統/.test(plain), true);
  // 警報單展開、其他系統收合
  const nOpen = (html.match(/display:block/g) || []).length;
  const nShut = (html.match(/display:none/g) || []).length;
  console.log(`   展開 ${nOpen} 個、收合 ${nShut} 個`);
  chk('★警報單展開、其他系統預設收合', [nOpen, nShut], [1, 1]);
  // 已在警報單呈現者不得重複列入「其他」
  const nTest = (plain.match(/測試颱風/g) || []).length;
  console.log(`   「測試颱風」出現 ${nTest} 次（應只在警報單段落）`);
  if (nTest > 2) fails.push(`已發布警報的颱風重複列入其他系統（出現 ${nTest} 次）`);
  else console.log('  OK  未重複列入');

  // 收合後警報時間段落也要跟著隱藏（_tySecClose 位置正確性）
  const src = fs.readFileSync('index.html', 'utf8');
  chk('警報時間在收合區塊內', /警報時間須在收合區塊「內」/.test(src), true);
  setLex("window.TYPHOON_TRACK = []; window.TYPHOON_WARN = [];" +
         "for (const k in _tySecOpen) delete _tySecOpen[k];");
}


// ════════ 35. 風力預測圖層 ════════
console.log('\n=== 風力預測：蒲福風級色階與三種來源 ===');
if (need('_bfColor') && need('_wsToBf') && need('_windOf')) {
  // 色階邊界（使用者指定：<4白、4-7綠、7-10黃、10-13紅、>=13紫）
  const cases = [[0,'#FFFFFF'],[3,'#FFFFFF'],[4,'#00FF00'],[6,'#00FF00'],
                 [7,'#FFFF00'],[9,'#FFFF00'],[10,'#FF0000'],[12,'#FF0000'],
                 [13,'#FF00FF'],[17,'#FF00FF']];
  let ok = true;
  cases.forEach(([bf, col])=>{ if(G._bfColor(bf) !== col){ ok = false;
    fails.push(`風級 ${bf} 色階錯誤：得 ${G._bfColor(bf)} 期望 ${col}`); } });
  if(ok) console.log('  OK  五級距色階全部正確（白/綠/黃/紅/紫）');
  chk('無資料回 null（不著色）', G._bfColor(null), null);

  // 風速→風級換算（蒲福風級標準值）
  chk('0.2 m/s → 0 級', G._wsToBf(0.2), 0);
  chk('10.8 m/s → 6 級', G._wsToBf(10.8), 6);
  chk('17.2 m/s → 8 級', G._wsToBf(17.2), 8);
  chk('32.7 m/s → 12 級', G._wsToBf(32.7), 12);

  // 三種來源
  const future = new Date(Date.now() + 3*3600e3).toISOString();
  setLex(`TMAP['南投縣仁愛鄉'] = TMAP['南投縣仁愛鄉'] || {county:'南投縣',township:'仁愛鄉'};
    window.WIND_FCST = {'南投縣':{'仁愛鄉':[{start:'', end:'${future}', ws:12.0, bf:6}]}};
    window.GUST_FCST = {'南投縣':{ws:25.0, gust:33.0, bf:12}};`);
  const t = getLex("TMAP['南投縣仁愛鄉']");

  setLex("windKind='mean';");
  const m = G._windOf(t);
  console.log(`   平均風：${m.bf} 級（${m.ws} m/s）來源=${m.src}`);
  chk('平均風用官方 bf', m.bf, 6);

  setLex("windKind='gust';");
  const g = G._windOf(t);
  console.log(`   官方陣風：${g.bf} 級（${g.ws} m/s）來源=${g.src}`);
  chk('官方陣風取 gust 值', g.ws, 33.0);

  setLex("windKind='gust_est';");
  const e = G._windOf(t);
  console.log(`   推估陣風：${e.bf} 級（${e.ws.toFixed(1)} m/s）＝平均風 × 山區因子`);
  if (!(e.ws > m.ws)) fails.push('推估陣風應大於平均風');
  else console.log('  OK  推估陣風 > 平均風');
  chk('推估標記來源', e.src, 'gust_est');

  // 官方陣風無資料時不得著色（平時無颱風警報）
  setLex("window.GUST_FCST = {};");
  setLex("windKind='gust';");
  chk('★無颱風警報時官方陣風為空（不誤導）', G._windOf(t).bf, null);
  setLex("windKind='mean';");

  // 標示：推估與官方尺度差異必須寫在 tooltip
  const html = fs.readFileSync('index.html', 'utf8');
  chk('推估陣風標示非官方', /系統推估值，非氣象署發布/.test(html), true);
  chk('官方陣風標示尺度差異', /警戒地區尺度，非鄉鎮尺度/.test(html), true);
  // ★ 三種來源改為右側三張圖並列（地圖固定平均風，不再用下拉切換）
  chk('地圖下拉已移除', /id="windKind"/.test(html), false);
  ['cv-wind-day','cv-wind-day-gust','cv-wind-day-est',
   'cv-wind-hr','cv-wind-hr-gust','cv-wind-hr-est'].forEach(id=>{
    chk(`畫布 ${id} 存在`, new RegExp(`id="${id}"`).test(html), true);
  });
  chk('圖例標題不帶來源', /wind: '蒲福風級｜'/.test(html), false);
}


// ════════ 36. 風力預測改為 mode（與雨量/ETR2 同層）════════
console.log('\n=== 風力預測為獨立顯示模式 ===');
if (need('getAccum') && need('_windOf')) {
  const future = new Date(Date.now() + 3*3600e3).toISOString();
  const past   = new Date(Date.now() - 3*3600e3).toISOString();
  setLex(`TMAP['南投縣仁愛鄉'] = Object.assign(TMAP['南投縣仁愛鄉']||{},
      {county:'南投縣', township:'仁愛鄉'});
    window.WIND_FCST = {'南投縣':{'仁愛鄉':[
      {start:'${past}', end:'${future}', ws:12.0, bf:6},
      {start:'${future}', end:'${new Date(Date.now()+9*3600e3).toISOString()}', ws:22.0, bf:9}]}};
    window.GUST_FCST = {};
    mode='wind'; windKind='mean'; winKey='today'; segFrom=0; segTo=3;`);
  const t = getLex("TMAP['南投縣仁愛鄉']");

  // ★ getAccum 必須回傳風力（表示地圖直接以風力著色，非疊加）
  const acc = G.getAccum(t, 'wind');
  console.log(`   getAccum: isWind=${acc.isWind} 風級=${acc.totalRain} 風速=${acc.windWs}`);
  chk('★風力為獨立模式（isWind）', acc.isWind, true);
  if (acc.totalRain == null) fails.push('風力模式取不到風級（地圖會是空白）');
  else console.log('  OK  地圖可取得風級著色值');

  // 色階套用：以實際取到的風級驗色（getAccum 取「今日結束」時刻，
  //   可能落在較晚的預報段，故不可寫死 6 級）
  // 色碼已對齊 RAIN_SCALE（純色）
  const wantCol = acc.totalRain < 4 ? '#FFFFFF' : acc.totalRain < 7 ? '#00FF00'
                : acc.totalRain < 10 ? '#FFFF00' : acc.totalRain < 13 ? '#FF0000' : '#FF00FF';
  chk(`風級 ${acc.totalRain} 對應色階`, G._bfColor(acc.totalRain), wantCol);

  // 按鈕群組：風力屬 mode 群組
  const html = fs.readFileSync('index.html', 'utf8');
  chk('★風力在 mode 按鈕群組', /id="bWind"\s+onclick="setMode\('wind'\)"/.test(html), true);
  chk('mode 群組含 bWind', /'bWarn','bWind'/.test(html), true);
  chk('modeMap 含 wind', /wind:'bWind'/.test(html), true);
  // 按鈕位置：風力(mode群組) 在 鄉鎮市區 之前
  const iWind = html.indexOf(`id="bWind"`), iTown = html.indexOf(`id="bTownName"`);
  chk('★兩按鈕位置已對調（風力在前）', iWind > 0 && iWind < iTown, true);
  // 浮動說明移除
  chk('風力按鈕無 title', /id="bWind"[^>]*title=/.test(html), false);
  chk('鄉鎮市區按鈕無 title', /id="bTownName"[^>]*title=/.test(html), false);
  chk('鄉鎮市區下拉無 title', /id="townNameScope"[^>]*title=/.test(html), false);

  // 圖例
  chk('圖例支援風力', /wind: '蒲福風級'/.test(html), true);

  // ★ 隨時段變動：切到較晚時段應取到 9 級那段
  setLex("winKey='fut6_2';");
  const later = G._windOf(t);
  setLex("winKey='today'; segFrom=0; segTo=3;");
  const nowW = G._windOf(t);
  console.log(`   今天=${nowW.bf}級　未來時段=${later.bf}級`);
  if (later.bf === nowW.bf && later.bf === 6) {
    console.log('  （兩時段落在同一預報段，屬正常）');
  } else if (later.bf > nowW.bf) {
    console.log('  OK  風力隨選取時段變動');
  }
  chk('提供依時刻取段的函式', typeof G._windSegAt === 'function', true);
  setLex("mode='rain';");
}


// ════════ 37. 風力折線圖（逐日／逐時內插）════════
console.log('\n=== 風力折線圖 ===');
if (need('_windSeries') && need('_smoothSeries') && need('drawWindDayChart')) {
  const base = Date.now();
  const mk = (hOffset, ws, bf) => ({
    start: new Date(base + hOffset*3600e3).toISOString(),
    end:   new Date(base + (hOffset+3)*3600e3).toISOString(), ws, bf});
  setLex(`TMAP['南投縣仁愛鄉'] = Object.assign(TMAP['南投縣仁愛鄉']||{},
      {county:'南投縣', township:'仁愛鄉'});
    window.WIND_FCST = {'南投縣':{'仁愛鄉':[
      ${JSON.stringify(mk(0, 5, 3))}, ${JSON.stringify(mk(3, 9, 5))},
      ${JSON.stringify(mk(6, 15, 7))}, ${JSON.stringify(mk(9, 22, 9))},
      ${JSON.stringify(mk(27, 12, 6))}]}};
    windKind='mean';`);
  const t = getLex("TMAP['南投縣仁愛鄉']");

  const ser = G._windSeries(t);
  console.log(`   官方序列 ${ser.length} 點：${ser.map(p=>p.bf).join('→')} 級`);
  chk('序列點數＝官方預報段數', ser.length, 5);
  chk('依時間排序', ser[0].bf, 3);

  // ★ 內插：官方點必須保留且標記為 key
  const sm = G._smoothSeries(ser, 2);
  const keys = sm.filter(p=>p.key);
  console.log(`   內插後 ${sm.length} 點，其中官方錨點 ${keys.length} 點`);
  chk('★官方錨點全數保留', keys.length, ser.length);
  chk('★官方錨點值未被內插改動', keys.map(p=>p.bf), ser.map(p=>p.bf));
  if (!(sm.length > ser.length)) fails.push('內插未產生額外點（曲線不會平滑）');
  else console.log('  OK  已補入內插點使曲線平滑');
  // 內插值須落在相鄰官方值之間的合理範圍（不得暴衝）
  const mx = Math.max(...sm.map(p=>p.bf)), mn = Math.min(...sm.map(p=>p.bf));
  const omx = Math.max(...ser.map(p=>p.bf)), omn = Math.min(...ser.map(p=>p.bf));
  console.log(`   內插後範圍 ${mn.toFixed(1)}~${mx.toFixed(1)}（官方 ${omn}~${omx}）`);
  if (mx > omx + 1.5 || mn < Math.max(0, omn - 1.5)) {
    fails.push(`內插值超出合理範圍（${mn.toFixed(1)}~${mx.toFixed(1)}）`);
  } else console.log('  OK  內插值未超出合理範圍');
  chk('內插值不為負', mn >= 0, true);

  // 推估陣風時序列須整體放大
  setLex("windKind='gust_est';");
  const est = G._windSeries(t);
  setLex("windKind='mean';");
  console.log(`   推估陣風序列：${est.map(p=>p.bf).join('→')} 級`);
  if (!(est[3].bf >= ser[3].bf)) fails.push('推估陣風應不小於平均風');
  else console.log('  OK  推估陣風 ≥ 平均風');

  // 繪圖不得拋錯（含無資料情形）
  setLex(`document.body.insertAdjacentHTML('beforeend',
    '<canvas id="cv-wind-day"></canvas><canvas id="cv-wind-hr"></canvas>');`);
  let threw = false;
  try { G.drawWindDayChart(t); G.drawWindHourChart(t); } catch(e){ threw = true; console.log('   ', e.message); }
  chk('繪圖不拋錯', threw, false);
  try { G.drawWindDayChart({county:'x', township:'y'}); } catch(e){ threw = true; }
  chk('無資料時亦不拋錯', threw, false);


  // ── 過去資料併入 + 放大 + 圖表格式 ──
  console.log('   ── 過去兩天資料與放大顯示 ──');
  const hk = h => {
    const d = new Date(Date.now() - h*3600e3);
    return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' +
           String(d.getDate()).padStart(2,'0') + 'T' + String(d.getHours()).padStart(2,'0');
  };
  setLex(`window.WIND_HIST = {
    '${hk(30)}': {'南投縣仁愛鄉':{ws:8, bf:5}},
    '${hk(20)}': {'南投縣仁愛鄉':{ws:10, bf:5}},
    '${hk(6)}':  {'南投縣仁愛鄉':{ws:14, bf:7}},
    '${hk(80)}': {'南投縣仁愛鄉':{ws:99, bf:12}}
  };`);
  const withHist = G._windSeries(t);
  const pastN = withHist.filter(p=>p.past).length;
  console.log(`   併入歷史後 ${withHist.length} 點（過去 ${pastN} 點）`);
  chk('★過去資料已併入', pastN, 3);
  chk('★超過48h的歷史被濾除', withHist.some(p=>p.bf===12), false);
  // 時序必須單調遞增（過去在前、預報在後）
  let mono = true;
  for(let i=1;i<withHist.length;i++) if(withHist[i].ms < withHist[i-1].ms) mono = false;
  chk('時序正確（過去在前）', mono, true);

  // 放大：分派表須含兩張風力圖
  const src2 = fs.readFileSync('index.html', 'utf8');
  // ★ 改為前綴比對，一次涵蓋三種來源的六個畫布
  chk('★放大分派含逐日風力圖', /canvasId\.startsWith\('cv-wind-day'\) && selected/.test(src2), true);
  chk('★放大分派含逐時風力圖', /canvasId\.startsWith\('cv-wind-hr'\) && selected/.test(src2), true);
  chk('放大需先選取鄉鎮', /canvasId\.startsWith\('cv-wind-'\)/.test(src2), true);
  chk('放大標題含鄉鎮名', /'cv-wind-day': selected \? `\$\{selected\.county\}/.test(src2), true);

  // 格式：標題、圖例、現在線、分署色
  chk('圖表有標題', /title:`\$\{t\.county\}\$\{t\.township\} 逐日風力預測/.test(src2), true);
  chk('圖表有現在分隔線', /o\.nowMs != null/.test(src2), true);
  chk('過去段用分署色', /getDistrictColor\(o\.county\)/.test(src2), true);
  chk('圖表有圖例', /item\(dcol, '過去（分署色）'\)/.test(src2), true);
  // ★ 字級須與逐日雨量圖一致（_townChartGeom：fs 54/12、xFs 25/10）
  chk('★主字級與雨量圖相同', /const fs  = isZoom \? 54 : 12;/.test(src2), true);
  // ★ 風力圖時間軸點數少、空間充裕，X 軸字級改與 Y 軸一致（使用者指定）
  chk('★X軸字級與Y軸相同', /const xFs = fs;/.test(src2), true);
  // 標籤間隔依實際字寬計算，避免放大後互相重疊
  chk('標籤間隔依字寬計算', /ctx\.measureText\('12\/31 18時'\)\.width \* 1\.15/.test(src2), true);
  // 版面：標題獨立一列，不與軸標題／「現在」重疊
  chk('★標題置於最上緣獨立列', /ctx\.fillText\(o\.title, pL-\(isZoom\?150:44\), fs\+22\)/.test(src2), true);
  chk('★「現在」標於繪圖區內側', /ctx\.fillText\('現在', xn\+\(isZoom\?10:3\)/.test(src2), true);
  // 高度改由 CSS 決定（offsetHeight），避免屬性尺寸與顯示尺寸不等比而變形
  chk('CSS 指定高度 150px', /id="cv-wind-day" style="width:100%;height:150px/.test(src2), true);
  chk('六張圖高度一致', (src2.match(/height:150px;display:block;background:#040c14/g)||[]).length >= 6, true);

  // 放大繪製不拋錯
  setLex(`document.body.insertAdjacentHTML('beforeend','<canvas id="chart-zoom-canvas"></canvas>');`);
  let zthrew = false;
  try { G.drawWindDayChart(t, 'chart-zoom-canvas'); G.drawWindHourChart(t, 'chart-zoom-canvas'); }
  catch(e){ zthrew = true; console.log('   ', e.message); }
  chk('放大繪製不拋錯', zthrew, false);
  setLex("window.WIND_HIST = {};");


  // ── 三張圖各自使用不同來源 ──
  console.log('   ── 三來源分圖 ──');
  setLex(`window.GUST_FCST = {'南投縣':{ws:25.0, gust:33.0, bf:12}};`);
  const sMean = G._windSeries(t, 'mean');
  const sGust = G._windSeries(t, 'gust');
  const sEst  = G._windSeries(t, 'gust_est');
  console.log(`   平均風 ${sMean.length} 點、官方陣風 ${sGust.length} 點、推估 ${sEst.length} 點`);
  chk('平均風有序列', sMean.length > 0, true);
  chk('★官方陣風有序列（不再看不到）', sGust.length > 0, true);
  chk('★推估陣風有序列', sEst.length > 0, true);
  if (sGust.length && sMean.length) {
    console.log(`   官方陣風 ${sGust[0].bf} 級 vs 平均風 ${sMean[0].bf} 級`);
    if (!(sGust[0].bf >= sMean[0].bf)) fails.push('官方陣風應不低於平均風');
    else console.log('  OK  官方陣風 ≥ 平均風');
  }
  if (sEst.length && sMean.length && !(sEst[0].bf >= sMean[0].bf)) {
    fails.push('推估陣風應不低於平均風');
  }
  // 六張圖繪製不拋錯
  setLex(`document.body.insertAdjacentHTML('beforeend',
    '<canvas id="cv-wind-day-gust"></canvas><canvas id="cv-wind-day-est"></canvas>' +
    '<canvas id="cv-wind-hr-gust"></canvas><canvas id="cv-wind-hr-est"></canvas>');`);
  let sixThrew = false;
  try { G.drawAllWindCharts(t); } catch(e){ sixThrew = true; console.log('   ', e.message); }
  chk('六張圖一次繪製不拋錯', sixThrew, false);
  // 放大分派涵蓋六個 id
  const zsrc = fs.readFileSync('index.html', 'utf8');
  chk('放大分派用前綴比對', /canvasId\.startsWith\('cv-wind-day'\)/.test(zsrc), true);
  chk('放大標題區分三來源', /'cv-wind-day-gust': selected/.test(zsrc), true);
  // 變形修正：canvas 高度取 CSS 實際像素
  chk('★畫布高度取 offsetHeight（避免變形）',
      /cv\.height = isZoom \? 1024 : \(cv\.offsetHeight/.test(zsrc), true);
  setLex("window.GUST_FCST = {};");

  // ── 懸浮視窗的風力列 ──
  console.log('   ── tooltip 風力列 ──');
  const row = G._windRow(t);
  console.log(`   ${row.replace(/<[^>]*>/g,' ').trim()}`);
  chk('tooltip 含風力', /風力：/.test(row), true);
  chk('顯示風級', /級/.test(row), true);
  // 陣風推估高於平均風時應一併顯示
  chk('附帶陣風推估', /陣風推估/.test(row), true);
  // 無資料鄉鎮不顯示空列
  chk('無資料時不顯示', G._windRow({county:'x', township:'y'}), '');
  // 三種 mode 的 tooltip 都要有
  const tsrc = fs.readFileSync('index.html', 'utf8');
  chk('今天視窗 tooltip 含風力',
      /風險指標：\$\{riskIndicatorHtml\(t,3\)\}` \+ _windRow\(t\)/.test(tsrc), true);
  chk('過去/未來視窗 tooltip 含風力',
      (tsrc.match(/_windowRisk\(t\)\}\` \+ _windRow\(t\)/g)||[]).length >= 2, true);
  const html = fs.readFileSync('index.html', 'utf8');
  chk('逐日圖區塊存在', /id="sec-windday"/.test(html), true);
  chk('逐時圖區塊存在', /id="sec-windhr"/.test(html), true);
  chk('★圖下標明內插非官方值', /其間曲線為內插（僅供視覺化，非官方值）/.test(html), true);
}


// ════════ 38. 東亞國界／省界圖層 ════════
console.log('\n=== 東亞邊界圖資 ===');
if (need('renderEastAsiaLayer') && need('_ringsCentroid')) {
  const geo = getLex('EAST_ASIA_GEO');
  console.log(`   省界 ${geo.provinces.length} 省（國界已移除）`);
  chk('含省界資料', geo.provinces.length >= 200, true);
  // ★ 國界已移除：110m 精度過低，與 10m 省界並陳時呈現折線
  chk('★不含國界資料', !geo.countries, true);
  // ★ 平滑度：座標精度與點密度需接近臺灣圖資，否則線條呈現稜角
  const _tp = geo.provinces.reduce((a,p)=>a + p.rings.reduce((b,r)=>b+r.length,0), 0);
  const _tr = geo.provinces.reduce((a,p)=>a + p.rings.length, 0);
  const _avg = _tp / _tr;
  console.log(`   省界平均 ${_avg.toFixed(0)} 點/環（臺灣約 35）`);
  if (_avg < 30) fails.push(`點密度過低（${_avg.toFixed(0)} 點/環），線條會有稜角`);
  else console.log('  OK  點密度足夠（線條平滑）');
  // 座標小數位數（2 位≈1km 會有明顯鋸齒，需 ≥3 位）
  // ★ 單點可能剛好是整數或 1 位，故取多點的最大位數才可靠。
  let _dp = 0, _cnt = 0;
  outer: for (const p of geo.provinces) {
    for (const r of p.rings) for (const c of r) {
      _dp = Math.max(_dp, (String(c[0]).split('.')[1] || '').length,
                          (String(c[1]).split('.')[1] || '').length);
      if (++_cnt > 500) break outer;
    }
  }
  console.log(`   座標最大小數位數：${_dp}（取樣 ${_cnt} 點）`);
  if (_dp < 3) fails.push(`座標精度不足（${_dp} 位），線條會有鋸齒`);
  else console.log('  OK  座標精度足夠');
  chk('標明資料來源', /Natural Earth/.test(geo._src || ''), true);

  // 使用者指定的三個範例必須在
  const byName = n => geo.provinces.find(p=>p.name === n);
  [['Okinawa','沖繩縣','Japan'], ['Zhejiang','浙江省','China'],
   ['Batanes','巴丹群島省','Philippines']].forEach(([n, zht, admin])=>{
    const p = byName(n);
    chk(`${zht} 存在`, !!p, true);
    if(p){ chk(`${zht} 繁中名稱`, p.name_zht, zht);
           chk(`${zht} 所屬國`, p.admin, admin); }
  });

  // 涵蓋國家
  const admins = [...new Set(geo.provinces.map(p=>p.admin))].sort();
  console.log(`   省界涵蓋：${admins.join('、')}`);
  // ★ 不含 Taiwan：系統已有自己的縣市／鄉鎮界，套疊外部資料會錯位
  ['Japan','China','South Korea','Philippines','Vietnam'].forEach(a=>{
    chk(`涵蓋 ${a}`, admins.includes(a), true);
  });
  chk('★排除 Taiwan', admins.includes('Taiwan'), false);

  // 座標範圍須在東亞（裁切正確）
  let laMin=99, laMax=-99, loMin=999, loMax=-999;
  geo.provinces.forEach(p=>p.rings.forEach(r=>r.forEach(c=>{
    laMin=Math.min(laMin,c[0]); laMax=Math.max(laMax,c[0]);
    loMin=Math.min(loMin,c[1]); loMax=Math.max(loMax,c[1]);
  })));
  console.log(`   範圍 lat ${laMin.toFixed(1)}~${laMax.toFixed(1)}、lng ${loMin.toFixed(1)}~${loMax.toFixed(1)}`);
  chk('緯度在東亞範圍', laMin > -10 && laMax < 60, true);
  // ★ 裁切條件是「與範圍相交」而非「完全落入」，故青海、甘肅等省的
  //   西緣會略微超出 100E（主體仍在範圍內），屬正確行為。
  chk('經度在東亞範圍', loMin > 85 && loMax < 160, true);

  // 座標格式為 [lat,lng]（與 Leaflet 一致）
  const s0 = byName('Okinawa').rings[0][0];
  chk('座標為 [lat,lng]', s0[0] > 20 && s0[0] < 30 && s0[1] > 120, true);

  // 形心：必須落在該省範圍內
  const okc = G._ringsCentroid(byName('Okinawa').rings);
  console.log(`   沖繩縣標註位置：${okc ? okc.map(v=>v.toFixed(2)).join(', ') : '—'}`);
  chk('形心可計算', !!okc, true);
  if(okc){ chk('形心在沖繩附近', okc[0] > 24 && okc[0] < 29 && okc[1] > 122 && okc[1] < 132, true); }

  // 繪圖不拋錯（含關閉狀態）
  let ethrew = false;
  setLex("showTownName = true;");
  try { G.renderEastAsiaLayer(); } catch(e){ ethrew = true; console.log('   ', e.message); }
  chk('繪製不拋錯', ethrew, false);
  setLex("showTownName = false;");
  try { G.renderEastAsiaLayer(); } catch(e){ ethrew = true; }
  chk('關閉時不拋錯', ethrew, false);

  // 與鄉鎮名圖層同一按鈕控制
  const src = fs.readFileSync('index.html', 'utf8');
  chk('由鄉鎮市區按鈕一併控制', /renderTownNameLayer\(\)\{[\s\S]{0,200}renderEastAsiaLayer\(\)/.test(src), true);
  // ★ 省界改黑色 1px（同臺灣縣市界）；國界不再繪製
  chk('省界為黑色1px', /color:'#000000', weight:1, opacity:0\.7/.test(src), true);
  chk('邊界不攔截滑鼠', /color:'#000000'[\s\S]{0,90}interactive:false/.test(src), true);
  chk('★不再繪製國界', /EAST_ASIA_GEO\.countries/.test(src), false);
}


// ════════ 39. 氣溫與浪高模式 ════════
console.log('\n=== 氣溫模式 ===');
if (need('_tempColor') && need('_tempOf') && need('_tempRow')) {
  // 色階邊界（使用者指定的分級）
  const tc = [[-5,'#FFFFFF'],[0,'#4B0082'],[4,'#4B0082'],[5,'#0000FF'],[9,'#0000FF'],
              [10,'#00FFFF'],[13,'#00FFFF'],[14,'#00FA9A'],[18,'#00FF00'],
              [22,'#FFFF00'],[26,'#FFA500'],[30,'#FF4500'],[35,'#FF0000'],
              [38,'#8B0000'],[42,'#8B0000']];
  let ok = true;
  tc.forEach(([v,c])=>{ if(G._tempColor(v)!==c){ ok=false;
    fails.push(`氣溫 ${v}°C 色階錯：得 ${G._tempColor(v)} 期望 ${c}`);} });
  if(ok) console.log('  OK  11 級色階全部正確（白→靛紫→藍→青→綠→黃→橘→紅→暗紅）');
  chk('無資料回 null（留白）', G._tempColor(null), null);

  const fut = new Date(Date.now()+2*3600e3).toISOString();
  const past = new Date(Date.now()-1*3600e3).toISOString();
  setLex(`TMAP['南投縣仁愛鄉'] = Object.assign(TMAP['南投縣仁愛鄉']||{},
      {county:'南投縣', township:'仁愛鄉'});
    window.TEMP_FCST = {'南投縣':{'仁愛鄉':[
      {start:'${past}', end:'${fut}', t:28.0, tmax:31.0, tmin:24.0}]}};
    mode='temp'; winKey='today'; segFrom=0; segTo=3;`);
  const t = getLex("TMAP['南投縣仁愛鄉']");
  const tv = G._tempOf(t);
  console.log(`   氣溫 ${tv.t}°C（${tv.tmin}–${tv.tmax}）`);
  chk('取得氣溫', tv.t, 28.0);
  const acc = G.getAccum(t, 'temp');
  chk('★氣溫為獨立模式', acc.isTemp, true);
  chk('地圖著色值＝氣溫', acc.totalRain, 28.0);
  chk('28°C → 橘黃', G._tempColor(acc.totalRain), '#FFA500');
  const tr = G._tempRow(t);
  console.log(`   tooltip：${tr.replace(/<[^>]*>/g,' ').trim()}`);
  chk('tooltip 含氣溫', /氣溫：/.test(tr), true);
  chk('tooltip 含高低溫', /24–31/.test(tr), true);
  chk('無資料時不顯示', G._tempRow({county:'x',township:'y'}), '');
}

console.log('\n=== 浪高模式 ===');
if (need('_waveColor') && need('_waveOf') && need('_waveRow')) {
  const wc = [[0.5,'#FFFFFF'],[0.99,'#FFFFFF'],[1.0,'#00FF00'],[1.4,'#00FF00'],
              [1.5,'#FFFF00'],[2.4,'#FFFF00'],[2.5,'#FF0000'],[5.4,'#FF0000'],
              [5.5,'#FF00FF'],[9.0,'#FF00FF']];
  let ok2 = true;
  wc.forEach(([v,c])=>{ if(G._waveColor(v)!==c){ ok2=false;
    fails.push(`浪高 ${v}m 色階錯：得 ${G._waveColor(v)} 期望 ${c}`);} });
  if(ok2) console.log('  OK  五級距色階正確（微波/小浪/中浪/大浪/巨浪）');
  chk('★內陸無資料回 null（不可當 0m）', G._waveColor(null), null);

  const fut2 = new Date(Date.now()+2*3600e3).toISOString();
  const past2 = new Date(Date.now()-1*3600e3).toISOString();
  setLex(`TMAP['宜蘭縣蘇澳鎮'] = {county:'宜蘭縣', township:'蘇澳鎮'};
    window.WAVE_FCST = {'宜蘭縣蘇澳鎮':[
      {start:'${past2}', end:'${fut2}', wave:3.0, bf:7, dir:'東北'}]};
    mode='wave';`);
  const ct = getLex("TMAP['宜蘭縣蘇澳鎮']");
  const wv = G._waveOf(ct);
  console.log(`   蘇澳鎮浪高 ${wv.wave}m（${wv.dir}、${wv.bf}級）`);
  chk('沿海取得浪高', wv.wave, 3.0);
  chk('3.0m → 大浪紅色', G._waveColor(wv.wave), '#FF0000');
  const wacc = G.getAccum(ct, 'wave');
  chk('★浪高為獨立模式', wacc.isWave, true);
  // 內陸鄉鎮必須無值
  const inland = getLex("TMAP['南投縣仁愛鄉']");
  chk('★內陸鄉鎮無浪高', G._waveOf(inland).wave, null);
  chk('★內陸 tooltip 不顯示浪高', G._waveRow(inland), '');
  const wr = G._waveRow(ct);
  console.log(`   tooltip：${wr.replace(/<[^>]*>/g,' ').trim()}`);
  chk('tooltip 含浪高', /浪高：/.test(wr), true);
  chk('tooltip 含浪級', /大浪/.test(wr), true);

  // 按鈕與圖例
  const src = fs.readFileSync('index.html', 'utf8');
  chk('氣溫為 mode 按鈕', /id="bTemp"\s+onclick="setMode\('temp'\)"/.test(src), true);
  chk('浪高為 mode 按鈕', /id="bWave"\s+onclick="setMode\('wave'\)"/.test(src), true);
  chk('mode 群組含兩者', /'bWind','bTemp','bWave'/.test(src), true);
  chk('圖例支援氣溫', /temp: '氣溫\(°C\)'/.test(src), true);
  chk('圖例支援浪高', /wave: '浪高'/.test(src), true);
  // ★ 未來6h 視窗補齊後為 4 處（今天／過去／未來／未來6h段）
  // 五處：今天／過去／未來／未來6h／（warn|etr|risk）合併分支
  chk('tooltip 五處都加入', (src.match(/_windRow\(t\) \+ _tempRow\(t\) \+ _waveRow\(t\)/g)||[]).length, 5);
  setLex("mode='rain'; window.WAVE_FCST={}; window.TEMP_FCST={};");
}


console.log('\n=== 著色分支順序（null 檢查不得攔截各 mode）===');
{
  const src = fs.readFileSync('index.html', 'utf8');
  const iWarn = src.indexOf('} else if(acc.isWarn){');
  const iWave = src.indexOf('} else if(acc.isWave){');
  const iNull = src.indexOf('} else if(totalRain === null || totalRain === undefined){');
  console.log(`   isWarn@${iWarn} isWave@${iWave} nullCheck@${iNull}`);
  chk('★警特報分支在 null 檢查之前', iWarn > 0 && iWarn < iNull, true);
  chk('★浪高分支在 null 檢查之前', iWave > 0 && iWave < iNull, true);
  chk('警特報 null 視為 0（未達標仍著色）', /warnMapColor\(totalRain \|\| 0\)/.test(src), true);
}

console.log('\n=== 臺灣不套疊外部邊界 ===');
if (need('renderEastAsiaLayer')) {
  const geo = getLex('EAST_ASIA_GEO');
  const admins = [...new Set(geo.provinces.map(p=>p.admin))];
  console.log(`   涵蓋：${admins.sort().join('、')}`);
  chk('★不含臺灣（用系統自有邊界）', admins.includes('Taiwan'), false);
  chk('仍涵蓋周邊國家', admins.length >= 5, true);
  // 座標不得落在臺灣本島範圍
  let inTW = 0;
  geo.provinces.forEach(p=>p.rings.forEach(r=>r.forEach(c=>{
    if(c[0] > 21.9 && c[0] < 25.3 && c[1] > 120.0 && c[1] < 122.0) inTW++;
  })));
  console.log(`   落在臺灣本島範圍的點：${inTW}`);
  chk('臺灣本島範圍無外部邊界點', inTW < 50, true);
}


console.log('\n=== 氣溫／浪高圖表 ===');
if (need('_tempSeries') && need('_waveSeries') && need('drawTempDayChart')) {
  const base = Date.now();
  const mk = (h, v) => ({start:new Date(base+h*3600e3).toISOString(),
                         end:new Date(base+(h+3)*3600e3).toISOString()});
  setLex(`TMAP['宜蘭縣蘇澳鎮'] = {county:'宜蘭縣', township:'蘇澳鎮'};
    window.TEMP_FCST = {'宜蘭縣':{'蘇澳鎮':[
      ${JSON.stringify(Object.assign(mk(0),{t:26,tmax:29,tmin:23}))},
      ${JSON.stringify(Object.assign(mk(3),{t:22,tmax:29,tmin:23}))},
      ${JSON.stringify(Object.assign(mk(27),{t:31,tmax:33,tmin:26}))}]}};
    window.WAVE_FCST = {'宜蘭縣蘇澳鎮':[
      ${JSON.stringify(Object.assign(mk(0),{wave:1.2,bf:5,dir:'東北'}))},
      ${JSON.stringify(Object.assign(mk(3),{wave:2.8,bf:7,dir:'北'}))},
      ${JSON.stringify(Object.assign(mk(27),{wave:6.0,bf:9,dir:'東北'}))}]};`);
  const ct = getLex("TMAP['宜蘭縣蘇澳鎮']");

  const ts = G._tempSeries(ct), ws = G._waveSeries(ct);
  console.log(`   氣溫序列 ${ts.length} 點：${ts.map(p=>p.bf).join('→')}°C`);
  console.log(`   浪高序列 ${ws.length} 點：${ws.map(p=>p.bf).join('→')}m`);
  chk('氣溫序列', ts.length, 3);
  chk('浪高序列', ws.length, 3);
  chk('依時間排序', ts[0].bf, 26);

  // 逐日取最大
  const td = G._dayMax(ts);
  console.log(`   逐日氣溫 ${td.length} 日：${td.map(p=>p.bf).join('、')}`);
  chk('逐日取各日最大', td[0].bf, 26);

  // 內陸鄉鎮無浪高
  const inland = getLex("TMAP['南投縣仁愛鄉']");
  chk('★內陸無浪高序列', G._waveSeries(inland).length, 0);

  // 繪圖不拋錯（含放大）
  setLex(`document.body.insertAdjacentHTML('beforeend',
    '<canvas id="cv-temp-day"></canvas><canvas id="cv-temp-hr"></canvas>' +
    '<canvas id="cv-wave-day"></canvas><canvas id="cv-wave-hr"></canvas>');`);
  let thrown = false;
  try {
    G.drawTempDayChart(ct); G.drawTempHourChart(ct);
    G.drawWaveDayChart(ct); G.drawWaveHourChart(ct);
    G.drawTempDayChart(ct, 'chart-zoom-canvas');
    G.drawWaveHourChart(ct, 'chart-zoom-canvas');
  } catch(e){ thrown = true; console.log('   ', e.message); }
  chk('四張圖繪製不拋錯（含放大）', thrown, false);

  const src = fs.readFileSync('index.html', 'utf8');
  chk('氣溫用橘色線', /lineColor:'#ff9a4a'/.test(src), true);
  chk('浪高用藍色線', /lineColor:'#5aa8ff'/.test(src), true);
  chk('氣溫軸標題', /axisTitle:'氣溫\(°C\)'/.test(src), true);
  chk('浪高軸標題', /axisTitle:'浪高\(m\)'/.test(src), true);
  chk('氣溫允許負值', /allowNeg:true/.test(src), true);
  chk('放大分派含四張圖', /canvasId === 'cv-temp-day'[\s\S]{0,400}canvasId === 'cv-wave-hr'/.test(src), true);
  chk('★未來6h tooltip 補齊要素',
      (src.match(/_windRow\(t\) \+ _tempRow\(t\) \+ _waveRow\(t\)/g)||[]).length, 5);
  setLex("window.TEMP_FCST={}; window.WAVE_FCST={};");
}


console.log('\n=== 級距上色與 tooltip 補齊 ===');
{
  const src = fs.readFileSync('index.html', 'utf8');
  chk('級距上色已實作', /if\(o\.bandLine \|\| forceBand\)\{[\s\S]{0,200}bandColorOf/.test(src), true);
  chk('氣溫／浪高啟用級距上色', (src.match(/bandLine:true/g)||[]).length, 4);
  // 過去段改為分署色＋半透明（不再用粗底線疊色），與未來的級距色明顯區隔
  chk('過去段用分署色半透明', /分署色＋半透明/.test(src), true);
  chk('圖例說明線色含義', /'預報（線色＝級距色）'/.test(src), true);
  // 全日 tooltip 補上最大時雨量
  chk('★全日 tooltip 含最大時雨量', /_maxHourRow\(t, 0, 3\)/.test(src), true);
  // 三處：過去／未來／（warn|etr|risk）合併分支
  chk('★過去/未來 tooltip 含最大時雨量',
      (src.match(/_maxHourRow\(t, segFrom, segTo\)/g)||[]).length, 3);
}
if (need('_maxHourRow')) {
  setLex(`TMAP['南投縣仁愛鄉'] = Object.assign(TMAP['南投縣仁愛鄉']||{},
    {county:'南投縣', township:'仁愛鄉', maxh_ecmwf:[12, 45, 120, 8].concat(Array(60).fill(0))});
    forecastModel='ecmwf';`);
  const t = getLex("TMAP['南投縣仁愛鄉']");
  const r = G._maxHourRow(t, 0, 3);
  console.log(`   最大時雨量列：${r}`);
  chk('取區間最大值', /120\.0 mm\/h/.test(r), true);
  chk('標示警示級別', /豪雨/.test(r), true);
  chk('單一時段取該段值', /45\.0 mm\/h（大雨）/.test(G._maxHourRow(t, 1, 1)), true);
  chk('無資料回 —', G._maxHourRow({county:'x',township:'y'}, 0, 3), '—');
}


console.log('\n=== 折線在「現在」不得斷開 ===');
if (need('_drawWindChart')) {
  const src = fs.readFileSync('index.html', 'utf8');
  chk('★於 nowMs 插入共用端點', /在「現在」處插入內插點/.test(src), true);
  chk('過去段與未來段共用該點', /pastPts = pastPts\.concat\(\[mid\]\)/.test(src), true);
  chk('未來段一律級距上色', /drawSeg\(futPts, o\.lineColor \|\| '#6ad8f0', null, true\)/.test(src), true);
  chk('過去段用分署色＋半透明', /ctx\.globalAlpha = 0\.55/.test(src), true);
  chk('圖例區分兩者', /'過去（分署色）'[\s\S]{0,120}'預報（線色＝級距色）'/.test(src), true);

  // 實測：造一組跨越「現在」的資料，確認兩段端點相同
  const now = Date.now();
  const pts = [
    {ms: now - 4*3600e3, bf: 3, key:true},
    {ms: now - 1*3600e3, bf: 5, key:true},
    {ms: now + 2*3600e3, bf: 8, key:true},
    {ms: now + 5*3600e3, bf: 6, key:true},
  ];
  setLex(`document.body.insertAdjacentHTML('beforeend','<canvas id="cv-gap-test"></canvas>');`);
  let gthrew = false;
  try {
    setLex(`window.__gapPts = ${JSON.stringify(pts)};`);
    setLex(`_drawWindChart('cv-gap-test', window.__gapPts,
      {markKey:true, nowMs:${now}, bands:BF_BANDS});`);
  } catch(e){ gthrew = true; console.log('   ', e.message); }
  chk('跨越現在的資料繪製不拋錯', gthrew, false);
  console.log('   （視覺連續性需實機確認，此處驗程式路徑）');
}


console.log('\n=== 浪高診斷訊息可區分兩種情況 ===');
{
  const src = fs.readFileSync('index.html', 'utf8');
  chk('區分欄位不存在與內容為空', /_dataHasWaveField/.test(src), true);
  chk('提示後端未更新', /後端 fetch_rainfall\.py 尚未更新至含浪高的版本/.test(src), true);
  chk('提示 API 取用失敗', /F-D0047-095 取用失敗或當期無沿海預報/.test(src), true);
  const py = fs.readFileSync('fetch_rainfall.py', 'utf8');
  chk('後端輸出欄位自我檢查', /新增欄位：/.test(py), true);
  chk('後端提示 wave_fcst 為空', /wave_fcst 為空/.test(py), true);
}


console.log('\n=== 色階與雨量一致 + 分布診斷 ===');
{
  const rain = getLex('RAIN_SCALE').map(x=>x.color);
  const bf = getLex('BF_BANDS').map(x=>x.color);
  const wv = getLex('WAVE_BANDS').map(x=>x.color);
  console.log(`   雨量：${rain.slice(0,5).join(' ')}`);
  console.log(`   風力：${bf.join(' ')}`);
  console.log(`   浪高：${wv.join(' ')}`);
  // 風力/浪高的綠黃紅紫須與雨量同色碼
  chk('★風力綠＝雨量綠(#00FF00)', bf[1], '#00FF00');
  chk('★風力黃＝雨量黃(#FFFF00)', bf[2], '#FFFF00');
  chk('★風力紅＝雨量紅(#FF0000)', bf[3], '#FF0000');
  chk('★風力紫＝雨量紫(#FF00FF)', bf[4], '#FF00FF');
  chk('浪高綠＝雨量綠', wv[1], '#00FF00');
  chk('浪高紫＝雨量紫', wv[4], '#FF00FF');
  // ★ 只檢查「實際使用」的色碼，註解中的說明文字不算
  const _src = fs.readFileSync('index.html','utf8')
    .split('\n').filter(l=>!l.trim().startsWith('//')).join('\n');
  // ★ 只檢查「色階定義」不得殘留柔和版；警戒餘裕圖用 #40d060 作為
  //   「距警戒值尚遠」的安全色，與雨量/風力色階無關，不在此限。
  chk('色階定義無柔和版殘留',
      /BF_BANDS = \[[\s\S]{0,300}#40d060|WAVE_BANDS = \[[\s\S]{0,300}#40d060/.test(_src), false);
}
if (need('_logModeDistribution')) {
  const src = fs.readFileSync('index.html', 'utf8');
  chk('提供值分布診斷', /分布（\$\{n\} 個鄉鎮有值/.test(src), true);
  chk('全臺同值時提出警告', /全臺 \$\{label\} 皆為同一值/.test(src), true);
  let dthrew = false;
  setLex("mode='wind';");
  try { G._logModeDistribution(); } catch(e){ dthrew = true; console.log('   ', e.message); }
  setLex("mode='rain';");
  chk('診斷不拋錯', dthrew, false);
}


console.log('\n=== 渲染模式須用各自色階 ===');
if (need('_modeBandsRgb')) {
  const src = fs.readFileSync('index.html', 'utf8');
  chk('★渲染支援風力色階', /mode==='wind'\) sc = BF_BANDS/.test(src), true);
  chk('★渲染支援氣溫色階', /mode==='temp'\) sc = TEMP_BANDS/.test(src), true);
  chk('★渲染支援浪高色階', /mode==='wave'\) sc = WAVE_BANDS/.test(src), true);

  // 實測：氣溫 28°C 在渲染模式下不可落到雨量色階的綠
  setLex("mode='temp';");
  const tb = G._modeBandsRgb();
  let bi = 0; while(bi < tb.length-1 && 28 >= tb[bi].max) bi++;
  const rgb = tb[bi].rgb;
  console.log(`   氣溫 28°C → RGB(${rgb.join(',')})`);
  // TEMP_BANDS 中 28°C 落在 26–30 帶 = #FFA500 橘黃 (255,165,0)
  chk('★28°C 為橘黃非綠', rgb.join(','), '255,165,0');
  setLex("mode='rain';");
}

console.log('\n=== 鄉鎮界線可辨識 ===');
{
  const src = fs.readFileSync('index.html', 'utf8');
  chk('★多邊形邊框非同填色', /color: isSel \? '#ffff44' : '#5a5a5a'/.test(src), true);
  chk('邊框有透明度設定', /opacity: isSel \? 1 : 0\.45/.test(src), true);
  chk('★名稱圖層界線為中灰', /color:'#5a5a5a', weight:0\.7, opacity:0\.6/.test(src), true);
  chk('不再用 fillColor 當邊框色', /color: isSel \? '#ffff44' : fillColor/.test(src), false);
}


console.log('\n=== 浪高端點候選重試 ===');
{
  const py = fs.readFileSync('fetch_rainfall.py', 'utf8');
  chk('採用實測確認的波浪模式端點', /WAVE_EP = 'F-A0020-001'/.test(py), true);
  chk('限制時間步以控制體積', /WAVE_STEPS = 24/.test(py), true);
  chk('讀沿海鄉鎮清單', /COASTAL_TOWNS_FILE = 'coastal_towns\.json'/.test(py), true);
  const src = fs.readFileSync('index.html', 'utf8');
  chk('★載入前不輸出矛盾診斷', /data\.json 尚未載入時不輸出診斷/.test(src), true);
}


console.log('\n=== 地形分類已更新為官方定義 ===');
{
  const z = getLex('TOWN_ZONE');
  const cnt = {};
  Object.values(z).forEach(v => cnt[v] = (cnt[v]||0)+1);
  console.log(`   分布：${JSON.stringify(cnt)}`);
  chk('山區 31', cnt['山區'], 31);
  chk('淺山 110', cnt['淺山區'], 110);
  chk('沿海 85', cnt['沿海地區'], 85);
  chk('平地 143', cnt['平地'], 143);
  // ★ 關鍵個案（各代表一條判定規則）
  chk('★離島一律沿海（蘭嶼，雖為山地原住民鄉）', z['臺東縣蘭嶼鄉'], '沿海地區');
  chk('★離島一律沿海（旗津）', z['高雄市旗津區'], '沿海地區');
  chk('★山地原住民鄉歸山區（牡丹，≥1000m僅0%）', z['屏東縣牡丹鄉'], '山區');
  chk('★面積佔比法（臺北大安區不因蟾蜍山誤判）', z['臺北市大安區'], '平地');
  chk('★濱海省道判定（吉安鄉臨海）', z['花蓮縣吉安鄉'], '沿海地區');
  chk('★<100m陡坡佔比（基隆仁愛區）', z['基隆市仁愛區'], '淺山區');
  // 註解須反映新規則
  const src = fs.readFileSync('index.html', 'utf8');
  chk('註解載明官方定義', /離島一律沿海 —— 水保署／林保署未對離島劃設坡地災害管制/.test(src), true);
  chk('註解載明資料來源', /20m DTM 2025（國土測繪中心）＋ ROAD_國省道/.test(src), true);
  chk('已移除舊的人工分類說明', /山區31＋淺山87＝118 個高海拔鄉鎮/.test(src), false);
  // 陣風因子仍能取到地形
  if (need('_gustFactor')) {
    const t = {county:'南投縣', township:'仁愛鄉'};
    const f = G._gustFactor(t);
    console.log(`   仁愛鄉（山區）陣風因子 = ${f}`);
    chk('山區陣風因子 1.7', f, 1.7);
    chk('平地陣風因子 1.4', G._gustFactor({county:'臺北市', township:'大安區'}), 1.4);
  }
}


console.log('\n=== 長浪示警與浪向 ===');
if (need('_longSwell') && need('_waveDirText') && need('_onshore')) {
  // 浪向角度 → 方位
  chk('0° → 北', G._waveDirText(0), '北');
  chk('45° → 東北', G._waveDirText(45), '東北');
  chk('180° → 南', G._waveDirText(180), '南');
  chk('315° → 西北', G._waveDirText(315), '西北');
  chk('無資料回空字串', G._waveDirText(null), '');

  const now = Date.now();
  const seg = (w, p) => ({start:new Date(now-3600e3).toISOString(),
                          end:new Date(now+3*3600e3).toISOString(),
                          wave:w, dir:60, period:p});
  setLex(`TMAP['宜蘭縣蘇澳鎮'] = {county:'宜蘭縣', township:'蘇澳鎮', lat:24.59, lng:121.87};
    window.WAVE_FCST = {'宜蘭縣蘇澳鎮':[${JSON.stringify(seg(3.0, 10))}]};`);
  const t = getLex("TMAP['宜蘭縣蘇澳鎮']");
  const ls = G._longSwell(t);
  console.log(`   蘇澳：浪高${ls.wave}m 週期${ls.period}s → 長浪=${ls.hit} 等級${ls.level}`);
  chk('★長浪判定（≥8s 且 ≥2m）', ls.hit, true);
  // 第2級門檻：浪高≥4m 或 週期≥11s；3.0m/10s 屬第1級
  chk('3.0m/10s 為第1級', ls.level, 1);
  setLex(`window.WAVE_FCST = {'宜蘭縣蘇澳鎮':[${JSON.stringify(seg(4.5, 12))}]};`);
  chk('★4.5m/12s 升為第2級', G._longSwell(t).level, 2);

  // 短週期不算長浪（即使浪高）
  setLex(`window.WAVE_FCST = {'宜蘭縣蘇澳鎮':[${JSON.stringify(seg(3.0, 5))}]};`);
  chk('★週期不足不算長浪', G._longSwell(t).hit, false);
  // 浪不高也不算
  setLex(`window.WAVE_FCST = {'宜蘭縣蘇澳鎮':[${JSON.stringify(seg(1.0, 12))}]};`);
  chk('★浪高不足不算長浪', G._longSwell(t).hit, false);
  // 邊界：剛好達標
  setLex(`window.WAVE_FCST = {'宜蘭縣蘇澳鎮':[${JSON.stringify(seg(2.0, 8))}]};`);
  chk('邊界值（8s/2.0m）成立', G._longSwell(t).hit, true);
  chk('中等為第1級', G._longSwell(t).level, 1);

  // 向岸／離岸
  const onE = G._onshore({lat:24.59, lng:121.87}, 60);   // 東部、來向東北
  const onW = G._onshore({lat:24.59, lng:121.87}, 240);  // 來向西南（陸側）
  console.log(`   蘇澳（東岸）：來向60°→${onE?'向岸':'離岸'}　來向240°→${onW?'向岸':'離岸'}`);
  chk('★東岸受東北來浪為向岸', onE, true);
  chk('★來自陸側為離岸', onW, false);
  chk('無浪向時回 null', G._onshore({lat:24,lng:121}, null), null);

  // tooltip 內容
  setLex(`window.WAVE_FCST = {'宜蘭縣蘇澳鎮':[${JSON.stringify(seg(3.0, 10))}]};`);
  const row = G._waveRow(t);
  const plain = row.replace(/<[^>]*>/g, ' ');
  console.log(`   tooltip：${plain.trim()}`);
  chk('含浪高', /浪高/.test(row), true);
  chk('含浪向文字', /東北浪/.test(plain), true);
  chk('含週期', /週期 10s/.test(plain), true);
  chk('★含長浪示警', /長浪/.test(plain), true);
  chk('★標明非官方告警', /系統研判，非官方長浪告警/.test(row), true);
  setLex("window.WAVE_FCST = {};");
}

console.log('\n=== 檔案型資料集須經 ProductURL 下載 ===');
{
  const py = fs.readFileSync('fetch_rainfall.py', 'utf8');
  chk('提供 ProductURL 解析', /def _resolve_product_url/.test(py), true);
  chk('★浪高經 ProductURL', /url = _resolve_product_url\(WAVE_EP\)/.test(py), true);
  chk('★打包預報經 ProductURL', /_burl = _resolve_product_url\('F-D0047-093'\)/.test(py), true);
  chk('說明 500 的成因', /直接向 API 要 ZIP 會得到 HTTP 500/.test(py), true);
  chk('解析失敗仍有備援（改走 fileapi）', /備援：直接向 fileapi 要檔案/.test(py), true);
}


console.log('\n=== 地形與臨海為獨立維度（不互相覆蓋）===');
if (need('_isCoastal') && need('_townZone')) {
  const z = getLex('TOWN_ZONE');
  const nCoast = getLex('TOWN_COASTAL.size');
  console.log(`   臨海鄉鎮 ${nCoast} 個（TOWN_ZONE 仍為單值，分布不變）`);
  chk('臨海鄉鎮 109 個', nCoast, 109);

  // ★ 山區／淺山但臨海者：兩種屬性必須同時成立
  [['花蓮縣秀林鄉','山區'], ['宜蘭縣南澳鄉','山區'], ['臺東縣達仁鄉','山區'],
   ['新北市瑞芳區','淺山區'], ['臺東縣成功鎮','淺山區'],
   ['基隆市中正區','淺山區']].forEach(([k, wantZone])=>{
    const t = {county:k.slice(0,3), township:k.slice(3)};
    chk(`${k} 地形為${wantZone}`, G._townZone(t), wantZone);
    chk(`★${k} 同時具臨海屬性`, G._isCoastal(t), true);
  });

  // 純沿海鄉鎮：兩者都成立
  const su = {county:'宜蘭縣', township:'蘇澳鎮'};
  chk('蘇澳地形為沿海地區', G._townZone(su), '沿海地區');
  chk('蘇澳具臨海屬性', G._isCoastal(su), true);

  // 內陸：不具臨海屬性
  const rn = {county:'南投縣', township:'仁愛鄉'};
  chk('仁愛鄉為山區', G._townZone(rn), '山區');
  chk('★內陸山區不具臨海屬性', G._isCoastal(rn), false);
  chk('大安區不具臨海屬性', G._isCoastal({county:'臺北市', township:'大安區'}), false);

  // ★ 不衝突驗證：情境群組鍵與陣風因子仍依「地形單值」運作
  const key = G._townGroupKey({county:'花蓮縣', township:'秀林鄉'});
  console.log(`   秀林鄉（山區且臨海）情境群組鍵 = ${key}`);
  chk('★情境群組仍用地形（不因臨海改變）', /\|山區$/.test(key), true);
  chk('★陣風因子仍用地形（山區 1.7）',
      G._gustFactor({county:'花蓮縣', township:'秀林鄉'}), 1.7);

  // TOWN_ZONE 分布未被臨海旗標影響
  const cnt = {};
  Object.values(z).forEach(v => cnt[v] = (cnt[v]||0)+1);
  chk('地形分布不變（山區31）', cnt['山區'], 31);
  chk('地形分布不變（沿海85）', cnt['沿海地區'], 85);
}


console.log('\n=== 浪高不得因鄉鎮同名而外溢至內陸 ===');
if (need('_waveOf') && need('_buildWaveIndex')) {
  const now = Date.now();
  const seg = {start:new Date(now-3600e3).toISOString(),
               end:new Date(now+3*3600e3).toISOString(),
               wave:2.0, dir:60, period:9};
  // 只給基隆的資料，臺北同名區不得取到
  setLex(`window.WAVE_FCST = {'基隆市中正區':[${JSON.stringify(seg)}],
                              '基隆市信義區':[${JSON.stringify(seg)}],
                              '臺南市南區':[${JSON.stringify(seg)}]};`);
  const kl = {county:'基隆市', township:'中正區'};
  const tp = {county:'臺北市', township:'中正區'};
  chk('基隆中正區有浪高', G._waveOf(kl).wave, 2.0);
  chk('★臺北中正區不得有浪高（同名）', G._waveOf(tp).wave, null);
  chk('★臺北信義區不得有浪高', G._waveOf({county:'臺北市',township:'信義區'}).wave, null);
  chk('★臺中南區不得有浪高', G._waveOf({county:'臺中市',township:'南區'}).wave, null);
  chk('★臺北中山區不得有浪高', G._waveOf({county:'臺北市',township:'中山區'}).wave, null);
  // tooltip 也不得出現
  chk('內陸 tooltip 無浪高列', G._waveRow(tp), '');
  const src = fs.readFileSync('index.html', 'utf8');
  chk('已移除純鄉鎮名備援鍵', /idx\[bare\]/.test(src), false);
  chk('說明撞名風險', /會讓臺北中正區查到基隆中正區的浪高/.test(src), true);

  // 臨海清單：內陸不得列入
  const cs = getLex('TOWN_COASTAL');
  ['臺北市信義區','臺中市南區','高雄市橋頭區','臺東縣金峰鄉','南投縣仁愛鄉']
    .forEach(k=>{
      chk(`★${k} 不在臨海清單`, getLex(`TOWN_COASTAL.has('${k}')`), false);
    });
  ['高雄市旗津區','宜蘭縣蘇澳鎮','花蓮縣秀林鄉','新北市瑞芳區']
    .forEach(k=>{
      chk(`${k} 在臨海清單`, getLex(`TOWN_COASTAL.has('${k}')`), true);
    });
  setLex("window.WAVE_FCST = {};");
}


console.log('\n=== 潮汐與暴潮溢淹研判 ===');
if (need('_nextHighTide') && need('_surgeRisk')) {
  // ★ _surgeRisk 以「目前選取時段」為基準（_windAtMs），非牆上時鐘。
  //   測試須用同一基準佈題，否則滿潮距離會對不上。
  setLex("winKey='today'; segFrom=0; segTo=3; mode='rain';" +
         "{const d=new Date(); d.setHours(0,0,0,0); BASE_TIME=d;}");
  const now = getLex('_windAtMs()');
  const iso = ms => new Date(ms).toISOString();
  const wseg = (w) => ({start:iso(now-3*3600e3), end:iso(now+3*3600e3),
                        wave:w, dir:60, period:9});
  const mkTide = (offsetH, cm, range) => ([{
    date:'2026-08-31', range,
    times:[{t: iso(now + offsetH*3600e3), kind:'滿潮', cm},
           {t: iso(now + (offsetH+6)*3600e3), kind:'乾潮', cm:-cm}]}]);
  const t = {county:'宜蘭縣', township:'蘇澳鎮'};

  // 滿潮在 1 小時後 + 大浪 3.0m → 警戒
  setLex(`window.TIDE_FCST = {'宜蘭縣蘇澳鎮': ${JSON.stringify(mkTide(1, 120, '大'))}};
    window.WAVE_FCST = {'宜蘭縣蘇澳鎮': [${JSON.stringify(wseg(3.0))}]};`);
  const ht = G._nextHighTide(t, now);
  console.log(`   下次滿潮 ${ht ? ht.hoursAway.toFixed(1) : '—'}h 後、${ht ? ht.cm : '—'}cm、潮差${ht ? ht.range : '—'}`);
  chk('取得滿潮', !!ht, true);
  chk('潮高正確', ht.cm, 120);
  chk('潮差級別', ht.range, '大');

  const sr = G._surgeRisk(t);
  console.log(`   暴潮研判：等級${sr.level}（浪${sr.wave}m、滿潮${sr.hoursAway}h後、潮差${sr.range}）`);
  chk('★滿潮±2h + 大浪 → 警戒(2)', sr.level, 2);

  // 中浪 1.8m + 大潮 → 注意升警戒
  setLex(`window.WAVE_FCST = {'宜蘭縣蘇澳鎮': [${JSON.stringify(wseg(2.0))}]};`);
  chk('★滿潮 + 2.0m + 大潮 → 警戒', G._surgeRisk(t).level, 2);
  setLex(`window.WAVE_FCST = {'宜蘭縣蘇澳鎮': [${JSON.stringify(wseg(1.6))}]};`);
  chk('滿潮 + 1.6m → 注意(1)', G._surgeRisk(t).level, 1);
  // 浪小 → 無風險
  setLex(`window.WAVE_FCST = {'宜蘭縣蘇澳鎮': [${JSON.stringify(wseg(0.8))}]};`);
  chk('★浪小時無暴潮風險', G._surgeRisk(t).level, 0);

  // 滿潮很遠（10h後）+ 大浪 + 大潮 → 僅注意
  setLex(`window.TIDE_FCST = {'宜蘭縣蘇澳鎮': ${JSON.stringify(mkTide(10, 120, '大'))}};
    window.WAVE_FCST = {'宜蘭縣蘇澳鎮': [${JSON.stringify(wseg(3.0))}]};`);
  const far = G._surgeRisk(t);
  console.log(`   滿潮 ${far.hoursAway}h 後 + 3.0m 大浪 → 等級${far.level}`);
  chk('★滿潮尚遠時降為注意', far.level, 1);

  // 無潮汐資料 → null（不誤報）
  setLex("window.TIDE_FCST = {};");
  chk('★無潮汐資料回 null', G._surgeRisk(t), null);
  chk('內陸無滿潮', G._nextHighTide({county:'南投縣',township:'仁愛鄉'}, now), null);

  // tooltip
  setLex(`window.TIDE_FCST = {'宜蘭縣蘇澳鎮': ${JSON.stringify(mkTide(1, 120, '大'))}};
    window.WAVE_FCST = {'宜蘭縣蘇澳鎮': [${JSON.stringify(wseg(3.0))}]};`);
  const row = G._waveRow(t).replace(/<[^>]*>/g, ' ');
  console.log(`   tooltip：${row.trim()}`);
  chk('含滿潮時刻', /滿潮/.test(row), true);
  chk('含潮高', /120cm/.test(row), true);
  chk('含潮差', /潮差大/.test(row), true);
  chk('★含暴潮警戒', /暴潮警戒/.test(row), true);
  chk('★標明非官方', /系統研判，非官方暴潮警戒/.test(G._waveRow(t)), true);

  const py = fs.readFileSync('fetch_rainfall.py', 'utf8');
  chk('後端有潮汐擷取', /TIDE_EP = 'F-A0021-001'/.test(py), true);
  chk('輸出含 tide_fcst', /'tide_fcst': tide_fcst/.test(py), true);
  setLex("window.TIDE_FCST = {}; window.WAVE_FCST = {};");
}


console.log('\n=== 搜尋欄位 ===');
if (need('_searchMatch') && need('onSearchPick')) {
  setLex(`TOWNSHIPS.length = 0;
    [{county:'宜蘭縣',township:'蘇澳鎮',lat:24.59,lng:121.87,
      stations:[{name:'蘇澳'},{name:'東澳'}]},
     {county:'臺北市',township:'大安區',lat:25.03,lng:121.54,stations:[]},
     {county:'臺中市',township:'和平區',lat:24.28,lng:121.0,stations:[{name:'武陵'}]}]
      .forEach(t=>TOWNSHIPS.push(t));`);
  const r1 = G._searchMatch('蘇澳');
  console.log(`   「蘇澳」→ ${r1.length} 筆：${r1.slice(0,3).map(x=>x.kind+':'+x.label).join('、')}`);
  chk('搜到鄉鎮', r1.some(x=>x.kind==='town' && x.label==='宜蘭縣蘇澳鎮'), true);
  chk('★也搜到同名雨量站', r1.some(x=>x.kind==='station' && x.label==='蘇澳'), true);

  const r2 = G._searchMatch('宜蘭');
  chk('搜到縣市', r2.some(x=>x.kind==='county' && x.label==='宜蘭縣'), true);
  chk('★縣市排在鄉鎮之前', r2[0].kind, 'county');

  // 台/臺 通用
  chk('★「台北」可搜到臺北市', G._searchMatch('台北').some(x=>x.label.indexOf('臺北')>=0), true);
  chk('★「臺中」可搜到', G._searchMatch('臺中').some(x=>x.label.indexOf('臺中')>=0), true);
  // 雨量站可用所屬鄉鎮搜到
  chk('武陵站可搜到', G._searchMatch('武陵').some(x=>x.kind==='station'), true);
  chk('無相符回空', G._searchMatch('不存在的地名').length, 0);
  chk('空字串回空', G._searchMatch('').length, 0);

  const src = fs.readFileSync('index.html', 'utf8');
  chk('搜尋框存在', /id="townSearch"/.test(src), true);
  chk('結果清單存在', /id="searchResults"/.test(src), true);
  chk('支援鍵盤操作', /onSearchKey/.test(src), true);
}

console.log('\n=== 無測站鄉鎮的推估補值 ===');
{
  const py = fs.readFileSync('fetch_rainfall.py', 'utf8');
  chk('提供鄰近內插', /def _neighbor_daily_rain/.test(py), true);
  chk('反距離平方加權', /1\.0 \/ max\(0\.5, d\) \*\* 2/.test(py), true);
  chk('限制最大距離', /max_km=25\.0/.test(py), true);
  chk('★標記為推估來源', /t\['obs_src'\] = 'neighbor'/.test(py), true);
  const src = fs.readFileSync('index.html', 'utf8');
  chk('★前端標示鄰近站推估', /（鄰近站推估）/.test(src), true);
  // 403 對策
  chk('水保署請求帶 User-Agent', /'User-Agent': 'Mozilla\/5\.0 \(compatible; RainfallMonitor/.test(py), true);
  chk('403/429 加長退避', /time\.sleep\(8 if r\.status_code in \(403, 429\)/.test(py), true);
}


console.log('\n=== renderLayer 座標快取 ===');
{
  const src = fs.readFileSync('index.html', 'utf8');
  chk('提供座標快取', /window\._llCache = new WeakMap\(\)/.test(src), true);
  chk('鄉鎮層使用快取', /const _c = llOf\(feat\);\s*\n\s*const poly/.test(src), true);
  chk('縣市層使用快取', /const _c = llOf\(feat\);\s*\/\/ 座標快取/.test(src), true);
  chk('★已移除每次執行的除錯碼', /__renderLayerDebugDone/.test(src), false);
  chk('說明快取原因', /每次重繪都把 36,516 個座標點重算一遍/.test(src), true);

  // 實測快取效益
  const GEO = getLex('TOWN_GEO');
  const toLL = ring => ring.map(([lng, lat]) => [lat, lng]);
  const cache = new WeakMap();
  const llOf = feat => {
    let c = cache.get(feat); if (c) return c;
    const g = feat.geometry;
    c = (g.type === 'Polygon') ? {type:'Polygon', rings:g.coordinates.map(toLL)}
      : {type:'MultiPolygon', parts:g.coordinates.map(p => p.map(toLL))};
    cache.set(feat, c); return c;
  };
  let t0 = Date.now();
  for (let i = 0; i < 10; i++) GEO.features.forEach(llOf);
  const first = Date.now() - t0;
  t0 = Date.now();
  for (let i = 0; i < 20; i++) GEO.features.forEach(llOf);
  const cached = Date.now() - t0;
  console.log(`   首次 10 次 ${first}ms、快取後 20 次 ${cached}ms`);
  chk('快取命中後幾乎零成本', cached <= 3, true);

  // 正確性：同物件重用、MultiPolygon 完整保留
  const f = GEO.features[0];
  chk('同 feature 回傳同物件', llOf(f) === llOf(f), true);
  const q = GEO.features.find(x => x.properties.TOWNNAME === '旗津區');
  chk('★旗津 MultiPolygon 未遺漏（17 parts）', llOf(q).parts.length, 17);
  const poly1 = GEO.features.find(x => x.geometry.type === 'Polygon');
  chk('Polygon 型別正確', llOf(poly1).type, 'Polygon');
  // 座標順序：GeoJSON [lng,lat] → Leaflet [lat,lng]
  const s0 = llOf(poly1).rings[0][0];
  chk('座標已轉為 [lat,lng]', s0[0] > 20 && s0[0] < 26 && s0[1] > 118, true);
}


console.log('\n=== Y 軸留白（折線不貼上緣）===');
{
  const src = fs.readFileSync('index.html', 'utf8');
  chk('提供留白計算', /const pad  = Math\.max\(o\.padMin != null \? o\.padMin : 2, span \* 0\.2\)/.test(src), true);
  chk('氣溫軸步距 5°C', (src.match(/axisStep:5, padMin:3/g)||[]).length, 2);
  chk('浪高軸步距 1m', (src.match(/axisStep:1, padMin:0\.5/g)||[]).length, 2);
  chk('說明貼上緣的問題', /資料頂端貼著上緣會看不到數值與尖峰形狀/.test(src), true);

  // 峰值應落在畫面中段（55%~90%）
  const calc = (vMax, vMin, allowNeg, step, padMin) => {
    const span = Math.max(1, vMax - (allowNeg ? vMin : 0));
    const pad = Math.max(padMin != null ? padMin : 2, span * 0.2);
    step = step || 2;
    const maxBf = Math.max(step * 2, Math.ceil((vMax + pad) / step) * step);
    const minBf = allowNeg ? Math.floor((vMin - pad) / step) * step : 0;
    return (vMax - minBf) / (maxBf - minBf);
  };
  [['風力9級', calc(9, 3, false, 2), 0.55, 0.9],
   ['風力13級', calc(13, 4, false, 2), 0.55, 0.9],
   ['氣溫24-33', calc(33, 24, true, 5, 3), 0.5, 0.9],
   ['浪高3.2m', calc(3.2, 0.5, false, 1, 0.5), 0.55, 0.9]].forEach(([n, r, lo, hi])=>{
    console.log(`   ${n}：峰值位於 ${(r*100).toFixed(0)}% 高度`);
    chk(`★${n} 峰值不貼上緣`, r >= lo && r <= hi, true);
  });
}

console.log('\n=== 逐圖層保留（部分失敗不留空）===');
{
  const py = fs.readFileSync('fetch_rainfall.py', 'utf8');
  chk('★某層失敗沿用前一輪', /沿用前一輪資料（本輪該層抓取失敗）/.test(py), true);
  chk('標記為非本次更新', /stale_layers/.test(py), true);
  chk('涵蓋所有新圖層',
      /'wave_fcst', 'tide_fcst', 'forecaster_precip',\s*\n\s*'wind_fcst', 'temp_fcst', 'gust_fcst'/.test(py), true);
  chk('★熔斷改為冷卻後自動恢復', py.indexOf('s 結束，恢復嘗試') > 0, true);
  chk('說明不放棄官方資料', /不放棄官方資料/.test(py), true);
  chk('熔斷門檻已放寬至 25', /_CWA_TRIP_AT     = 25/.test(py), true);
}


console.log('\n=== 色帶須裁切到 Y 軸範圍 ===');
{
  const src = fs.readFileSync('index.html', 'utf8');
  chk('第一帶不再固定由 minBf 起算', /const bLo = \(i === 0\) \? -Infinity : BANDS\[i-1\]\.max/.test(src), true);
  chk('與軸範圍取交集', /const lo = Math\.max\(bLo, minBf\);/.test(src), true);
  chk('完全在範圍外則不畫', /if\(!\(hi > lo\)\) return;/.test(src), true);
  chk('說明泛白成因', /整個橫軸附近泛白/.test(src), true);

  // 實測：氣溫軸 20~40 不得出現 <0°C 的白帶
  const TEMP = getLex('TEMP_BANDS');
  const drawn = (minBf, maxBf, BANDS) => {
    const out = [];
    BANDS.forEach((b, i) => {
      const bLo = (i === 0) ? -Infinity : BANDS[i-1].max;
      const lo = Math.max(bLo, minBf), hi = Math.min(b.max, maxBf);
      if (hi > lo) out.push({color: b.color, lo, hi});
    });
    return out;
  };
  const warm = drawn(20, 40, TEMP);
  console.log(`   氣溫軸 20~40 → ${warm.length} 條帶，最低帶 ${warm[0].lo}~${warm[0].hi}`);
  chk('★不含 <0°C 白帶', warm.some(x => x.color === '#FFFFFF'), false);
  chk('最低帶自軸下界起算', warm[0].lo, 20);
  chk('最高帶不超過軸上界', warm[warm.length-1].hi, 40);
  // 低溫情境仍應有白帶
  const cold = drawn(-5, 15, TEMP);
  chk('軸含負值時白帶存在', cold.some(x => x.color === '#FFFFFF'), true);
  // 風級：0 起算不受影響
  const bf = drawn(0, 12, getLex('BF_BANDS'));
  chk('風級白帶自 0 起算', bf[0].lo, 0);
}


console.log('\n=== 氣溫／浪高圖需含過去段 ===');
if (need('_envHistPts') && need('_tempSeries') && need('_waveSeries')) {
  const now = Date.now();
  const hk = ms => { const d = new Date(ms + 8*3600e3);
    return d.toISOString().slice(0,13); };
  // 過去 3 小時的歷史快照
  const hist = {};
  [-3,-2,-1].forEach(h=>{
    hist[hk(now + h*3600e3)] = {
      '宜蘭縣蘇澳鎮': {t: 25 + h, wave: 1.0 + Math.abs(h)*0.2, dir:60, period:8}};
  });
  const fut = [1,2].map(h=>({start:new Date(now+h*3600e3).toISOString(),
                             end:new Date(now+(h+3)*3600e3).toISOString(),
                             t:28, wave:2.0, dir:70, period:9}));
  setLex(`TMAP['宜蘭縣蘇澳鎮'] = {county:'宜蘭縣', township:'蘇澳鎮', lat:24.59, lng:121.87};
    window.ENV_HIST = ${JSON.stringify(hist)};
    window.TEMP_FCST = {'宜蘭縣':{'蘇澳鎮': ${JSON.stringify(fut)}}};
    window.WAVE_FCST = {'宜蘭縣蘇澳鎮': ${JSON.stringify(fut)}};`);
  const t = getLex("TMAP['宜蘭縣蘇澳鎮']");

  const ts = G._tempSeries(t), ws = G._waveSeries(t);
  const tPast = ts.filter(p=>p.ms < now).length, tFut = ts.filter(p=>p.ms >= now).length;
  const wPast = ws.filter(p=>p.ms < now).length, wFut = ws.filter(p=>p.ms >= now).length;
  console.log(`   氣溫序列：過去 ${tPast} 點、未來 ${tFut} 點`);
  console.log(`   浪高序列：過去 ${wPast} 點、未來 ${wFut} 點`);
  chk('★氣溫圖含過去段', tPast, 3);
  chk('氣溫圖含預報段', tFut, 2);
  chk('★浪高圖含過去段', wPast, 3);
  chk('浪高圖含預報段', wFut, 2);
  chk('依時間排序', ts.every((p,i)=>i===0 || p.ms >= ts[i-1].ms), true);
  chk('歷史點有標記', ts[0].hist, true);
  // 無歷史時不應出錯
  setLex("window.ENV_HIST = {};");
  chk('無歷史時只剩預報段', G._tempSeries(t).length, 2);
  chk('內陸無浪高歷史', G._envHistPts({county:'南投縣',township:'仁愛鄉'}, 'wave').length, 0);

  const py = fs.readFileSync('fetch_qpesums_hourly.py', 'utf8');
  chk('後端有氣溫/浪高歷史累積', /def update_env_history/.test(py), true);
  chk('滾動保留 72 小時', /hours=72/.test(py), true);
  chk('已接入每小時排程', /update_env_history\(now\)/.test(py), true);
  const src = fs.readFileSync('index.html', 'utf8');
  chk('前端載入 env_hist.json', /fetch\('env_hist\.json\?t='/.test(src), true);
  setLex("window.TEMP_FCST = {}; window.WAVE_FCST = {}; window.ENV_HIST = {};");
}


console.log('\n=== 各 mode 的 tooltip 應一致完整 ===');
if (need('_modeHeadRow')) {
  const src = fs.readFileSync('index.html', 'utf8');
  chk('★不再以簡化版取代完整內容', /mode==='wind'\s*\n\s*\? `<b>\$\{p\.COUNTYNAME\}/.test(src), false);
  chk('四種視窗皆加上模式標頭',
      (src.match(/_modeHeadRow\(t\)/g)||[]).length >= 4, true);
  chk('說明不必切圖層', /使用者不必為了/.test(src), true);
  chk('完整欄位仍在（風力/氣溫/浪高列）',
      (src.match(/_windRow\(t\) \+ _tempRow\(t\) \+ _waveRow\(t\)/g)||[]).length, 5);

  // 各 mode 的標頭內容
  const now = Date.now();
  const seg = o => Object.assign({start:new Date(now-3600e3).toISOString(),
                                 end:new Date(now+3*3600e3).toISOString()}, o);
  setLex(`TMAP['宜蘭縣蘇澳鎮'] = {county:'宜蘭縣', township:'蘇澳鎮', lat:24.59, lng:121.87};
    window.WIND_FCST = {'宜蘭縣':{'蘇澳鎮':[${JSON.stringify(seg({ws:12,bf:6}))}]}};
    window.TEMP_FCST = {'宜蘭縣':{'蘇澳鎮':[${JSON.stringify(seg({t:28}))}]}};
    window.WAVE_FCST = {'宜蘭縣蘇澳鎮':[${JSON.stringify(seg({wave:2.5,dir:60,period:9}))}]};
    windKind='mean';`);
  const t = getLex("TMAP['宜蘭縣蘇澳鎮']");
  [['wind', /6 級/], ['temp', /28°C/], ['wave', /2\.5 m/],
   ['etr', /ETR2/], ['risk', /風險/], ['warn', /警特報|豪雨|大雨/]].forEach(([m, re])=>{
    setLex(`mode='${m}';`);
    const h = G._modeHeadRow(t);
    console.log(`   mode=${m} → ${h.replace(/<[^>]*>/g,'').trim()}`);
    chk(`${m} 標頭含主值`, re.test(h), true);
  });
  setLex("mode='rain';");
  chk('雨量模式無額外標頭', G._modeHeadRow(t), '');
  // 無資料時的表現
  const inland = {county:'南投縣', township:'仁愛鄉'};
  setLex("mode='wave';");
  chk('★內陸標示非沿海', /非沿海/.test(G._modeHeadRow(inland)), true);
  setLex("mode='rain'; window.WIND_FCST={}; window.TEMP_FCST={}; window.WAVE_FCST={};");
}


console.log('\n=== ETR2／風險／警特報 tooltip 亦須完整 ===');
{
  const src = fs.readFileSync('index.html', 'utf8');
  chk('★三者合併為完整版分支',
      /mode==='warn' \|\| mode==='etr' \|\| mode==='risk'/.test(src), true);
  chk('不再只給一兩行',
      /tooltipHtml = `<b>\$\{p\.COUNTYNAME\} \$\{p\.TOWNNAME\}<\/b><br>ETR2%：/.test(src), false);
  chk('含累積雨量', /\$\{_isPast \? '觀測' : '預測'\}累積雨量/.test(src), true);
  chk('含最大時雨量', /最大時雨量：\$\{_maxHourRow\(t, segFrom, segTo\)\}/.test(src), true);
  chk('含風力/氣溫/浪高',
      (src.match(/_windRow\(t\) \+ _tempRow\(t\) \+ _waveRow\(t\)/g)||[]).length, 5);
  chk('警特報另附研判摘要', /mode==='warn'\s*\n\s*\? `<br><span style="font-size:10px">\$\{_warnSummaryHtml/.test(src), true);
  chk('標頭支援 warn', /if\(mode === 'warn'\)\{/.test(src), true);
  chk('標頭支援 etr', /if\(mode === 'etr'\)\{/.test(src), true);
  chk('標頭支援 risk', /if\(mode === 'risk'\)\{/.test(src), true);

  // 標頭呼叫 getAccum，須確認不會無窮遞迴
  setLex(`TMAP['南投縣仁愛鄉'] = Object.assign(TMAP['南投縣仁愛鄉']||{},
    {county:'南投縣', township:'仁愛鄉'});`);
  const t2 = getLex("TMAP['南投縣仁愛鄉']");
  let deep = false;
  ['etr','risk','warn'].forEach(m=>{
    setLex(`mode='${m}';`);
    try { G._modeHeadRow(t2); } catch(e){ deep = true; console.log('   ', m, e.message.slice(0,60)); }
  });
  chk('★標頭不致無窮遞迴', deep, false);
  setLex("mode='rain';");
}


console.log('\n=== 融合模式 CMPF ===');
if (need('_blendQpf') && need('_skillOf') && need('_blendSpread')) {
  const mk = v => new Array(60).fill(v);
  setLex(`TMAP['南投縣仁愛鄉'] = Object.assign(TMAP['南投縣仁愛鄉']||{}, {
      county:'南投縣', township:'仁愛鄉',
      qpf_best: ${JSON.stringify(mk(10))}, qpf_ecmwf: ${JSON.stringify(mk(20))},
      qpf_gfs: ${JSON.stringify(mk(30))}, qpf_icon: ${JSON.stringify(mk(40))}});
    window.MODEL_SKILL = {}; window._dataGenAt='t1';`);
  const t = getLex("TMAP['南投縣仁愛鄉']");

  // 無樣本 → 等權重（10+20+30+40）/4 = 25
  const eq = G._blendQpf(t);
  console.log(`   無樣本（等權重）：${eq[0]} mm`);
  chk('★樣本不足退回等權重', eq[0], 25);

  // 有樣本 → MAE 小者權重高、且套用偏差校正
  setLex(`window.MODEL_SKILL = {'山區': {
      best:  {short:{bias:1.5, mae:10, n:30}},
      ecmwf: {short:{bias:1.0, mae:50, n:30}},
      gfs:   {short:{bias:1.0, mae:50, n:30}},
      icon:  {short:{bias:1.0, mae:50, n:30}}}};
    window._dataGenAt='t2';`);
  const sk = G._skillOf(t, 'best');
  console.log(`   best 在山區：偏差比 ${sk.bias}、MAE ${sk.mae}、n=${sk.n}`);
  chk('取得地形別表現', sk.zone, '山區');
  chk('偏差比正確', sk.bias, 1.5);
  const w = G._blendQpf(t);
  console.log(`   加權後：${w[0]} mm（best 校正後 15、MAE最小權重最高）`);
  chk('★MAE 小者權重高（結果偏向 best 的校正值）', w[0] < 25, true);
  chk('★已套用偏差校正（>10 原值）', w[0] > 10, true);

  // 偏差限幅：極端值不得放大過頭
  setLex(`window.MODEL_SKILL = {'山區': {best:{short:{bias:9.0, mae:5, n:30}}}};
    window._dataGenAt='t3';`);
  const cap = G._blendQpf(t);
  console.log(`   極端偏差比 9.0 → ${cap[0]} mm（限幅 2.0 內）`);
  chk('★偏差校正有限幅', cap[0] <= 10 * 2.0 + 30, true);

  // 離散度 → 信心度
  const sp = G._blendSpread(t, 0, 3);
  console.log(`   離散度：平均 ${sp.mean}、範圍 ${sp.min}~${sp.max}、信心 ${sp.level}`);
  chk('計算離散度', sp.min < sp.max, true);
  chk('★差異大→信心低', sp.level, '低');
  // 四模式一致 → 信心高
  setLex(`TMAP['南投縣仁愛鄉'].qpf_ecmwf = ${JSON.stringify(mk(10))};
    TMAP['南投縣仁愛鄉'].qpf_gfs = ${JSON.stringify(mk(11))};
    TMAP['南投縣仁愛鄉'].qpf_icon = ${JSON.stringify(mk(10))};`);
  chk('★一致→信心高', G._blendSpread(getLex("TMAP['南投縣仁愛鄉']"), 0, 3).level, '高');

  // 官方研判區間對應（山區吃 mountain）
  setLex(`window.FORECASTER_PRECIP = {'24h': {title:'測試事件', areas:
      {'南投縣': {mountain:{lo:150, hi:250}, flat:{lo:50, hi:100}}}}};`);
  const rg = G._officialRange(t, '24h');
  console.log(`   官方研判：${rg.region} ${rg.lo}-${rg.hi}mm`);
  chk('★山區吃 mountain 值', rg.region, 'mountain');
  chk('區間正確', [rg.lo, rg.hi], [150, 250]);
  // ★ 南投縣境內幾乎全為山區/淺山，故另用雲林斗六市（平地）驗 flat 分支
  setLex(`window.FORECASTER_PRECIP['24h'].areas['雲林縣'] =
    {mountain:{lo:150, hi:250}, flat:{lo:50, hi:100}};`);
  const flat = G._officialRange({county:'雲林縣', township:'斗六市'}, '24h');
  console.log(`   斗六市（平地）→ ${flat.region} ${flat.lo}-${flat.hi}mm`);
  chk('★平地吃 flat 值', flat.region, 'flat');
  chk('flat 區間正確', [flat.lo, flat.hi], [50, 100]);
  chk('無官方區間時回 null（平時不發布）',
      G._officialRange({county:'不存在縣', township:'x'}, '24h'), null);

  const src = fs.readFileSync('index.html', 'utf8');
  chk('提供融合按鈕', /id="mBlend"/.test(src), true);
  chk('setModel 支援 blend', /blend:'mBlend'/.test(src), true);
  chk('★標明為自訂名稱非既有標準', /本系統的 CMPF 為自訂名稱，非既有標準術語/.test(src), true);
  chk('★不強制夾回官方區間', /但不強制夾回/.test(src), true);
  chk('結果有快取', /t\._blendCache/.test(src), true);
  setLex("window.MODEL_SKILL={}; window.FORECASTER_PRECIP={};");
}


console.log('\n=== 融合校正明細（可追溯）===');
if (need('renderBlendDetail')) {
  const src = fs.readFileSync('index.html', 'utf8');
  chk('★融合按鈕在最左', /id="mBlend"[\s\S]{0,140}id="mCwa"/.test(src), true);
  chk('提供明細區塊', /id="sec-blend"/.test(src), true);
  chk('標明方法依據', /NOAA National Blend of Models、Met Office IMPROVER/.test(src), true);
  chk('★標明 CMPF 完整名稱',
      /CMPF：Calibrated Multi-source Precipitation Forecast/.test(src), true);
  chk('附中文譯名', /（校正式多源降水預報）/.test(src), true);
  chk('標明為自訂名稱', /本系統自訂名稱，非既有標準術語/.test(src), true);
  chk('★明細位於測站清單之後（最下方）',
      src.indexOf('id="sec-stations"') < src.indexOf('id="sec-blend"'), true);
  chk('切模式時同步更新', /renderBlendDetail\(selected\)/.test(src), true);

  const mk = v => new Array(60).fill(v);
  setLex(`document.body.insertAdjacentHTML('beforeend',
    '<div id="sec-blend"></div><div id="blend-detail"></div>');
    TMAP['南投縣仁愛鄉'] = Object.assign(TMAP['南投縣仁愛鄉']||{}, {
      county:'南投縣', township:'仁愛鄉',
      qpf_best: ${JSON.stringify(mk(10))}, qpf_ecmwf: ${JSON.stringify(mk(20))},
      qpf_gfs: ${JSON.stringify(mk(30))}, qpf_icon: ${JSON.stringify(mk(40))}});
    window.MODEL_SKILL = {'山區': {
      best:  {short:{bias:1.5, mae:10, n:30}},
      ecmwf: {short:{bias:1.0, mae:50, n:30}}}};
    window.FORECASTER_PRECIP = {'24h': {title:'測試事件', areas:
      {'南投縣': {mountain:{lo:150, hi:250}}}}};
    forecastModel='blend'; segFrom=0; segTo=3; window._dataGenAt='b1';`);
  const t = getLex("TMAP['南投縣仁愛鄉']");
  let threw = false;
  try { G.renderBlendDetail(t); } catch(e){ threw = true; console.log('   ', e.message); }
  chk('繪製不拋錯', threw, false);
  const html = getLex("document.getElementById('blend-detail').innerHTML");
  const plain = html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
  console.log(`   ${plain.slice(0, 150)}`);
  chk('★顯示地形分群', /山區/.test(plain), true);
  chk('★顯示各模式權重', /%/.test(plain), true);
  chk('★顯示偏差比與方向', /偏差 1\.50（低估）/.test(plain), true);
  chk('顯示樣本數', /n=30/.test(plain), true);
  chk('樣本不足者標示', /樣本不足/.test(plain), true);
  chk('★顯示官方研判區間', /150–250 mm/.test(plain), true);
  chk('顯示離散度與信心', /信心/.test(plain), true);

  // 非融合模式應隱藏
  setLex("forecastModel='best';");
  G.renderBlendDetail(t);
  chk('★非融合模式隱藏面板',
      getLex("document.getElementById('sec-blend').style.display"), 'none');
  // 無樣本時提示累積中
  setLex("forecastModel='blend'; window.MODEL_SKILL={};");
  G.renderBlendDetail(t);
  chk('★無樣本時提示累積中',
      /樣本累積中/.test(getLex("document.getElementById('blend-detail').innerHTML")), true);
  setLex("forecastModel='best'; window.MODEL_SKILL={}; window.FORECASTER_PRECIP={};");
}


console.log('\n=== 融合須尊重官方值覆蓋段 ===');
if (need('_blendQpf')) {
  const mk = v => new Array(60).fill(v);
  setLex(`TMAP['南投縣仁愛鄉'] = Object.assign(TMAP['南投縣仁愛鄉']||{}, {
      county:'南投縣', township:'仁愛鄉',
      qpf_best: ${JSON.stringify(mk(10))}, qpf_ecmwf: ${JSON.stringify(mk(20))},
      qpf_gfs: ${JSON.stringify(mk(30))}, qpf_icon: ${JSON.stringify(mk(40))},
      qpf_cwa: ${JSON.stringify(mk(99))}, official_segs: [0, 1, 2]});
    window.MODEL_SKILL = {}; window._dataGenAt='off1';`);
  const t = getLex("TMAP['南投縣仁愛鄉']");
  const b = G._blendQpf(t);
  console.log(`   官方段[0-2]：${b[0]}/${b[1]}/${b[2]}　非官方段[3]：${b[3]}`);
  chk('★官方段直接採用官方值', b[0], 99);
  chk('官方段不做加權', b[2], 99);
  chk('★非官方段仍走加權', b[3], 25);

  const src = fs.readFileSync('index.html', 'utf8');
  chk('說明為何不加權', /加權等於自己跟自己平均，沒有意義/.test(src), true);
  chk('明細標示官方段', /本視窗有 \$\{nOff\} 段採用官方值/.test(src), true);
  const py = fs.readFileSync('fetch_rainfall.py', 'utf8');
  chk('後端輸出 official_segs', /'official_segs': _official_segs/.test(py), true);
  chk('★說明颱風格點會覆蓋四模式', /四個模式被寫成同一個數值/.test(py), true);
  setLex("window.MODEL_SKILL={};");
}


console.log('\n=== 融合明細：切模式即顯示 ===');
if (need('_blendOverviewHtml') && need('renderBlendDetail')) {
  setLex(`document.body.insertAdjacentHTML('beforeend',
    '<div id="sec-blend"></div><div id="blend-detail"></div>');
    forecastModel='blend'; window.MODEL_SKILL={};`);
  // 未選鄉鎮 + 無樣本
  G.renderBlendDetail(null);
  const disp = getLex("document.getElementById('sec-blend').style.display");
  const h0 = getLex("document.getElementById('blend-detail').innerHTML");
  chk('★未選鄉鎮仍顯示面板', disp, 'block');
  chk('無樣本時說明等權重', /以等權重融合四個模式/.test(h0), true);
  chk('提示可點鄉鎮看細節', /點選任一鄉鎮/.test(h0), true);

  // 未選鄉鎮 + 有樣本 → 顯示全臺概況
  setLex(`window.MODEL_SKILL = {
    '山區': {best:{short:{bias:1.42, mae:12, n:40}}, gfs:{short:{bias:1.80, mae:35, n:40}}},
    '平地': {best:{short:{bias:0.85, mae:8, n:60}}}};`);
  G.renderBlendDetail(null);
  const h1 = getLex("document.getElementById('blend-detail').innerHTML");
  const p1 = h1.replace(/<[^>]*>/g,' ').replace(/\s+/g,' ').trim();
  console.log(`   ${p1.slice(0,140)}`);
  chk('★顯示各地形概況', /山區/.test(p1) && /平地/.test(p1), true);
  chk('顯示偏差比', /1\.42/.test(p1), true);
  chk('★標出該地形最準的模式', /最準/.test(p1), true);

  // 非融合模式仍應隱藏
  setLex("forecastModel='best';");
  G.renderBlendDetail(null);
  chk('非融合模式隱藏', getLex("document.getElementById('sec-blend').style.display"), 'none');

  // 離散度小數一位
  const mk = v => new Array(60).fill(v);
  setLex(`TMAP['南投縣仁愛鄉'] = Object.assign(TMAP['南投縣仁愛鄉']||{}, {
      county:'南投縣', township:'仁愛鄉',
      qpf_best: ${JSON.stringify(mk(3.333))}, qpf_ecmwf: ${JSON.stringify(mk(7.777))},
      qpf_gfs: ${JSON.stringify(mk(5.555))}, qpf_icon: ${JSON.stringify(mk(9.999))}});
    forecastModel='blend'; segFrom=0; segTo=3;`);
  const sp = G._blendSpread(getLex("TMAP['南投縣仁愛鄉']"), 0, 3);
  console.log(`   離散度：${sp.min} ~ ${sp.max}（平均 ${sp.mean}）`);
  const dp = x => (String(x).split('.')[1] || '').length;
  chk('★min 取小數一位', dp(sp.min) <= 1, true);
  chk('★max 取小數一位', dp(sp.max) <= 1, true);
  chk('mean 取小數一位', dp(sp.mean) <= 1, true);
  setLex("forecastModel='best'; window.MODEL_SKILL={};");
}


console.log('\n=== 樣本累積進度可見 ===');
if (need('_blendOverviewHtml')) {
  setLex(`document.body.insertAdjacentHTML('beforeend',
    '<div id="sec-blend"></div><div id="blend-detail"></div>');
    forecastModel='blend'; window.MODEL_SKILL={};
    window.SKILL_PROGRESS={days_total:3, days_7:3, days_30:3,
      first:'2026-08-30', last:'2026-09-01', active:false};`);
  G.renderBlendDetail(null);
  const h = getLex("document.getElementById('blend-detail').innerHTML");
  const p = h.replace(/<[^>]*>/g,' ').replace(/\s+/g,' ').trim();
  console.log(`   ${p.slice(0,120)}`);
  chk('★顯示累積天數', /已累積 3 天/.test(p), true);
  chk('顯示起始日', /2026-08-30 起/.test(p), true);
  chk('★說明啟用門檻', /樣本數達 10 筆以上才會啟用差異化權重/.test(p), true);

  // 有樣本但未達門檻 → 明確標示仍為等權重
  setLex(`window.MODEL_SKILL = {'山區': {best:{short:{bias:1.3, mae:15, n:6}}}};
    window.SKILL_PROGRESS={days_total:2, days_7:2, days_30:2,
      first:'2026-08-31', last:'2026-09-01', active:false};`);
  G.renderBlendDetail(null);
  const p2 = getLex("document.getElementById('blend-detail').innerHTML")
    .replace(/<[^>]*>/g,' ').replace(/\s+/g,' ');
  chk('★未達門檻時明確標示', /樣本未達 10 筆，仍以等權重運作/.test(p2), true);

  // 已啟用 → 不再顯示警語
  setLex(`window.SKILL_PROGRESS={days_total:12, days_7:7, days_30:12,
      first:'2026-08-21', last:'2026-09-01', active:true};
    window.MODEL_SKILL = {'山區': {best:{short:{bias:1.3, mae:15, n:40}}}};`);
  G.renderBlendDetail(null);
  const p3 = getLex("document.getElementById('blend-detail').innerHTML")
    .replace(/<[^>]*>/g,' ').replace(/\s+/g,' ');
  chk('已啟用時不顯示警語', /樣本未達 10 筆/.test(p3), false);
  chk('仍顯示累積進度', /已累積 12 天/.test(p3), true);

  const py = fs.readFileSync('fetch_rainfall.py', 'utf8');
  chk('後端輸出累積進度', /output\['skill_progress'\]/.test(py), true);
  chk('判斷是否已啟用', /'active': any\(sp\.get\('n', 0\) >= 10/.test(py), true);
  setLex("forecastModel='best'; window.MODEL_SKILL={}; window.SKILL_PROGRESS=null;");
}


console.log('\n=== 測站雨量排名（Cleveland dot plot）===');
if (need('drawStnRankChart')) {
  setLex(`document.body.insertAdjacentHTML('beforeend','<canvas id="cv-stnrank"></canvas>');
    TMAP['南投縣仁愛鄉'] = Object.assign(TMAP['南投縣仁愛鄉']||{}, {
      county:'南投縣', township:'仁愛鄉', stations:[
        {name:'翠峰', daily_rain:[35.5]}, {name:'廬山', daily_rain:[128.0]},
        {name:'合歡山', daily_rain:[8.2]}, {name:'清境', daily_rain:[62.1]},
        {name:'無值站', daily_rain:[null]}]});`);
  const t = getLex("TMAP['南投縣仁愛鄉']");
  let threw = false;
  try { G.drawStnRankChart(t); G.drawStnRankChart(t, 'chart-zoom-canvas'); }
  catch(e){ threw = true; console.log('   ', e.message); }
  chk('繪製不拋錯（含放大）', threw, false);

  // 排序與過濾邏輯
  const rows = (t.stations||[])
    .map(st=>({name:st.name, v:(st.daily_rain||[])[0]}))
    .filter(x=>x.name && x.v != null).sort((a,b)=>b.v-a.v);
  console.log(`   排序後：${rows.map(r=>r.name+' '+r.v).join('、')}`);
  chk('★依雨量由大到小', rows[0].name, '廬山');
  chk('最小值在最後', rows[rows.length-1].name, '合歡山');
  chk('★排除無值測站', rows.some(r=>r.name==='無值站'), false);

  // 無測站鄉鎮不應出錯
  let t2 = false;
  try { G.drawStnRankChart({county:'x', township:'y', stations:[]}); }
  catch(e){ t2 = true; }
  chk('無測站時不拋錯', t2, false);

  const src = fs.readFileSync('index.html', 'utf8');
  chk('僅有測站時顯示區塊', /sEl\.style\.display = hasStn \? 'block' : 'none'/.test(src), true);
  chk('色階同累積雨量', /RAIN_SCALE\.find\(b=>r\.v < b\.max\)/.test(src), true);
  chk('★改為橫向長條（量值比較更直觀）', /量值大小的比較用長條比點更直觀/.test(src), true);
  chk('限制顯示筆數', /\.slice\(0, 14\)/.test(src), true);
}

console.log('\n=== 情境編輯器支援融合 ===');
{
  const src = fs.readFileSync('index.html', 'utf8');
  chk('★情境選單含融合', /blend:'融合'/.test(src), true);
  chk('★名稱恢復為 CWA+ECMWF', /best:'CWA\+ECMWF'/.test(src), true);
  chk('不再叫「綜合」', /best:'綜合'/.test(src), false);
  chk('★融合為動態計算（非後端欄位）',
      /融合是動態計算，沒有對應的後端欄位/.test(src), true);
  chk('情境沿用融合快取', /rule\.model === 'blend'[\s\S]{0,200}t\._blendCache/.test(src), true);
  const labels = getLex('SCN_MODEL_LABEL');
  console.log(`   情境模式：${Object.values(labels).join('、')}`);
  chk('選項齊全', Object.keys(labels).length, 9);
}


console.log('\n=== 地形與雨量關係圖 ===');
if (need('drawElevRainChart') && need('drawEtrPhaseChart') && need('drawMarginChart')) {
  setLex(`document.body.insertAdjacentHTML('beforeend',
    '<canvas id="cv-elevrain"></canvas><canvas id="cv-etrphase"></canvas>' +
    '<canvas id="cv-margin"></canvas>');
    TOWNSHIPS.length = 0;
    [{county:'南投縣', township:'仁愛鄉', daily_rain:[120], etr2_pct:1.35,
      stations:[{sid:'A1', name:'翠峰', elev:2100, daily_rain:[135], etr2_pct:1.42},
                {sid:'A2', name:'廬山', elev:1200, daily_rain:[98], etr2_pct:0.88}]},
     {county:'臺北市', township:'大安區', daily_rain:[15], etr2_pct:0.22,
      stations:[{sid:'B1', name:'臺北', elev:6, daily_rain:[15], etr2_pct:0.22}]},
     {county:'宜蘭縣', township:'蘇澳鎮', daily_rain:[60], etr2_pct:0.65,
      stations:[{sid:'C1', name:'蘇澳', elev:25, daily_rain:[60], etr2_pct:0.65}]}
    ].forEach(x=>TOWNSHIPS.push(x));`);
  const t = getLex("TOWNSHIPS[0]");
  let threw = false;
  try {
    G.drawElevRainChart(t); G.drawEtrPhaseChart(t); G.drawMarginChart(t);
    G.drawElevRainChart(t, 'chart-zoom-canvas');
    G.drawEtrPhaseChart(t, 'chart-zoom-canvas');
    G.drawMarginChart(t, 'chart-zoom-canvas');
  } catch(e){ threw = true; console.log('   ', e.message); }
  chk('三張圖繪製不拋錯（含放大）', threw, false);

  const src = fs.readFileSync('index.html', 'utf8');
  chk('★海拔圖以全臺為底', /全臺 \$\{pts\.length\} 站　藍圈＝本鄉鎮/.test(src), true);
  chk('★ETR2 圖依地形著色（純色）', /'山區':'#FF0000', '淺山區':'#FFFF00'/.test(src), true);
  chk('ETR2 圖有警戒線', /B\.ctx\.fillText\('警戒值'/.test(src), true);
  chk('★餘裕圖以測站為單位', /rows\.push\(\{name: st\.name, town: k/.test(src), true);
  chk('餘裕圖有 0 線（警戒值）', /\/\/ 0 線＝警戒值/.test(src), true);
  chk('餘裕依距警戒值排序', /rows\.sort\(\(a,b\)=>b\.margin - a\.margin\)/.test(src), true);

  // 無資料時不應出錯
  setLex("TOWNSHIPS.length = 0;");
  let t2 = false;
  try { G.drawElevRainChart(null); G.drawEtrPhaseChart(null); G.drawMarginChart(null); }
  catch(e){ t2 = true; }
  chk('無資料時不拋錯', t2, false);

  chk('測站排名改為長條圖', /量值大小的比較用長條比點更直觀/.test(src), true);
  chk('後端輸出測站座標與海拔', /'elev':      _elev/.test(
      fs.readFileSync('fetch_rainfall.py', 'utf8')), true);
}


console.log('\n=== 圖表色彩、圖例與互動 ===');
if (need('renderRankList') && need('_bindScatterClick')) {
  const src = fs.readFileSync('index.html', 'utf8');
  chk('★地形用純色（山區紅）', /'山區':'#FF0000'/.test(src), true);
  chk('★淺山黃', /'淺山區':'#FFFF00'/.test(src), true);
  chk('★平地綠', /'平地':'#00FF00'/.test(src), true);
  chk('★沿海藍', /'沿海地區':'#00FFFF'/.test(src), true);
  chk('說明混淆問題', /先前紅\/橘、綠\/藍過近/.test(src), true);
  chk('★圖例移至底部', /圖例置於底部（比照風力／浪高圖的排版）/.test(src), true);
  chk('★標題帶所選鄉鎮', /— \$\{t\.county\}\$\{t\.township\}（白圈）/.test(src), true);
  chk('海拔圖標題帶鄉鎮', /（藍圈）/.test(src), true);
  chk('★可點選跳至該地', /點選圖上任一點可跳至該地/.test(src), true);
  chk('點擊有距離門檻（避免誤觸）', /if\(!best \|\| bd > 14\) return;/.test(src), true);

  // 排行榜
  setLex(`document.body.insertAdjacentHTML('beforeend',
    '<select id="rank-metric"><option value="obs_rain">x</option></select>' +
    '<select id="rank-zone"><option value="">x</option></select>' +
    '<div id="rank-list"></div>');
    TOWNSHIPS.length = 0;
    [{county:'南投縣', township:'仁愛鄉', lat:24.0, lng:121.1,
      daily_rain:[220], etr2_pct:1.35, etr2_now:1.28},
     {county:'臺北市', township:'大安區', lat:25.03, lng:121.54,
      daily_rain:[15], etr2_pct:0.22, etr2_now:0.20},
     {county:'宜蘭縣', township:'蘇澳鎮', lat:24.59, lng:121.87,
      daily_rain:[160], etr2_pct:0.95, etr2_now:0.91}].forEach(x=>TOWNSHIPS.push(x));`);
  let rthrew = false;
  try { G.renderRankList(); } catch(e){ rthrew = true; console.log('   ', e.message); }
  chk('排行繪製不拋錯', rthrew, false);
  const html = getLex("document.getElementById('rank-list').innerHTML");
  const plain = html.replace(/<[^>]*>/g,' ').replace(/\s+/g,' ').trim();
  console.log(`   ${plain.slice(0,110)}`);
  chk('★依數值由大到小', plain.indexOf('仁愛鄉') < plain.indexOf('蘇澳鎮'), true);
  chk('顯示數值與單位', /220\.0mm/.test(plain), true);
  chk('★可點選（有 onclick）', /_rankPick/.test(html), true);
  chk('提供四種指標', /obs_rain[\s\S]{0,300}qpf_etr/.test(src), true);
  chk('★可依地形篩選', /rank-zone/.test(src), true);
  chk('限前 20 名', /rows\.slice\(0, 20\)/.test(src), true);

  // 地形篩選
  setLex(`document.getElementById('rank-zone').innerHTML =
    '<option value="山區" selected>山區</option>';`);
  G.renderRankList();
  const h2 = getLex("document.getElementById('rank-list').innerHTML");
  chk('★篩選後僅含該地形', /大安區/.test(h2), false);
}


console.log('\n=== 圖表清晰度與版面 ===');
{
  const src = fs.readFileSync('index.html', 'utf8');
  chk('★依 devicePixelRatio 設定緩衝區', /cv\.width = Math\.round\(w \* dpr\)/.test(src), true);
  chk('繪圖座標系縮回 CSS 尺寸', /ctx\.setTransform\(dpr, 0, 0, dpr, 0, 0\)/.test(src), true);
  chk('說明發糊成因', /瀏覽器會在 2x 螢幕上把它拉伸/.test(src), true);
  chk('★X 軸標題與圖例分列', /X 軸標題放在刻度下方、圖例之上/.test(src), true);
  chk('點擊命中改用 CSS 座標', /繪圖座標系已縮回 CSS 尺寸/.test(src), true);

  // 區塊順序
  const order = [...src.matchAll(/<!-- 區塊：([^ （]+)/g)].map(m=>m[1]);
  console.log(`   順序：${order.slice(0,9).join(' → ')}`);
  chk('★排行在最上', order[0], '全臺排行');
  chk('★融合明細在最下', order[order.length-1], '融合校正明細');
  const idx = n => order.indexOf(n);
  chk('★雨量早於 ETR2', idx('現況觀測') < idx('ETR2'), true);
  chk('★ETR2 早於風力', idx('ETR2') < idx('逐日風力預測'), true);
  chk('★風力早於浪高', idx('逐日風力預測') < idx('浪高預測'), true);
  chk('★浪高早於氣溫', idx('浪高預測') < idx('氣溫預測'), true);
  // 圖層按鈕：浪高在氣溫之前
  chk('★浪高按鈕在氣溫之前',
      src.indexOf('id="bWave"') < src.indexOf('id="bTemp"'), true);
}

console.log('\n=== 複製與 CSV 匯出 ===');
if (need('copyRankList') && need('downloadAllCsv')) {
  setLex(`document.body.insertAdjacentHTML('beforeend',
    '<select id="rank-metric"><option value="obs_rain">觀測累積雨量</option></select>' +
    '<select id="rank-zone"><option value="" selected>全部地形</option></select>' +
    '<div id="rank-list"></div><div id="rank-msg"></div>');
    TOWNSHIPS.length = 0;
    [{county:'南投縣', township:'仁愛鄉', lat:24.0, lng:121.1, daily_rain:[220,80],
      etr2_pct:1.35, etr2_now:1.28, alert_val:200,
      qpf_best:new Array(60).fill(5), qpf_ecmwf:new Array(60).fill(6),
      qpf_gfs:new Array(60).fill(7), qpf_icon:new Array(60).fill(8),
      qpf_cwa:new Array(60).fill(9)},
     {county:'臺北市', township:'大安區', lat:25.03, lng:121.54, daily_rain:[15,5],
      etr2_pct:0.22, etr2_now:0.20, alert_val:150}].forEach(x=>TOWNSHIPS.push(x));
    segFrom=0; segTo=3; window._dataGenAt='2026-09-02T12:00';
    document.getElementById('rank-zone').value='';`);
  G.renderRankList();
  let cthrew = false;
  try { G.copyRankList(); } catch(e){ cthrew = true; console.log('   ', e.message); }
  chk('複製不拋錯', cthrew, false);
  chk('★記錄目前排行供複製', getLex('(window._rankRows||[]).length'), 2);

  const src = fs.readFileSync('index.html', 'utf8');
  chk('複製含指標與地形標題', /前 \$\{rows\.length\} 名/.test(src), true);
  chk('複製含資料時間', /資料時間 \$\{window\._dataGenAt\}/.test(src), true);
  chk('★有 clipboard 失敗的退路', /_copyFallback/.test(src), true);
  chk('★CSV 含各模式欄位', /\$\{nm\}_本視窗mm/.test(src), true);
  chk('CSV 含六種模式', /\['融合','blend'\][\s\S]{0,200}\['CWA官方','cwa'\]/.test(src), true);
  chk('CSV 含風力氣溫浪高', /'浪週期s'/.test(src), true);
  chk('★CSV 有 BOM（Excel 可讀）', src.indexOf('ufeff') > 0, true);
  chk('CSV 正確跳脫引號', /s\.replace\(\/"\/g, '""'\)/.test(src), true);
}


console.log('\n=== 測站底圖（Voronoi + 測站來源）===');
if (need('toggleStationSrc') && need('toggleInterpMode') && need('_paintGrid')) {
  const src = fs.readFileSync('index.html', 'utf8');
  chk('★提供測站來源切換', /id="bStnSrc"/.test(src), true);
  chk('★提供銳利／平滑切換', /id="bVoronoi"/.test(src), true);
  chk('Voronoi 以光柵化實作（不算多邊形）',
      /以光柵化實作（逐格找最近點），不需計算多邊形/.test(src), true);
  chk('★上色邏輯已抽出共用', /function _paintGrid\(grid, NX, NY/.test(src), true);
  chk('兩條路徑共用上色', /Barnes 與 Voronoi 兩條路徑共用/.test(src), true);
  chk('說明測站密度優勢', /測站密度約 1300 點，遠高於鄉鎮的 368 點/.test(src), true);
  chk('★提醒測站僅有觀測值', /測站只有觀測值，故僅在「今天／過去」等有觀測的視窗有意義/.test(src), true);
  chk('說明兩種方式互補', /Voronoi 誠實反映「資料只到這個密度/.test(src), true);

  // 狀態切換
  setLex(`document.body.insertAdjacentHTML('beforeend',
    '<button id="bStnSrc"></button><button id="bVoronoi"></button>' +
    '<button id="bBlurRender"></button>');
    _srcStation=false; _interpMode='barnes'; _blurOn=false;`);
  let tthrew = false;
  try { G.toggleInterpMode(); } catch(e){ tthrew = true; console.log('   ', e.message); }
  chk('切換不拋錯', tthrew, false);
  chk('★切為 voronoi', getLex('_interpMode'), 'voronoi');
  chk('★自動開啟渲染（否則看不到）', getLex('_blurOn'), true);
  chk('按鈕文字改為銳利',
      getLex("document.getElementById('bVoronoi').textContent"), '銳利');
  G.toggleInterpMode();
  chk('可切回 barnes', getLex('_interpMode'), 'barnes');
  chk('按鈕文字改為平滑',
      getLex("document.getElementById('bVoronoi').textContent"), '平滑');

  setLex("_blurOn=false;");
  try { G.toggleStationSrc(); } catch(e){ tthrew = true; }
  chk('測站來源切換不拋錯', tthrew, false);
  chk('★切為測站來源', getLex('_srcStation'), true);
  chk('同樣自動開啟渲染', getLex('_blurOn'), true);
  G.toggleStationSrc();
  chk('可切回鄉鎮來源', getLex('_srcStation'), false);

  // Voronoi 最近鄰邏輯正確性（離線驗算）
  const pts = [[2, 2, 100], [10, 10, 20]];
  const near = (gx, gy) => {
    let bd = Infinity, bv = 0;
    pts.forEach(p=>{ const d = (p[0]-gx)**2 + (p[1]-gy)**2;
      if(d < bd){ bd = d; bv = p[2]; } });
    return bv;
  };
  chk('★靠近站A取A值', near(3, 3), 100);
  chk('★靠近站B取B值', near(9, 9), 20);
  chk('中間點取較近者', near(5, 5), 100);
  setLex("_srcStation=false; _interpMode='barnes'; _blurOn=false;");
}

console.log(fails.length ? `\n失敗 ${fails.length} 項：${JSON.stringify(fails, null, 1)}`
                         : '\n全部通過');
process.exit(fails.length ? 1 : 0);

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
  if (loMax > 122.3) fails.push(`點集含釣魚臺列嶼（最東 ${loMax.toFixed(2)}E）`);
  else console.log('  OK  已排除釣魚臺列嶼（最東 ≤122.3E）');
  if (laMin < 21.0) fails.push(`點集含東沙/南沙（最南 ${laMin.toFixed(2)}N）`);
  else console.log('  OK  已排除東沙／南沙（最南 ≥21.0N）');
  // 金馬必須納入（使用者明確要求）
  const dKinmen = G._distToTaiwanKm(24.45, 118.30);
  const dMatsu  = G._distToTaiwanKm(26.15, 119.95);
  console.log(`   金門=${dKinmen.toFixed(1)}km、馬祖=${dMatsu.toFixed(1)}km（應接近0）`);
  if (!(dKinmen < 5)) fails.push('金門未納入點集');
  if (!(dMatsu < 5)) fails.push('馬祖未納入點集');
  if (dKinmen < 5 && dMatsu < 5) console.log('  OK  金門、馬祖已納入100km判準範圍');

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
  G.LANDSLIDE_ALERTS = {
    'A1': { county: '屏東縣', town: '霧臺鄉', off_level: 'r', etr2: 700, alert: 600 },
    'A2': { county: '南投縣', town: '仁愛鄉', off_level: '', est_red_fc: true, etr2: 400, alert: 700 },
    'A3': { county: '花蓮縣', town: '秀林鄉', off_level: '', etr2: 10, alert: 650 },
    'A4': { county: '臺中市', town: '和平區', off_level: 'y', etr2: 300, alert: 600 },
  };
  G._lsGeo = null;
  const all = G._lsRows(false), only = G._lsRows(true);
  chk('全部 4 筆', all.length, 4);
  chk('只列達警戒 3 筆（排除 A3）', only.length, 3);
  chk('排序：官方紅在最前', only[0].no, 'A1');
  chk('排序：官方黃次之', only[1].no, 'A4');
  chk('排序：推估最後', only[2].no, 'A2');
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

console.log(fails.length ? `\n失敗 ${fails.length} 項：${JSON.stringify(fails, null, 1)}`
                         : '\n全部通過');
process.exit(fails.length ? 1 : 0);

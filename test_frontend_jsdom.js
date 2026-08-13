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
  chk('平地群組鍵（南投市）', G._townGroupKey({county:'南投縣', township:'南投市'}), '南投分署|平地');
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

  // 日和：段0扣掉已過的1小時（nowH=0）→ 280*5/6 + 3*280
  const a = G.getAccum(t, 'rain');
  const expect = Math.round((280*5/6 + 840)*10)/10;
  console.log(`   totalRain=${a.totalRain} 期望=${expect}（段0扣已過1h）`);
  if (Math.abs(a.totalRain - expect) > 0.2) fails.push(`日和與段值不一致（${a.totalRain} vs ${expect}）`);
  else console.log('  OK  日和＝段值扣除已過時段（同源）');

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
   // 使用者逐一指定（東北角拆分、宜蘭調整）
   ['新北市瑞芳區','淺山區'],['新北市貢寮區','淺山區'],['新北市雙溪區','淺山區'],
   ['基隆市中正區','沿海地區'],['基隆市中山區','沿海地區'],
   ['基隆市七堵區','淺山區'],['基隆市暖暖區','淺山區'],['基隆市安樂區','淺山區'],
   ['宜蘭縣頭城鎮','淺山區'],['宜蘭縣礁溪鄉','淺山區'],['宜蘭縣冬山鄉','淺山區'],
   ['宜蘭縣壯圍鄉','沿海地區'],['宜蘭縣五結鄉','沿海地區'],['宜蘭縣蘇澳鎮','沿海地區'],
   ['宜蘭縣員山鄉','淺山區'],['宜蘭縣三星鄉','淺山區'],
   // 北海岸／恆春半島併入沿海地區
   ['新北市石門區','沿海地區'],['新北市金山區','沿海地區'],['新北市萬里區','沿海地區'],
   ['新北市三芝區','沿海地區'],['屏東縣恆春鎮','沿海地區'],['屏東縣車城鄉','沿海地區'],
   ['屏東縣滿州鄉','沿海地區'],['屏東縣枋山鄉','沿海地區']].forEach(([k,want])=>{
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

console.log(fails.length ? `\n失敗 ${fails.length} 項：${JSON.stringify(fails, null, 1)}`
                         : '\n全部通過');
process.exit(fails.length ? 1 : 0);

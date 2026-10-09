// FORMOSA 圖面完整性測試（2026-10-09）
// ------------------------------------------------------------------
// 起因：使用者連續兩輪指出同一類問題 ——「只修你指出來的那一處，
//       其他同類的地方還在」。本測試把這一類缺陷寫成規則，涵蓋
//       **所有**畫 ETR2 的圖，而不只是螢幕截圖裡那一張。
//
// 涵蓋五類：
//   [A] null 不得被畫成 0（折線、節點、資料標籤）
//   [B] 聚合起始值不得為 0（全缺時必須是 null）
//   [C] 聚合母體隨覆蓋率變動時必須留白（子集取最大會低估真值）
//   [D] 軸刻度必須是整數（上限可被刻度數整除）
//   [E] 左右軸標題不得重疊
const fs = require('fs');
const puppeteer = require('puppeteer');
const _EXEC = [process.env.PUPPETEER_EXECUTABLE_PATH,
  '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  '/opt/pw-browsers/chromium/chrome-linux/chrome'].find(p => p && fs.existsSync(p));
const _LAUNCH = { args: ['--no-sandbox', '--disable-dev-shm-usage'] };
if (_EXEC) _LAUNCH.executablePath = _EXEC;

const src = fs.readFileSync('index.html', 'utf8');
const sf = [];
const ok = (cond, msg) => { if (!cond) sf.push(msg); };

// ── [源碼層] 不得退回舊寫法 ─────────────────────────────────────
ok((src.match(/function _strokeNullableSeries/g) || []).length === 1,
   '[A] _strokeNullableSeries 不是恰好一份');
ok((src.match(/function _gateByCoverage/g) || []).length === 1,
   '[C] _gateByCoverage 不是恰好一份');
ok((src.match(/function _niceAxisMax/g) || []).length === 1,
   '[D] _niceAxisMax 不是恰好一份');
ok((src.match(/function _drawAxisTitles/g) || []).length === 1,
   '[E] _drawAxisTitles 不是恰好一份');

// 五張畫 ETR2 折線的圖都必須走共用折線工具
const strokeCalls = (src.match(/_strokeNullableSeries\(/g) || []).length - 1;
ok(strokeCalls >= 5, `[A] 走共用折線的圖只有 ${strokeCalls} 張，應 ≥5（鄉鎮逐時/逐日、測站、分署逐時/逐日）`);

// 手寫折線迴圈（會把 null 連成線）不得復活
ok(!/etrRows\[di\]\.forEach\(\(v,i\)=>\{\s*const x=pL\+i\*itemW\+itemW\/2,y=pT\+ch\*\(1-v\/axisMaxEtr\);\s*i===0\?ctx\.moveTo/.test(src),
   '[A] _drawDistrictChart 又出現手寫折線迴圈');
ok(!/etr\.forEach\(\(v,i\)=>\{\s*const x=pL\+i\*itemW\+itemW\/2,y=pT\+ch\*\(1-v\/axisMaxEtr\);\s*i===0\?ctx\.moveTo/.test(src),
   '[A] _drawTownChart 又出現手寫折線迴圈');

// 聚合起始值
ok(!/const ee=new Array\(n\)\.fill\(0\)/.test(src),
   '[B] _calcDistrictHourly 又改回 fill(0)');
ok((src.match(/let mxR=0,mxE=0/g) || []).length === 0,
   '[B] 分署逐日/組體聚合又改回 mxE=0（全缺時會推 0 而非 null）');
// 分署逐日、分署組體、組體 tooltip 三處聚合都必須 null 起始
ok((src.match(/let mxR=0,mxE=null,nE=0/g) || []).length >= 3,
   `[B] null 起始的分署聚合只有 ${(src.match(/let mxR=0,mxE=null,nE=0/g)||[]).length} 處，應 ≥3`);

// 覆蓋率閘門必須套在三個分署聚合上
ok((src.match(/_gateByCoverage\(/g) || []).length - 1 >= 3,
   '[C] 覆蓋率閘門沒有套用在全部三個分署聚合（逐時/逐日/組體）');

// 軸刻度不得再用 ceilTo10 直接當上限（4 等分時 50/4=12.5 → 13%）
ok(!/const axisMaxEtr\s*=Math\.max\(ceilTo10/.test(src),
   '[D] 仍有 axisMaxEtr 直接用 ceilTo10（刻度會變 13%/38%）');

// 左右軸標題不得再各自 fillText 同一個 y
ok(!/ctx\.fillText\('左軸：[^']*',\s*pL\s*,\s*axTitleY\)/.test(src),
   '[E] 仍有未經量測就直接畫的左軸標題');

// ── [F] tooltip 幾何必須取自圖本身 ──────────────────────────────
// 原本 tooltip 各自寫死 pL=isZoom?72:34，而各圖實際是 52/48、46/44、100/96，
// cv-all-hyeto 更已從 8 格改為 120 格 —— 滑鼠位置對到的是別的時間。
ok((src.match(/function _registerChartGeom/g) || []).length === 1,
   '[F] _registerChartGeom 不是恰好一份');
ok((src.match(/function _geomIndexAt/g) || []).length === 1,
   '[F] _geomIndexAt 不是恰好一份');
const regCalls = (src.match(/_registerChartGeom\(/g) || []).length - 1;
ok(regCalls >= 4,
   `[F] 只有 ${regCalls} 張圖登記幾何，應 ≥4（鄉鎮逐時/逐日、分署逐時/逐日）`);
// 不得再有「沒有 _G 後備」的寫死邊距
const hardCoded = (src.match(/const pL=isZoom\?72:34, pR=isZoom\?72:34;/g) || []).length;
ok(hardCoded === 0,
   `[F] 仍有 ${hardCoded} 處 tooltip 寫死邊距（未改讀登記簿）`);
// 逐時圖的 tooltip 必須由 kind 分派，不能再靠 canvasId 猜格數
ok(/_G\.kind==='district-hourly'/.test(src),
   '[F] 各分署逐時圖沒有專屬 tooltip 分支（仍會被當成 8 格組體圖）');
ok(/_G\.kind==='hourly'/.test(src),
   '[F] 鄉鎮逐時圖沒有專屬 tooltip 分支');
// TDZ：tip 不得在宣告前被引用
const tipDecl = src.indexOf('let tip = document.getElementById(\'chart-hover-tip\')');
const fnStart = src.indexOf('function showChartTooltip(');
ok(tipDecl > fnStart && src.slice(fnStart, tipDecl).indexOf('tip.style.display') < 0,
   '[F] showChartTooltip 內在 let tip 宣告前就引用 tip（會丟 ReferenceError）');

(async () => {
  const browser = await puppeteer.launch(_LAUNCH);
  const page = await browser.newPage();
  page.on('pageerror', e => sf.push('頁面例外：' + e.message));

  // 只注入待測函式，不載入整頁（避免外部資料相依）
  const pick = (name) => {
    const i = src.indexOf(`function ${name}(`);
    if (i < 0) throw new Error(`找不到 ${name}`);
    let d = 0, started = false;
    for (let j = i; j < src.length; j++) {
      if (src[j] === '{') { d++; started = true; }
      else if (src[j] === '}') { d--; if (started && d === 0) return src.slice(i, j + 1); }
    }
    throw new Error(`${name} 括號不平衡`);
  };
  const bundle = [
    'function ceilTo10(v){ return Math.ceil((v||0)/10)*10; }',
    'const AGG_COV_MIN = 0.8;',
    pick('_niceUnit'), pick('_niceAxisMax'), pick('_axisMaxOfNullable'),
    pick('_gateByCoverage'), pick('_strokeNullableSeries'), pick('_drawAxisTitles'),
    pick('_geomIndexAt'),
  ].join('\n');
  await page.evaluate(bundle);

  const r = await page.evaluate(() => {
    const out = {};

    // [D] 刻度整數性：對一大批上限值，檢查每一格刻度都是整數
    const bad = [];
    for (let nDiv of [3, 4]) {
      for (let v = 1; v <= 600; v++) {
        const mx = _niceAxisMax(v, nDiv, 10);
        if (mx < v) bad.push(`上限 ${mx} < 資料 ${v}`);
        for (let i = 0; i <= nDiv; i++) {
          const tick = mx * i / nDiv;
          if (Math.abs(tick - Math.round(tick)) > 1e-9)
            bad.push(`nDiv=${nDiv} v=${v} max=${mx} 刻度 ${tick} 非整數`);
        }
      }
    }
    out.tickBad = bad.slice(0, 5);
    out.tickBadN = bad.length;
    // 截圖中的實例：gE≈38 → v=48
    out.sample48 = _niceAxisMax(48, 4, 20);

    // [A] 軸上限忽略 null
    out.axisNull = _axisMaxOfNullable([null, null, 37, null], 10, 4);
    out.axisAllNull = _axisMaxOfNullable([null, null], 10, 4);

    // [C] 覆蓋率閘門
    //   情境＝截圖：前段 60 個鄉鎮有值（max 38），中段只剩 2 個（max 5），後段恢復
    const vals = [38, 37, 36, 5, 5, 5, 34, 33];
    const cnts = [60, 60, 59, 2, 1, 2, 58, 60];
    out.gated = _gateByCoverage(vals, cnts);
    //   全程覆蓋率都低但一致（某署永遠只有 3 個鄉鎮有官方值）→ 不應整條消失
    out.gatedLow = _gateByCoverage([12, 11, 10], [3, 3, 3]);
    //   全缺
    out.gatedNone = _gateByCoverage([null, null], [0, 0]);

    // [A] 折線遇 null 斷開，且不得有點落在 v=0
    const pts = [];
    const ctx = {
      beginPath(){ pts.push(['begin']); }, stroke(){ pts.push(['stroke']); },
      moveTo(x, y){ pts.push(['move', x, y]); }, lineTo(x, y){ pts.push(['line', x, y]); },
    };
    _strokeNullableSeries(ctx, [10, 20, null, null, 30, 40],
      i => i * 10, v => 100 - v);
    out.penOps = pts.filter(p => p[0] !== 'begin' && p[0] !== 'stroke');
    out.moveCount = pts.filter(p => p[0] === 'move').length;
    out.anyAtZero = out.penOps.some(p => p[2] === 100);   // y=100 代表 v=0

    // [E] 左右軸標題不重疊：以實際量測驗證
    const mk = () => {
      const calls = [];
      let font = '12px sans-serif', align = 'left';
      return {
        calls,
        set font(f){ font = f; }, get font(){ return font; },
        set textAlign(a){ align = a; }, get textAlign(){ return align; },
        fillStyle: '',
        measureText(s){
          const px = parseFloat(font) || 12;
          // 粗估：中日文全形 1.0em、半形 0.55em
          let w = 0;
          for (const c of s) w += (c.charCodeAt(0) > 0x2000 ? 1.0 : 0.55) * px;
          return { width: w };
        },
        fillText(s, x, y){
          const px = parseFloat(font) || 12;
          let w = 0;
          for (const c of s) w += (c.charCodeAt(0) > 0x2000 ? 1.0 : 0.55) * px;
          calls.push({ s, x0: align === 'right' ? x - w : x, x1: align === 'right' ? x : x + w, y });
        },
      };
    };
    const longLeft = '左軸：時雨量(mm/h，署內鄉鎮最大)　※過去48h為官方觀測（QPESUMS逐時/日觀測分配，暗色）';
    const right = '右軸：ETR2%（逐時，署內最大；覆蓋不足之時段留白）';
    // 截圖的實際幾何：W=2048, pL=170, pR=160 → cw=1718；axFs=30
    const c1 = mk();
    _drawAxisTitles(c1, longLeft, right, 170, 170 + 1718, 900, 30, '#9fc8e8', '#e8e8e8');
    const [L1, R1] = c1.calls;
    out.case1 = { sameLine: L1.y === R1.y, overlap: (L1.y === R1.y) && (R1.x0 < L1.x1),
                  gap: Math.round(R1.x0 - L1.x1) };
    // 極窄畫布：必須換行而不是疊字
    const c2 = mk();
    _drawAxisTitles(c2, longLeft, right, 0, 400, 900, 30, '#9fc8e8', '#e8e8e8');
    const [L2, R2] = c2.calls;
    out.case2 = { sameLine: L2.y === R2.y, overlap: (L2.y === R2.y) && (R2.x0 < L2.x1) };
    // 短字串：應維持同一列
    const c3 = mk();
    _drawAxisTitles(c3, '左軸：累積雨量(mm)', '右軸：ETR2%', 0, 1200, 900, 30, '#9fc8e8', '#e8e8e8');
    out.case3 = { sameLine: c3.calls[0].y === c3.calls[1].y,
                  overlap: c3.calls[1].x0 < c3.calls[0].x1 };
    // [F] 幾何換算必須是繪圖座標的反函式
    //     繪圖：x = pL + i*itemW + itemW/2　→　tooltip 必須換回同一個 i
    const geoms = [
      { name: '鄉鎮逐日(小)',   pL: 52,  pR: 48,  nItems: 16,  W: 260 },
      { name: '鄉鎮逐日(zoom)', pL: 210, pR: 190, nItems: 16,  W: 2048 },
      { name: '分署逐日(小)',   pL: 46,  pR: 44,  nItems: 16,  W: 1440 },
      { name: '分署逐時(小)',   pL: 100, pR: 96,  nItems: 120, W: 1440 },
      { name: '分署逐時(zoom)', pL: 170, pR: 160, nItems: 120, W: 2048 },
    ];
    const mis = [];
    for (const g0 of geoms) {
      const g = { pL: g0.pL, itemW: (g0.W - g0.pL - g0.pR) / g0.nItems, nItems: g0.nItems };
      for (let i = 0; i < g0.nItems; i++) {
        const xCenter = g.pL + i * g.itemW + g.itemW / 2;   // 繪圖用的槽中心
        const back = _geomIndexAt(g, xCenter);
        if (back !== i) mis.push(`${g0.name} i=${i} → ${back}`);
      }
      // 繪圖區外必須回 -1
      if (_geomIndexAt(g, g.pL - 1) !== -1) mis.push(`${g0.name} 左界外未回 -1`);
      if (_geomIndexAt(g, g.pL + g.itemW * g.nItems + 1) !== -1) mis.push(`${g0.name} 右界外未回 -1`);
    }
    out.geomMis = mis.slice(0, 5);
    out.geomMisN = mis.length;

    // 寫死 34/34 會錯幾格？（證明這不是理論問題）
    const gWrong = { pL: 34, itemW: (260 - 34 - 34) / 16, nItems: 16 };
    const gRight = { pL: 52, itemW: (260 - 52 - 48) / 16, nItems: 16 };
    out.offBy = [];
    for (let i = 0; i < 16; i++) {
      const x = gRight.pL + i * gRight.itemW + gRight.itemW / 2;
      out.offBy.push(_geomIndexAt(gWrong, x) - i);
    }
    out.maxOffBy = Math.max(...out.offBy.map(Math.abs));

    // cv-all-hyeto：圖是 120 格逐時，tooltip 卻按 8 格組體換算 —— 錯的不是幾格，是整個時間軸
    const hyWrong = { pL: 34, itemW: (1440 - 68) / 8, nItems: 8 };
    const hyRight = { pL: 100, itemW: (1440 - 196) / 120, nItems: 120 };
    const xNow = hyRight.pL + 48 * hyRight.itemW + hyRight.itemW / 2;   // 「現在」那一格
    out.hyetoNow = { hourSlot: _geomIndexAt(hyRight, xNow),
                     oldBarIdx: _geomIndexAt(hyWrong, xNow) };

    return out;
  });

  // ── 判定 ─────────────────────────────────────────────────────
  ok(r.tickBadN === 0, `[D] 刻度非整數 ${r.tickBadN} 例，如：${r.tickBad.join('；')}`);
  ok(r.sample48 === 60, `[D] 截圖情境（v=48,4等分）上限應為 60（刻度 0/15/30/45/60），實得 ${r.sample48}`);
  ok(r.axisNull === 40, `[A] 軸上限忽略 null 失敗：[null,null,37,null] → ${r.axisNull}（應 40）`);
  ok(r.axisAllNull === 12, `[A] 全 null 時軸上限應回退地板（10 → 4等分取整後為 12），實得 ${r.axisAllNull}`);

  ok(JSON.stringify(r.gated) === JSON.stringify([38, 37, 36, null, null, null, 34, 33]),
     `[C] 覆蓋率閘門未擋下塌陷值：${JSON.stringify(r.gated)}`);
  ok(JSON.stringify(r.gatedLow) === JSON.stringify([12, 11, 10]),
     `[C] 覆蓋率一致偏低不應整條消失：${JSON.stringify(r.gatedLow)}`);
  ok(JSON.stringify(r.gatedNone) === JSON.stringify([null, null]),
     `[C] 全無覆蓋應全 null：${JSON.stringify(r.gatedNone)}`);

  ok(r.moveCount === 2, `[A] 折線應斷成 2 段（moveTo 2 次），實得 ${r.moveCount}`);
  ok(r.anyAtZero === false, '[A] 折線有頂點落在 v=0（null 被當成 0）');
  ok(r.penOps.length === 4, `[A] 應只畫 4 個有值點，實得 ${r.penOps.length}`);

  ok(r.case1.sameLine === true, '[E] 寬畫布下左右標題應維持同一列');
  ok(r.case1.overlap === false, `[E] 寬畫布下左右標題重疊（間距 ${r.case1.gap}px）`);
  ok(r.case2.overlap === false, '[E] 窄畫布下左右標題重疊（應換行）');
  ok(r.case2.sameLine === false, '[E] 窄畫布下右標題未換行');
  ok(r.case3.sameLine === true && r.case3.overlap === false, '[E] 短字串情境排版異常');

  ok(r.geomMisN === 0, `[F] 幾何換算與繪圖座標不符 ${r.geomMisN} 例：${r.geomMis.join('；')}`);
  console.log(`   （佐證：鄉鎮逐日小圖若沿用寫死的 34/34，右側最多偏 ${r.maxOffBy} 格 —— `
            + `偏移序列 ${JSON.stringify(r.offBy)}）`);
  ok(r.maxOffBy > 0, '[F] 測試前提有誤：寫死邊距應會造成偏移');
  console.log(`   （佐證：各分署組體圖滑到「現在」那一格 → 正確為逐時第 ${r.hyetoNow.hourSlot} 槽，`
            + `舊 tooltip 會算成 6h 組體第 ${r.hyetoNow.oldBarIdx} 段 —— 時間完全不同）`);
  ok(r.hyetoNow.hourSlot === 48, '[F] 測試前提有誤：逐時槽換算不正確');

  await browser.close();

  if (sf.length) {
    console.log('❌ test_chart_integrity 失敗 ' + sf.length + ' 項');
    sf.forEach(s => console.log('   - ' + s));
    process.exit(1);
  }
  console.log('✅ test_chart_integrity 全數通過（18 項）');
})().catch(e => { console.error('❌ 例外：', e); process.exit(1); });

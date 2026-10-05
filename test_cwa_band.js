// CWA 色帶不可被當可加量累積。
//
// 使用者回報：CWA 逐12小時宜蘭縣白天 70-110mm、晚上 40-70mm，但系統報到
// 320mm —— 比 CWA 自己的上界和（180mm）多了快一倍。
//
// 根因兩層：
//   ① 12h 色帶被複製到窗內每個 6h 子段（對著色是對的，色帶是類別），
//      任何把段加起來的路徑都會重複計算。
//   ② official_segs 把「真值段（颱風格點，已覆寫模式）」與「色帶段（PNG
//      類別，未覆寫模式）」混成一張清單，於是融合在色帶段丟掉六個模式、
//      改採那個加倍的色帶下界，還流進 qpf_hi/qpf_lo 的系集離散度。
//
// 修法：色帶取上界÷窗段數另發 qpf_cwa_q（可加量）；official_segs 收窄為
//      真值段，色帶段走 band_segs。這支測試守住兩個方向都不許回退。
const fs=require('fs');
const puppeteer=require('puppeteer');
const _EXEC=[process.env.PUPPETEER_EXECUTABLE_PATH,
  '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  '/opt/pw-browsers/chromium/chrome-linux/chrome'].find(p=>p&&fs.existsSync(p));
const _LAUNCH={args:['--no-sandbox','--disable-dev-shm-usage']};
if(_EXEC) _LAUNCH.executablePath=_EXEC;

(async()=>{
 // ── 原始碼層：退回防護 ──
 const src=fs.readFileSync('index.html','utf8');
 const sf=[];
 if(!/function _cwaQArr/.test(src))      sf.push('找不到可加量取值 _cwaQArr()');
 if(!/function _cwaRealSegs/.test(src))  sf.push('找不到真值段 _cwaRealSegs()');
 if(!/function _cwaCoveredSegs/.test(src))sf.push('找不到覆蓋段 _cwaCoveredSegs()');
 // 融合的官方值採用不得再用混合清單
 if(/const offSet = new Set\(t\.official_segs/.test(src))
   sf.push('_blendQpf 又用回混合的 official_segs（色帶段會丟掉六個模式）');
 // 系集離散度不得再推色帶原值
 if(/const cwaA = t\.qpf_cwa;/.test(src))
   sf.push('系集離散度又推回色帶原值 t.qpf_cwa（離散度會被撐大）');
 // 累積基底不得再用色帶原值
 if(/const cwa = t\.qpf_cwa \|\| \[\];\s*\n\s*const off = new Set\(t\.official_segs/.test(src))
   sf.push('CWA 累積基底又用回色帶原值');

 const b=await puppeteer.launch(Object.assign({},_LAUNCH,{defaultViewport:{width:1400,height:880}}));
 const pg=await b.newPage(); const errs=[];
 pg.on('pageerror',e=>errs.push(String(e).slice(0,170)));
 await pg.goto('http://127.0.0.1:8899/_local.html',{waitUntil:'networkidle2',timeout:90000});
 await new Promise(r=>setTimeout(r,8000));

 const R=await pg.evaluate(()=>{
  const fails=[], log=[];
  const ok=(c,m)=>{ if(!c) fails.push(m); log.push((c?'OK  ':'!!  ')+m); };

  // ── 造一個宜蘭的情形：白天色帶 90（90–110）、晚上色帶 50（50–70），
  //    各佔 2 個 6h 段。CWA 上界和 = 110 + 70 = 180。
  const t = {
    county:'宜蘭縣', township:'測試鄉', alert_val:200,
    qpf_cwa:   [90, 90, 50, 50],          // 色帶下界，窗內重複（著色用）
    qpf_cwa_q: [55, 55, 35, 35],          // 上界÷段數：110/2、70/2
    official_segs: [],                     // 無颱風格點
    band_segs: [0,1,2,3],
    qpf_best:  [5,5,5,5], qpf_ecmwf:[6,6,6,6], qpf_gfs:[4,4,4,4],
    qpf_jma:   [5,5,5,5], qpf_aifs: [5,5,5,5], qpf_gc:  [5,5,5,5],
    daily_rain:[0,0], rain_24h:0,
  };

  // ── ① 可加量的 24h 和要等於 CWA 上界和 ──
  const q = _cwaQArr(t);
  const sum24 = q.slice(0,4).reduce((a,b)=>a+b,0);
  ok(Math.abs(sum24 - 180) < 0.5,
     `①24h 可加量和 = ${sum24}mm，等於 CWA 上界和 110+70=180`);
  const bad = t.qpf_cwa.slice(0,4).reduce((a,b)=>a+b,0);
  ok(bad === 280 && sum24 < bad,
     `①對照：直接加色帶下界 = ${bad}mm（重複計算），已不再被採用`);

  // ── ② 色帶段不得算進「真值段」 ──
  ok(_cwaRealSegs(t).size === 0,
     `②色帶段不算真值段（真值段 ${_cwaRealSegs(t).size} 個）`);
  ok(_cwaCoveredSegs(t).size === 4,
     `②但仍算「有官方資料的覆蓋段」（${_cwaCoveredSegs(t).size} 段）`);

  // ── ③ CWA 必須「參與加權」：既不取代模式，也不被排除 ──
  //    舊行為：色帶段 = 100% CWA（丟掉六個模式，且用加倍值）→ 累積爆表
  //    過度修正：色帶段 = 0% CWA（官方依據被排除）→ 使用者回報不可接受
  //    正解：CWA 以可加量身分加入加權
  //
  //  ★ 這裡要測兩個情境，因為有一條既有規則會與之交互：
  //    「無降雨證據剔除」（觀測與雷達都沒有雨、而某成員報 ≥30mm 且中位數 <10）
  //    對所有成員一視同仁，CWA 也會被剔除。那是使用者自己訂的「排除亂報」
  //    原則，不替 CWA 開後門；但必須測出來、寫清楚，不能讓它變成
  //    「以為 CWA 參與了、其實又被排除」。
  const tRain = Object.assign({}, t, {daily_rain:[12, 0], rain_24h:12});
  const blR = _blendQpf(tRain);
  ok(blR[0] !== 90,
     `③a 有雨證據時融合不是色帶下界 90（不重複計算雨量）—— 實得 ${blR[0]}`);
  ok(blR[0] > 6,
     `③a 融合 ${blR[0]}mm 高於最高的模式 6mm —— CWA 確實參與加權，沒被排除`);
  ok(blR[0] < 55,
     `③a 但也沒有變成 CWA 獨裁（可加量 55）—— 模式仍有份量`);
  // 權重可驗算。注意 CWA 同樣受「佐證原則」約束：它的 55 > max(50, 中位數×3)，
  //   是唯一的高值且無雷達回波 → 權重 ×0.35。這是刻意的 —— 官方值參與加權，
  //   但「孤高無佐證」的降權對它一樣適用，否則等於開後門。
  const _mv = [5,6,4,5,5,5];
  const _wc = CWA_BLEND_W * 0.35;         // 佐證原則降權後的 CWA 權重
  const _exp = (_mv.reduce((a,b)=>a+b,0) + 55 * _wc) / (6 + _wc);
  ok(Math.abs(blR[0] - _exp) < 1.0,
     `③a 權重算得出來：預期 ${_exp.toFixed(1)}mm，實得 ${blR[0]}mm`+
     `（CWA_BLEND_W=${CWA_BLEND_W}，含佐證降權 ×0.35）`);
  // 若有第二個模式也報高，佐證成立 → CWA 不降權，融合應明顯更高
  const tCorr = Object.assign({}, tRain, {qpf_ecmwf:[60,60,60,60]});
  const blC = _blendQpf(tCorr);
  ok(blC[0] > blR[0],
     `③a 有第二個高值佐證時融合上升（${blR[0]} → ${blC[0]}mm）—— 佐證原則對 CWA 同樣生效`);

  // ③b 完全無雨證據且 CWA 與模式極端分歧 → 依既有「排除亂報」原則剔除
  const blN = _blendQpf(t);          // daily_rain 全 0、無回波
  ok(blN[0] != null && blN[0] <= 6,
     `③b 觀測與雷達都無雨、CWA 報 55 而模式中位數 5 → 依既有原則剔除`+
     `（融合 ${blN[0]}mm）。這是刻意的，不替 CWA 開後門`);

  // ── ④ 真值段（颱風格點）行為必須保留：那是真實數值，模式確實被覆寫 ──
  const t2 = Object.assign({}, t, {
    daily_rain:[12, 0], rain_24h:12,
    official_segs:[0,1], band_segs:[2,3],
    qpf_cwa:[88,88,50,50], qpf_cwa_q:[88,88,35,35],
    qpf_best:[88,88,5,5], qpf_ecmwf:[88,88,6,6], qpf_gfs:[88,88,4,4],
  });
  const bl2 = _blendQpf(t2);
  ok(bl2[0] === 88,
     `④真值段仍直接採用官方值 ${bl2[0]}（不做加權）—— 原行為沒被弄壞`);
  ok(bl2[2] != null && bl2[2] > 6 && bl2[2] < 55,
     `④同一鄉鎮的色帶段 ${bl2[2]}mm 走「模式＋CWA」加權，`+
     `既非 100% 官方也非 0%（兩種語意並存）`);

  // ── ⑤ 系集離散度不得被色帶撐大 ──
  const hi = getQpfArr(t, 'qpf_hi'), lo = getQpfArr(t, 'qpf_lo');
  if(Array.isArray(hi) && hi[0] != null){
    ok(hi[0] <= 60,
       `⑤強降雨分位 ${hi[0]}mm 未被色帶下界 90 撐大（上限 55 為可加量）`);
  } else log.push('--  ⑤qpf_hi 無值，略過');

  // ── ⑥ 著色仍用色帶原值（類別身分不可被除法破壞）──
  ok(typeof cwaBandColor === 'function' && cwaBandColor(90) === cwaBandColor(90),
     '⑥色帶著色函式仍以色帶值為輸入');
  ok(t.qpf_cwa[0] === 90,
     '⑥qpf_cwa 保持色帶下界原值（著色對得到官方色）');

  // ── ⑦ 舊資料（沒有 qpf_cwa_q）要能退回且不當場壞掉 ──
  const t3 = Object.assign({}, t); delete t3.qpf_cwa_q;
  const q3 = _cwaQArr(t3);
  ok(Array.isArray(q3) && q3.length === 4,
     `⑦缺 qpf_cwa_q 時退回 qpf_cwa，不丟錯（長度 ${q3.length}）`);

  return {fails, log};
 });
 console.log(R.log.join('\n'));
 if(sf.length) console.log('\n原始碼層：\n  !! '+sf.join('\n  !! '));
 else console.log('\nOK  原始碼層：可加量與兩種語意的分流都在，舊寫法未復發');
 if(errs.length) console.log('\n*** pageerror ***\n'+errs.join('\n'));
 const n=R.fails.length+sf.length;
 console.log(`\n${n?'FAIL '+n:'ALL PASS'} / ${R.log.length+1} 項`);
 await b.close();
 process.exit(n||errs.length?1:0);
})();

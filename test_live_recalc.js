// 鎖定決策 A：風險指標與坡地推估「必須」留在前端即時重算。
//
// 背景：曾經有一版 _withEst 用後端寫死的 a.etr2 當分子，結果
//   ‧ 切換時段不會改變推估（永遠是現在值）
//   ‧ 情境／倍率完全無效 → 面板恆為 0 條，與 ETR2% 警戒面板數字打架
// 使用者明訂必須改為前端重算（五個呼叫點都標註「吃情境／倍率」）。
//
// 為什麼需要這支測試：情境是「無限維度」—— 每天 × 每個分署|地形分組可以填
// 任意 add／mul，沒有任何有限的預算表能覆蓋。所以只要有人為了保密把
// calcRiskIndicator 或 _slopeEstJS 搬到後端預算，這些斷言就會失敗。
// 這是把「不能搬」這個決定寫成機器可檢查的形式，而不是只寫在文件裡。

const puppeteer=require('puppeteer');
(async()=>{
 const b=await puppeteer.launch({executablePath:'/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
   args:['--no-sandbox','--disable-dev-shm-usage'],defaultViewport:{width:1400,height:880}});
 const pg=await b.newPage(); const errs=[];
 pg.on('pageerror',e=>errs.push(String(e).slice(0,170)));
 await pg.goto('http://127.0.0.1:8899/_local.html',{waitUntil:'networkidle2',timeout:90000});
 await new Promise(r=>setTimeout(r,8000));
 const R=await pg.evaluate(()=>{
  const fails=[], log=[];
  const ok=(c,m)=>{ if(!c) fails.push(m); log.push((c?'OK  ':'!!  ')+m); };

  // ── 取一個有警戒值的鄉鎮（ETR2 路徑需要 alert_val）──
  const t=TOWNSHIPS.find(x=>x.alert_val>0) || TOWNSHIPS[0];
  if(!(t.alert_val>0)) return {fails:['找不到 alert_val>0 的鄉鎮，無法測 ETR2 路徑'], log};
  log.push(`（樣本 ${t.county}${t.township}　警戒值 ${t.alert_val}）`);

  const savedMode=mode, savedModel=forecastModel, savedWin=winKey;
  const savedFrom=segFrom, savedTo=segTo, savedScnOn=_scnOn, savedScnDays=_scnDays;
  const snap={}; Object.keys(t).forEach(k=>{ snap[k]=t[k]; });

  const clearCache=()=>{ delete t._blendCache; delete t._warnCache;
    delete t._blendKey; delete t._warnKey; delete t._ncInfo; };
  // 注入：第 2 段有雨、第 5 段無雨（用來驗「時段」確實影響結果）
  const inject=()=>{
    Object.values(MODEL_FIELD).forEach(f=>{ delete t[f]; });
    ['best','ecmwf','gfs','jma','aifs','gc'].forEach(k=>{
      const f=MODEL_FIELD[k]; if(!f) return;
      const a=new Array(64).fill(0);
      a[2]=40; a[3]=40; a[4]=40; a[5]=0; a[6]=0;
      t[f]=a;
    });
    t.official_segs=[]; t.qpf_cwa=[];
    t.pop_6h=new Array(64).fill(80);
    clearCache();
  };
  // 情境：所有天數 × 所有「分署|地形」分組都 +60mm（用 add 而非 mul，
  //   因為示範資料可能全是 0，乘法看不出差別）
  const scnOn=()=>{
    const g={}; _scnGroupKeys().forEach(k=>{ g[k]={add:60, mul:1}; });
    const days={}; for(let d=0; d<SCN_DAYS_MAX; d++) days[d]={model:null, g};
    _scnDays=days; _scnOn=true; clearCache();
  };
  const scnOff=()=>{ _scnOn=false; _scnDays={}; clearCache(); };

  mode='risk'; winKey='fut6_2'; segFrom=2; segTo=2;
  forecastModel='best';
  inject(); scnOff();

  // ── ① 風險指標必須吃情境 ──
  const r0=calcRiskIndicator(t, 2);
  scnOn();
  const r1=calcRiskIndicator(t, 2);
  ok(r1.R !== r0.R,
     `①風險指標吃情境：情境前 R=${r0.R} → 情境後 R=${r1.R}（必須不同）`);
  ok(r1.R > r0.R, `①情境加雨 → 風險上升（${r0.R} → ${r1.R}）`);
  scnOff();

  // ── ② 風險指標必須吃時段 ──
  const rWet=calcRiskIndicator(t, 2).R;     // 後續有雨
  const rDry=calcRiskIndicator(t, 8).R;     // 後續無雨
  ok(rWet !== rDry, `②風險指標吃時段：第2段 R=${rWet}、第8段 R=${rDry}（必須不同）`);

  // ── ③ 風險指標必須吃模式切換 ──
  const fE=MODEL_FIELD['ecmwf'];
  const aE=new Array(64).fill(0); aE[2]=0; aE[3]=0; aE[4]=0;
  t[fE]=aE; clearCache();
  forecastModel='ecmwf';
  const rEc=calcRiskIndicator(t, 2).R;
  forecastModel='best';
  clearCache();
  const rBest=calcRiskIndicator(t, 2).R;
  ok(rEc !== rBest,
     `③風險指標吃模式：ECMWF R=${rEc}、best R=${rBest}（必須不同）`);

  // ── ④ 坡地推估（_slopeEstJS 經 _withEst）必須吃情境 ──
  //    _withEst 走 TMAP 找鄉鎮，故必須注入在真實的 TOWNSHIPS 物件上
  inject(); scnOff();
  const mkA=()=>({county:t.county, town:t.township, village:'測試',
                  id:'TEST-001', alert:t.alert_val, etr2:(t.etr2!=null?t.etr2:50)});
  const e0=_withEst(mkA());
  scnOn();
  const e1=_withEst(mkA());
  ok(e0.est_src==='frontend' && e1.est_src==='frontend',
     `④推估由前端重算（est_src=${e0.est_src}／${e1.est_src}；'backend' 代表已退回預算值）`);
  ok(e1.fc_etr2 !== e0.fc_etr2,
     `④推估吃情境：情境前 fc_etr2=${e0.fc_etr2} → 情境後 ${e1.fc_etr2}（必須不同）`);
  ok(e1.fc_etr2 > e0.fc_etr2, `④情境加雨 → 預估 ETR2 上升`);
  scnOff();

  // ── ⑤ 坡地推估必須吃時段 ──
  segTo=2; const s2=_withEst(mkA()).fc_etr2;
  segTo=8; const s8=_withEst(mkA()).fc_etr2;
  ok(s2 !== s8, `⑤推估吃時段：segTo=2 → ${s2}、segTo=8 → ${s8}（必須不同）`);

  // ── ⑥ 守住「公開版仍可讀到這兩個函式」：它們留在前端是刻意的 ──
  ok(typeof calcRiskIndicator === 'function' && typeof _slopeEstJS === 'function',
     '⑥兩個函式仍在前端（決策 A：保留情境支援，接受公式可見）');

  // 還原
  Object.keys(t).forEach(k=>{ if(!(k in snap)) delete t[k]; });
  Object.keys(snap).forEach(k=>{ t[k]=snap[k]; });
  clearCache();
  mode=savedMode; forecastModel=savedModel; winKey=savedWin;
  segFrom=savedFrom; segTo=savedTo; _scnOn=savedScnOn; _scnDays=savedScnDays;
  return {fails, log};
 });
 console.log(R.log.join('\n'));
 if(errs.length){ console.log('\n*** pageerror ***\n'+errs.join('\n')); }
 console.log(`\n${R.fails.length?'FAIL '+R.fails.length:'ALL PASS'} / ${R.log.length} 項`);
 await b.close();
 process.exit(R.fails.length||errs.length?1:0);
})();

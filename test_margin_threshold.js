// 警戒門檻一致性：「警戒餘裕排行」與「預測達標時間」必須用同一個標準，
// 且兩張圖都要把門檻數值寫在畫面上。
//
// 背景（使用者回報）：縱軸畫的是「距警戒的差值」（pct − 90），所以 ETR2 在
// 40% 的鄉鎮長條落在 −50。旁邊只標「0 ＝ 達警戒」，於是被誤讀成「警戒標準
// 是 50%」，而排行圖標的是 90% —— 兩張並看就像兩套標準。這在預警系統裡
// 會直接造成判讀錯誤。
//
// 修正有兩層，這支測試兩層都守：
//   ① 門檻只有一個來源 _marginWarnOf()（先前 drawEtrWaterfall 寫死 90／den*0.9）
//   ② 兩張圖都必須把門檻數值畫在畫面上，不能只寫「達警戒」

const fs=require('fs');
const puppeteer=require('puppeteer');
(async()=>{
 // ── 原始碼層：寫死的門檻不得再出現（防止被改回去）──
 const src=fs.readFileSync('index.html','utf8');
 const srcFails=[];
 if(/basis\s*===\s*'pct'\s*\)\s*\?\s*90\s*:\s*den\s*\*\s*0\.9/.test(src))
   srcFails.push('drawEtrWaterfall 又出現寫死的 90／den*0.9');
 if(!/function _marginWarnOf/.test(src))
   srcFails.push('找不到單一門檻來源 _marginWarnOf()');
 const nWarn=(src.match(/const MARGIN_WARN\s*=/g)||[]).length;
 if(nWarn!==1) srcFails.push(`MARGIN_WARN 定義了 ${nWarn} 次（必須恰好 1 次）`);
 if(/\(\s*90\s*-\s*cur\s*\)/.test(src))
   srcFails.push('drawCountyTowns 又出現寫死的 (90 - cur)');
 if(/\(\s*90\s*-\s*x\.v\s*\)/.test(src))
   srcFails.push('renderRankList 又出現寫死的 (90 - x.v)');

 const b=await puppeteer.launch({executablePath:'/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
   args:['--no-sandbox','--disable-dev-shm-usage'],defaultViewport:{width:1400,height:880}});
 const pg=await b.newPage(); const errs=[];
 pg.on('pageerror',e=>errs.push(String(e).slice(0,170)));
 await pg.goto('http://127.0.0.1:8899/_local.html',{waitUntil:'networkidle2',timeout:90000});
 await new Promise(r=>setTimeout(r,8000));
 const R=await pg.evaluate(()=>{
  const fails=[], log=[];
  const ok=(c,m)=>{ if(!c) fails.push(m); log.push((c?'OK  ':'!!  ')+m); };

  const t=TOWNSHIPS.find(x=>x.alert_val>0);
  if(!t) return {fails:['找不到 alert_val>0 的鄉鎮'], log};
  const den=_etrDen(t);
  log.push(`（樣本 ${t.county}${t.township}　警戒值 den=${den}）`);

  // ── ① 門檻單一來源 ──
  ok(_marginWarnOf(t,'pct')===MARGIN_WARN && MARGIN_WARN===90,
     `①百分比門檻＝MARGIN_WARN＝${_marginWarnOf(t,'pct')}`);
  ok(Math.abs(_marginWarnOf(t,'mm') - den*MARGIN_WARN/100) < 1e-9,
     `①雨量門檻＝den×90%＝${_marginWarnOf(t,'mm').toFixed(1)}mm`);
  ok(_marginWarnOf(t,'mm') !== _marginWarnOf(t,'pct'),
     '①兩種基礎的門檻是不同量（mm vs %），不可混用');

  // ── 攔截 fillText，讀出畫面上真正畫了什麼字 ──
  const proto=CanvasRenderingContext2D.prototype;
  const real=proto.fillText;
  let drawn=[];
  proto.fillText=function(s,...a){ drawn.push(String(s)); return real.call(this,s,...a); };
  const render=(fn,basis)=>{
    const el=document.getElementById('margin-metric');
    if(el) el.value=basis;
    drawn=[];
    try{ fn(t); }catch(e){ drawn.push('THREW:'+e.message); }
    return drawn.slice();
  };

  // ── ② 預測達標時間：mm 基礎必須寫出實際 mm 門檻 ──
  const wfMm=render(drawEtrWaterfall,'mm');
  ok(!wfMm.some(s=>/資料不足|請點選|無警戒值/.test(s)),
     `②瀑布圖有實際繪製（非「資料不足」佔位，共 ${wfMm.length} 個文字）`);
  const expMm=(den*MARGIN_WARN/100).toFixed(0);
  ok(wfMm.some(s=>s.includes('達警戒') && s.includes(expMm) && s.includes('mm')),
     `②mm 基礎標出門檻 ${expMm}mm（實得：${wfMm.filter(s=>s.includes('達警戒')).join(' / ')||'無'}）`);

  // ── ③ 預測達標時間：pct 基礎必須寫出 90% ──
  const wfPct=render(drawEtrWaterfall,'pct');
  ok(wfPct.some(s=>s.includes('達警戒') && s.includes(String(MARGIN_WARN))),
     `③pct 基礎標出門檻 ${MARGIN_WARN}%（實得：${wfPct.filter(s=>s.includes('達警戒')).join(' / ')||'無'}）`);

  // ── ④ 舊的模糊標籤不得再出現 ──
  ok(![...wfMm,...wfPct].some(s=>s.trim()==='0 ＝ 達警戒'),
     '④不再出現沒有數值的「0 ＝ 達警戒」（那正是被誤讀成 50% 的原因）');

  // ── ⑤ 軸名要說明負值是餘裕，不是絕對值 ──
  ok([...wfMm,...wfPct].some(s=>/負值?＝尚有餘裕/.test(s)),
     '⑤畫面上說明「負值＝尚有餘裕」（畫在零線下方＝餘裕區本身）');

  // ── ⑤b 版面：摘要（結論）任何情況都不可被省略或被壓住 ──
  //    圖例放不下時應該讓掉圖例，而不是讓掉摘要。
  ok(wfMm.some(s=>/期間內|預估/.test(s)) && wfPct.some(s=>/期間內|預估/.test(s)),
     '⑤b 兩種基礎都畫出了結論摘要（何時達警戒／期間內未達）');

  // ── ⑥ 鄉鎮警戒餘裕排行（drawCountyTowns）也必須標出同一個門檻 ──
  //    ★ 注意：drawMarginChart 已無呼叫點、cv-margin 不在 DOM，是死碼。
  //      使用者看到的排行是 drawCountyTowns（cv-countytowns）。
  const renderCT=(mv)=>{
    const el=document.getElementById('ct-metric');
    if(el) el.value=mv;
    drawn=[];
    try{ drawCountyTowns(); }catch(e){ drawn.push('THREW:'+e.message); }
    return drawn.slice();
  };
  const ctPct=renderCT('margin_pct');
  ok(ctPct.length>0 && !ctPct.some(s=>/THREW|無資料/.test(s)),
     `⑥排行圖有實際繪製（共 ${ctPct.length} 個文字）`);
  ok(ctPct.some(s=>s.includes('門檻') && s.includes(MARGIN_WARN+'%')),
     `⑥排行圖標出門檻 ${MARGIN_WARN}%（實得：${ctPct.filter(s=>s.includes('門檻')).join(' / ')||'無'}）`);
  const ctMm=renderCT('margin_mm');
  ok(ctMm.some(s=>s.includes('門檻') && s.includes(MARGIN_WARN+'%')),
     `⑥排行圖 mm 基礎也標出 ${MARGIN_WARN}%`);

  // ── ⑦ 兩張圖標的是同一個數字 ──
  ok(wfPct.some(s=>s.includes('達警戒') && s.includes(MARGIN_WARN+'%'))
     && ctPct.some(s=>s.includes('門檻') && s.includes(MARGIN_WARN+'%')),
     `⑦達標時間與排行都標 ${MARGIN_WARN}%（同一標準）`);
  // ★ 不能斷言「畫面上不許出現 50%」—— 縱軸的 -50% 是正確的格線值
  //   （距警戒 −50 個百分點），那正是被誤讀的那一格，必須留著。
  //   要鎖的是「出現負值格線時，必須同時有消除歧義的說明」。
  const negGrid = wfPct.filter(s=>/^-\d+%?$/.test(s.trim()));
  ok(negGrid.length === 0 || (
       wfPct.some(s=>/負值?＝尚有餘裕/.test(s)) &&
       wfPct.some(s=>s.includes('達警戒') && s.includes(MARGIN_WARN+'%'))),
     `⑦軸上有負值格線（${negGrid.join(',')||'無'}）時，必須同時標出「負＝尚有餘裕」與門檻值`);

  proto.fillText=real;
  return {fails, log};
 });
 console.log(R.log.join('\n'));
 if(srcFails.length) console.log('\n原始碼層：\n  !! '+srcFails.join('\n  !! '));
 else console.log('\nOK  原始碼層：門檻僅一個來源，寫死版本未再出現');
 if(errs.length) console.log('\n*** pageerror ***\n'+errs.join('\n'));
 const n=R.fails.length+srcFails.length;
 console.log(`\n${n?'FAIL '+n:'ALL PASS'} / ${R.log.length+1} 項`);
 await b.close();
 process.exit(n||errs.length?1:0);
})();

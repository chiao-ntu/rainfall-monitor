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
 // ★ 2026-11-08 門檻改 100%：任何寫死的 90 差值都會讓圖與門檻不一致
 if(/\(\s*90\s*-|[\w)\]]\s*-\s*90\b(?!\d)/.test(src.replace(/\/\/.*$/mg,'')))
   srcFails.push('出現寫死的「90 −」或「− 90」差值，門檻必須取 MARGIN_WARN');
 const nWarn=(src.match(/const MARGIN_WARN\s*=/g)||[]).length;
 if(nWarn!==1) srcFails.push(`MARGIN_WARN 定義了 ${nWarn} 次（必須恰好 1 次）`);

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
  ok(_marginWarnOf(t,'pct')===MARGIN_WARN && MARGIN_WARN===100,
     `①百分比門檻＝MARGIN_WARN＝${_marginWarnOf(t,'pct')}`);
  ok(Math.abs(_marginWarnOf(t,'mm') - den*MARGIN_WARN/100) < 1e-9,
     `①雨量門檻＝den×${MARGIN_WARN}%＝${_marginWarnOf(t,'mm').toFixed(1)}mm`);
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
  ok([...wfMm,...wfPct].some(s=>s.includes('負值＝尚有餘裕')),
     '⑤軸名說明「負值＝尚有餘裕」');

  // ── ⑥ 警戒餘裕排行（現行路徑 drawCountyTowns，metric=ct-metric）必須把同一個門檻寫在圖上 ──
  //   ★ 2026-11-08：原本測的是 drawMarginChart，那是已無呼叫點的死碼（canvas 不在 DOM），
  //     測試因此永遠畫不出東西 —— 守錯對象等於沒守。改測使用者實際看到的那張圖。
  const renderCT=(metric)=>{
    const el=document.getElementById('ct-metric'); const old=el?el.value:null;
    if(el) el.value=metric;
    drawn=[];
    try{ drawCountyTowns(); }catch(e){ drawn.push('THREW:'+e.message); }
    if(el && old!=null) el.value=old;
    return drawn.slice();
  };
  const mcPct=renderCT('margin_pct');
  ok(mcPct.length>0 && !mcPct.some(s=>/^THREW/.test(s)),
     `⑥排行圖有實際繪製（共 ${mcPct.length} 個文字）`);
  ok(mcPct.some(s=>s.includes('門檻') && s.includes(MARGIN_WARN+'%')),
     `⑥排行圖標出門檻 ${MARGIN_WARN}%（實得：${mcPct.filter(s=>s.includes('門檻')).join(' / ')||'無'}）`);
  const mcMm=renderCT('margin_mm');
  ok(mcMm.some(s=>s.includes('門檻') && s.includes(MARGIN_WARN+'%')),
     `⑥排行圖 mm 基礎也標出 ${MARGIN_WARN}%`);

  // ── ⑦ 兩張圖標示的是同一個數字 ──
  //   ★ 取「= N%」或「＝ETR2% N%」的 N，不可抓到「ETR2%」裡的 2
  const pick=arr=>{ const s=arr.find(x=>/達警戒|門檻/.test(x))||'';
    const m=s.match(/(?:=|＝)\s*(?:ETR2%\s*)?(\d+)\s*%/); return m?m[1]:null; };
  ok(pick(wfPct)===pick(mcPct) && pick(wfPct)===String(MARGIN_WARN),
     `⑦兩張圖標的百分比門檻一致：達標時間 ${pick(wfPct)}%、排行 ${pick(mcPct)}%`);

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

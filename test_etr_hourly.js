// 逐時 ETR2 的兩條路徑必須同源、且必須會折減。
//
// 使用者回報（2026-10-04，宜蘭南澳，警戒值 400mm）：
//   「預測達標時間」圖最後只差 39mm（＝ETR2 321mm＝80%），
//   但逐日 QPF 圖同時間是 25%、逐時圖約 28~34% —— 差約 3.2 倍。
//   而且逐日／逐時在 10/6 之後明顯折減，達標時間圖卻是平的還在上升。
//
// 根因：_etrAtHour 自成一套公式，把未來第 1~h 小時的雨全部加總後
//   整包以 W[0]=1 當「今天」計入（七日權重只套過去觀測日）：
//     ① 未來 72h＝三天的雨全用權重 1 → 約 3 倍高估
//     ② 累加量只增不減 → 永遠不折減
//   這與 _etr2HourlySeries 註解裡記載、早已修過的「差 6~8 倍」是同一個病。
//
// 修法：_etrAtHour 改為與 _etr2HourlySeries 完全相同的作法
//   （calcEtr2AtSeg 逐段 + 段內線性內插）。本測試守住這個不變量。

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

  const t=TOWNSHIPS.find(x=>x.alert_val>0);
  if(!t) return {fails:['找不到 alert_val>0 的鄉鎮'], log};
  const snap={}; Object.keys(t).forEach(k=>{ snap[k]=t[k]; });
  const savedModel=forecastModel;

  const den=_etrDen(t);
  const nowH=Math.max(0,Math.min(23,
    Math.floor((Date.now()-SEG_EPOCH().getTime())/3600e3)));
  log.push(`（樣本 ${t.county}${t.township}　警戒值 ${den}mm　nowH=${nowH}）`);

  const clear=()=>{ delete t._blendCache; delete t._warnCache;
                    delete t._blendKey; delete t._warnKey; };
  // 注入：未來前 12 段（約 3 天）有雨，之後完全無雨 —— 用來驗折減
  const inject=(perSeg, nSeg)=>{
    Object.values(MODEL_FIELD).forEach(f=>{ delete t[f]; });
    ['best','ecmwf','gfs','jma','aifs','gc'].forEach(k=>{
      const f=MODEL_FIELD[k]; if(!f) return;
      const a=new Array(64).fill(0);
      for(let s=0;s<nSeg;s++) a[s]=perSeg;
      t[f]=a;
    });
    t.official_segs=[]; t.qpf_cwa=[]; t.etr2=0; clear();
  };
  forecastModel='best';
  inject(20, 12);        // 前 12 段各 20mm，之後 0

  // ── ① 兩條逐時路徑必須給出同一個數字 ──
  let maxDiff=0, worst=null;
  for(let h=-24; h<=72; h+=3){
    const a=_etrAtHour(t, h, MODEL_FIELD.best);
    const hAbs=nowH+h;
    const s=_etr2HourlySeries(t, hAbs, hAbs);
    const bv=(s && s.length) ? Math.round(s[0]*10)/10 : null;
    if(a==null||bv==null) continue;
    const d=Math.abs(a-bv);
    if(d>maxDiff){ maxDiff=d; worst=`h=${h}: _etrAtHour=${a}% vs 逐時圖=${bv}%`; }
  }
  ok(maxDiff<=0.15,
     `①兩條逐時 ETR2 路徑一致（最大差 ${maxDiff.toFixed(2)} 個百分點${worst?'，'+worst:''}）`);

  // ── ② 必須會折減：雨停之後 ETR2 要下降（舊版只增不減）──
  const ePeak=_etrAtHour(t, 66, MODEL_FIELD.best);   // 仍在雨中/剛停
  const eLate=_etrAtHour(t, 72, MODEL_FIELD.best);   // 雨停後
  const eEarly=_etrAtHour(t, 0, MODEL_FIELD.best);
  ok(eLate < ePeak,
     `②雨停後折減：+66h ${ePeak}% → +72h ${eLate}%（必須下降）`);
  ok(eEarly < ePeak, `②有雨期間上升：0h ${eEarly}% → +66h ${ePeak}%`);

  // ── ③ 不得再出現「未來三天的雨全部當今天」的 3 倍高估 ──
  //    舊公式：W[0]×(今日觀測 + 未來所有小時雨量) = 12段×20mm = 240mm
  //    正確值：逐段 0.7^(1/4) 折減遞推，必然遠低於 240mm
  const naive = 12*20/den*100;                 // 舊公式的量級
  const real  = _etrAtHour(t, 72, MODEL_FIELD.best);
  ok(real < naive*0.6,
     `③無累加式高估：正確 ${real}%，舊式會到 ${naive.toFixed(0)}%（必須明顯更低）`);

  // ── ④ 段邊界必須與 calcEtr2AtSeg（唯一權威）對得上 ──
  let segDiff=0, segWorst=null;
  for(let sg=0; sg<10; sg++){
    const hRel = sg*6 + 5 - nowH;              // 該段最後一小時
    if(hRel < -24 || hRel > 72) continue;
    const viaHour=_etrAtHour(t, hRel, MODEL_FIELD.best);
    const viaSeg=calcEtr2AtSeg(t, sg, MODEL_FIELD.best);
    if(viaHour==null||viaSeg==null) continue;
    const segPct=Math.round(viaSeg/den*1000)/10;
    const d=Math.abs(viaHour-segPct);
    if(d>segDiff){ segDiff=d; segWorst=`段${sg}: 逐時 ${viaHour}% vs 分段 ${segPct}%`; }
  }
  ok(segDiff<=1.0,
     `④段邊界與 calcEtr2AtSeg 一致（最大差 ${segDiff.toFixed(2)} 個百分點${segWorst?'，'+segWorst:''}）`);

  // ── ⑤ 時間原點：必須用 SEG_EPOCH 而非觀看者時鐘 ──
  //    把 new Date() 整體往後推 5 小時但不動 SEG_EPOCH，結果應該跟著 nowH 走，
  //    若程式用的是 getHours() 也會動 —— 這裡只驗「不會拋錯且仍與逐時圖一致」。
  const a1=_etrAtHour(t, 6, MODEL_FIELD.best);
  const s1=_etr2HourlySeries(t, nowH+6, nowH+6);
  ok(s1 && Math.abs(a1 - Math.round(s1[0]*10)/10) <= 0.15,
     `⑤+6h 與逐時圖同源（${a1}% vs ${s1?Math.round(s1[0]*10)/10:'—'}%）`);

  // ── ⑥ 使用者情境重現：南澳型（警戒 400mm、三日雨 14/83/25）不得衝到 80% ──
  //    逐日圖經官方公式驗算為 ~38%。這裡用相同量級的雨驗證不會再出現 2 倍以上高估。
  t.etr2 = 0.27*400;                            // 錨點 27%（＝使用者圖上的 10/4）
  const daily=[14,83,25,6.6];
  Object.values(MODEL_FIELD).forEach(f=>{ delete t[f]; });
  ['best','ecmwf','gfs','jma','aifs','gc'].forEach(k=>{
    const f=MODEL_FIELD[k]; if(!f) return;
    const a=new Array(64).fill(0);
    daily.forEach((mm,d)=>{ for(let s=0;s<4;s++) a[d*4+s]=mm/4; });
    t[f]=a;
  });
  t.alert_val=400; t.etr2_alert=400; clear();
  let peak=0;
  for(let h=0; h<=72; h++){ const v=_etrAtHour(t,h,MODEL_FIELD.best); if(v>peak) peak=v; }
  ok(peak < 60,
     `⑥南澳型情境峰值 ${peak}%（逐日圖驗算約 38%；舊版會到 80%，門檻設 60%）`);

  Object.keys(t).forEach(k=>{ if(!(k in snap)) delete t[k]; });
  Object.keys(snap).forEach(k=>{ t[k]=snap[k]; });
  clear(); forecastModel=savedModel;
  return {fails, log};
 });
 console.log(R.log.join('\n'));
 if(errs.length) console.log('\n*** pageerror ***\n'+errs.join('\n'));
 console.log(`\n${R.fails.length?'FAIL '+R.fails.length:'ALL PASS'} / ${R.log.length} 項`);
 await b.close();
 process.exit(R.fails.length||errs.length?1:0);
})();

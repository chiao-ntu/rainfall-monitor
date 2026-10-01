// 針對使用者回報「沒有模式報雨的地方累積雨量反而最大」的三個成因做回歸測試
const puppeteer=require('puppeteer');
(async()=>{
 const b=await puppeteer.launch({executablePath:'/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
   args:['--no-sandbox','--disable-dev-shm-usage'],defaultViewport:{width:1300,height:860}});
 const pg=await b.newPage(); const errs=[];
 pg.on('pageerror',e=>errs.push(String(e).slice(0,170)));
 await pg.goto('http://127.0.0.1:8899/_local.html',{waitUntil:'networkidle2',timeout:90000});
 await new Promise(r=>setTimeout(r,8000));
 const R=await pg.evaluate(()=>{
  const fails=[], log=[];
  const ok=(c,m)=>{ if(!c) fails.push(m); log.push((c?'OK  ':'!!  ')+m); };
  // ★ 容器時鐘常落在整段起點（已過 0 小時），那樣主要路徑根本沒被走到。
  //   用 Proxy 把「new Date()（無參數）」固定到段內第 4 小時，
  //   帶參數的 new Date(y,m,d,h) 維持真實行為（_obsAtHour 組時間鍵要用）。
  const RealDate = Date;
  const ns=_nowSeg(), segStart=6*ns;
  const FAKE = new RealDate(); FAKE.setHours(segStart+4, 30, 0, 0);
  window.Date = new Proxy(RealDate, {
    construct(T, args){ return args.length ? new T(...args) : new T(FAKE.getTime()); }
  });
  window.Date.now = ()=>FAKE.getTime();
  const now=new Date();
  const el=Math.max(0,Math.min(5, now.getHours()-segStart));
  log.push(`（段 ${ns}，已過 ${el} 小時）`);
  const p=n=>String(n).padStart(2,'0');
  const hkey=h=>`${now.getFullYear()}-${p(now.getMonth()+1)}-${p(now.getDate())}T${p(h)}`;

  const t=Object.assign({}, TOWNSHIPS[0]);
  const key=(t.county||'')+(t.township||'');
  const reset=()=>{ Object.values(MODEL_FIELD).forEach(f=>{ delete t[f]; });
    delete t._blendCache; delete t._warnCache; delete t._blendKey; delete t._warnKey;
    delete t._ncInfo; t.official_segs=[]; t.qpf_cwa=[]; delete t.qpf_radar_1h; };
  const put=v=>Object.entries(v).forEach(([k,x])=>{ const f=MODEL_FIELD[k];
    if(f){ const a=new Array(64).fill(0); a[ns]=x; t[f]=a; } });

  // ── ① 量綱：三個測站 2/2/20mm，面平均 8、極值 20 ──
  //    段內小時齊備
  const hrs=[], maxA=[], meanA=[];
  for(let h=0; h<=segStart+5; h++){ hrs.push(hkey(h)); maxA.push(20); meanA.push(8); }
  window.RAIN_HOURLY_HOURS=hrs;
  window.RAIN_HOURLY={[key]:maxA};
  window.RAIN_HOURLY_MEAN={[key]:meanA};
  window._radarQpfTime='';

  reset(); put({best:0,ecmwf:0,gfs:0,jma:0,aifs:0,gc:0});   // 所有模式都沒報雨
  const mean=_blendQpf(t,'mean')[ns];
  const warn=_blendQpf(t,'warn')[ns];
  ok(Math.abs(mean-8*el)<0.2, `量的軌道用面平均：${mean}mm（${el}h × 8mm）`);
  ok(Math.abs(warn-20*el)<0.2, `警戒軌道用站極值：${warn}mm（${el}h × 20mm）`);
  ok(el===0 || mean < warn, `量 ${mean} < 警戒 ${warn} —— 不再拿點極值撐累積量`);

  // ── ② 時間對齊：序列缺掉段內某一小時 → 整段退回模式，不得拿舊資料充數 ──
  if(el >= 2){
    const h2=hrs.filter(k=>k!==hkey(segStart+1));      // 挖掉段內第 2 小時
    const idx=hrs.indexOf(hkey(segStart+1));
    const m2=meanA.slice(), x2=maxA.slice(); m2.splice(idx,1); x2.splice(idx,1);
    window.RAIN_HOURLY_HOURS=h2;
    window.RAIN_HOURLY={[key]:x2}; window.RAIN_HOURLY_MEAN={[key]:m2};
    reset(); put({best:6,ecmwf:6,gfs:6,jma:6,aifs:6,gc:6});
    const v=_blendQpf(t,'mean')[ns];
    ok(Math.abs(v-6)<0.2, `段內缺一小時 → 整段退回模式 ${v}mm（不拿鄰近小時頂替）`);
    window.RAIN_HOURLY_HOURS=hrs;
    window.RAIN_HOURLY={[key]:maxA}; window.RAIN_HOURLY_MEAN={[key]:meanA};
  } else { log.push('--  （段內已過 <2 小時，略過缺口測試）'); }

  // ── ③ 雷達新鮮度：過期的雷達值不得再加 ──
  reset(); put({best:6,ecmwf:6,gfs:6,jma:6,aifs:6,gc:6}); t.qpf_radar_1h=50;
  const loc=ms=>{const d=new RealDate(ms);return `${d.getFullYear()}-${p(d.getMonth()+1)}-`
    +`${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;};
  window._radarQpfTime=loc(RealDate.now()-3*3600e3);
  const stale=_blendQpf(t,'mean')[ns];
  reset(); put({best:6,ecmwf:6,gfs:6,jma:6,aifs:6,gc:6}); t.qpf_radar_1h=50;
  window._radarQpfTime=loc(RealDate.now()-10*60e3);
  const fresh=_blendQpf(t,'mean')[ns];
  ok(fresh > stale + 40, `雷達 3 小時前 → 不採用（${stale}mm）；10 分鐘前 → 採用（${fresh}mm）`);

  reset(); put({best:6,ecmwf:6,gfs:6,jma:6,aifs:6,gc:6}); t.qpf_radar_1h=50;
  window._radarQpfTime=loc(RealDate.now()+3*3600e3);        // 時戳在未來（時區不符）
  const future=_blendQpf(t,'mean')[ns];
  // 期望值＝「有實測、但不加雷達」，也就是 stale 那一組的結果
  ok(Math.abs(future-stale)<0.2,
     `雷達時戳在未來（時區不符）→ 拒用，結果與過期時相同（${future}mm）`);

  // ── ④ 完全沒有觀測 → 不動模式值 ──
  window.RAIN_HOURLY_HOURS=[]; window.RAIN_HOURLY={}; window.RAIN_HOURLY_MEAN={};
  window._radarQpfTime='';
  reset(); put({best:6,ecmwf:6,gfs:6,jma:6,aifs:6,gc:6});
  const none=_blendQpf(t,'mean')[ns];
  ok(Math.abs(none-6)<0.2, `無任何觀測 → 維持模式值 ${none}mm`);
  window.Date = RealDate;
  return {log, fails};
 });
 R.log.forEach(l=>console.log(l));
 console.log('errs:',errs.slice(0,3));
 console.log(R.fails.length?`\n失敗 ${R.fails.length}`:'\n全部通過');
 await b.close(); process.exit(R.fails.length?1:0);
})();

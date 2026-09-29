// 0-6h 觀測接管 與 極端訊號旗標 的行為驗證
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
  const ns=_nowSeg(), el=Math.max(0,Math.min(5,new Date().getHours()-6*ns));
  log.push(`（當前段 ${ns}，已過 ${el} 小時）`);

  const t=Object.assign({}, TOWNSHIPS[0]);
  const reset=()=>{ Object.values(MODEL_FIELD).forEach(f=>{ delete t[f]; });
    delete t._blendCache; delete t._warnCache; delete t._blendKey; delete t._warnKey;
    delete t._ncInfo; t.official_segs=[]; t.qpf_cwa=[]; delete t.qpf_radar_1h; };
  const put=v=>Object.entries(v).forEach(([k,x])=>{ const f=MODEL_FIELD[k];
    if(f){ const a=new Array(64).fill(0); a[ns]=x; t[f]=a; } });

  // 造假實測：每小時 3mm
  window.RAIN_HOURLY={};
  const key=(t.county||'')+(t.township||'');
  window.RAIN_HOURLY[key]=new Array(50).fill(3);

  // ① 觀測接管：實測取代已過的小時
  reset(); put({best:6,ecmwf:6,gfs:6,jma:6,aifs:6,gc:6});   // 模式該段 6mm → 每小時 1mm
  _nowcastOn=false; const raw=_blendQpf(t,'mean')[ns];
  reset(); put({best:6,ecmwf:6,gfs:6,jma:6,aifs:6,gc:6});
  _nowcastOn=true;  const nc=_blendQpf(t,'mean')[ns];
  const expect = 3*el + 1 + 1*Math.max(0,6-el-1);
  ok(Math.abs(nc-expect)<0.15,
     `接管後 ${nc}mm（模式原值 ${raw}、預期 實測${3*el}＋模式${(6-el).toFixed(0)*1/1}＝${expect}）`);
  ok(el===0 || nc>raw, `已過 ${el} 小時、實測較大 → 接管後數值上修（${raw}→${nc}）`);

  // ② 雷達接手次 1 小時
  reset(); put({best:6,ecmwf:6,gfs:6,jma:6,aifs:6,gc:6}); t.qpf_radar_1h=20;
  const ncR=_blendQpf(t,'mean')[ns];
  ok(ncR > nc, `雷達次 1 小時 20mm → ${ncR}mm（無雷達時 ${nc}mm）`);
  ok(t._ncInfo && /雷達/.test(t._ncInfo.src), `來源標示：${t._ncInfo && t._ncInfo.src}`);

  // ③ 關掉接管 → 完全回到模式值
  reset(); put({best:6,ecmwf:6,gfs:6,jma:6,aifs:6,gc:6}); t.qpf_radar_1h=20;
  _nowcastOn=false; const off=_blendQpf(t,'mean')[ns]; _nowcastOn=true;
  ok(Math.abs(off-6)<0.15, `關閉接管 → 回到模式值 ${off}（應 6）`);

  // ④ 只動當前段，其他段不受影響
  reset();
  Object.entries(MODEL_FIELD).forEach(([k,f])=>{ if(BLEND_MODELS.includes(k))
    t[f]=new Array(64).fill(6); });
  t.qpf_radar_1h=20;
  const arr=_blendQpf(t,'mean');
  const others=[0,1,2,3].filter(i=>i!==ns).map(i=>arr[i]);
  ok(others.every(v=>Math.abs(v-6)<0.15), `其他段維持 6：${others.join('/')}`);

  // ⑤ 極端旗標：孤例被警戒軌道壓掉時要標 suppressed
  reset(); put({best:5,ecmwf:5,gfs:5,jma:5,aifs:5,gc:300});
  const f1=_extremeFlag(t, ns);
  const warn=getQpfArr(t,'qpf_warn')[ns];
  ok(f1 && f1.n===1 && f1.max===300, `旗標抓到 1 個成員報 300mm`);
  ok(f1 && f1.suppressed===true,
     `標記為「警戒軌道未採信」（警戒值 ${warn}）`);

  // ⑥ 多數成員都報極端 → 警戒軌道會跟上，不標 suppressed
  reset(); put({best:120,ecmwf:150,gfs:130,jma:140,aifs:110,gc:160});
  const f2=_extremeFlag(t, ns);
  ok(f2 && f2.n===6, `六個成員都報 ≥80mm（n=${f2&&f2.n}）`);
  ok(f2 && f2.suppressed===false,
     `警戒軌道跟上（${getQpfArr(t,'qpf_warn')[ns]}mm）→ 不標未採信`);

  // ⑦ 強制「段內已過 4 小時」：容器時鐘剛好在整段起點，
  //    不假造時間就驗不到「實測取代已過小時」這條主路徑
  (()=>{
    const RealDate = Date;
    const fake = 6*ns + 4;                       // 段內第 4 小時
    // eslint-disable-next-line no-global-assign
    window.Date = class extends RealDate {
      getHours(){ return fake; }
      static now(){ return RealDate.now(); }
    };
    try{
      reset(); put({best:6,ecmwf:6,gfs:6,jma:6,aifs:6,gc:6});   // 每小時 1mm
      const v = _blendQpf(t,'mean')[ns];
      // 預期：實測 4h×3mm ＝12 ＋ 模式剩 2 小時×1mm ＝ 14
      ok(Math.abs(v-14)<0.15,
        `已過 4 小時：實測 12mm ＋ 模式 2mm ＝ ${v}mm（模式原值 6mm）`);
      ok(t._ncInfo && t._ncInfo.el===4 && t._ncInfo.obs===12,
        `來源明細：已過 ${t._ncInfo&&t._ncInfo.el} 小時、實測 ${t._ncInfo&&t._ncInfo.obs}mm`);
      // 缺一小時實測 → 整段不用實測（半套會系統性少算）
      reset(); put({best:6,ecmwf:6,gfs:6,jma:6,aifs:6,gc:6});
      const arr2=window.RAIN_HOURLY[key].slice(); arr2[arr2.length-3]=null;
      window.RAIN_HOURLY[key]=arr2;
      const v2=_blendQpf(t,'mean')[ns];
      ok(Math.abs(v2-6)<0.15, `實測缺一小時 → 整段退回模式 ${v2}mm（不混用）`);
      window.RAIN_HOURLY[key]=new Array(50).fill(3);
    } finally {
      // eslint-disable-next-line no-global-assign
      window.Date = RealDate;
    }
  })();

  // ⑧ 平常沒有極端值 → 不產生旗標
  reset(); put({best:5,ecmwf:6,gfs:4,jma:5,aifs:7,gc:5});
  ok(_extremeFlag(t, ns)===null, '無極端值 → 不產生旗標');
  return {log, fails};
 });
 R.log.forEach(l=>console.log(l));
 console.log('errs:',errs.slice(0,3));
 console.log(R.fails.length?`\n失敗 ${R.fails.length}`:'\n全部通過');
 await b.close(); process.exit(R.fails.length?1:0);
})();

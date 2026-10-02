// 端到端：逐時視窗下 2mm/h 的鄉鎮在「四條著色路徑」上都必須看得見
//   路徑①鄉鎮填色 ②播放中的漸層色 ③渲染內插層 ④圖例
// 並兼任執行期安全網（原 runtime_check.js／layer_check.js 走 jsdom，
// 在 3.8MB 的 index.html 上已整體超時，改用真實瀏覽器重建）。
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
  const isWhiteish=h=>{ const m=/^#?([0-9a-f]{6})$/i.exec(String(h||'').trim());
    if(!m) return false; const n=parseInt(m[1],16);
    return ((n>>16)&255)>230 && ((n>>8)&255)>230 && (n&255)>230; };

  // ── 執行期安全網（取代失效的 jsdom 檢查）──
  ok(typeof map.getPane==='function', '地圖已建立');
  ok(!!map.getPane('seaPane'), 'seaPane 存在');
  ok(!!map.getPane('townPane'), 'townPane 存在');
  ok(townLayer && townLayer.getLayers && townLayer.getLayers().length>300,
     `鄉鎮圖層已建立（${townLayer&&townLayer.getLayers?townLayer.getLayers().length:0} 個）`);
  ok(RAIN_SCALE.length===7, '累積雨量色階 7 級');
  ok(document.getElementById('loading').style.display==='none', '初始化完成');

  const savedMode=mode, savedHour=_hourIdx, savedWin=winKey;

  // ── 造一個「6 小時共 12mm」的鄉鎮：無 hourly_ifs → 段內平均 2mm/h ──
  //   qpf_best 是動態融合值（走 _blendQpf + t._blendCache），
  //   不能直接塞 t.qpf_best，必須設各模式欄位並清掉快取。
  const t=Object.assign({}, TOWNSHIPS[0]);
  const ns=_nowSeg();
  Object.values(MODEL_FIELD).forEach(f=>{ delete t[f]; });
  delete t._blendCache; delete t._warnCache; delete t._blendKey; delete t._warnKey;
  t.official_segs=[]; t.qpf_cwa=[]; delete t.qpf_radar_1h; delete t.hourly_ifs;
  ['best','ecmwf','gfs','jma','aifs','gc'].forEach(k=>{
    const f=MODEL_FIELD[k]; if(!f) return;
    const a=new Array(64).fill(0); a[ns]=12; t[f]=a;
  });
  mode='rain'; _hourIdx=1; winKey='fut1h_1';

  const acc=getAccum(t);
  ok(acc && acc.isHourly===true, `取到逐時值（isHourly=${acc&&acc.isHourly}）`);
  ok(acc && Math.abs(acc.totalRain-2)<0.051,
     `段內平均＝2.0 mm/h（實得 ${acc&&acc.totalRain}）`);

  // ① 鄉鎮填色
  const f=_townFill(t,false);
  ok(!isWhiteish(f.fillColor), `①鄉鎮填色不是白色（${f.fillColor}）`);
  ok(f.fillColor==='#00FFFF', `①填色＝時雨量色階的 1–10 淺藍（${f.fillColor}）`);

  // ② 播放中的漸層色（先前硬寫 RAIN_SCALE，一播放就變白）
  const rc=_rampColor(acc.totalRain, _rainScaleOf(acc));
  ok(!isWhiteish(rc), `②播放漸層色不是白色（${rc}）`);

  // ③ 渲染內插層的色帶（開啟渲染後先前會變回日累積門檻）
  const bands=_modeBandsRgb();
  let bi=0; while(bi<bands.length-1 && 2>=bands[bi].max) bi++;
  const rgb=bands[bi].rgb;
  ok(!(rgb[0]>230&&rgb[1]>230&&rgb[2]>230),
     `③渲染層 2mm/h 不是白色（rgb ${rgb.join(',')}）`);

  // ④ 圖例：標題與色帶都必須是時雨量，且與地圖同色
  renderLegend();
  const lgTxt=(document.getElementById('cb-title')||{}).textContent||'';
  ok(/mm\/h/.test(lgTxt), `④圖例標題為時雨量（實得「${lgTxt}」）`);
  ok(_isHourlyRainWin() && rainColor(2,HOURLY_SCALE)===f.fillColor,
     '④圖例色階與地圖填色同源（使用者反映的分歧已消除）');

  // ── 反向保護：同一視窗下「非逐時」的累積量不得被時雨量色階高估 ──
  const accDaily={totalRain:40};
  ok(rainColor(40,_rainScaleOf(accDaily))==='#FFFF00',
     '退回累積量的 40mm 仍為黃色（未被誤畫成時雨量的紅色）');

  // ── 還原，並確認切回六小時累積後顏色回到日累積語意 ──
  _hourIdx=null; winKey='fut6';
  ok(_isHourlyRainWin()===false, '切回六小時累積：恢復累積雨量語意');
  const bands6=_modeBandsRgb();
  ok(bands6[0].max===RAIN_SCALE_CWA[0].max,
     `切回後渲染層恢復 CWA 官方色帶（第一級 ${bands6[0].max}）`);

  mode=savedMode; _hourIdx=savedHour; winKey=savedWin;
  renderLegend();
  return {fails, log};
 });
 console.log(R.log.join('\n'));
 if(errs.length){ console.log('\n*** pageerror ***\n'+errs.join('\n')); }
 console.log(`\n${R.fails.length?'FAIL '+R.fails.length:'ALL PASS'} / ${R.log.length} 項`);
 await b.close();
 process.exit(R.fails.length||errs.length?1:0);
})();

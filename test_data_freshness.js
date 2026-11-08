// 資料新鮮度與氣象署定量降水預報報別標示（2026-11-08 使用者回報：系統 CWA 累積雨量與官方圖資不一致）
//   根因：GitHub 排程被延後，系統停在 11 時報、官方已是 17 時報。
//   守門：① 回推報別 ② 判斷是否落後官方 ③ 標頭顯示延誤 ④ CWA 模式標示報別 ⑤ 不跳警示視窗
const puppeteer=require('puppeteer');
(async()=>{
 const b=await puppeteer.launch({executablePath:'/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
   args:['--no-sandbox','--disable-dev-shm-usage'],defaultViewport:{width:1400,height:880}});
 const pg=await b.newPage(); const errs=[]; let dialogs=0;
 pg.on('pageerror',e=>errs.push(String(e).slice(0,200)));
 pg.on('dialog',d=>{dialogs++; d.dismiss();});
 await pg.goto('http://127.0.0.1:8899/_local.html',{waitUntil:'networkidle2',timeout:120000});
 await new Promise(r=>setTimeout(r,6000));
 const R=await pg.evaluate(()=>{
  const fails=[], log=[];
  const ok=(c,m)=>{ if(!c) fails.push(m); log.push((c?'OK  ':'!!  ')+m); };
  const T=s=>_tpeMs(s);
  ok(_tpeFmt(T('2026-11-08T17:00:00'))==='11/8 17時', '時間格式（臺灣時間，與瀏覽器時區無關）');
  ok(_cwaExpectedSlotMs(T('2026-11-08T19:43:00'))===T('2026-11-08T17:00:00'), '19:43 → 官方最新應為 17 時報');
  ok(_cwaExpectedSlotMs(T('2026-11-08T17:20:00'))===T('2026-11-08T11:00:00'), '17:20（新報尚未上線）→ 仍為 11 時報');
  ok(_cwaExpectedSlotMs(T('2026-11-09T02:00:00'))===T('2026-11-08T23:00:00'), '跨日：02:00 → 前一日 23 時報');
  // 舊資料（無 cwa_qpf_issued）由窗回推
  ok(_cwaIssuedFromData({cwa_qpf_windows:[{start:'2026-11-08T11:00:00',order:0},{start:'2026-11-08T23:00:00',order:1}]})
     ===T('2026-11-08T11:00:00'), '舊資料由各窗起點回推報別');
  ok(_cwaIssuedFromData({cwa_qpf_issued:'2026-11-08T17:00:00'})===T('2026-11-08T17:00:00'), '新資料直接讀 cwa_qpf_issued');
  ok(_cwaIssuedFromData({})===null, '無常態 QPF → null（不顯示報別）');
  const save=_cwaIssuedMs;
  _cwaIssuedMs=T('2026-11-08T11:00:00');
  const lag=_cwaLag(T('2026-11-08T19:43:00'));
  ok(lag && lag.expMs===T('2026-11-08T17:00:00'), '使用者回報情境：系統 11 時報、19:43 → 判定落後（官方 17 時報）');
  ok(_cwaLag(T('2026-11-08T18:10:00'))===null, '17 時報上線後 90 分內仍在正常更新週期 → 不判落後');
  _cwaIssuedMs=T('2026-11-08T17:00:00');
  ok(_cwaLag(T('2026-11-08T19:43:00'))===null, '系統已是 17 時報 → 不落後');
  // 標頭
  _cwaIssuedMs=T('2026-11-08T11:00:00'); _dataGenMs=Date.now()-4.5*3600e3;
  _renderBaseTag();
  const tag=document.getElementById('base-tag');
  ok(/已 4\.5 小時未更新/.test(tag.textContent) && /更新時間：\d+\/\d+ \d\d:\d\d/.test(tag.textContent), `標頭顯示延誤（${tag.textContent}）`);
  ok(/採用 \d+\/\d+ 11時報/.test(tag.title), '標頭滑過列出採用的報別');
  ok(tag.style.color!=='', '延誤時標頭改色');
  _dataGenMs=Date.now()-20*60e3; _cwaIssuedMs=_cwaExpectedSlotMs(Date.now());
  _renderBaseTag();
  ok(!/未更新/.test(tag.textContent) && tag.style.color==='', `正常時不加註（${tag.textContent}）`);
  // CWA 模式標頭列出報別
  const t=TOWNSHIPS.find(x=>(x.band_segs||[]).length);
  if(t){
    setModel('cwa'); setWin('fut6_2');
    const h=_modeHeadRow(t);
    ok(/時報/.test(h), 'CWA 模式鄉鎮標頭列出報別');
    ok(!document.getElementById('mCwa').title && !document.getElementById('mCwaf').title, 'CWA／CWA+F 按鈕不加 tooltip（使用者明訂）');
    setModel('blend');
  }
  _cwaIssuedMs=save;
  return {fails,log};
 });
 R.log.forEach(l=>console.log(l));
 if(dialogs) R.fails.push(`跳出警示視窗 ${dialogs} 次`);
 if(errs.length) R.fails.push('頁面錯誤：'+errs.join(' | '));
 console.log(R.fails.length?`\nFAIL ${R.fails.length}：\n`+R.fails.join('\n'):'\nALL PASS');
 await b.close(); process.exit(R.fails.length?1:0);
})();

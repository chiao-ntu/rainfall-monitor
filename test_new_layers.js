// 2026-11-08 新增圖層守門：AR6 危害度、村里界、交通下拉、重要公共設施、各國颱風路徑。
//   ‧ 未開啟前不下載（首頁不被拖慢）
//   ‧ 開得起來、畫得出東西、tooltip 有內容、關得乾淨
//   ‧ 線／面 pane 一律不攔截指標事件（否則鄉鎮點不到）
//   ‧ 不拖慢鄉鎮著色
const puppeteer=require('puppeteer');
(async()=>{
 const b=await puppeteer.launch({executablePath:'/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
   args:['--no-sandbox','--disable-dev-shm-usage'],defaultViewport:{width:1400,height:900}});
 const pg=await b.newPage(); const errs=[], req=[];
 pg.on('pageerror',e=>errs.push(String(e).slice(0,200)));
 pg.on('request',r=>req.push(r.url()));
 await pg.goto('http://127.0.0.1:8899/_local.html',{waitUntil:'networkidle2',timeout:120000});
 await new Promise(r=>setTimeout(r,6000));
 const early=req.filter(u=>/ar6\/|villages\/|transport\.json|schools\.json|multity\.json/.test(u));
 const R=await pg.evaluate(async(early)=>{
  const fails=[], log=[];
  const ok=(c,m)=>{ if(!c) fails.push(m); log.push((c?'OK  ':'!!  ')+m); };
  const wait=ms=>new Promise(r=>setTimeout(r,ms));
  ok(early.length===0, `①未開啟前不下載新圖資（實得 ${early.length} 筆）`);
  ['bAr6','bTrans','bFac','bMty'].forEach(id=>ok(!!document.getElementById(id), `①按鈕 ${id} 存在`));
  ok(!document.querySelector('#layer-row2 button#bRoads'), '①原「道路」按鈕已併入「交通」下拉');
  const bench=()=>{ const t=performance.now(); try{ renderLayer(); }catch(e){} return performance.now()-t; };
  bench(); const base=Math.min(bench(),bench(),bench());

  // ── AR6 ──
  openAr6Menu(document.getElementById('bAr6'));
  const m=document.getElementById('ddAr6');
  ok(m && m.style.display==='block' && m.querySelectorAll('input[type=radio]').length===4,
     '②AR6 下拉：四種情境可選');
  await _ar6Set('flood', true); await wait(1500);
  ok(_ar6.layers.length>=1, `②淹水危害度疊圖 ${_ar6.layers.length} 張`);
  ok(map.getPane('ar6Pane').style.pointerEvents==='none', '②ar6Pane 不攔截點擊');
  // 宜蘭五結（淹水高潛勢）取得等級
  const lv=_ar6LevelAt('flood', L.latLng(24.69, 121.79));
  ok(lv>=3, `②宜蘭五結淹水危害度可讀出（第 ${lv} 級）`);
  const lvMt=_ar6LevelAt('flood', L.latLng(24.45, 121.35));
  ok(lvMt===0, `②中央山脈無淹水危害（實得 ${lvMt}）`);
  for(const [k] of AR6_SC){ await _ar6Scen(k); ok(_ar6.layers.length>=1, `②情境 ${k} 有疊圖`); }
  await _ar6Set('slope', true); await wait(800);
  ok(/坡地危害度/.test(document.getElementById('overlay-legend').innerHTML), '②圖例列出坡地危害度');
  await _ar6Set('flood', false); await _ar6Set('slope', false);
  ok(_ar6.layers.length===0, '②關閉後疊圖移除');

  // ── 交通 ──
  await _trSet('rail', true); await _trSet('hsr', true); await _trSet('st_hsr', true);
  await _trSet('airport', true); await wait(500);
  ok(_tr.data && _tr.data.rail.length>10, `③交通圖資載入（臺鐵 ${_tr.data && _tr.data.rail.length} 條）`);
  ok(map.getPane('railPane').style.pointerEvents==='none', '③railPane 不攔截點擊');
  ok(_tr.pt && _tr.pt.getLayers().length>0, `③車站／機場點 ${_tr.pt && _tr.pt.getLayers().length} 個`);
  ok(/交通/.test(document.getElementById('overlay-legend').innerHTML), '③圖例列出交通');
  const withL=Math.min(bench(),bench(),bench());
  ok(withL < base*2+25, `③不拖慢鄉鎮著色（${base.toFixed(0)}→${withL.toFixed(0)}ms）`);
  await _trSet('road', true); await wait(2000);
  ok(_roadOn, '③下拉「道路」開啟既有道路圖層');
  await _trSet('road', false);
  // ── 2026-11-08 官方路線色／點位統一顯示／實心圖示／不出現縮放級數 ──
  const OFF={'淡水信義線':'#E3002C','板南線':'#0070BD','文湖線':'#C48C31','環狀線':'#FFDB00',
    '桃園機場捷運':'#8246AF','紅線':'#E20B65','橘線':'#FAA73F','烏日文心北屯線（綠線）':'#8EC31C','安坑輕軌':'#C3B091'};
  const allLines=[..._tr.data.mrt,..._tr.data.lrt];
  const badC=Object.entries(OFF).filter(([n,c])=>!allLines.some(f=>f.n===n&&f.c===c));
  ok(!badC.length, `③各線為官方路線色（不符：${badC.map(x=>x[0]).join('、')||'無'}）`);
  ok(_tr.data.rail.filter(f=>/^臺鐵/.test(f.n)).every(f=>f.c==='#090980')
     && _tr.data.hsr.every(f=>f.c==='#C35617'), '③臺鐵／高鐵為官方識別色');
  ok(allLines.filter(f=>!f.depot).every(f=>/^#[0-9A-F]{6}$/i.test(f.c)&&f.c!==TR_NO_OFFICIAL&&f.term),
     '③捷運／輕軌路線全部有官方色與起訖（灰色只用於機廠軌道）');
  ok(allLines.every(f=>f.n), '③沒有未標名稱的捷運／輕軌段（圖資未標名者已依位置標為機廠軌道）');
  ok(allLines.filter(f=>f.depot).every(f=>/機廠（機廠軌道）$/.test(f.n)&&f.c===TR_NO_OFFICIAL), '③機廠軌道以機廠名稱標示、中性灰');
  ok(_tr.data.rail.every(f=>f.n), '③沒有未判定經營者的鐵路段');
  ok(allLines.some(f=>f.n==='紅線'&&f.term==='小港－岡山車站') && allLines.some(f=>f.n==='新北投支線'),
     '③正式名稱與起訖（高雄紅線小港－岡山車站、新北投支線）');
  const stn=_tr.data.st_mrt;
  ok(!stn.some(s=>/出入口|興建中|_[A-Z]{1,2}\d/.test(s.n)), `③捷運站名不含出入口／站碼／興建中（${stn.length} 站）`);
  ok(stn.every(s=>s.ln&&s.ln.length), '③每個捷運站都有所屬路線');
  const gt=stn.find(s=>s.n==='古亭站');
  ok(gt&&gt.ln.length===2&&stn.filter(s=>s.n==='古亭站').length===1, '③轉乘站（古亭）只一站、兩條路線');
  const ja=stn.find(s=>s.n==='景安站');
  ok(ja&&ja.sys.length===2&&ja.ln.includes('環狀線')&&ja.ln.includes('中和新蘆線'), '③跨系統同站（景安）合併');
  map.setView(TW_HOME.center, TW_HOME.zoom, {animate:false}); await wait(300);
  ['st_tra','st_mrt','port','mrt'].forEach(k=>_tr.on[k]=true); renderTransLayer(); await wait(300);
  const cnt=k=>_tr.pt.getLayers().filter(m=>(m.getTooltip()||{})._content!==undefined).length;
  const nAll=_tr.pt.getLayers().length;
  ok(nAll >= 265+12+150+20+250, `③全臺視野（預設縮放）勾選即顯示所有點位（${nAll} 個）`);
  const leg=document.getElementById('overlay-legend').innerHTML;
  ok(!/放大到|級顯示/.test(leg), '③圖例不出現「放大到 N 級顯示」');
  ok(/#E3002C|rgb\(227, 0, 44\)/i.test(leg) && /#8246AF|rgb\(130, 70, 175\)/i.test(leg), '③圖例列出各線官方色');
  const ptTip=_tr.pt.getLayers().map(m=>m.getTooltip()&&m.getTooltip()._content||'').find(t=>/漁港/.test(t))||'';
  ok(/類別<\/span>　漁港/.test(ptTip), '③碼頭 tooltip 列出類別');
  ok(['mCwa','mCwaf'].every(id=>!document.getElementById(id).title), '③CWA／CWA+F 按鈕不加 tooltip');
  const badge=document.querySelector('.tr-badge span');
  const bg=badge?getComputedStyle(badge).backgroundColor:'';
  ok(badge && bg!=='rgba(0, 0, 0, 0)' && bg!=='transparent', `③機場／碼頭為實心色底圖示（${bg}）`);
  map.setView([25.05, 121.52], 13, {animate:false}); await wait(400); renderTransLayer();
  const tipTxt=_tr.pt.getLayers().map(m=>m.getTooltip()&&m.getTooltip()._content||'').join('|');
  ok(/板南線/.test(tipTxt)&&/淡水信義線/.test(tipTxt), '③車站 tooltip 列出所屬路線');
  ok(/出入口（\d+ 處）/.test(tipTxt) && /<span>\w+ 號<\/span><span>[^<]*[路街口站]/.test(tipTxt), '③捷運站 tooltip 列出各出入口位置');
  ok(/站碼/.test(tipTxt) && /站址/.test(tipTxt), '③捷運站 tooltip 列出站碼、站址');
  ok(/路線<\/span>　縱貫線/.test(tipTxt) && /電話/.test(tipTxt), '③臺鐵站 tooltip 列出路線與電話');
  ['rail','hsr','st_hsr','airport','st_tra','st_mrt','port','mrt'].forEach(k=>_tr.on[k]=false); renderTransLayer();
  ok(!_tr.pt || _tr.pt.getLayers().length===0, '③交通關閉後清空');

  // ── 學校 ──
  map.setView([25.03, 121.52], 14, {animate:false}); await wait(400);
  await _facSet('school', true); await wait(600);
  ok(_fac.pt && _fac.pt.getLayers().length>5, `④學校點（臺北市中心 ${_fac.pt && _fac.pt.getLayers().length} 處）`);
  ok(!!_fac.poly, '④放大 14 級顯示校地範圍');
  map.setView(TW_HOME.center, TW_HOME.zoom, {animate:false}); await wait(500); renderFacLayer();
  ok(_fac.pt && _fac.pt.getLayers().length>3000, `④全臺視野勾選即顯示學校（${_fac.pt && _fac.pt.getLayers().length} 所）`);
  ok(!/放大到|級顯示/.test(document.getElementById('overlay-legend').innerHTML), '④圖例不出現縮放級數');
  const stip=_fac.pt.getLayers()[0].getTooltip()._content;
  ok(/學制/.test(stip) && /校地面積/.test(stip), '④學校 tooltip 列出學制與校地面積');
  ok(_fac.school.feats.every(f=>!/[校學部院]\d+$/.test(f.n)), '④校名不帶圖資流水號');
  await _facSet('school', false);
  map.setView([25.03, 121.52], 14, {animate:false}); await wait(400);

  // ── 村里界（跟行政區名綁定）──
  if(!showTownName) toggleTownNameLayer();
  await wait(2500);
  ok(!!_vill.layer, '⑤行政區名開啟且放大 ≥12 → 村里界');
  ok(_vill.lbl && _vill.lbl.getLayers().length>5, `⑤村里名稱 ${_vill.lbl && _vill.lbl.getLayers().length} 個`);
  ok(map.getPane('villPane').style.pointerEvents==='none', '⑤villPane 不攔截點擊');
  map.setZoom(10, {animate:false}); await wait(800);
  ok(!_vill.layer, '⑤縮小到 12 級以下不畫村里');
  toggleTownNameLayer(); await wait(300);

  // ── 各國颱風路徑 ──
  await _mtyLoad();
  ok(_mtyGroups().size>=2, `⑥各國路徑分組 ${_mtyGroups().size} 組`);
  if(!showTyphoon) toggleTyphoonLayer(); await wait(500);
  ok(_mty.layer && _mty.layer.getLayers().length>0, '⑥颱風圖層開啟時畫出各國路徑');
  const k0=[..._mtyGroups().keys()][0];
  const n0=_mty.layer.getLayers().length;
  _mtySet(k0, false); await wait(300);
  ok(_mty.layer.getLayers().length < n0, `⑥取消勾選「${k0}」後該組不畫（${n0}→${_mty.layer.getLayers().length}）`);
  ok(JSON.parse(localStorage.getItem(MTY_LS)||'[]').indexOf(k0)<0, '⑥勾選記住（localStorage）');
  _mtyAll(true); toggleTyphoonLayer(); await wait(200);
  ok(!_mty.layer, '⑥颱風圖層關閉時各國路徑一起移除');
  return {fails, log};
 }, early);
 console.log(R.log.join('\n'));
 if(errs.length) console.log('\n*** pageerror ***\n'+errs.join('\n'));
 console.log(`\n${R.fails.length?'FAIL '+R.fails.length:'ALL PASS'} / ${R.log.length} 項`);
 await b.close();
 process.exit(R.fails.length||errs.length?1:0);
})();

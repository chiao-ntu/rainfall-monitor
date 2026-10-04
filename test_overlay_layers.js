// 道路／避難收容所疊圖：開得起來、關得乾淨、而且不可拖慢或擋住既有功能。
//
// 最重要的兩條（過去踩過的坑）：
//   ‧ Canvas 渲染下 pane 是整片元素，不關指標事件就會蓋住鄉鎮色塊 → 地圖點不到
//   ‧ 疊圖不得拖慢 renderLayer（鄉鎮著色是主要功能，不能被選用圖層拖累）
const puppeteer=require('puppeteer');
(async()=>{
 const b=await puppeteer.launch({executablePath:'/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
   args:['--no-sandbox','--disable-dev-shm-usage'],defaultViewport:{width:1400,height:900}});
 const pg=await b.newPage(); const errs=[];
 pg.on('pageerror',e=>errs.push(String(e).slice(0,170)));
 await pg.goto('http://127.0.0.1:8899/_local.html',{waitUntil:'networkidle2',timeout:90000});
 await new Promise(r=>setTimeout(r,8000));
 const R=await pg.evaluate(async()=>{
  const fails=[], log=[];
  const ok=(c,m)=>{ if(!c) fails.push(m); log.push((c?'OK  ':'!!  ')+m); };
  const wait=ms=>new Promise(r=>setTimeout(r,ms));

  // ── ① 按鈕存在且在行政區名右邊 ──
  const tn=document.getElementById('bTownName');
  const rd=document.getElementById('bRoads');
  const sh=document.getElementById('bShelter');
  ok(!!rd && !!sh, '①兩個按鈕都存在');
  ok(tn && rd && (tn.compareDocumentPosition(rd) & Node.DOCUMENT_POSITION_FOLLOWING),
     '①道路按鈕排在「行政區名」之後');
  ok(tn && sh && tn.parentElement===rd.parentElement && rd.parentElement===sh.parentElement,
     '①三個按鈕在同一列（圖層列第二排）');

  // ── ② 未按下前不得載入資料（延遲載入）──
  ok(typeof _roadData!=='undefined' && _roadData===null,
     '②未按下前道路資料未載入（避免拖慢首頁）');

  // ── ③ 基準：未開疊圖時的 renderLayer 耗時 ──
  const bench=()=>{ const t=performance.now(); try{ renderLayer(); }catch(e){}
                    return performance.now()-t; };
  bench(); const base=Math.min(bench(),bench(),bench());

  // ── ④ 開啟道路（縮放到門檻以上）──
  map.setZoom(Math.max(map.getZoom(), ROAD_MIN_ZOOM), {animate:false});
  await wait(300);
  const t0=performance.now();
  await toggleRoadLayer();
  await wait(1200);
  const onMs=Math.round(performance.now()-t0);
  ok(_roadOn && _roadData, `④道路圖資載入成功（${(_roadData.features||[]).length} 段，${onMs}ms）`);
  ok(!!_roadLayer, '④道路圖層已加到地圖');

  // ── ⑤ pane 設定：必須在 townPane 之上、且不攔截點擊 ──
  const rp=map.getPane('roadPane');
  ok(!!rp, '⑤roadPane 已建立');
  ok(rp && +rp.style.zIndex > +map.getPane('townPane').style.zIndex,
     `⑤roadPane(${rp&&rp.style.zIndex}) 在 townPane(${map.getPane('townPane').style.zIndex}) 之上`);
  ok(rp && rp.style.pointerEvents==='none',
     '⑤roadPane 關閉指標事件（否則整片 canvas 會蓋住鄉鎮、地圖點不到）');

  // ── ⑥ 不得拖慢既有鄉鎮著色 ──
  const withRoad=Math.min(bench(),bench(),bench());
  ok(withRoad < base*2 + 20,
     `⑥renderLayer 未被拖慢：無疊圖 ${base.toFixed(0)}ms → 有道路 ${withRoad.toFixed(0)}ms`);

  // ── ⑦ 橋樑旗標有保留（使用者問「道路有包含橋樑嗎」）──
  const fs=_roadData.features||[];
  const nb=fs.filter(f=>f.properties&&f.properties.b).length;
  const nt=fs.filter(f=>f.properties&&f.properties.t).length;
  ok(nb>1000, `⑦含橋樑並可辨識：${nb} 段橋樑、${nt} 段隧道`);

  // ── ⑧ 縮放門檻：縮太遠不繪，並在按鈕上說明原因 ──
  map.setZoom(ROAD_MIN_ZOOM-2,{animate:false});
  renderRoadLayer();
  ok(!_roadLayer, '⑧縮放小於門檻時不繪製（全臺視野下 29k 條線只會糊成一團）');
  ok(/放大/.test(document.getElementById('bRoads').textContent),
     `⑧按鈕說明原因（實得「${document.getElementById('bRoads').textContent}」）`);
  map.setZoom(ROAD_MIN_ZOOM,{animate:false});
  renderRoadLayer();
  ok(!!_roadLayer, '⑧放大回門檻後恢復繪製');

  // ── ⑨ 避難收容所 ──
  await toggleShelterLayer();
  await wait(600);
  ok(_shelterOn && _shelterData, `⑨收容所載入成功（${(_shelterData.features||[]).length} 處）`);
  ok(!!_shelterLayer, '⑨收容所圖層已加到地圖');

  // ── ⑩ 關閉後必須清乾淨 ──
  await toggleRoadLayer();
  await toggleShelterLayer();
  ok(!_roadLayer && !_shelterLayer, '⑩兩個圖層關閉後都已自地圖移除');
  const after=Math.min(bench(),bench(),bench());
  ok(after < base*2 + 20,
     `⑩關閉後 renderLayer 回到基準（${after.toFixed(0)}ms vs 基準 ${base.toFixed(0)}ms）`);

  return {fails, log};
 });
 console.log(R.log.join('\n'));
 if(errs.length) console.log('\n*** pageerror ***\n'+errs.join('\n'));
 console.log(`\n${R.fails.length?'FAIL '+R.fails.length:'ALL PASS'} / ${R.log.length} 項`);
 await b.close();
 process.exit(R.fails.length||errs.length?1:0);
})();

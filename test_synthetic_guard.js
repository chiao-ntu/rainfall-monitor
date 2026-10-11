// 前端的合成資料保險：data.json 仍是舊的（含偽造值、adaptive_blend 仍列 gem）
// 時，前端必須自己判定並排除，部署當下就生效。
// ===================================================================
// 使用者回報：「剛剛的資料部署上去之後還是有參照 gem 資料，沒有進行排除」——
// 因為後端修好了，線上那份 data.json 卻還是舊後端產的。
// 所以判準不能是「後端說哪個不能用」，必須是「從資料本身看得出不是預報」。
const fs=require('fs'), pup=require('puppeteer');
const E=[process.env.PUPPETEER_EXECUTABLE_PATH,
  '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  '/opt/pw-browsers/chromium/chrome-linux/chrome'].find(p=>p&&fs.existsSync(p));
const L={args:['--no-sandbox','--disable-dev-shm-usage']}; if(E)L.executablePath=E;
const FIX=JSON.parse(fs.readFileSync('fixture_fabricated_20261011.json','utf8'));
const sf=[], ok=(c,m)=>{ if(!c) sf.push(m); };

(async()=>{
 const b=await pup.launch(L), pg=await b.newPage();
 pg.on('pageerror',e=>sf.push('頁面例外：'+e.message));
 await pg.goto('http://127.0.0.1:8899/_local.html',{waitUntil:'networkidle2',timeout:180000});
 await new Promise(r=>setTimeout(r,2500));

 const r=await pg.evaluate((FIX)=>{
   // 把 2026-10-11 07:30 的**實際輸出**灌進去（含偽造值）
   const by={}; FIX.townships.forEach(x=>by[x.county+x.township]=x);
   TOWNSHIPS.length=0;
   FIX.townships.forEach(x=>{ TOWNSHIPS.push(Object.assign({}, x)); });
   TOWNSHIPS.forEach(t=>{ TMAP[t.county+t.township]=t; });
   window.ADAPTIVE_BLEND=FIX.adaptive_blend;        // 舊的，仍列 gem=1.2
   const det=detectSynthetic(TOWNSHIPS);
   _FAKE_BLOCK=det.blocked; _FAKE_WHY=det.why;
   const out={caught:Array.from(det.blocked).sort(), why:det.why,
     abHasGem:Object.keys(FIX.adaptive_blend).filter(z=>
       (FIX.adaptive_blend[z].models||{}).gem!=null)};
   // 融合是否真的不再用到它們
   const t0=TOWNSHIPS.find(t=>t.alert_val>0 && (t.qpf_gem||[]).length>8);
   out.town=t0.county+t0.township;
   out.gemRaw=(t0.qpf_gem||[]).slice(0,4);
   TOWNSHIPS.forEach(t=>{ t._blendCache=null; t._blendKey=null; });
   out.blend=_blendQpf(t0).slice(0,4);
   // 沒有 adaptive_blend 的退路路徑也要擋得住
   window.ADAPTIVE_BLEND={};
   TOWNSHIPS.forEach(t=>{ t._blendCache=null; t._blendKey=null; });
   out.blendFallback=_blendQpf(t0).slice(0,4);
   // 總量離譜：只示警不排除（真的極端事件不能被消音）
   out.suspect=det.suspect||{};
   // 單一模式報颱風、別人沒報 → 必須仍留在融合裡
   const T2=TOWNSHIPS.map(t=>Object.assign({},t));
   T2.forEach(t=>{ t.qpf_ukmo=(t.qpf_ukmo||[]).map(v=>v==null?v:v+120); });
   const d2=detectSynthetic(T2);
   out.extremeBlocked=d2.blocked.has('ukmo');
   out.extremeSuspect=!!(d2.suspect||{}).ukmo;
   out.excl={gem:blendExcluded('gem'), icon:blendExcluded('icon'),
             graphcast:blendExcluded('graphcast'),
             best:blendExcluded('best'), ecmwf:blendExcluded('ecmwf'),
             gfs:blendExcluded('gfs'), jma:blendExcluded('jma'),
             aifs:blendExcluded('aifs'), ukmo:blendExcluded('ukmo'),
             mf:blendExcluded('mf')};
   try{ _showSyntheticNotice();
        out.notice=(document.getElementById('synthetic-notice')||{}).innerText||''; }
   catch(e){ out.noticeErr=e.message; }
   return out;
 }, FIX);
 await b.close();

 ok(r.abHasGem.length>=3, `fixture 的 adaptive_blend 應仍列著 gem（才測得出保險有用），實得 ${r.abHasGem}`);
 ['gem','icon','kma','cma'].forEach(m=>
   ok(r.caught.includes(m), `沒抓到合成資料：${m}`));
 ['best','ecmwf','gfs','jma','aifs','ukmo','mf'].forEach(m=>
   ok(!r.excl[m], `真實模式被誤殺：${m}`));
 ok(r.excl.gem && r.excl.icon, '判定出來了，blendExcluded 卻放行');
 ok(r.excl.graphcast===false, 'graphcast/gc 鍵對應錯誤（真實模式被誤擋）');
 const gemSum=r.gemRaw.reduce((a,v)=>a+(v||0),0);
 const blSum=r.blend.reduce((a,v)=>a+(v||0),0);
 const fbSum=r.blendFallback.reduce((a,v)=>a+(v||0),0);
 ok(gemSum>20, `fixture 的 gem 應有大量假雨，實得 ${gemSum}`);
 ok(blSum < gemSum*0.25, `融合仍受假資料帶動：gem 24h ${gemSum.toFixed(1)}mm、融合 ${blSum.toFixed(1)}mm`);
 ok(fbSum < gemSum*0.25, `無 adaptive_blend 的退路路徑沒擋住：${fbSum.toFixed(1)}mm`);
 ok(/排除出 FORMOSA 融合/.test(r.notice||''), '沒有顯示排除提示（沉默正是這次事故的放大器）');
 ok(!r.extremeBlocked, '單一模式報出極端事件被當成合成資料排除 —— 把真的極端預報消音是最危險的失效');
 ok(r.extremeSuspect, '單一模式報出遠高於其他模式的量，卻完全沒有示警');

 console.log(`   判定為合成資料：${r.caught.join('、')}`);
 Object.keys(r.why).forEach(m=>console.log(`     ${m}：${r.why[m]}`));
 console.log(`   ${r.town}　gem 原值 ${JSON.stringify(r.gemRaw)}（24h ${gemSum.toFixed(1)}mm）`);
 console.log(`   融合 24h：${blSum.toFixed(1)} mm（無 adaptive_blend 退路：${fbSum.toFixed(1)} mm）`);
 console.log(`   單一模式報極端事件（UKMO +120mm/段）：排除=${r.extremeBlocked}（須為 false）、示警=${r.extremeSuspect}`);
 if(sf.length){ console.log('❌ test_synthetic_guard 失敗 '+sf.length+' 項');
   sf.forEach(x=>console.log('   - '+x)); process.exit(1); }
 console.log('✅ test_synthetic_guard 全數通過（19 項）');
})().catch(e=>{console.error(e);process.exit(1)});

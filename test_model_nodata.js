// 模式陣列為空時：必須是「無資料」，不得變成 0mm，也不得讓融合崩掉
const fs=require('fs'), pup=require('puppeteer');
const E=[process.env.PUPPETEER_EXECUTABLE_PATH,'/opt/pw-browsers/chromium-1194/chrome-linux/chrome','/opt/pw-browsers/chromium/chrome-linux/chrome'].find(p=>p&&fs.existsSync(p));
const L={args:['--no-sandbox','--disable-dev-shm-usage']}; if(E)L.executablePath=E;
const REAL=JSON.parse(fs.readFileSync('_real_qpf.json','utf8'));
(async()=>{
 const b=await pup.launch(L), pg=await b.newPage(); const errs=[];
 pg.on('pageerror',e=>errs.push(e.message));
 await pg.goto('http://127.0.0.1:8899/_local.html',{waitUntil:'networkidle2',timeout:180000});
 await new Promise(r=>setTimeout(r,2500));
 const r=await pg.evaluate((REAL)=>{
   // 把真實的 QPF 陣列灌進前 40 個鄉鎮
   const byKey={}; REAL.towns.forEach(x=>byKey[x.county+x.township]=x);
   window.ADAPTIVE_BLEND=REAL.adaptive_blend;
   let n=0;
   TOWNSHIPS.forEach(t=>{ const r0=byKey[t.county+t.township]; if(!r0) return;
     Object.keys(r0).forEach(k=>{ if(k.startsWith('qpf_')||k.endsWith('_segs')) t[k]=r0[k]; });
     t._blendCache=null; t._blendKey=null; n++; });
   const t0=TOWNSHIPS.find(t=>byKey[t.county+t.township]);
   const out={n, zone:_townZone(t0), town:t0.county+t0.township};
   out.before={blend:_blendQpf(t0).slice(0,8),
     gem:(t0.qpf_gem||[]).slice(0,4), icon:(t0.qpf_icon||[]).slice(0,4)};
   // 清空本輪未抓到的模式
   TOWNSHIPS.forEach(t=>{ t.qpf_icon=[];t.qpf_kma=[];t.qpf_cma=[];t.qpf_gem=[];
     t._blendCache=null;t._blendKey=null;t._hiloArr=null;t._hiloKey=null; });
   const bl=_blendQpf(t0);
   out.after={blend:bl.slice(0,8)};
   out.iconArr=getQpfArr(t0,'qpf_icon').slice(0,8);
   out.hilo={hi:getQpfArr(t0,'qpf_hi').slice(0,4), lo:getQpfArr(t0,'qpf_lo').slice(0,4)};
   //  ★ 真正要驗的是「使用者看到什麼」：選到沒資料的模式，
   //    畫面必須說無資料，而不是畫成一片 0mm。
   const _m0=forecastModel, _mo0=mode;
   forecastModel='icon'; mode='rain'; qpfField=MODEL_FIELD['icon'];
   TOWNSHIPS.forEach(t=>{ t._accCache=null; t._accKey=null; });
   out.warnIcon=_coverageWarn();
   out.accIcon=(getAccum(t0,'rain')||{}).totalRain;
   forecastModel='best'; qpfField=MODEL_FIELD['best'];
   TOWNSHIPS.forEach(t=>{ t._accCache=null; t._accKey=null; });
   out.warnBest=_coverageWarn();
   out.accBest=(getAccum(t0,'rain')||{}).totalRain;
   forecastModel=_m0; mode=_mo0; qpfField=MODEL_FIELD[_m0]||qpfField;
   out.blendFinite=bl.slice(0,8).filter(v=>v!=null&&isFinite(v)).length;
   try{ renderLayer(); renderStationList(); renderRankList(); }
   catch(e){ out.renderErr=e.message; }
   return out;
 }, REAL);
 await b.close();
 const sf=[], ok=(c,m)=>{ if(!c) sf.push(m); };
 ok(r.n>=20, `只灌到 ${r.n} 個鄉鎮，測試會空轉`);
 ok(r.before.blend.some(v=>v>1), `清空前融合應該有明顯的雨（才測得出差異），實得 ${JSON.stringify(r.before.blend)}`);
 ok(r.iconArr.every(v=>v==null), `空模式的段沒有回 null：${JSON.stringify(r.iconArr)}`);
 ok(!r.iconArr.some(v=>v===0), '空模式被當成 0mm（把「沒資料」說成「不會下雨」）');
 ok(r.blendFinite>0, '清空 4 個模式後融合整個沒值了（不該因此崩掉）');
 ok(!r.renderErr, '重繪例外：'+r.renderErr);
 ok(r.warnIcon, '選到沒資料的模式時，畫面沒有提示「此模式目前無資料」');
 ok(r.accIcon == null, `沒資料的模式算出了累積值 ${r.accIcon}（應為 null）`);
 ok(!r.warnBest, '有資料的模式被誤判為無資料（誤殺）');
 ok(r.accBest != null, '有資料的模式算不出累積值');
 errs.forEach(e=>sf.push('頁面例外：'+e));
 const s0=r.before.blend.reduce((a,v)=>a+(v||0),0), s1=r.after.blend.reduce((a,v)=>a+(v||0),0);
 console.log(`   ${r.town}（${r.zone}）　gem 原值 ${JSON.stringify(r.before.gem)}`);
 console.log(`   融合 48h：清空前 ${s0.toFixed(1)} mm → 清空後 ${s1.toFixed(1)} mm`);
 console.log(`   選到 ICON（已清空）：累積=${r.accIcon}　提示「${r.warnIcon||'（無）'}」`);
 console.log(`   選到 BEST（有資料）：累積=${r.accBest}　提示「${r.warnBest||'（無）'}」`);
 if(sf.length){ console.log('❌ 失敗 '+sf.length+' 項'); sf.forEach(s=>console.log('   - '+s)); process.exit(1); }
 console.log('✅ 空模式處理正確（null 不等於 0，融合不崩）');
})().catch(e=>{console.error(e);process.exit(1)});

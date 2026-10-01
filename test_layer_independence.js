// 切換圖層時，「語意上固定」的數值不得跟著變
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
  const MODES=['rain','etr','risk','warn','wind','wave','temp'];
  const saved=mode;

  // 取一個有警戒值的鄉鎮當樣本
  const t=TOWNSHIPS.find(x=>x.alert_val>0) || TOWNSHIPS[0];

  // ① _summaryRain：切任何圖層都必須相同
  const vals={};
  MODES.forEach(m=>{ mode=m; vals[m]=_summaryRain(t); });
  mode=saved;
  const uniq=[...new Set(Object.values(vals).map(v=>JSON.stringify(v)))];
  ok(uniq.length===1,
     `_summaryRain 跨 ${MODES.length} 個圖層一致：${JSON.stringify(vals)}`);

  // ② getAccum(t,'rain') 的 totalRain 與 etrPct 都不得隨圖層變
  const rr={}, ee={};
  MODES.forEach(m=>{ mode=m; const a=getAccum(t,'rain');
    rr[m]=a&&a.totalRain; ee[m]=a&&a.etrPct; });
  mode=saved;
  ok([...new Set(Object.values(rr).map(String))].length===1,
     `getAccum(t,'rain').totalRain 一致：${JSON.stringify(rr)}`);
  ok([...new Set(Object.values(ee).map(String))].length===1,
     `getAccum(t,'rain').etrPct 一致：${JSON.stringify(ee)}`);

  // ③ 對照：不指定 forceMode 時本來就該隨圖層變（這是設計，不是 bug）
  const raw={};
  MODES.forEach(m=>{ mode=m; const a=getAccum(t); raw[m]=a&&a.totalRain; });
  mode=saved;
  ok([...new Set(Object.values(raw).map(String))].length>1,
     `對照組：不指定時確實隨圖層變（${JSON.stringify(raw)}）`);

  // ④ 呼叫後全域 mode 必須還原，不可被污染
  mode='warn';
  getAccum(t,'rain'); _summaryRain(t);
  ok(mode==='warn', `呼叫後全域 mode 已還原（現為 ${mode}）`);
  mode=saved;

  // ⑤ 各分署警戒摘要：整個面板在不同圖層下的「累積雨量」區塊必須相同
  const snaps=MODES.map(m=>{ mode=m;
    try{ updateDistrictSummary(); }catch(e){ return 'ERR'; }
    const el=document.getElementById('district-summary');
    const txt=el?el.textContent.replace(/\s+/g,' '):'';
    const i=txt.indexOf('累積雨量'), j=txt.indexOf('ETR2%');
    return (i>=0&&j>i)?txt.slice(i,j):txt.slice(0,200);
  });
  mode=saved; try{ updateDistrictSummary(); }catch(e){}
  const su=[...new Set(snaps)];
  ok(su.length===1,
     `各分署摘要的「累積雨量」區塊跨圖層一致（相異版本數 ${su.length}）`);
  if(su.length>1) log.push('    差異範例：'+su.slice(0,2).map(x=>x.slice(0,90)).join(' ||| '));
  return {log, fails};
 });
 R.log.forEach(l=>console.log(l));
 console.log('errs:',errs.slice(0,3));
 console.log(R.fails.length?`\n失敗 ${R.fails.length}`:'\n全部通過');
 await b.close(); process.exit(R.fails.length?1:0);
})();

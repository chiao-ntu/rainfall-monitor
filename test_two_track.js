// 兩條軌道的行為驗證：同一批成員，平均 vs 加權 85 分位數
const puppeteer=require('puppeteer');
(async()=>{
 const b=await puppeteer.launch({executablePath:'/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
   args:['--no-sandbox','--disable-dev-shm-usage'],defaultViewport:{width:1300,height:860}});
 const pg=await b.newPage(); const errs=[];
 pg.on('pageerror',e=>errs.push(String(e).slice(0,160)));
 await pg.goto('http://127.0.0.1:8899/_local.html',{waitUntil:'networkidle2',timeout:90000});
 await new Promise(r=>setTimeout(r,8000));
 const R=await pg.evaluate(()=>{
  const fails=[];
  const ok=(c,m)=>{ if(!c) fails.push(m); return (c?'OK  ':'!!  ')+m; };
  const log=[];
  // 取一個真實鄉鎮當載體，換掉各模式的 QPF 陣列
  const t=Object.assign({}, TOWNSHIPS[0]);
  const put=(vals)=>{ Object.entries(vals).forEach(([k,v])=>{
      const f=MODEL_FIELD[k]; if(f) t[f]=[v]; }); };
  const clear=()=>{ Object.values(MODEL_FIELD).forEach(f=>{ delete t[f]; }); 
    delete t._blendCache; delete t._warnCache; delete t._blendKey; delete t._warnKey;
    t.official_segs=[]; t.qpf_cwa=[]; };

  // ① 成員分散時，警戒軌道必須高於量的軌道
  clear(); put({best:10, ecmwf:12, gfs:15, jma:40, aifs:60, gc:18});
  let mean=_blendQpf(t,'mean')[0], warn=_blendQpf(t,'warn')[0];
  log.push(ok(warn>mean, `分散情境：警戒 ${warn} > 平均 ${mean}`));
  log.push(ok(warn<=60, `警戒不超過最大成員（${warn} ≤ 60）`));

  // ② 成員一致時，兩條軌道應該幾乎相同
  clear(); put({best:20, ecmwf:20, gfs:20, jma:20, aifs:20, gc:20});
  mean=_blendQpf(t,'mean')[0]; warn=_blendQpf(t,'warn')[0];
  log.push(ok(Math.abs(warn-mean)<0.6, `一致情境：警戒 ${warn} ≈ 平均 ${mean}`));

  // ③ 單一模式亂報：佐證原則仍生效，警戒不該被它綁架
  clear(); put({best:5, ecmwf:5, gfs:5, jma:5, aifs:5, gc:300});
  mean=_blendQpf(t,'mean')[0]; warn=_blendQpf(t,'warn')[0];
  log.push(ok(warn<300, `孤例 300mm：警戒 ${warn} 未被單一模式綁架`));
  log.push(ok(warn>=mean, `但仍 ≥ 平均（${warn} ≥ ${mean}）`));

  // ④ 兩份快取不互相覆寫
  clear(); put({best:10, ecmwf:12, gfs:15, jma:40, aifs:60, gc:18});
  const m1=getQpfArr(t,'qpf_blend')[0], w1=getQpfArr(t,'qpf_warn')[0];
  const m2=getQpfArr(t,'qpf_blend')[0];
  log.push(ok(m1===m2 && w1!==m1, `快取獨立：量 ${m1}/${m2}、警戒 ${w1}`));

  // ⑤ ETR2 走警戒軌道
  clear(); put({best:10, ecmwf:12, gfs:15, jma:40, aifs:60, gc:18});
  t.etr2=null; t.alert_val=300;
  const eB=_calcEtr2Raw(t,0,'qpf_blend'), eW=_calcEtr2Raw(t,0,'qpf_warn');
  const eVia=calcEtr2AtSeg(t,0,'qpf_blend');
  log.push(ok(Math.abs(eVia-eW)<1e-6 && Math.abs(eVia-eB)>1e-9,
    `ETR2 已改走警戒軌道（${eVia.toFixed(2)} ＝警戒 ${eW.toFixed(2)}，非平均 ${eB.toFixed(2)}）`));
  return {log, fails};
 });
 R.log.forEach(l=>console.log(l));
 console.log('errs:',errs.slice(0,3));
 console.log(R.fails.length?`\n失敗 ${R.fails.length}`:'\n全部通過');
 await b.close(); process.exit(R.fails.length?1:0);
})();

// 過去的觀測必須是觀測：不得以模式形狀分配，也不得以觀測回推 ETR2。
//
// 使用者回報（2026-10-06）：同一段已經過去的時間，兩次排程畫出來的時雨量不一樣
//   10/4 18時附近：一次 27/34/62、一次 88/49/17。
// 根因：缺逐時觀測的小時是用「官方日觀測總量 × 模式逐時形狀」分配補齊，
//   而 qpf_1h_p48（模式過去回算）每一輪都會更新 —— 總量是官方的，
//   分布是模式的。已經發生過的觀測不可能改變，所以那不是觀測。
//
// 同時守住 ETR2：過去段沒有官方歷史就留白，不以觀測回推冒充。
const fs=require('fs');
const puppeteer=require('puppeteer');
const _EXEC=[process.env.PUPPETEER_EXECUTABLE_PATH,
  '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  '/opt/pw-browsers/chromium/chrome-linux/chrome'].find(p=>p&&fs.existsSync(p));
const _LAUNCH={args:['--no-sandbox','--disable-dev-shm-usage']};
if(_EXEC) _LAUNCH.executablePath=_EXEC;

(async()=>{
 const src=fs.readFileSync('index.html','utf8');
 const sf=[];
 if(/const w = dayShapeSum\[di\] > 0/.test(src))
   sf.push('_hourlyBars 又出現以模式形狀分配過去時雨量');
 if(/cache\[sg\] = \(v == null \|\| !isFinite\(v\)\) \? 0 : v;/.test(src))
   sf.push('逐時 ETR2 又把 null 轉成 0（會畫成貼地的假線）');

 const b=await puppeteer.launch(Object.assign({},_LAUNCH,{defaultViewport:{width:1400,height:880}}));
 const pg=await b.newPage(); const errs=[];
 pg.on('pageerror',e=>errs.push(String(e).slice(0,170)));
 await pg.goto('http://127.0.0.1:8899/_local.html',{waitUntil:'networkidle2',timeout:90000});
 await new Promise(r=>setTimeout(r,8000));
 const R=await pg.evaluate(()=>{
  const fails=[], log=[];
  const ok=(c,m)=>{ if(!c) fails.push(m); log.push((c?'OK  ':'!!  ')+m); };

  const mk=(shape)=>{
    const obs=new Array(48).fill(null);
    for(let i=20;i<48;i++) obs[i]=(i%7);        // 只有後 28h 有逐時觀測
    return {county:'宜蘭縣',township:'南澳鄉',alert_val:400,etr2_alert:400,etr2:200.9,
      obs_1h_p48:obs, daily_rain:[30,120,180,0,0,0,0], rain_24h:120,
      qpf_1h_p48:new Array(48).fill(shape),     // 模式形狀，每輪會變
      qpf_best:new Array(60).fill(0), qpf_warn:new Array(60).fill(0),
      official_segs:[], band_segs:[]};
  };

  // ① 無逐時觀測的小時必須留白
  const b1=_hourlyBars(mk(9));
  const past1=b1.vals.slice(0,48);
  ok(b1.nObs===28 && b1.nGap===20,
     `①觀測 ${b1.nObs}h／無觀測 ${b1.nGap}h`);
  ok(past1.slice(0,20).every(v=>v==null),
     '①無逐時觀測的小時留白（不以模式形狀分配）');

  // ② 模式形狀改變時，過去的長條不得跟著變 —— 這是本案的核心
  const b2=_hourlyBars(mk(99));
  const past2=b2.vals.slice(0,48);
  const same=past1.every((v,i)=>(v==null&&past2[i]==null)||v===past2[i]);
  ok(same,
     '②模式形狀從 9 改成 99，過去的時雨量完全沒變（已發生的觀測不可能改變）');

  // ③ 有觀測的小時必須原樣呈現，不被加工
  ok(past1[30]===(30%7) && past1[47]===(47%7),
     `③有觀測的小時原樣呈現（第30格 ${past1[30]}、第47格 ${past1[47]}）`);

  // ④ 過去 ETR2 沒有官方歷史 → 全部 null
  const t4=mk(9);
  const segs=[-8,-4,-2,-1].map(s=>calcEtr2AtSeg(t4,s,'qpf_best'));
  ok(segs.every(v=>v==null),
     `④無官方歷史時過去段回 null（實得 ${JSON.stringify(segs)}）`);
  const ser=_etr2HourlySeries(t4,-48,-1);
  ok(ser.every(v=>v==null),
     `④逐時序列同樣留白（${ser.filter(v=>v==null).length}/${ser.length}）`);

  // ⑤ 有官方歷史 → 用當時的官方值，不重建
  const H=new Array(36).fill(null); H[32-8]=150; H[32-1]=200;
  const t5=Object.assign(mk(9),{etr2_hist:H, etr2_hist_base:32});
  ok(calcEtr2AtSeg(t5,-8,'qpf_best')===150 && calcEtr2AtSeg(t5,-1,'qpf_best')===200,
     '⑤有官方歷史時取當時的官方值');
  ok(calcEtr2AtSeg(t5,-4,'qpf_best')===null,
     '⑤歷史沒涵蓋的段仍留白（不內插、不回推）');

  // ⑥ 標示要說實話
  const el=document.getElementById('chart-canvas');
  if(el){
    const proto=CanvasRenderingContext2D.prototype, real=proto.fillText;
    let drawn=[];
    proto.fillText=function(s,...a){ drawn.push(String(s)); return real.call(this,s,...a); };
    try{ _drawHourlyChart(t4,'chart-canvas',false); }catch(e){ drawn.push('THREW:'+e.message); }
    proto.fillText=real;
    ok(!drawn.some(s=>/分配呈現|日觀測分配/.test(s)),
       '⑥標示不再宣稱「以日觀測分配」');
    ok(drawn.some(s=>/無觀測（留白）|尚無逐時觀測|全為官方逐時觀測/.test(s)),
       `⑥標示說明留白（實得：${drawn.filter(s=>s.includes('過去48h')).join(' / ')||'無'}）`);
  } else log.push('--  ⑥找不到 chart-canvas，略過');

  return {fails, log};
 });
 console.log(R.log.join('\n'));
 if(sf.length) console.log('\n原始碼層：\n  !! '+sf.join('\n  !! '));
 else console.log('\nOK  原始碼層：模式形狀分配與 null→0 都未復發');
 if(errs.length) console.log('\n*** pageerror ***\n'+errs.join('\n'));
 const n=R.fails.length+sf.length;
 console.log(`\n${n?'FAIL '+n:'ALL PASS'} / ${R.log.length+1} 項`);
 await b.close();
 process.exit(n||errs.length?1:0);
})();

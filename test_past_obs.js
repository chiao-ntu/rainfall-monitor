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

  // ① 無逐時觀測的小時：以官方日總量平均分配（不是留白、也不是模式形狀）
  const b1=_hourlyBars(mk(9));
  const past1=b1.vals.slice(0,48);
  ok(b1.nObs===28 && b1.nGap===20,
     `①逐時觀測 ${b1.nObs}h／以日總量分配 ${b1.nGap}h`);
  ok(past1.slice(0,20).every(v=>v!=null),
     '①官方日觀測存在就必須畫出來（不可因缺逐時觀測而整段消失）');
  const uniq=new Set(past1.slice(0,20).map(v=>Math.round(v*100)));
  ok(uniq.size<=2,
     `①同一日的分配值一致（平均分配，非模式形狀；相異值 ${uniq.size} 種）`);
  ok(b1.estMask.slice(0,20).every(m=>m===true),
     '①分配值標記為 estMask（繪圖時以更暗色呈現，與真觀測可分辨）');

  // ② 模式形狀改變時，過去的長條不得跟著變 —— 這是本案的核心
  const b2=_hourlyBars(mk(99));
  const past2=b2.vals.slice(0,48);
  const same=past1.every((v,i)=>(v==null&&past2[i]==null)||v===past2[i]);
  ok(same,
     '②模式形狀從 9 改成 99，過去的時雨量完全沒變（已發生的觀測不可能改變）');

  // ③ 有觀測的小時必須原樣呈現，不被加工
  ok(past1[30]===(30%7) && past1[47]===(47%7),
     `③有觀測的小時原樣呈現（第30格 ${past1[30]}、第47格 ${past1[47]}）`);

  // ③b 守恆：分配之後，該日的總量必須等於官方日觀測（不多不少）
  //    這是「觀測多少就是多少」的硬性檢驗。
  const t3=mk(9), dr=t3.daily_rain;
  const b3=_hourlyBars(t3);
  const nowH3=b3.nowH;
  const sum=[0,0,0];
  for(let h=b3.hFrom; h<nowH3; h++){
    const i=h-b3.hFrom, di = h>=0?0:(h>=-24?1:2);
    if(b3.vals[i]!=null) sum[di]+=b3.vals[i];
  }
  // 前天（di=2）在視窗內只有一部分，按可見比例分攤
  const visFrac2=(24-nowH3)/24;
  ok(Math.abs(sum[1]-dr[1])<0.5,
     `③b 昨天：分配後總量 ${sum[1].toFixed(1)} ≈ 官方日觀測 ${dr[1]}`);
  ok(Math.abs(sum[2]-dr[2]*visFrac2)<0.5,
     `③b 前天（視窗內 ${(visFrac2*100).toFixed(0)}%）：`+
     `${sum[2].toFixed(1)} ≈ ${(dr[2]*visFrac2).toFixed(1)}`);

  // ③c 瀑布圖：沒有官方 ETR2 的時段不得由 0 線性爬升（會生出不存在的降雨）
  const t3c=mk(9);
  const hs=[-30,-24,-12,-6];
  const wf=hs.map(h=>_etrAtHour(t3c,h,'qpf_best'));
  ok(wf.every(v=>v==null),
     `③c 無官方歷史時瀑布圖不給值（實得 ${JSON.stringify(wf)}）—— `+
     `先前 null 被當 0，再內插到官方值，看起來像「平均增加的雨量」`);

  // ③d ETR2% 一律整數
  const H3=new Array(36).fill(null); H3[32-4]=222.5;
  const t3d=Object.assign(mk(9),{etr2_hist:H3, etr2_hist_base:32, etr2_alert:550});
  const p3=_etrPctAt(t3d,-4,'qpf_best');
  ok(p3!=null && Number.isInteger(p3),
     `③d ETR2% 為整數（222.5/550 → ${p3}，不是 40.5）`);

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

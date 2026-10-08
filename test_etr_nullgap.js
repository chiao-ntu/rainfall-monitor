// 三張逐時圖：ETR2 沒有官方值時必須「斷線留白」，不可畫成 0。
//
// 使用者回報（2026-10-09，各分署逐時降雨圖）：中間一段線掉到 0 再跳回來，
// 看起來像 ETR2 真的歸零。實際上那是「沒有官方值」。
// 成因有兩種，兩種都出現過：
//   (a) 折線迴圈直接 Math.min(null, max) → null 被算成 0，畫出貼地的線
//   (b) 分署聚合 new Array(n).fill(0) 搭配 `if(es[h] > ee[h])`，
//       而 null > 0 為 false，於是全署無值的小時永遠停在 0
// 三張圖（鄉鎮／測站／分署）原本各有一份折線迴圈，同樣的錯出現兩次，
// 故收斂成 _strokeNullableSeries 一支。
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
 if((src.match(/function _strokeNullableSeries/g)||[]).length!==1)
   sf.push('_strokeNullableSeries 不是恰好一份');
 if((src.match(/_strokeNullableSeries\(/g)||[]).length-1 < 3)
   sf.push('三張逐時圖沒有都改用共用折線工具');
 if(/const ee=new Array\(n\)\.fill\(0\);/.test(src))
   sf.push('分署聚合又改回 fill(0)（null>0 為 false，無值小時會停在 0）');
 if(/etr\.forEach\(\(ev,i\)=>\{[\s\S]{0,120}i===0\?ctx\.moveTo/.test(src))
   sf.push('測站圖又出現會把 null 連成線的迴圈');

 const b=await puppeteer.launch(Object.assign({},_LAUNCH,{defaultViewport:{width:1400,height:900}}));
 const pg=await b.newPage(); const errs=[];
 pg.on('pageerror',e=>errs.push(String(e).slice(0,170)));
 await pg.goto('http://127.0.0.1:8899/_local.html',{waitUntil:'networkidle2',timeout:90000});
 await new Promise(r=>setTimeout(r,8000));
 const R=await pg.evaluate(()=>{
  const fails=[], log=[];
  const ok=(c,m)=>{ if(!c) fails.push(m); log.push((c?'OK  ':'!!  ')+m); };

  // ── ① 共用工具本身：null 要斷線，不可落到 y(0) ──
  const pts=[];
  const fakeCtx={beginPath(){}, stroke(){},
    moveTo(x,y){pts.push(['M',Math.round(x),Math.round(y)]);},
    lineTo(x,y){pts.push(['L',Math.round(x),Math.round(y)]);}};
  const arr=[10,20,null,null,40,50];
  _strokeNullableSeries(fakeCtx, arr, i=>i*10, v=>100-v);
  ok(pts.length===4, `①六格含兩個 null → 畫 4 個點（實得 ${pts.length}）`);
  ok(pts[0][0]==='M' && pts[2][0]==='M',
     `①null 之後重新起筆，不連線（實得 ${pts.map(p=>p[0]).join('')}）`);
  ok(!pts.some(p=>p[2]===100),
     `①沒有任何點落在 v=0 的位置（y=100）—— null 未被當成 0`);

  // ── ② 軸上限忽略 null ──
  ok(_axisMaxOfNullable([null,null,35,null],10)>=40,
     `②軸上限由實際值決定（實得 ${_axisMaxOfNullable([null,null,35,null],10)}）`);
  ok(_axisMaxOfNullable([null,null,null],10)===10,
     '②全 null 時回下限，不會變成 0 把軸壓扁');

  // ── ③ 分署聚合：全署無值的小時必須是 null，不是 0 ──
  const _real=_etr2HourlySeries;
  try{
    // 讓所有鄉鎮在第 50~59 格回 null，其餘回 30
    window._etr2HourlySeries=(t,a,b2)=>{
      const n=b2-a+1, o=new Array(n);
      for(let i=0;i<n;i++) o[i]=(i>=50&&i<60)?null:30;
      return o;
    };
    const D=_calcDistrictHourly();
    const er=(D.etrRows||[]).find(r=>r && r.some(v=>v!=null));
    ok(!!er, '③分署聚合有產出序列');
    if(er){
      ok(er[55]===null,
         `③全署都沒有官方值的小時聚合為 null（實得 ${er[55]}）—— 先前是 0`);
      ok(er[40]===30,
         `③有值的小時正常取大（實得 ${er[40]}）`);
      ok(!er.slice(50,60).some(v=>v===0),
         '③缺值區段沒有任何 0（0 會畫成貼地的假線）');
    }
  } finally { window._etr2HourlySeries=_real; }

  return {fails, log};
 });
 console.log(R.log.join('\n'));
 if(sf.length) console.log('\n原始碼層：\n  !! '+sf.join('\n  !! '));
 else console.log('\nOK  原始碼層：折線工具唯一、三張圖都已接上、fill(0) 未復發');
 if(errs.length) console.log('\n*** pageerror ***\n'+errs.join('\n'));
 const n=R.fails.length+sf.length;
 console.log(`\n${n?'FAIL '+n:'ALL PASS'} / ${R.log.length+1} 項`);
 await b.close();
 process.exit(n||errs.length?1:0);
})();

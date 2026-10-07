// 預報校驗面板：名詞必須找得到、看得懂、兩個面板對得起來。
//
// 使用者回報（2026-10-07）：
//   「CSI 跟相對誤差好像在預報校驗裡面看不到」—— 其實兩者都在，但
//   下拉選單寫「相對誤差 30%（基線）」、主欄位卻顯示「CSI」，
//   兩個名字從不同時出現，所以對不起來。
//   「RMSE 好像沒有說明到」—— 有選項但說明區沒寫。
//
// 另外修掉一個真正的設計缺陷：
//   預報校驗的「偏差比」＝報雨地點數÷實際雨點數（>1 報太多）
//   融合校正明細的「偏差比」＝觀測÷模式、直接乘回模式值（>1 模式低估）
//   同一個名字、相反的方向。校正端已改稱「校正倍率」。
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
 // 融合面板不得再用「偏差比」這個名字（與校驗面板方向相反）
 if(/全臺各地形的模式表現（偏差比/.test(src))
   sf.push('融合校正明細又用回「偏差比」（與校驗面板方向相反，必須叫校正倍率）');
 if(/<span style="color:#8a9aaa;font-size:10px">　偏差比 \$\{parts/.test(src))
   sf.push('融合明細的逐模式列又用回「偏差比」');

 const b=await puppeteer.launch(Object.assign({},_LAUNCH,{defaultViewport:{width:1400,height:900}}));
 const pg=await b.newPage(); const errs=[];
 pg.on('pageerror',e=>errs.push(String(e).slice(0,170)));
 await pg.goto('http://127.0.0.1:8899/_local.html',{waitUntil:'networkidle2',timeout:90000});
 await new Promise(r=>setTimeout(r,8000));
 const R=await pg.evaluate(()=>{
  const fails=[], log=[];
  const ok=(c,m)=>{ if(!c) fails.push(m); log.push((c?'OK  ':'!!  ')+m); };

  // 合成校驗資料（含 amt，否則 MAE/RMSE 算不出來）
  const mk=(h,m,f,cn,n,ae,se)=>({hit:h,miss:m,'false':f,correct_neg:cn,
    amt:{n:n,ae:ae,se:se,err:ae*0.3,obs:n*8,mod:n*9},
    thr10:{hit:h,miss:m,'false':f,correct_neg:cn},
    thr80:{hit:Math.round(h/3),miss:m,'false':Math.round(f/2),correct_neg:cn},
    hourly40:{hit:2,miss:5,'false':3,correct_neg:300}});
  const day={'全臺':{}};
  ['blend','best','ecmwf','gfs','jma','aifs','gc','icon','kma','gem','ukmo','mf','cma','bom']
    .forEach((m,i)=>{ day['全臺'][m]=mk(30+i,70-i,40+i,900,200,200+i*10,900+i*80); });
  window.VERIFY_DATA={days:{'2026-10-05':day,'2026-10-06':day,'2026-10-07':day}};

  const sel=document.getElementById('vf-metric');
  ok(!!sel, '①指標下拉存在');
  if(!sel) return {fails, log};
  const vals=[...sel.options].map(o=>o.value);

  // ── ① 每個指標都要畫得出來，且主欄位標題要能對回下拉的用語 ──
  const head={};
  for(const v of vals){
    sel.value=v;
    try{ renderVerify(); }catch(e){ head[v]='THREW:'+e.message; continue; }
    head[v]=[...document.querySelectorAll('#vf-table th')].map(x=>x.textContent.trim()).join('|');
  }
  ok(vals.every(v=>!/THREW/.test(head[v])),
     `①十個指標全部可渲染（實得 ${Object.values(head).filter(h=>/THREW/.test(h)).length} 個失敗）`);

  // ── ② CSI 與「相對誤差」必須同時出現，否則對不起來 ──
  ok(/CSI/.test(head['basic']) && /相對誤差/.test(head['basic']),
     `②選「相對誤差 30%」時主欄同時標示 CSI 與相對誤差（實得：${
       (head['basic']||'').split('|')[3]}）`);
  ok(/相對誤差/.test([...sel.options].find(o=>o.value==='basic').text)
     && /CSI/.test([...sel.options].find(o=>o.value==='basic').text),
     '②下拉選項本身也同時寫出兩個名字');

  // ── ③ MAE／RMSE：有選項、有主欄、且是「越小越好」的升冪 ──
  ok(/MAE mm/.test(head['MAE']) && /RMSE mm/.test(head['RMSE']),
     '③MAE／RMSE 主欄位標題帶單位 mm');
  ok(/▲/.test(head['MAE']) && /▲/.test(head['RMSE']),
     '③MAE／RMSE 預設升冪（越小越好排前面）');
  const o1=[...sel.options].find(o=>o.value==='MAE').text;
  const o2=[...sel.options].find(o=>o.value==='RMSE').text;
  ok(/平均絕對誤差/.test(o1) && /均方根誤差/.test(o2),
     '③下拉寫出中文全名（平均絕對誤差／均方根誤差）');

  // ── ④ 說明區塊要涵蓋這三個先前缺的名詞 ──
  sel.value='ETS80'; renderVerify();
  const html=document.getElementById('vf-table').innerHTML;
  ok(/相對誤差 30%/.test(html) && /主欄顯示的就是 CSI|主欄顯示 CSI/.test(html),
     '④說明寫出「相對誤差 30% ↔ CSI」的對應');
  ok(/均方根誤差/.test(html) && /放大少數大誤差/.test(html),
     '④說明寫出 RMSE 的意義（會放大少數大誤差）');
  ok(/RMSE 一定 ≥ MAE/.test(html),
     '④說明寫出 MAE 與 RMSE 的合併判讀');

  // ── ⑤ 兩個面板的名詞對照（最容易誤讀的一組）──
  ok(/校正倍率/.test(html) && /方向相反/.test(html),
     '⑤說明明確指出「偏差比」與「校正倍率」方向相反、不可互相套用');
  ok(/融合權重只用 MAE 與校正倍率/.test(html),
     '⑤說明寫出融合端沒有對應的技術得分欄位');

  // ── ⑥ 固定欄位不隨下拉改變，使用者才知道去哪裡找 ──
  const fixed=['POD','FAR','偏差比','MAE','樣本'];
  ok(vals.every(v=>fixed.every(f=>head[v].includes(f))),
     `⑥POD／FAR／偏差比／MAE／樣本 五欄在所有指標下都固定顯示`);
  ok(/固定顯示，不隨下拉改變/.test(html),
     '⑥說明寫出哪些欄位固定、哪些隨下拉切換');

  return {fails, log};
 });
 console.log(R.log.join('\n'));
 if(sf.length) console.log('\n原始碼層：\n  !! '+sf.join('\n  !! '));
 else console.log('\nOK  原始碼層：融合面板未再使用與校驗面板衝突的「偏差比」');
 if(errs.length) console.log('\n*** pageerror ***\n'+errs.join('\n'));
 const n=R.fails.length+sf.length;
 console.log(`\n${n?'FAIL '+n:'ALL PASS'} / ${R.log.length+1} 項`);
 await b.close();
 process.exit(n||errs.length?1:0);
})();

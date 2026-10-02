// 逐時／雷達視窗的雨量色階必須是時雨量色階，且不得把累積量誤塗成時雨量
// 使用者回報：六小時的圖看到有雨，逐時卻看不到 —— 逐時地圖抓到日累積色階，
//            時雨量 <5mm 全部被塗白。
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
  const savedMode=mode, savedHour=_hourIdx, savedWin=winKey;

  // ① 核心回歸：3mm/h 在時雨量色階下必須有顏色（先前被日累積色階塗白）
  ok(rainColor(3, HOURLY_SCALE)==='#00FFFF',
     `3mm/h → 時雨量色階淺藍（實得 ${rainColor(3, HOURLY_SCALE)}）`);
  ok(rainColor(3, HOURLY_SCALE)!=='#FFFFFF',
     '3mm/h 不得為白色（這就是「逐時看不到雨」的直接原因）');

  // ② 累積雨量色階一字不改：3mm 日累積本來就該是白色
  ok(rainColor(3)==='#FFFFFF', `3mm 日累積仍為白（實得 ${rainColor(3)}）`);
  ok(rainColor(3, RAIN_SCALE)==='#FFFFFF', '明確指定 RAIN_SCALE 行為不變');
  ok(rainColor(1e9)==='#FFAAFF', '日累積溢出色與修改前相同（#FFAAFF）');
  ok(rainColor(1e9, HOURLY_SCALE)==='#D8A0FF',
     '時雨量溢出色取自時雨量色階，不可沿用日累積的粉色');

  // ③ 色階由「數值來歷」決定，而非全域視窗猜測
  mode='rain';
  ok(_rainScaleOf({isHourly:true})===HOURLY_SCALE, 'isHourly → 時雨量色階');
  ok(_rainScaleOf({isRadar:true})===HOURLY_SCALE, '雷達1h → 時雨量色階');
  // ★ 反向保護：逐時視窗下取不到逐時值會退回 6h／日累積量，
  //   那個值必須仍用累積色階，否則 40mm 日累積會被畫成時雨量的紅色。
  _hourIdx=5;
  ok(_rainScaleOf({})===RAIN_SCALE,
     '逐時視窗但值非逐時（退回累積量）→ 仍用累積色階，不可高估');
  ok(rainColor(40, _rainScaleOf({}))==='#FFFF00',
     '40mm 累積量在逐時視窗仍為黃色，不得變成時雨量的紅色');
  ok(rainColor(40, HOURLY_SCALE)==='#FF0000',
     '對照：40 若當成時雨量確實是紅色（證明上一項真的防住了高估）');

  // ④ 非雨量圖層不受影響
  mode='etr';
  ok(_rainScaleOf({isHourly:true})===RAIN_SCALE, 'ETR2 圖層不套時雨量色階');
  mode='risk';
  ok(_rainScaleOf({isHourly:true})===RAIN_SCALE, '風險圖層不套時雨量色階');

  // ⑤ 圖例與地圖必須同一條判斷（使用者的症狀就是兩者分歧）
  mode='rain'; _hourIdx=5; winKey='fut1h_5';
  ok(_isHourlyRainWin()===true, '逐時視窗：判定為時雨量語意');
  mode='rain'; _hourIdx=null; winKey='radar';
  ok(_isHourlyRainWin()===true, '雷達1h：也是時雨量語意（先前圖例與地圖都用日累積）');
  mode='rain'; _hourIdx=null; winKey='fut6';
  ok(_isHourlyRainWin()===false, '六小時累積：維持累積雨量語意');
  mode='temp'; _hourIdx=5;
  ok(_isHourlyRainWin()===false, '氣溫圖層不論視窗都不是時雨量語意');

  // ⑥ 渲染內插層的色帶必須跟著切（開啟渲染後也不能變回日累積）
  mode='rain'; _hourIdx=5; winKey='fut1h_5';
  const bH=_modeBandsRgb();
  ok(bH.length===HOURLY_SCALE.length && bH[0].max===HOURLY_SCALE[0].max,
     `渲染層在逐時用時雨量色帶（第一級 ${bH[0].max}）`);
  mode='rain'; _hourIdx=null; winKey='fut6';
  const bD=_modeBandsRgb();
  ok(bD[0].max===RAIN_SCALE_CWA[0].max,
     `渲染層在累積視窗仍用 CWA 官方色帶（第一級 ${bD[0].max}）`);

  mode=savedMode; _hourIdx=savedHour; winKey=savedWin;
  return {fails, log};
 });
 console.log(R.log.join('\n'));
 if(errs.length){ console.log('\n*** pageerror ***\n'+errs.join('\n')); }
 console.log(`\n${R.fails.length?'FAIL '+R.fails.length:'ALL PASS'} / ${R.log.length} 項`);
 await b.close();
 process.exit(R.fails.length||errs.length?1:0);
})();

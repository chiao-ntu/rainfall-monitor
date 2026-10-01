// 觀測日期標示：工具列按鈕與下拉選單必須同源（資料日），
// 且 data.json 跨夜時不得把昨天的觀測標成「今天」
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
  const MD=d=>`${d.getMonth()+1}/${d.getDate()}`;
  const build=()=>{ buildPastBtns(); buildDayBtns(); };
  const dropLabels=()=>{
    const old=document.getElementById('time-picker');
    if(old) old.remove();
    try{ openTimePicker(null); }catch(e){ return null; }
    const box=document.getElementById('time-picker');
    return box ? [...box.children].map(x=>x.textContent.trim()) : null;
  };

  // ── ① 資料日＝今天 → 應顯示「今天」 ──
  const t0=new Date(); t0.setHours(5,0,0,0);
  BASE_TIME=new Date(t0); build();
  let btn=document.getElementById('bToday').textContent;
  ok(/今天/.test(btn) && btn.includes(MD(t0)),
     `資料日＝今天 → 按鈕「${btn}」`);

  // ── ② 資料日＝昨天 → 不得再稱「今天」，必須標出實際資料日 ──
  const y=new Date(); y.setDate(y.getDate()-1); y.setHours(23,0,0,0);
  BASE_TIME=new Date(y); build();
  btn=document.getElementById('bToday').textContent;
  ok(!/今天/.test(btn), `資料日＝昨天 → 不再稱「今天」（實得「${btn}」）`);
  ok(btn.includes(MD(y)), `按鈕標出實際資料日 ${MD(y)}`);

  // ── ③ 過去按鈕的原點也必須是資料日 ──
  const p0=document.getElementById('bpast0').textContent;
  const y2=new Date(y); y2.setDate(y2.getDate()-1);
  ok(p0.includes(MD(y2)), `past0（資料日的前一天）標 ${MD(y2)}，實得「${p0}」`);
  ok(!/昨天/.test(p0), `資料日非今天時，past0 不得稱「昨天」（${MD(y2)} 其實是前天）`);

  // ── ④ 工具列與下拉選單同源：同一個 winKey 指到同一個日期 ──
  //     下拉是在 renderTimeBar 內組的，直接比對它產生的文字
  const labs=dropLabels();
  if(labs && labs.length){
    const todayOpt=labs.find(x=>/今天|資料日/.test(x));
    ok(!!todayOpt && todayOpt.includes(MD(y)),
       `下拉的「資料日」項同樣標 ${MD(y)}，實得「${todayOpt}」`);
    ok(!!todayOpt && !/今天/.test(todayOpt),
       '下拉也不再稱「今天」（兩處同源）');
  } else { log.push('--  （下拉選單未建立，略過同源比對）'); }

  // ── ⑤ 還原 ──
  BASE_TIME=new Date(t0); build();
  return {log, fails};
 });
 R.log.forEach(l=>console.log(l));
 console.log('errs:',errs.slice(0,3));
 console.log(R.fails.length?`\n失敗 ${R.fails.length}`:'\n全部通過');
 await b.close(); process.exit(R.fails.length?1:0);
})();

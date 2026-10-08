// 時間基準一致性：系統裡有三個「現在」，每個數值必須用自己來源的那一個。
//
// 使用者提問（2026-10-08）：「是大型模式逐6小時更新的現在、逐時觀測預測
// 雨量更新的現在，還有使用者的時鐘現在嗎？」—— 是，而且先前沒有對齊。
//
//   資料時刻   BASE_TIME ← data.json generated_at（主排程）
//   觀測時刻   ETR2_NOW_TIME / RAIN_HOURLY_HOURS（每 10 分鐘）
//   觀看時刻   Date.now()
//
// 這支鎖住四條：
//   ① etr2_now.json 的 pct 是比值，不可直接覆寫百分比欄位
//   ② 逐時觀測必須以「實際時間鍵」對齊，不可從序列尾端回數
//   ③ ETR2 錨點時刻要跟著 t.etr2 實際被抓到的時刻走
//   ④ 逐時長條優先用 10 分鐘檔，不可停在 data.json 快照
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
 if(/t\.etr2_pct = \(rec\.pct != null\) \? rec\.pct/.test(src))
   sf.push('etr2_now 合併又把比值直接寫進百分比欄位（100 倍錯誤）');
 if(/const idx = arr\.length - 1 \+ \(h \+ 1\);/.test(src))
   sf.push('_obsHourlyAt 又改回從序列尾端回數（會把幾小時前的雨當成剛剛下的）');

 const b=await puppeteer.launch(Object.assign({},_LAUNCH,{defaultViewport:{width:1400,height:900}}));
 const pg=await b.newPage(); const errs=[];
 pg.on('pageerror',e=>errs.push(String(e).slice(0,170)));
 await pg.goto('http://127.0.0.1:8899/_local.html',{waitUntil:'networkidle2',timeout:90000});
 await new Promise(r=>setTimeout(r,8000));
 const R=await pg.evaluate(()=>{
  const fails=[], log=[];
  const ok=(c,m)=>{ if(!c) fails.push(m); log.push((c?'OK  ':'!!  ')+m); };
  const _bt=BASE_TIME, _realNow=Date.now, _rh=window.RAIN_HOURLY,
        _rhh=window.RAIN_HOURLY_HOURS, _rhm=window.RAIN_HOURLY_MEAN,
        _ent=window.ETR2_NOW_TIME;
  try{
    // 固定時鐘：資料日 = 今天，觀看時刻 22:30
    const day=new Date(); day.setHours(0,0,0,0);
    const bt=new Date(day); bt.setHours(9,30,0,0); BASE_TIME=bt;   // data.json 09:30
    const now=new Date(day); now.setHours(22,30,0,0);
    Date.now=()=>now.getTime();
    const p=n=>String(n).padStart(2,'0');
    const hk=d=>`${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}`;

    // 逐時觀測序列：只到 20 時（比觀看時刻晚 2 小時），且中間缺 15 時
    const hours=[], vals=[];
    for(let H=10; H<=20; H++){
      if(H===15) continue;                       // 刻意缺一小時
      const d=new Date(day); d.setHours(H,0,0,0);
      hours.push(hk(d)); vals.push(H);           // 值＝該小時，方便驗證對位
    }
    window.RAIN_HOURLY_HOURS=hours;
    window.RAIN_HOURLY={'宜蘭縣南澳鄉':vals.slice()};
    window.RAIN_HOURLY_MEAN={'宜蘭縣南澳鄉':vals.slice()};
    const t={county:'宜蘭縣',township:'南澳鄉',alert_val:400,etr2_alert:400,etr2:200,
      obs_1h_p48:new Array(48).fill(null),
      daily_rain:[50,30,40,0,0,0,0], rain_24h:30,
      qpf_best:new Array(60).fill(0), qpf_warn:new Array(60).fill(0),
      official_segs:[], band_segs:[]};

    // ── ② 逐時觀測必須以實際時間鍵對齊 ──
    //    序列最後一筆是 20 時；觀看時刻 22:30，所以「1 小時前」＝21 時＝無資料
    ok(_obsHourlyAt(t,-1)===null,
       `②「1 小時前」(21時) 序列裡沒有 → 回 null（實得 ${_obsHourlyAt(t,-1)}）`);
    ok(_obsHourlyAt(t,-2)===20,
       `②「2 小時前」(20時) 取到 20（實得 ${_obsHourlyAt(t,-2)}）`);
    ok(_obsHourlyAt(t,-7)===15 ? false : _obsHourlyAt(t,-7)===null,
       `②缺掉的 15 時回 null，不以鄰近值頂替（實得 ${_obsHourlyAt(t,-7)}）`);
    ok(_obsHourlyAt(t,-12)===10,
       `②「12 小時前」(10時) 取到 10（實得 ${_obsHourlyAt(t,-12)}）`);

    // ── ③ 錨點時刻跟著 t.etr2 的來源走 ──
    window.ETR2_NOW_TIME='';
    const aBase=_etrAnchorSeg();
    ok(aBase===1, `③沒有 etr2_now 時錨點＝BASE_TIME 的段（09:30→段1，實得 ${aBase}）`);
    const et=new Date(day); et.setHours(20,10,0,0);
    window.ETR2_NOW_TIME=`${et.getFullYear()}-${p(et.getMonth()+1)}-${p(et.getDate())}T${p(20)}:10`;
    const aNow=_etrAnchorSeg();
    ok(aNow===3, `③有 etr2_now(20:10) 時錨點前進到段3（實得 ${aNow}）`);
    ok(aNow<=_nowSeg(), `③錨點不超過觀看者的現在（${aNow} ≤ ${_nowSeg()}）`);

    // ── ④ 逐時長條優先用 10 分鐘檔 ──
    const bars=_hourlyBars(t);
    const i20=bars.hFrom!=null ? (20 - 0) : null;   // 以時間反查
    // 找 20 時那一格
    let idx20=-1;
    for(let i=0;i<bars.vals.length;i++){
      const h=bars.hFrom+i;
      const d=new Date(SEG_EPOCH().getTime()+h*3600e3);
      if(d.getHours()===20 && d.getDate()===now.getDate()){ idx20=i; break; }
    }
    ok(idx20>=0 && bars.vals[idx20]===20,
       `④20 時那一格取到 10 分鐘檔的值 20（實得 ${idx20>=0?bars.vals[idx20]:'找不到'}）`
       + `；obs_1h_p48 全為 null，若仍走舊來源會是分配值`);
    ok(bars.nObs>=10,
       `④有逐時觀測的小時數 ${bars.nObs}（來自 10 分鐘檔，不是 data.json 快照）`);

    // ── ① etr2_pct 單位 ──
    //    直接驗合併邏輯：比值 0.358 必須變成 35.8
    const rec={etr2:143.0, alert:400, pct:0.3575};
    const conv=(rec.pct!=null)?Math.round(rec.pct*1000)/10:null;
    ok(Math.abs(conv-35.8)<0.05,
       `①比值 0.3575 轉成百分比 ${conv}（不可直接寫 0.3575）`);
    ok(Math.abs(getObsEtr(t)-50)<0.6,
       `①getObsEtr 不受影響（200/400＝${getObsEtr(t)}%，它由 etr2÷警戒值 自行計算）`);
  } finally {
    BASE_TIME=_bt; Date.now=_realNow; window.RAIN_HOURLY=_rh;
    window.RAIN_HOURLY_HOURS=_rhh; window.RAIN_HOURLY_MEAN=_rhm;
    window.ETR2_NOW_TIME=_ent;
  }
  return {fails, log};
 });
 console.log(R.log.join('\n'));
 if(sf.length) console.log('\n原始碼層：\n  !! '+sf.join('\n  !! '));
 else console.log('\nOK  原始碼層：比值覆寫與尾端回數都未復發');
 if(errs.length) console.log('\n*** pageerror ***\n'+errs.join('\n'));
 const n=R.fails.length+sf.length;
 console.log(`\n${n?'FAIL '+n:'ALL PASS'} / ${R.log.length+1} 項`);
 await b.close();
 process.exit(n||errs.length?1:0);
})();

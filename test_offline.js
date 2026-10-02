// 離線單檔驗證：完全封鎖網路，用 file:// 開啟，確認地圖與資料都能運作
const puppeteer=require('puppeteer');
(async()=>{
 const b=await puppeteer.launch({executablePath:'/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
   args:['--no-sandbox','--disable-dev-shm-usage','--allow-file-access-from-files'],
   defaultViewport:{width:1300,height:860}});
 const pg=await b.newPage();
 const errs=[], blocked=[];
 pg.on('pageerror',e=>errs.push(String(e).slice(0,170)));
 // ★ 徹底斷網：任何非 file:// 的請求一律攔掉
 await pg.setRequestInterception(true);
 pg.on('request',r=>{
   // data: 是內嵌資源（地形圖），不算外部請求
   if(r.url().startsWith('file://') || r.url().startsWith('data:')) return r.continue();
   blocked.push(r.url().slice(0,60)); r.abort();
 });
 await pg.goto('file:///tmp/claude-0/offtest/formosa_offline.html',
   {waitUntil:'load',timeout:90000});
 await new Promise(r=>setTimeout(r,9000));
 const R=await pg.evaluate(()=>{
  const fails=[], log=[];
  const ok=(c,m)=>{ if(!c) fails.push(m); log.push((c?'OK  ':'!!  ')+m); };
  ok(typeof L !== 'undefined', 'Leaflet 已內嵌並載入');
  ok(typeof html2canvas === 'function', 'html2canvas 已內嵌並載入');
  ok(typeof map === 'object' && map && typeof map.getPane === 'function', '地圖已建立');
  ok(!!(window.__FORMOSA_OFFLINE__ && window.__FORMOSA_OFFLINE__['data.json']),
     '資料已內嵌');
  const n = (TOWNSHIPS || []).length;
  ok(n > 0, `鄉鎮資料已載入：${n} 筆`);
  ok(document.getElementById('loading').style.display === 'none', '初始化完成');
  const tl = townLayer && townLayer.getLayers ? townLayer.getLayers().length : 0;
  ok(tl > 300, `鄉鎮圖層已繪製：${tl} 個多邊形`);
  const bn = document.querySelector('div');
  ok(/離線單檔版/.test(document.body.textContent), '離線橫幅已顯示');
  ok(/2026-09-30T22:00:00/.test(document.body.textContent), '資料時間已標示');
  // 資料過時橫幅：兩個方向都要測。
  // ★ 原本斷言「打包資料才 1.4h，不該跳」，那是寫測試當天的狀況；
  //   打包檔放幾天後資料自然變舊，橫幅本來就該跳，這條會變成假警報。
  //   改為自己注入時間，與打包當下的新鮮度無關。
  const age=document.getElementById('data-age-banner');
  BASE_TIME = new Date(Date.now() - 1.4*3600e3);
  _checkDataAge();
  ok(age && age.style.display === 'none', '注入資料時間 1.4 小時 → 不誤報過時');
  BASE_TIME = new Date(Date.now() - 8*3600e3);
  _checkDataAge();
  ok(age && age.style.display !== 'none' && /過時/.test(age.textContent),
     '資料改為 8 小時前 → 過時警示跳出');
  ok(/請勿作為現況判讀依據/.test(age.textContent), '超過 6 小時 → 標示不可作為判讀依據');
  return {log, fails};
 });
 R.log.forEach(l=>console.log(l));
 console.log(`\n被攔截的外部請求 ${blocked.length} 個` +
   (blocked.length?`（前 3：${[...new Set(blocked)].slice(0,3).join(' , ')}）`:''));
 console.log('errs:',errs.slice(0,4));
 console.log(R.fails.length?`\n失敗 ${R.fails.length}`:'\n全部通過（完全斷網下可運作）');
 await b.close(); process.exit(R.fails.length?1:0);
})();

const fs=require('fs');const {JSDOM,VirtualConsole}=require('jsdom');
const h=fs.readFileSync('index.html','utf8')
 .replace(/<script src="[^"]*leaflet[^"]*"><\/script>/i,
  '<script>'+fs.readFileSync('node_modules/leaflet/dist/leaflet-src.js','utf8')+'</script>');
const vc=new VirtualConsole();
const errs=[];
vc.on('jsdomError', e=>errs.push(e.message.split('\n')[0].slice(0,150)));
const dom=new JSDOM(h,{runScripts:'dangerously',pretendToBeVisual:true,
                       url:'https://example.org/',virtualConsole:vc});
// ★ 執行期檢查：語法正確但執行中斷的錯誤（例如變數不在作用域、
//   pane 被誤刪）只有實際跑一次才抓得到。實測發生過四次，故納入常態檢查。
setTimeout(()=>{
  const w=dom.window;
  const q=n=>{try{return w.eval(n);}catch(e){return 'ERR:'+e.message.slice(0,90);}};
  const fails=[];
  // 排除 jsdom 環境本身的限制（無 canvas、無 SVG renderer）
  const real = errs.filter(e=>!/Not implemented|getContext|_removePath|clearRect/.test(e));
  if(real.length) real.slice(0,4).forEach(e=>fails.push('執行期錯誤：'+e));
  const checks = [
    ["typeof map.getPane", 'function', '地圖未建立'],
    ["!!map.getPane('seaPane')", true, '缺 seaPane'],
    ["!!map.getPane('townPane')", true, '缺 townPane'],
    ["typeof townLayer", 'object', '鄉鎮圖層未建立'],
    ["townLayer && townLayer.getLayers ? townLayer.getLayers().length > 300 : false",
     true, '鄉鎮圖層數量不足'],
    ["RAIN_SCALE.length", 7, '色階未定義'],
    ["document.getElementById('loading').style.display", 'none', '初始化未完成'],
  ];
  checks.forEach(([expr, want, msg])=>{
    const got = q(expr);
    if(got !== want) fails.push(`${msg}（${expr} → ${got}）`);
  });
  if(fails.length){
    console.log('=== 執行期檢查失敗 ' + fails.length + ' 項 ===');
    fails.forEach(f=>console.log('  !!', f));
    process.exit(1);
  }
  console.log('=== 執行期檢查全部通過 ===');
}, 3000);

#!/usr/bin/env node
// 產出可公開的前端：index.public.html
//
// 目的：index.html 裡的註解把整套方法寫得很清楚（為什麼用面平均不用站極值、
//       偏差校正怎麼夾、兩軌怎麼分、哪些坑踩過）。那些註解是真正的 know-how，
//       而且是最容易被讀懂的部分 —— 程式碼要逆推得花工夫，註解是直接送上。
//       本腳本把註解從「公開版」拿掉，私有原始碼完整保留，維護性不受影響。
//
// ★ 刻意只做「移除註解」，不做改名（mangle）也不做壓縮（compress）：
//   - 這份 HTML 有多個 script 區塊共用全域（mode、_hourIdx、winKey、
//     TOWNSHIPS…），改名會跨區塊對不上。
//   - 測試與除錯是靠函式名抓的，改名等於把安全網一起拆掉。
//   - 壓縮會重寫運算式。對預警系統來說，「省幾百 KB」換「行為可能有差」
//     是不能接受的交換。
//   移除註解不改變任何執行語意，這是唯一零風險的那一刀。
//
// 用法：node build_public.js [輸入] [輸出]
//   預設 index.html → index.public.html
//
// ★ 建置完一定要對「產出物」跑 verify_html.py 與瀏覽器測試，
//   不是對原始碼跑。建置本身就是一個新的故障來源。

const fs = require('fs');
const { minify } = require('terser');

const IN  = process.argv[2] || 'index.html';
const OUT = process.argv[3] || 'index.public.html';

(async () => {
  const src = fs.readFileSync(IN, 'utf8');
  let out = '';
  let cursor = 0;
  let nBlocks = 0, bytesBefore = 0, bytesAfter = 0;
  const failures = [];

  // 逐個找出內嵌 <script>（有 src= 的是外部檔，不處理）
  const reOpen = /<script\b([^>]*)>/gi;
  let m;
  while ((m = reOpen.exec(src)) !== null) {
    const attrs = m[1] || '';
    const bodyStart = m.index + m[0].length;
    const closeIdx = src.indexOf('</script>', bodyStart);
    if (closeIdx < 0) break;

    // 有 src 或非 JS 型別 → 原樣保留
    if (/\bsrc\s*=/.test(attrs) ||
        (/\btype\s*=/.test(attrs) && !/type\s*=\s*["']?(text\/javascript|module)["']?/i.test(attrs))) {
      reOpen.lastIndex = closeIdx + 9;
      continue;
    }

    const body = src.slice(bodyStart, closeIdx);
    if (!body.trim()) { reOpen.lastIndex = closeIdx + 9; continue; }

    const isModule = /type\s*=\s*["']?module["']?/i.test(attrs);
    let res;
    try {
      res = await minify(body, {
        parse:    { bare_returns: false },
        compress: false,          // ★ 不改運算式
        mangle:   false,          // ★ 不改名
        // ★ beautify + 保留原始引號：目的是「移除註解」，不是壓到最小。
        //   terser 預設會把 '單引號' 正規化成 "雙引號"、並把換行收掉，
        //   結果 verify_html.py 那套檢查（對行首與引號敏感）對產出物全部失效
        //   —— 等於為了省 KB 把安全網拆了。保留格式，檢查才驗得到產出物。
        format:   { comments: false, beautify: true, quote_style: 3,
                    indent_level: 1, semicolons: true },
        sourceMap: false,
        module: isModule,
      });
    } catch (e) {
      failures.push(`區塊 #${nBlocks + 1}（第 ${src.slice(0, m.index).split('\n').length} 行附近）：${e.message}`);
      reOpen.lastIndex = closeIdx + 9;
      continue;
    }
    if (!res || typeof res.code !== 'string') {
      failures.push(`區塊 #${nBlocks + 1}：terser 沒有回傳程式碼`);
      reOpen.lastIndex = closeIdx + 9;
      continue;
    }

    out += src.slice(cursor, bodyStart);
    out += '\n' + res.code + '\n';
    cursor = closeIdx;
    nBlocks++;
    bytesBefore += Buffer.byteLength(body, 'utf8');
    bytesAfter  += Buffer.byteLength(res.code, 'utf8');
    reOpen.lastIndex = closeIdx + 9;
  }
  out += src.slice(cursor);

  if (failures.length) {
    console.error('=== 建置失敗，未產出檔案 ===');
    failures.forEach(f => console.error('  !! ' + f));
    console.error('★ 刻意不產出半成品：預警系統寧可沒有公開版，也不要一份壞的公開版。');
    process.exit(1);
  }

  // HTML 註解也一併移除（<!--[if ...]--> 條件註解保留，以免動到相容性處理）
  const htmlCommentsBefore = (out.match(/<!--[\s\S]*?-->/g) || []).length;
  out = out.replace(/<!--(?!\[if)([\s\S]*?)-->/g, '');

  fs.writeFileSync(OUT, out, 'utf8');

  const kb = n => (n / 1024).toFixed(0) + 'KB';
  const removed = bytesBefore - bytesAfter;
  console.log(`已產出 ${OUT}`);
  console.log(`  處理內嵌 script 區塊 ${nBlocks} 個`);
  console.log(`  JS ${kb(bytesBefore)} → ${kb(bytesAfter)}（移除 ${kb(removed)}，`
            + `${(removed / bytesBefore * 100).toFixed(1)}%）`);
  console.log(`  HTML 註解 ${htmlCommentsBefore} 處已移除`);
  console.log(`  全檔 ${kb(Buffer.byteLength(src, 'utf8'))} → ${kb(Buffer.byteLength(out, 'utf8'))}`);
  console.log('');
  console.log('★ 接著必須對產出物跑驗證，不是對原始碼：');
  console.log(`    python3 verify_html.py ${OUT}`);
  console.log(`    cp ${OUT} _local.html && node test_hourly_paint.js`);
})();

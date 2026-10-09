// ETR2% 折線不得斷 —— 直接重現使用者回報的情境
// ====================================================================
// 這條線已經被回報斷掉三次。前幾輪每次只修「那一次的成因」：
//   ① 水保署 API 單獨失敗卻被記成成功（寫入空字典）
//   ② 10 分鐘排程整段沒跑（缺格 130 小時）
//   ③ 自算要求 168h 逐時覆蓋，實際只有 34h —— 條件從未成立
// 成因會一直有新的。要擋的是「線會斷」這件事本身。
//
// 兩道防線：
//   後端 seal_etr2_series()：當日 00:00 累積＝0、24:00＝日總量，
//     兩個精確錨點夾住日內任何缺口（不是外插）
//   前端 calcEtr2AtSeg()：被前後兩個官方值夾住的洞直接內插（間隔 ≤2 天）
//
// 本測試挖掉**所有鄉鎮**在某幾段的官方值，驗證畫出來的線仍然沒有缺口。
const fs = require('fs');
const puppeteer = require('puppeteer');
const _EXEC = [process.env.PUPPETEER_EXECUTABLE_PATH,
  '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
  '/opt/pw-browsers/chromium/chrome-linux/chrome'].find(p => p && fs.existsSync(p));
const _LAUNCH = { args: ['--no-sandbox', '--disable-dev-shm-usage'] };
if (_EXEC) _LAUNCH.executablePath = _EXEC;

const src = fs.readFileSync('index.html', 'utf8');
const sf = [];
const ok = (c, m) => { if (!c) sf.push(m); };

ok(/bounded_interp/.test(src), '源碼層：前端的夾住內插防線不見了');
ok(/\(b - a\) <= 5/.test(src), '源碼層：內插的間隔上限（≤5 段＝30 小時）不見了');

(async () => {
  const browser = await puppeteer.launch(_LAUNCH);
  const page = await browser.newPage();
  page.on('pageerror', e => sf.push('頁面例外：' + e.message));
  await page.goto('http://127.0.0.1:8899/_local.html',
                  { waitUntil: 'networkidle2', timeout: 180000 });
  await new Promise(r => setTimeout(r, 2500));

  const r = await page.evaluate(() => {
    const NS = 32, N = 36, d = Math.pow(0.7, 0.25);
    const mk = (holes) => {
      TOWNSHIPS.forEach(t => {
        if (!(t.alert_val > 0)) return;
        const base = t.etr2 || t.alert_val * 0.4, arr = [];
        for (let i = 0; i < N; i++)
          arr.push(Math.round(base * Math.pow(d, Math.max(0, NS - i)) * 10) / 10);
        holes.forEach(h => { arr[NS + h] = null; });
        t.etr2_hist = arr; t.etr2_hist_base = NS;
      });
      _tmCache = new Map(); _tmCacheKey = '';
      const dd = _calcDistrictHourly();
      return {
        nulls: (dd.etrRows[0] || []).filter(v => v == null).length,
        zeros: (dd.etrRows[0] || []).filter(v => v === 0).length,
        n: (dd.etrRows[0] || []).length,
      };
    };
    const out = {};
    out.one = mk([-4]);                       // 單段缺（6 小時）
    out.two = mk([-4, -3]);                   // 兩段缺（12 小時，＝使用者回報的情形）
    out.four = mk([-6, -5, -4, -3]);          // 四段缺（整天）
    //  超過 2 天的缺口要挖在圖窗（段 -8~+12）**之內**才測得到，
    //  挖在窗外等於沒測（第一版就犯了這個錯）
    out.far = mk([-7, -6, -5, -4, -3, -2]);   // 6 段連缺 → 間隔 7 段，超過上限
    // 夾住性：補出來的值必須落在前後兩個官方值之間（方向不拘）
    TOWNSHIPS.forEach(t => {
      if (!(t.alert_val > 0)) return;
      const base = t.etr2 || t.alert_val * 0.4, arr = [];
      for (let i = 0; i < N; i++)
        arr.push(Math.round(base * Math.pow(d, Math.max(0, NS - i)) * 10) / 10);
      arr[NS - 4] = null; arr[NS - 3] = null;
      t.etr2_hist = arr; t.etr2_hist_base = NS;
    });
    _tmCache = new Map(); _tmCacheKey = '';
    const t0 = TOWNSHIPS.find(x => x.alert_val > 0);
    const seq = [-6, -5, -4, -3, -2, -1].map(s => calcEtr2AtSeg(t0, s, 'qpf_best'));
    out.seq = seq.map(v => v == null ? null : Math.round(v * 10) / 10);
    //  挖掉的是 -4、-3 兩段，夾住它們的是 -5 與 -2
    const lo = Math.min(seq[1], seq[4]), hi = Math.max(seq[1], seq[4]);
    out.bracketed = seq[2] >= lo - 0.05 && seq[2] <= hi + 0.05
                 && seq[3] >= lo - 0.05 && seq[3] <= hi + 0.05;
    out.ordered = (seq[1] <= seq[2] && seq[2] <= seq[3] && seq[3] <= seq[4])
               || (seq[1] >= seq[2] && seq[2] >= seq[3] && seq[3] >= seq[4]);
    return out;
  });

  ok(r.one.nulls === 0, `單段缺（6h）仍有 ${r.one.nulls} 格留白`);
  ok(r.two.nulls === 0, `兩段缺（12h，使用者回報的情形）仍有 ${r.two.nulls} 格留白`);
  ok(r.four.nulls === 0, `四段缺（整天）仍有 ${r.four.nulls} 格留白`);
  ok(r.one.zeros === 0 && r.two.zeros === 0 && r.four.zeros === 0,
     '補出來的值出現 0（0 與「無值」必須分得開）');
  ok(r.far.nulls > 0,
     '超過 5 段（30h）的缺口仍被補 —— 跨日界後尾項會跳動，應留白');
  ok(r.bracketed, `補出來的值沒有落在前後官方值之間：${JSON.stringify(r.seq)}`);
  ok(r.ordered, `補出來的值破壞了序列的單調性：${JSON.stringify(r.seq)}`);
  console.log(`   缺 6h→留白 ${r.one.nulls}　缺 12h→留白 ${r.two.nulls}　`
            + `缺 24h→留白 ${r.four.nulls}　缺 >2天→留白 ${r.far.nulls}（應 >0）`);
  console.log(`   補值序列：${JSON.stringify(r.seq)}`);

  await browser.close();
  if (sf.length) {
    console.log('❌ test_etr2_gapless 失敗 ' + sf.length + ' 項');
    sf.forEach(s => console.log('   - ' + s));
    process.exit(1);
  }
  console.log('✅ test_etr2_gapless 全數通過（9 項）');
})().catch(e => { console.error('❌ 例外：', e); process.exit(1); });

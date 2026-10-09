// 「缺資料」不得被當成「沒下雨的證據」
// ------------------------------------------------------------------
// _noRainEvidence() 的回傳值會在自適應融合時剔除模式：
//   若判定「雷達與實測都沒雨」，則報 ≥30mm 而中位數 <10mm 的模式會被丟掉。
// 原實作：三小時逐時觀測全缺時 obs 仍為 0，`obs < 1` 成立 → 判定「沒雨」。
// 也就是說，10 分鐘排程一中斷，系統會在最需要預警時安靜地拿掉高量模式。
// 這違反「以預測不失準為優先」。
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

ok(/if\(nObs < 3 && today == null\) return false;/.test(src),
   '源碼層：缺觀測時的保護條件不見了（會退回「缺資料＝沒下雨」）');

(async () => {
  const browser = await puppeteer.launch(_LAUNCH);
  const page = await browser.newPage();
  page.on('pageerror', e => sf.push('頁面例外：' + e.message));

  const i = src.indexOf('function _noRainEvidence(');
  let d = 0, started = false, body = '';
  for (let j = i; j < src.length; j++) {
    if (src[j] === '{') { d++; started = true; }
    else if (src[j] === '}') { d--; if (started && d === 0) { body = src.slice(i, j + 1); break; } }
  }
  await page.evaluate(`
    let _OBS = {};           // h → 值（null 代表該小時沒有資料）
    let _ECHO = false;
    function _radarHasEcho(){ return _ECHO; }
    function _obsHourlyAt(t, h){ return (h in _OBS) ? _OBS[h] : null; }
    ${body}
  `);

  const r = await page.evaluate(() => {
    const run = (obs, today, echo) => {
      _OBS = obs; _ECHO = !!echo;
      return _noRainEvidence({ daily_rain: [today] });
    };
    return {
      // ① 三小時觀測齊全且都沒雨、今日也幾乎沒雨 → 確實是「沒雨的證據」
      allDry:      run({ '-1': 0, '-2': 0, '-3': 0 }, 0, false),
      // ② 三小時觀測齊全但有雨 → 不是沒雨
      wet:         run({ '-1': 2, '-2': 0, '-3': 0 }, 2, false),
      // ③ 觀測完全缺、也沒有官方日總量 → 無從判定，不得宣稱沒雨
      noData:      run({}, null, false),
      // ④ 只有 1 小時有觀測、無日總量 → 仍不足以判定
      partial:     run({ '-1': 0 }, null, false),
      // ⑤ 觀測缺，但官方日總量說今天幾乎沒雨 → 有證據，可判定
      dailyOnly:   run({}, 0, false),
      // ⑥ 觀測缺，官方日總量顯示有雨 → 不是沒雨
      dailyWet:    run({}, 20, false),
      // ⑦ 雷達有回波 → 一律不是沒雨（優先於一切）
      echo:        run({}, null, true),
    };
  });

  ok(r.allDry === true,    '①三小時觀測齊全且無雨，應判定為「沒雨的證據」');
  ok(r.wet === false,      '②有觀測到雨，不應判定為沒雨');
  ok(r.noData === false,   '③觀測與日總量都沒有時，仍判定「沒雨」—— 缺資料被當成證據');
  ok(r.partial === false,  '④逐時觀測不齊（1/3）且無日總量時，仍判定「沒雨」');
  ok(r.dailyOnly === true, '⑤有官方日總量佐證無雨時，應可判定');
  ok(r.dailyWet === false, '⑥官方日總量顯示有雨，不應判定為沒雨');
  ok(r.echo === false,     '⑦雷達有回波時，不應判定為沒雨');

  await browser.close();
  if (sf.length) {
    console.log('❌ test_evidence_gate 失敗 ' + sf.length + ' 項');
    sf.forEach(s => console.log('   - ' + s));
    process.exit(1);
  }
  console.log('✅ test_evidence_gate 全數通過（8 項）');
})().catch(e => { console.error('❌ 例外：', e); process.exit(1); });

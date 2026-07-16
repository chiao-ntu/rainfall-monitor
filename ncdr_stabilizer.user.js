// ==UserScript==
// @name         NCDR 監控頁面穩定器 (Dashboard 用)
// @namespace    chiao.dashboard
// @version      1.0
// @description  讓 NCDR 即時降雨 / WISSDOM 等頁面每次載入都自動播放並維持一致版面,避免嵌入 dashboard 時框位跑掉
// @author       chiao
// @match        https://watch.ncdr.nat.gov.tw/watch_nowcast*
// @match        https://watch.ncdr.nat.gov.tw/watch_wissdom_taiwan*
// @run-at       document-idle
// @grant        none
// @noframes     false
// ==/UserScript==

/*
  ── 使用說明 ────────────────────────────────────────────────
  1. 安裝 Tampermonkey 擴充(Chrome / Edge / Firefox 都有)。
  2. 點 Tampermonkey 圖示 →「建立新指令碼」→ 全選刪除範本 → 貼上本檔全部內容 → Ctrl+S 儲存。
  3. 先開「原始頁面」測試(比在 dashboard 裡好除錯):
       https://watch.ncdr.nat.gov.tw/watch_nowcast
       https://watch.ncdr.nat.gov.tw/watch_wissdom_taiwan
     重整看看:是否自動開始播放、版面是否每次一致。
  4. OK 後再回 dashboard(Tampermonkey 預設也會在 iframe 內執行)。
     ※ 若 dashboard 裡沒作用:到 Tampermonkey 設定確認「允許在框架(iframe)中執行」為開啟。
  5. 隱藏通知列後版面會上移,請在 dashboard 那格「重新拖好框位 → 匯出佈局」再貼給我烘進去。

  ── 若要微調 ────────────────────────────────────────────────
  - 播放沒點中/點錯 → 把播放鍵的 HTML 貼給我,我填進 CONFIG.playSelectors(最精準)。
  - 還有其它會跳動的橫幅 → 把那段文字加進 CONFIG.hideTexts。
  - 想套用到別的頁面 → 上面多加一行 @match。
  ──────────────────────────────────────────────────────────
*/

(function () {
  'use strict';

  const CONFIG = {
    autoplay: true,
    // 含這些文字的「小元素/列」會被隱藏,避免忽隱忽現造成版面上下跳動
    hideTexts: ['資料接收異常', '部分服務暫停', '敬請見諒'],
    // 自動點擊:含這些文字/alt/title 的按鈕(動畫、播放)
    playTexts: ['動畫', '播放', 'play', 'Play'],
    // 若你知道播放鍵的確切 CSS 選擇器,填在這裡(優先且最精準),例如 '#playBtn'、'.btn-animate'
    playSelectors: [],
    forceScrollTop: true,
  };

  let played = false;

  // 隱藏會變動的通知列(idempotent,可重複執行)
  function hideVariable() {
    if (!CONFIG.hideTexts.length) return;
    const els = document.querySelectorAll('td, tr, div, p, span, li');
    els.forEach(el => {
      if (el.dataset._ncdrHidden) return;
      const t = (el.textContent || '').trim();
      // 只隱藏「文字短、且幾乎沒有子元素」的元素,避免把整塊內容一起藏掉
      if (t && t.length < 60 && el.children.length <= 1 &&
          CONFIG.hideTexts.some(x => t.includes(x))) {
        el.style.display = 'none';
        el.dataset._ncdrHidden = '1';
      }
    });
  }

  // 自動播放:一次載入只點一次(避免把播放又切回暫停)
  function tryPlay() {
    if (!CONFIG.autoplay || played) return;

    for (const sel of CONFIG.playSelectors) {
      const el = document.querySelector(sel);
      if (el) { safeClick(el); played = true; return; }
    }

    const cand = document.querySelectorAll('a, button, span, img, div, input');
    for (const el of cand) {
      const text = (el.textContent || '').trim();
      const alt = el.getAttribute ? (el.getAttribute('alt') || '') : '';
      const title = el.getAttribute ? (el.getAttribute('title') || '') : '';
      const s = (text + ' ' + alt + ' ' + title).trim();
      if (s && s.length < 20 && CONFIG.playTexts.some(x => s.includes(x))) {
        safeClick(el);
        played = true;
        return;
      }
    }
  }

  function safeClick(el) {
    try {
      el.click();
      // 保險:有些站的 handler 綁在滑鼠事件上,補送一次
      el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
    } catch (e) { /* 忽略 */ }
  }

  function stabilize() {
    hideVariable();
    if (CONFIG.forceScrollTop) window.scrollTo(0, 0);
    tryPlay();
  }

  // 初次 + 多次重試(頁面資料多為非同步載入)
  window.addEventListener('load', stabilize);
  [300, 1000, 2500, 5000, 8000].forEach(t => setTimeout(stabilize, t));

  // 內容變動時持續維持版面一致(播放已用 played 旗標鎖住,不會被重複點)
  let raf = null;
  const mo = new MutationObserver(() => {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = null; hideVariable(); if (!played) tryPlay(); });
  });
  mo.observe(document.documentElement, { childList: true, subtree: true });
})();

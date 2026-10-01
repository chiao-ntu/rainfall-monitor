#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 FORMOSA 打包成單一 HTML 檔，完全不需要網路即可運作。

為什麼要這個：
  鏡像站解決的是「我們的主機掛了」，但預警系統真正會遇到的狀況是
  「颱風天，使用端的網路斷了」—— 那時任何鏡像都連不到。
  這支腳本把當下的資料、地圖元件、地形圖全部內嵌成一個檔案，
  複製到隨身碟或筆電就能開，沒有網路也能查。

  代價是資料凍結在打包當下，所以檔案內會強制顯示離線橫幅與資料時間，
  絕不能讓人誤以為是即時值。

用法：
    python3 build_offline.py                 # 讀當前目錄，輸出 formosa_offline.html
    python3 build_offline.py --out /tmp/x.html
"""
import base64
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

TPE = timezone(timedelta(hours=8))

# 前端會 fetch 的資料檔（缺的自動略過）
DATA_FILES = [
    'data.json', 'radar.json', 'rain_hourly.json', 'etr2_now.json',
    'verify.json', 'model_skill.json', 'obs_history.json',
    'landslide_warning_stations.json', 'town_polys.json',
    'terrain_zones_official.json', 'coastal_towns.json',
    'all_townships.json', 'east_asia_geo.json', 'terrain_relief.json',
]
# 以 <img>／imageOverlay 載入的圖檔（改成 data URI）
IMAGE_FILES = ['terrain_relief.png']
VENDOR_CSS = ['vendor/leaflet.css']
VENDOR_JS = ['vendor/leaflet.js', 'vendor/html2canvas.min.js']


def _read(p, binary=False):
    with open(p, 'rb' if binary else 'r', encoding=None if binary else 'utf-8') as f:
        return f.read()


def build(src='index.html', out='formosa_offline.html'):
    if not os.path.exists(src):
        print(f"找不到 {src}")
        return 2
    html = _read(src)
    miss = []

    # ── ① 內嵌 vendor（原本是同源檔案，離線時沒有伺服器可提供）──
    for css in VENDOR_CSS:
        tag = f'<link rel="stylesheet" href="{css}"/>'
        if tag in html:
            if os.path.exists(css):
                html = html.replace(tag, '<style>' + _read(css) + '</style>')
            else:
                miss.append(css)
    for js in VENDOR_JS:
        tag = f'<script src="{js}"></script>'
        if tag in html:
            if os.path.exists(js):
                html = html.replace(tag, '<script>' + _read(js) + '</script>')
            else:
                miss.append(js)

    # ── ② 內嵌圖檔 ──
    for img in IMAGE_FILES:
        if os.path.exists(img) and img in html:
            b64 = base64.b64encode(_read(img, True)).decode('ascii')
            uri = f'data:image/png;base64,{b64}'
            html = html.replace(f"'{img}'", f"'{uri}'")
        elif img in html:
            miss.append(img)

    # ── ③ 內嵌資料，並攔截 fetch ──
    blob, sizes = {}, []
    for fn in DATA_FILES:
        if not os.path.exists(fn):
            continue
        try:
            with open(fn, encoding='utf-8') as f:
                blob[fn] = json.load(f)
            sizes.append((fn, os.path.getsize(fn)))
        except Exception as e:
            print(f"  略過 {fn}（讀取失敗：{e}）")
    if not blob:
        print("!! 找不到任何資料檔（data.json…）；請在資料目錄下執行。")
        return 2

    base_time = ((blob.get('data.json') or {}).get('base_time')) or '未知'
    built = datetime.now(TPE).strftime('%Y-%m-%d %H:%M')
    shim = """<script>
/* ★ 離線單檔：把原本 fetch 出去的資料改為就地取用。
   非資料的請求（如 Himawari 圖磚）維持原樣往外送，離線時自然失敗，
   對應圖層會空白，其餘功能不受影響 —— 這是刻意的，不要假裝有那些圖層。 */
window.__FORMOSA_OFFLINE__ = %s;
window.__FORMOSA_BUILT__ = %s;
(function(){
  var real = window.fetch ? window.fetch.bind(window) : null;
  window.fetch = function(u, o){
    var k = String(u && u.url ? u.url : u).split('?')[0].replace(/^\\.\\//, '');
    k = k.split('/').pop();
    if(Object.prototype.hasOwnProperty.call(window.__FORMOSA_OFFLINE__, k)){
      var body = JSON.stringify(window.__FORMOSA_OFFLINE__[k]);
      if(typeof Response === 'function'){
        return Promise.resolve(new Response(body,
          {status:200, headers:{'Content-Type':'application/json'}}));
      }
      return Promise.resolve({ok:true, status:200,
        json:function(){ return Promise.resolve(JSON.parse(body)); },
        text:function(){ return Promise.resolve(body); }});
    }
    return real ? real(u, o) : Promise.reject(new Error('offline'));
  };
  window.addEventListener('DOMContentLoaded', function(){
    var d = document.createElement('div');
    d.style.cssText = 'padding:6px 14px;background:#1a3a5a;color:#cfe4f5;'
      + 'font-size:12px;border-bottom:2px solid #3a7aaa;line-height:1.5';
    d.innerHTML = '<b>\\u{1F4E6} 離線單檔版</b>　資料凍結於 <b>' +
      window.__FORMOSA_BUILT__.base_time + '</b>（打包時間 ' +
      window.__FORMOSA_BUILT__.built + '）　'
      + '此檔不會自動更新，僅供連線中斷時查閱；恢復連線後請改用線上版。';
    document.body.insertBefore(d, document.body.firstChild);
  });
})();
</script>
""" % (json.dumps(blob, ensure_ascii=False, separators=(',', ':')),
       json.dumps({'built': built, 'base_time': base_time}, ensure_ascii=False))

    # 插在第一個 <script> 之前，確保 shim 早於任何 fetch
    i = html.find('<script')
    if i < 0:
        print("!! index.html 找不到 <script>")
        return 2
    html = html[:i] + shim + html[i:]

    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)

    mb = os.path.getsize(out) / 1024 / 1024
    print(f"已產生 {out}（{mb:.1f} MB）")
    print(f"  資料時間：{base_time}　打包時間：{built}")
    print(f"  內嵌資料檔 {len(blob)} 個：" +
          "、".join(f"{n}({s // 1024}KB)" for n, s in sorted(
              sizes, key=lambda x: -x[1])[:6]))
    if miss:
        print(f"  !! 缺少檔案（該部分功能會失效）：{'、'.join(miss)}")
    print("  離線時無法使用：Himawari 衛星雲圖、外援連結、繪圖地圖分頁")
    return 0


if __name__ == '__main__':
    out = 'formosa_offline.html'
    if '--out' in sys.argv:
        out = sys.argv[sys.argv.index('--out') + 1]
    raise SystemExit(build(out=out))

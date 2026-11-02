"""前端单页（内联 HTML/CSS/JS，零外部依赖，断网也能用）。"""

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QQ 归档 · 群文件与视频</title>
<style>
  :root{
    --bg:#0e1116; --bg2:#151a22; --bg3:#1c232e; --bg4:#242d3a;
    --line:#28303d; --line2:#3a465c;
    --fg:#e8ebf0; --fg2:#9aa5b8; --fg3:#6b7688;
    --acc:#4c8dff; --acc2:#2b6fe0; --accsoft:rgba(76,141,255,.18);
    --ok:#3ecf8e; --warn:#f5a623; --err:#f0506e;
    --r:12px; --r-sm:9px;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 10px 26px -14px rgba(0,0,0,.7);
    --thumb:linear-gradient(145deg,#0b0e13,#121722);
  }
  /* 跟随系统浅色。原来只有一套深色，白天在浏览器里看很刺眼。 */
  @media (prefers-color-scheme: light){
    :root{
      --bg:#f6f7f9; --bg2:#ffffff; --bg3:#eef1f5; --bg4:#e2e7ee;
      --line:#dde2ea; --line2:#c2cbd8;
      --fg:#1a1f28; --fg2:#4a5568; --fg3:#8a94a6;
      --acc:#2b6fe0; --acc2:#1f5ec9; --accsoft:rgba(43,111,224,.14);
      --shadow:0 1px 2px rgba(0,0,0,.05), 0 10px 28px -16px rgba(0,0,0,.25);
      --thumb:linear-gradient(145deg,#eceff4,#f7f9fb);
    }
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{
    margin:0;background:var(--bg);color:var(--fg);
    font:14px/1.55 -apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;
    overflow:hidden;-webkit-font-smoothing:antialiased;
  }
  button,input,select,textarea{font:inherit;color:inherit}
  button{cursor:pointer;border:1px solid var(--line);background:var(--bg3);
    padding:6px 12px;border-radius:var(--r-sm);
    transition:background .15s,border-color .15s,color .15s,transform .1s}
  button:hover{background:var(--bg4);border-color:var(--line2)}
  button:active{transform:translateY(1px)}
  button.primary{background:var(--acc2);border-color:var(--acc2);color:#fff}
  button.primary:hover{background:var(--acc);border-color:var(--acc)}
  button.ghost{background:transparent;border-color:transparent;color:var(--fg2)}
  button.ghost:hover{background:var(--bg3);border-color:var(--line);color:var(--fg)}
  button:disabled{opacity:.45;cursor:not-allowed}
  button.iconbtn{padding:6px 9px;font-size:15px;line-height:1}
  input,select{background:var(--bg2);border:1px solid var(--line);border-radius:var(--r-sm);
    padding:7px 10px;outline:none;width:100%;transition:border-color .15s,box-shadow .15s}
  input:focus,select:focus{border-color:var(--acc);box-shadow:0 0 0 3px var(--accsoft)}
  a{color:var(--acc);text-decoration:none}

  /* ---------- 布局 ---------- */
  #app{display:flex;flex-direction:column;height:100%}
  header{
    display:flex;align-items:center;gap:10px;padding:9px 16px;
    background:var(--bg2);border-bottom:1px solid var(--line);flex-wrap:wrap;flex:none;
  }
  .brand{font-weight:700;font-size:15px;letter-spacing:.2px;white-space:nowrap;
    display:flex;align-items:center;gap:6px}
  .brand .logo{font-size:16px;filter:saturate(1.15)}
  .brand span{color:var(--acc)}
  .dot{width:8px;height:8px;border-radius:50%;display:inline-block;
    background:var(--fg3);flex:0 0 8px}
  .dot.on{background:var(--ok);box-shadow:0 0 0 3px rgba(62,207,142,.22)}
  .dot.off{background:var(--err);box-shadow:0 0 0 3px rgba(240,80,110,.22)}
  .pill{display:flex;align-items:center;gap:7px;padding:4px 11px;border-radius:999px;
    background:var(--bg3);border:1px solid var(--line);font-size:12.5px;color:var(--fg2);
    white-space:nowrap;max-width:360px;overflow:hidden;text-overflow:ellipsis}
  .spacer{flex:1}

  #body{flex:1;display:flex;min-height:0}
  aside{
    width:258px;flex:0 0 258px;background:var(--bg2);border-right:1px solid var(--line);
    display:flex;flex-direction:column;min-height:0;
  }
  aside .sec{padding:10px 12px 6px;display:flex;flex-direction:column;
    min-height:0;flex:1}
  aside .sec h4{margin:0 0 8px;font-size:11.5px;letter-spacing:.6px;
    color:var(--fg3);font-weight:600;
    display:flex;justify-content:space-between;align-items:center}
  .grp-list{overflow:auto;flex:1;min-height:0;padding:0 0 8px;margin:0 -4px}
  .grp{
    display:flex;align-items:center;gap:8px;padding:7px 9px;border-radius:8px;
    cursor:pointer;font-size:13px;color:var(--fg2);position:relative;
    transition:background .12s,color .12s
  }
  .grp:hover{background:var(--bg3);color:var(--fg)}
  .grp.active{background:var(--bg3);color:var(--fg);font-weight:600}
  .grp.active::before{content:"";position:absolute;left:0;top:7px;bottom:7px;width:2px;
    border-radius:2px;background:var(--acc)}
  .grp .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-left:3px}
  .grp .ct{font-size:11px;color:var(--fg3);font-variant-numeric:tabular-nums;
    background:var(--bg3);border-radius:99px;padding:1px 7px;flex:0 0 auto}
  .grp.active .ct{background:var(--bg4);color:var(--fg2)}
  .sw{position:relative;width:30px;height:16px;border-radius:99px;background:#39435a;
    flex:0 0 30px;transition:background .18s;cursor:pointer}
  .sw::after{content:"";position:absolute;top:2px;left:2px;width:12px;height:12px;
    border-radius:50%;background:#98a4b8;transition:.18s}
  .sw.on{background:var(--acc2)}
  .sw.on::after{left:16px;background:#fff}

  main{flex:1;display:flex;flex-direction:column;min-width:0;min-height:0}
  .toolbar{display:flex;gap:8px;align-items:center;padding:10px 14px 8px;
    flex-wrap:wrap;background:var(--bg);flex:none}
  .toolbar .grow{flex:1;min-width:180px;position:relative}
  .search .sicon{position:absolute;left:10px;top:50%;transform:translateY(-50%);
    font-size:12px;opacity:.5;pointer-events:none}
  .search input{padding-left:29px}
  .seg{display:flex;border:1px solid var(--line);border-radius:var(--r-sm);
    overflow:hidden;background:var(--bg2)}
  .seg button{border:0;border-radius:0;background:transparent;padding:6px 12px;
    font-size:13px;color:var(--fg2)}
  .seg button.on{background:var(--acc2);color:#fff}
  /* 类别改成 chip：实心品牌色块在深色底上太抢眼，chip 更像"筛选条件"而不是"主按钮" */
  .chips{display:flex;gap:6px;flex-wrap:wrap;align-items:center;padding:0 14px 10px;
    flex:none}
  .chip{border:1px solid var(--line);border-radius:999px;background:var(--bg2);
    padding:4px 11px;font-size:12.5px;color:var(--fg2);display:flex;
    align-items:center;gap:6px;transition:background .15s,border-color .15s,color .15s}
  .chip:hover{background:var(--bg3);border-color:var(--line2);color:var(--fg)}
  .chip.on{background:var(--acc2);border-color:var(--acc2);color:#fff}
  .chip b{font-weight:600;font-size:11px;opacity:.65;font-variant-numeric:tabular-nums}
  .chip.on b{opacity:.9}

  /* ---------- 文件列表 ----------
     这里原本是「缩略图卡片流」，换成列表行，原因很实在：
     库里文档类占了绝大多数（146 个文件里 98 个是 PDF/Word/Excel），
     而这些格式在浏览器里没法生成预览图。卡片流给每张卡留 120px 高的预览区，
     文档类只能填一个灰色图标，于是整页一半以上的格子是空的——比纯列表还难看。
     列表行把左边固定成 56px 的方块：图片/视频放真缩略图（好看的那部分留着），
     文档放带类别色的图标块（不再是空灰块）。两种都有内容，且一屏能看十几行。 */
  #grid{flex:1;overflow:auto;padding:8px 14px 16px;display:flex;
    flex-direction:column;gap:6px;align-content:start}
  .row{display:flex;align-items:center;gap:11px;padding:7px 10px;
    background:var(--bg2);border:1px solid var(--line);border-radius:var(--r-sm);
    transition:border-color .13s,background .13s;position:relative;min-width:0}
  .row:hover{border-color:var(--line2);background:var(--bg3)}
  /* 56×56 统一缩略格。object-fit:cover 让 16:9 的视频封面也能填满且不拉伸。 */
  .row .thumb{width:56px;height:56px;flex:none;border-radius:8px;overflow:hidden;
    background:var(--thumb);display:flex;align-items:center;justify-content:center;
    position:relative;cursor:pointer}
  .row .thumb img{width:100%;height:100%;object-fit:cover;display:block}
  .row .thumb .ic{font-size:24px;line-height:1}
  /* 播放角标压在缩略图右下角，比整块盖一层半透明遮罩克制。 */
  .row .thumb .play{position:absolute;right:2px;bottom:2px;width:17px;height:17px;
    border-radius:50%;background:rgba(0,0,0,.62);color:#fff;font-size:9px;
    display:flex;align-items:center;justify-content:center;pointer-events:none}
  .row .main{flex:1;min-width:0;display:flex;flex-direction:column;gap:3px}
  .row .fn{font-size:13px;line-height:1.35;font-weight:500;color:var(--fg);
    overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .row .sub{font-size:11.5px;color:var(--fg3);display:flex;gap:7px;
    align-items:center;min-width:0;overflow:hidden}
  .row .gname{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
    flex:0 1 auto;min-width:0}
  .row .acts{display:flex;gap:5px;flex:none}
  .row .acts button{padding:4px 10px;font-size:11.5px}
  /* 窄屏：把「目录」按钮收成图标，否则文件名可读宽度不够。 */
  @media(max-width:640px){
    .row .acts button[data-act="folder"]{padding:4px 8px}
    .row .acts button[data-act="folder"] .lbl{display:none}
  }
  .tag{background:var(--bg3);border-radius:5px;padding:1px 6px;font-size:10.5px;
    color:var(--fg2);flex:none}
  .tag.v{color:#7fd1ff;background:rgba(127,209,255,.14)}
  .tag.d{color:#ffd479;background:rgba(255,212,121,.14)}
  .tag.z{color:#c9a2ff;background:rgba(201,162,255,.14)}
  /* 类别图标块按类别上色。文档类没有缩略图，靠这块颜色区分 PDF/表格/幻灯片，
     否则一屏十几个一模一样的灰方块，还是分不清。 */
  .row .thumb.i-doc{background:rgba(127,209,255,.12);color:#7fd1ff}
  .row .thumb.i-sheet{background:rgba(120,220,150,.13);color:#78dc96}
  .row .thumb.i-ppt{background:rgba(255,168,110,.14);color:#ffa86e}
  .row .thumb.i-zip{background:rgba(201,162,255,.14);color:#c9a2ff}
  .row .thumb.i-audio{background:rgba(255,212,121,.14);color:#ffd479}
  .row .thumb.i-code{background:rgba(160,180,210,.14);color:#a0b4d2}
  .row .thumb.i-other{background:var(--bg3);color:var(--fg3)}

  #empty{display:none;flex:1;align-items:center;justify-content:center;
    flex-direction:column;gap:6px;color:var(--fg3);text-align:center;padding:24px}
  #empty .big{font-size:46px;opacity:.3;margin-bottom:2px}

  footer{display:flex;gap:14px;align-items:center;padding:7px 16px;
    border-top:1px solid var(--line);background:var(--bg2);font-size:12px;
    color:var(--fg2);flex:none;min-width:0;overflow:hidden}
  /* 保存路径可能很长（C:\Users\...\课程），窄窗口里会把 footer 撑出屏幕，
     实测 420px 溢出 119px。这里让它可缩、超长省略，完整值走 title。 */
  footer #fRoot{flex:0 1 auto;min-width:0;overflow:hidden;text-overflow:ellipsis;
    white-space:nowrap;color:var(--fg3)}
  .bar{height:5px;border-radius:99px;background:var(--bg3);overflow:hidden;
    width:110px;flex:0 0 110px}
  .bar i{display:block;height:100%;background:var(--acc);width:0;transition:.3s}

  /* ---------- 弹层 ---------- */
  .mask{position:fixed;inset:0;background:rgba(0,0,0,.62);display:none;
    align-items:center;justify-content:center;z-index:50;padding:20px}
  .mask.show{display:flex}
  .modal{background:var(--bg2);border:1px solid var(--line);border-radius:14px;
    max-width:min(1040px,96vw);max-height:92vh;display:flex;flex-direction:column;
    overflow:hidden;box-shadow:0 24px 60px rgba(0,0,0,.5)}
  .modal .mh{display:flex;align-items:center;gap:10px;padding:12px 16px;
    border-bottom:1px solid var(--line);flex:none}
  .modal .mh .t{flex:1;font-size:13.5px;font-weight:600;overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap}
  .modal .mb{padding:16px;overflow:auto}
  .modal video,.modal audio{max-width:100%;max-height:74vh;display:block;
    background:#000;border-radius:8px;margin:0 auto}
  .modal img{max-width:100%;max-height:74vh;display:block;border-radius:8px;
    margin:0 auto}

  .field{margin-bottom:14px}
  .field label{display:block;font-size:12px;color:var(--fg2);margin-bottom:5px}
  .field .hint{font-size:11.5px;color:var(--fg3);margin-top:4px}
  .row{display:flex;gap:12px}
  .row>.field{flex:1}
  .chk{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--fg2);
    padding:5px 0;cursor:pointer}
  .chk input{width:auto;margin:0}
  code.path{background:var(--bg);border:1px solid var(--line);border-radius:6px;
    padding:2px 6px;font-size:12px;word-break:break-all}

  /* 队列改成列表行：6 列表格在窄弹层里必然挤成一团 */
  .tl{display:flex;flex-direction:column;border:1px solid var(--line);
    border-radius:var(--r-sm);overflow:hidden}
  .tl-row{display:flex;gap:10px;align-items:flex-start;padding:9px 11px;
    border-bottom:1px solid var(--line)}
  .tl-row:last-child{border-bottom:0}
  .tl-main{flex:1;min-width:0;display:flex;flex-direction:column;gap:3px}
  .tl-name{font-size:12.5px;font-weight:500;overflow:hidden;
    text-overflow:ellipsis;white-space:nowrap}
  .tl-meta{font-size:11px;color:var(--fg3);display:flex;gap:8px;
    flex-wrap:wrap;align-items:center}
  .tl-err{font-size:11px;color:var(--err);display:-webkit-box;
    -webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;
    word-break:break-all}
  .tl-err.warn{color:var(--warn)}
  .st{border-radius:999px;padding:1px 8px;font-size:10.5px;border:1px solid;
    white-space:nowrap;flex:0 0 auto}
  .st.pending{color:var(--fg3);border-color:var(--line2)}
  .st.running{color:var(--acc);border-color:var(--acc)}
  .st.done{color:var(--ok);border-color:var(--ok)}
  .st.failed{color:var(--err);border-color:var(--err)}
  .st.gone{color:var(--warn);border-color:var(--warn)}
  .st.skipped{color:var(--fg3);border-color:var(--line)}

  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(124px,1fr));
    gap:10px;margin-bottom:14px}
  .kpi{background:var(--bg3);border:1px solid var(--line);
    border-radius:var(--r-sm);padding:9px 11px}
  .kpi .k{font-size:11px;color:var(--fg3);margin-bottom:2px}
  .kpi .v{font-size:18px;font-weight:700;font-variant-numeric:tabular-nums;
    letter-spacing:-.2px}
  .kpi.warn .v{color:var(--warn)}
  .kpi.err .v{color:var(--err)}

  /* 设置面板分区，七零八落的字段排下来分不清哪组管什么 */
  .set-sec{border:1px solid var(--line);border-radius:var(--r-sm);
    padding:12px 13px;margin-bottom:12px;background:var(--bg)}
  .set-sec h5{margin:0 0 10px;font-size:12px;color:var(--fg2);font-weight:600;
    display:flex;align-items:center;gap:7px}
  .set-sec h5::before{content:"";width:3px;height:12px;border-radius:2px;
    background:var(--acc)}
  .set-sec .field:last-child{margin-bottom:0}

  #toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%) translateY(20px);
    background:var(--bg4);border:1px solid var(--line2);color:var(--fg);
    padding:9px 18px;border-radius:99px;font-size:13px;opacity:0;
    pointer-events:none;transition:.22s;z-index:99;box-shadow:var(--shadow)}
  #toast.show{opacity:1;transform:translateX(-50%) translateY(0)}

  ::-webkit-scrollbar{width:10px;height:10px}
  ::-webkit-scrollbar-thumb{background:var(--line2);border-radius:99px}
  ::-webkit-scrollbar-thumb:hover{background:var(--fg3)}
  ::-webkit-scrollbar-track{background:transparent}

  @media(max-width:820px){
    aside{position:fixed;left:0;top:0;bottom:0;z-index:40;
      transform:translateX(-100%);transition:.22s;box-shadow:0 0 40px #000}
    aside.open{transform:none}
  }
</style>
</head>
<body>
<div id="app">
  <header>
    <button class="ghost iconbtn" id="btnAside" style="display:none"
            title="群列表">☰</button>
    <div class="brand"><span class="logo">🐋</span>QQ<span>归档</span></div>
    <div class="pill" title="NapCat 连接状态">
      <i class="dot" id="connDot"></i><span id="connTxt">未连接</span></div>
    <div class="pill" id="statPill">0 个文件</div>
    <div class="spacer"></div>
    <button id="btnScan" title="立刻补扫一遍群文件">补扫</button>
    <button id="btnTasks" title="查看下载队列与补扫状态">队列</button>
    <button id="btnCfg" title="保存位置、抓取范围、群监听">设置</button>
  </header>

  <div id="body">
    <aside id="aside">
      <div class="sec">
        <h4>群聊
          <button class="ghost" id="btnRefreshGrp"
              style="padding:1px 7px;font-size:11px">刷新</button></h4>
        <div class="grp-list" id="grpList"></div>
      </div>
    </aside>

    <main>
      <div class="toolbar">
        <div class="grow search">
          <span class="sicon">🔍</span>
          <input id="q" placeholder="搜索文件名 / 群名 / 上传者…">
        </div>
        <div class="seg" id="segSrc">
          <button data-src="" class="on">全部来源</button>
          <button data-src="群文件">群文件</button>
          <button data-src="聊天视频">聊天视频</button>
        </div>
        <select id="sort" style="width:auto">
          <option value="new">最新在前</option>
          <option value="old">最早在前</option>
          <option value="size">大小</option>
          <option value="name">文件名</option>
        </select>
      </div>
      <div class="chips" id="segCat"></div>
      <div id="grid"></div>
      <div id="empty">
        <div class="big">🗂</div>
        <div>还没有抓到文件</div>
        <div style="font-size:12.5px">确认 NapCat 已连上本软件，并且至少有一个群被打开监听</div>
      </div>
    </main>
  </div>

  <footer>
    <span id="fGroup">全部群</span>
    <span id="fCount">—</span>
    <span class="spacer"></span>
    <span id="fDl">队列空闲</span>
    <div class="bar"><i id="dlBar"></i></div>
    <span id="fRoot"></span>
  </footer>
</div>

<!-- 预览 -->
<div class="mask" id="mView"><div class="modal">
  <div class="mh"><div class="t" id="vTitle"></div>
    <button class="ghost" id="vFolder">打开目录</button>
    <button class="ghost" id="vClose">✕</button></div>
  <div class="mb" id="vBody"></div>
</div></div>

<!-- 队列 -->
<div class="mask" id="mTasks"><div class="modal" style="width:min(820px,94vw)">
  <div class="mh"><div class="t">下载队列与补扫状态</div>
    <button class="ghost" id="tRetry">重试失败</button>
    <button class="ghost" id="tClose">✕</button></div>
  <div class="mb" id="tBody"></div>
</div></div>

<!-- 设置 -->
<div class="mask" id="mCfg"><div class="modal" style="width:min(700px,94vw)">
  <div class="mh"><div class="t">设置</div><button class="ghost" id="cClose">✕</button></div>
  <div class="mb" id="cBody"></div>
</div></div>

<div id="toast"></div>

<script>
"use strict";
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];

const S = {
  files: [], total: 0, offset: 0, limit: 60,
  q: "", group_id: "", category: "", source: "", sort: "new",
  groups: [], groupMap: {}, status: {}, stats: {}, openId: null,
  rendered: 0, loading: false,
};

/* ---------------- 工具 ---------------- */
function esc(s){
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function fmtSize(n){
  n = Number(n)||0;
  if(n < 1024) return n + " B";
  const u = ["KB","MB","GB","TB"]; let i = -1;
  do { n /= 1024; i++; } while(n >= 1024 && i < u.length-1);
  return n.toFixed(n < 10 ? 2 : 1) + " " + u[i];
}
function fmtTime(ts){
  if(!ts) return "";
  const d = new Date(Number(ts) * 1000);
  if(isNaN(d)) return "";
  const p = x => String(x).padStart(2,"0");
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
let toastTimer = null;
function toast(msg){
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}
async function api(path, opts){
  const r = await fetch(path, opts);
  if(!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}
function postJSON(path, body){
  return api(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body || {})
  });
}

/* ---------------- 图标与状态文案 ---------------- */
const ICON = {
  "视频":"🎬","图片":"🖼","音频":"🎵","文档":"📄","表格":"📊","幻灯片":"📽",
  "压缩包":"🗜","软件":"⚙","代码":"⌨","其他":"📦"
};
function iconFor(cat){ return ICON[cat] || "📦"; }
const TAGCLS = {"视频":"v","文档":"d","压缩包":"z"};
/* 文档类没有缩略图，靠缩略格的底色区分 format：一屏十几个文件，
   全灰的话还是分不清哪个是 PDF、哪个是表格、哪个是 PPT。 */
const ICONCLS = {"文档":"i-doc","表格":"i-sheet","幻灯片":"i-ppt",
                 "压缩包":"i-zip","音频":"i-audio","代码":"i-code"};

/* 状态文案要和后端状态机一一对应。gone 必须和 failed 分开显示——
   它跟「失败」是两件事：文件在群里已经没了，重试也不会变。 */
const STATE_TXT = {
  pending:"待下载", running:"下载中", done:"已完成",
  skipped:"已跳过", gone:"群里已无", failed:"失败"
};

/* ---------------- 类别 chip ---------------- */
const CATS = ["", "视频", "图片", "音频", "文档", "表格", "幻灯片",
              "压缩包", "代码", "软件", "其他"];

function paintCats(){
  const counts = {};
  for(const c of (S.stats.by_category || [])) counts[c.category] = c.n;
  const box = $("#segCat");
  box.innerHTML = CATS.map(c => {
    const n = c ? (counts[c] || 0) : (S.stats.total || 0);
    const label = c ? `${iconFor(c)} ${c}` : "全部";
    /* 计数为 0 的类别不显示（除了「全部」），否则一排全是 0 很吵 */
    if(c && !n) return "";
    return `<button class="chip${S.category === c ? " on" : ""}" data-cat="${c}">
      ${label}${n ? `<b>${n}</b>` : ""}</button>`;
  }).join("");
}

/* ---------------- 加载列表 ---------------- */
async function loadFiles(append){
  if(S.loading) return;
  S.loading = true;
  const p = new URLSearchParams({
    q: S.q, group_id: S.group_id, category: S.category,
    source: S.source, sort: S.sort,
    limit: S.limit, offset: append ? S.files.length : 0
  });
  let d;
  try { d = await api("/api/files?" + p); }
  catch(e){ toast("加载失败: " + e.message); S.loading = false; return; }
  S.loading = false;
  S.total = d.total;
  if(append) S.files = S.files.concat(d.files);
  else { S.files = d.files; S.rendered = 0; $("#grid").innerHTML = ""; }
  renderFiles();
  renderFooter();
}

function renderFiles(){
  const g = $("#grid");
  const frag = document.createDocumentFragment();
  for(let i = S.rendered; i < S.files.length; i++){
    const f = S.files[i];
    const el = document.createElement("div");
    el.className = "row";
    el.dataset.id = f.id;
    const isMedia = ["视频","图片","音频"].includes(f.category);
    const icon = iconFor(f.category);
    const tagCls = TAGCLS[f.category] || "";
    /* 文档类没有缩略图，给缩略格挂一个类别色块类名（i-doc / i-sheet …），
       让一屏十几个文件靠颜色就能分门别类。 */
    const icCls = ICONCLS[f.category] || "i-other";
    el.innerHTML = `
      <div class="thumb ${isMedia ? "" : icCls}">
        ${isMedia ? `<img loading="lazy" src="/thumb/${f.id}" alt="">` : `<div class="ic">${icon}</div>`}
        ${f.category === "视频" ? `<div class="play">▶</div>` : ""}
      </div>
      <div class="main">
        <div class="fn" title="${esc(f.file_name)}">${esc(f.file_name)}</div>
        <div class="sub">
          <span class="tag ${tagCls}">${esc(f.category || "其他")}</span>
          <span>${fmtSize(f.size)}</span>
          <span>${fmtTime(f.created_at)}</span>
          <span class="gname" title="${esc(f.group_name || f.group_id)}">${esc(f.group_name || f.group_id)}</span>
        </div>
      </div>
      <div class="acts">
        <button data-act="view">${isMedia ? "播放" : "打开"}</button>
        <button data-act="folder">📁<span class="lbl"> 目录</span></button>
      </div>`;
    const img = el.querySelector("img");
    if(img) img.addEventListener("error", () => {
      /* 兜底也要带类别色：否则缩略图挂了会退化成灰块，跟文档类混在一起。 */
      const box = img.parentElement;
      box.classList.add(icCls);
      img.replaceWith(Object.assign(document.createElement("div"),
        {className:"ic", textContent:icon}));
    });
    el.querySelector(".thumb").addEventListener("click", () => openView(f));
    el.querySelector('[data-act="view"]').addEventListener("click", () => openView(f));
    el.querySelector('[data-act="folder"]').addEventListener("click", () => openFolder(f));
    frag.appendChild(el);
  }
  S.rendered = S.files.length;
  g.appendChild(frag);
  $("#empty").style.display = S.files.length ? "none" : "flex";
}

function renderFooter(){
  const gname = S.group_id
    ? (S.groupMap[S.group_id] || S.group_id)
    : "全部群";
  $("#fGroup").textContent = gname;
  const shown = S.files.length, tot = S.total;
  $("#fCount").textContent = shown < tot ? `显示 ${shown} / ${tot}` : `共 ${tot} 个文件`;
}

/* ---------------- 卡片操作 ---------------- */
function openView(f){
  S.openId = f.id;
  $("#vTitle").textContent = f.file_name;
  const box = $("#vBody");
  box.innerHTML = "";
  if(f.category === "视频"){
    box.innerHTML = `<video controls autoplay playsinline src="/media/${f.id}"></video>`;
  } else if(f.category === "音频"){
    box.innerHTML = `<audio controls autoplay src="/media/${f.id}"></audio>`;
  } else if(f.category === "图片"){
    box.innerHTML = `<img src="/media/${f.id}" alt="">`;
  } else {
    box.innerHTML = `<div style="color:var(--fg2);line-height:2">
      该文件类型无法在浏览器里预览，可点右上角「打开目录」用本地程序打开。<br>
      路径：<code class="path">${esc(f.save_path || "")}</code></div>`;
  }
  $("#mView").classList.add("show");
}
function closeView(){
  $("#mView").classList.remove("show");
  $("#vBody").innerHTML = "";  // 停掉播放
  S.openId = null;
}
async function openFolder(f){
  try { await postJSON("/api/open_folder", {id: f.id}); }
  catch(e){ toast("打开失败: " + e.message); }
}

/* ---------------- 群列表 ---------------- */
async function loadGroups(){
  let d;
  try { d = await api("/api/groups"); }
  catch(e){ return; }
  S.groups = d.groups || [];
  S.groupMap = {};
  const box = $("#grpList");
  box.innerHTML = "";

  const all = document.createElement("div");
  all.className = "grp" + (S.group_id === "" ? " active" : "");
  all.innerHTML = `<span class="nm">全部群聊</span>`;
  all.addEventListener("click", () => { S.group_id = ""; loadGroups(); loadFiles(false); });
  box.appendChild(all);

  for(const g of S.groups){
    S.groupMap[g.group_id] = g.group_name || g.group_id;
    const el = document.createElement("div");
    el.className = "grp" + (S.group_id === g.group_id ? " active" : "");
    el.innerHTML = `
      <span class="nm" title="${esc(g.group_name || g.group_id)}">${esc(g.group_name || g.group_id)}</span>
      <span class="ct">${g.file_count ?? ""}</span>
      <span class="sw ${g.enabled ? "on" : ""}" title="监听开关"></span>`;
    el.querySelector(".nm").addEventListener("click", () => {
      S.group_id = g.group_id; loadGroups(); loadFiles(false);
    });
    el.querySelector(".sw").addEventListener("click", async ev => {
      ev.stopPropagation();
      const now = !ev.currentTarget.classList.contains("on");
      try {
        await postJSON("/api/group_enable", {group_id: g.group_id, enabled: now});
        ev.currentTarget.classList.toggle("on", now);
        g.enabled = now;
      } catch(e){ toast("操作失败: " + e.message); }
    });
    box.appendChild(el);
  }
}

/* ---------------- 状态轮询 ---------------- */
function paintStatus(st){
  S.status = st || {};
  const on = !!st.onebot_connected;
  $("#connDot").className = "dot " + (on ? "on" : "off");
  $("#connTxt").textContent = on
    ? `已连接${st.nickname ? " · " + st.nickname : ""}${st.self_id ? " (" + st.self_id + ")" : ""}`
    : "未连接 NapCat";
  $("#fRoot").textContent = st.save_root || "";
  $("#fRoot").title = st.save_root || "";
  if(st.save_root && !st.save_root_ok) $("#fRoot").textContent += "（不可写入！）";
}

async function poll(){
  try { paintStatus(await api("/api/status")); } catch(e){ paintStatus({}); }
  try {
    const s = await api("/api/stats");
    S.stats = s;
    const parts = [`${s.total ?? 0} 个文件`, fmtSize(s.total_size || 0)];
    const v = (s.by_category || []).find(x => x.category === "视频");
    if(v) parts.push(`视频 ${v.n}`);
    $("#statPill").textContent = parts.join(" · ");
    const d = s.downloader || {};
    const pend = (d.pending || 0) + (d.running || 0);
    $("#fDl").textContent = pend
      ? `下载中 ${pend} 项`
      : `已完成 ${d.done || 0}`
        + (d.failed ? ` · 失败 ${d.failed}` : "")
        + (d.gone ? ` · 已无 ${d.gone}` : "");
    const tot = pend + (d.done||0) + (d.failed||0) + (d.gone||0);
    $("#dlBar").style.width = tot ? Math.round(((d.done||0) / tot) * 100) + "%" : "0";
    paintCats();
  } catch(e){}
}

/* ---------------- 队列面板 ---------------- */
async function openTasks(){
  $("#mTasks").classList.add("show");
  let d;
  try { d = await api("/api/tasks"); }
  catch(e){ $("#tBody").innerHTML = "加载失败"; return; }
  const dl = d.downloader || {}, sc = d.scanner || {};
  const rows = (d.tasks || []).map(t => {
    const st = t.state || "";
    const isGone = st === "gone";
    return `<div class="tl-row">
      <div class="tl-main">
        <div class="tl-name" title="${esc(t.file_name)}">${esc(t.file_name)}</div>
        <div class="tl-meta">
          <span class="st ${esc(st)}">${esc(STATE_TXT[st] || st || "-")}</span>
          <span>${esc(t.group_name || t.group_id || "")}</span>
          <span>${esc(t.source || "")}</span>
          <span>${fmtSize(t.size)}</span>
          ${t.attempts ? `<span>重试 ${t.attempts} 次</span>` : ""}
        </div>
        ${t.last_error ? `<div class="tl-err${isGone ? " warn" : ""}"
           title="${esc(t.last_error)}">${esc(t.last_error)}</div>` : ""}
      </div>
    </div>`;
  }).join("");
  $("#tBody").innerHTML = `
    <div class="kpis">
      <div class="kpi"><div class="k">待下载</div><div class="v">${dl.pending||0}</div></div>
      <div class="kpi"><div class="k">下载中</div><div class="v">${dl.running||0}</div></div>
      <div class="kpi"><div class="k">已完成</div><div class="v">${dl.done||0}</div></div>
      <div class="kpi ${dl.failed ? "err" : ""}">
        <div class="k">失败</div><div class="v">${dl.failed||0}</div></div>
      <div class="kpi ${dl.gone ? "warn" : ""}">
        <div class="k">群里已无</div><div class="v">${dl.gone||0}</div></div>
    </div>
    <div class="set-sec">
      <h5>补扫器</h5>
      <div style="font-size:12.5px;color:var(--fg2)">
        ${sc.running ? "扫描中 · " + esc(sc.group||"") : "空闲"}
        · 上次 ${sc.last_run ? fmtTime(sc.last_run) : "未运行"}
        ${sc.read_errors ? ` · <span style="color:var(--warn)">读取目录失败 ${sc.read_errors} 次</span>` : ""}
      </div>
      ${sc.last_read_error
        ? `<div class="tl-err warn" style="margin-top:6px">${esc(sc.last_read_error)}</div>`
        : ""}
    </div>
    ${rows ? `<div class="tl">${rows}</div>`
      : `<div style="color:var(--fg3);padding:20px 0">暂无任务记录</div>`}`;
}

/* ---------------- 设置面板 ---------------- */
async function openCfg(){
  $("#mCfg").classList.add("show");
  let c;
  try { c = await api("/api/config"); }
  catch(e){ $("#cBody").innerHTML = "加载失败"; return; }
  const st = S.status || {};
  const LAYOUT = {
    group_date_type: "群名 / 年-月 / 类别（推荐）",
    group_type: "群名 / 类别",
    date_type: "年-月 / 类别",
    type_only: "只按类别",
  };
  $("#cBody").innerHTML = `
    <div class="set-sec">
      <h5>保存位置</h5>
      <div class="field" style="margin-bottom:8px">
        <code class="path">${esc(c.save_root)}</code>
        <div class="hint">${st.save_root_ok === false
          ? '<span style="color:var(--err)">该目录不可写入，请检查权限</span>'
          : "文件按下方布局自动分目录存放"}</div>
      </div>
      <div class="field">
        <label>目录布局</label>
        <select id="cfLayout">
          ${Object.entries(LAYOUT).map(([k,v]) =>
            `<option value="${k}" ${c.layout===k?"selected":""}>${v}</option>`).join("")}
        </select>
      </div>
    </div>

    <div class="set-sec">
      <h5>抓取范围</h5>
      <label class="chk"><input type="checkbox" id="cfGroupFiles" ${c.grab_group_files?"checked":""}>
        群文件（所有类型）</label>
      <label class="chk"><input type="checkbox" id="cfChatVideo" ${c.grab_chat_video?"checked":""}>
        聊天里直接发的视频</label>
      <label class="chk"><input type="checkbox" id="cfChatFile" ${c.grab_chat_file?"checked":""}>
        聊天里直接发的文件</label>
      <label class="chk"><input type="checkbox" id="cfImages" ${c.grab_images?"checked":""}>
        图片</label>
    </div>

    <div class="set-sec">
      <h5>群监听</h5>
      <div class="field">
        <label>只监听这些群（留空 = 全部群）</label>
        <input id="cfWatch" placeholder="群号，用逗号分隔"
               value="${esc((c.watch_groups||[]).join(", "))}">
        <div class="hint">留空时按左侧群列表的开关逐个控制</div>
      </div>
    </div>

    <div class="set-sec">
      <h5>性能与补扫</h5>
      <div class="row">
        <div class="field"><label>同时下载数</label>
          <input type="number" id="cfConc" min="1" max="8" value="${c.concurrency||3}">
        </div>
        <div class="field"><label>补扫间隔（分钟）</label>
          <input type="number" id="cfInterval" min="1" max="1440" value="${c.scan_interval_min||30}">
        </div>
      </div>
      <label class="chk"><input type="checkbox" id="cfScan" ${c.scan_enabled?"checked":""}>
        启用定时补扫（补回软件没开时上传的群文件）</label>
      <label class="chk"><input type="checkbox" id="cfDedupe" ${c.dedupe?"checked":""}>
        内容去重（同一文件不重复保存）</label>
    </div>

    <div class="set-sec">
      <h5>大小过滤</h5>
      <div class="row">
        <div class="field"><label>跳过小于（KB）</label>
          <input type="number" id="cfMin" min="0" value="${Math.round((c.min_size||0)/1024)}">
        </div>
        <div class="field"><label>跳过大于（MB，0=不限）</label>
          <input type="number" id="cfMax" min="0" value="${c.max_size_mb||0}">
        </div>
      </div>
    </div>

    <div style="display:flex;gap:10px;margin-top:6px">
      <button class="primary" id="cfSave">保存</button>
      <button class="ghost" id="cfCancel">取消</button>
    </div>`;

  $("#cfCancel").addEventListener("click", () => $("#mCfg").classList.remove("show"));
  $("#cfSave").addEventListener("click", async () => {
    const watch = $("#cfWatch").value.split(/[,\s，]+/).filter(Boolean);
    const body = {
      layout: $("#cfLayout").value,
      grab_group_files: $("#cfGroupFiles").checked,
      grab_chat_video: $("#cfChatVideo").checked,
      grab_chat_file: $("#cfChatFile").checked,
      grab_images: $("#cfImages").checked,
      watch_groups: watch,
      concurrency: Number($("#cfConc").value) || 3,
      scan_interval_min: Number($("#cfInterval").value) || 30,
      scan_enabled: $("#cfScan").checked,
      dedupe: $("#cfDedupe").checked,
      min_size: Math.round(Number($("#cfMin").value) || 0) * 1024,
      max_size_mb: Number($("#cfMax").value) || 0,
    };
    try {
      await postJSON("/api/config", body);
      toast("设置已保存");
      $("#mCfg").classList.remove("show");
      poll(); loadGroups(); loadFiles(false);
    } catch(e){ toast("保存失败: " + e.message); }
  });
}

/* ---------------- 事件绑定 ---------------- */
$("#q").addEventListener("input", debounce(e => {
  S.q = e.target.value.trim(); loadFiles(false);
}, 320));

$("#segCat").addEventListener("click", e => {
  const b = e.target.closest("button"); if(!b) return;
  S.category = b.dataset.cat || "";
  paintCats(); loadFiles(false);
});
$("#segSrc").addEventListener("click", e => {
  const b = e.target.closest("button"); if(!b) return;
  $$("#segSrc button").forEach(x => x.classList.remove("on"));
  b.classList.add("on"); S.source = b.dataset.src; loadFiles(false);
});
$("#sort").addEventListener("change", e => { S.sort = e.target.value; loadFiles(false); });

$("#grid").addEventListener("scroll", () => {
  if(S.files.length >= S.total) return;
  const g = $("#grid");
  if(g.scrollTop + g.clientHeight > g.scrollHeight - 240) loadFiles(true);
});

$("#btnScan").addEventListener("click", async () => {
  try { await postJSON("/api/scan"); toast("补扫已触发，稍候查看队列"); }
  catch(e){ toast("触发失败: " + e.message); }
});
$("#btnTasks").addEventListener("click", openTasks);
$("#btnCfg").addEventListener("click", openCfg);
$("#btnRefreshGrp").addEventListener("click", async () => {
  try { await postJSON("/api/refresh_groups"); toast("群列表已刷新"); }
  catch(e){ toast("刷新失败: " + e.message); }
  loadGroups();
});
$("#tClose").addEventListener("click", () => $("#mTasks").classList.remove("show"));
$("#tRetry").addEventListener("click", async () => {
  try {
    const r = await postJSON("/api/retry");
    toast(`已重试 ${r.retried} 个任务`);
    openTasks(); poll();
  } catch(e){ toast("重试失败: " + e.message); }
});
$("#vClose").addEventListener("click", closeView);
$("#vFolder").addEventListener("click", () => {
  const f = S.files.find(x => x.id === S.openId); if(f) openFolder(f);
});
$("#mView").addEventListener("click", e => { if(e.target.id === "mView") closeView(); });
$("#mTasks").addEventListener("click", e => { if(e.target.id === "mTasks") $("#mTasks").classList.remove("show"); });
$("#mCfg").addEventListener("click", e => { if(e.target.id === "mCfg") $("#mCfg").classList.remove("show"); });
document.addEventListener("keydown", e => {
  if(e.key === "Escape"){ closeView(); $("#mTasks").classList.remove("show"); $("#mCfg").classList.remove("show"); }
});
$("#btnAside").addEventListener("click", () => $("#aside").classList.toggle("open"));

function debounce(fn, ms){
  let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

/* ---------------- 启动 ---------------- */
(async function boot(){
  if(window.innerWidth <= 820){
    $("#btnAside").style.display = "";
    $("#aside").classList.remove("open");
  }
  paintCats();
  await poll();
  await loadGroups();
  await loadFiles(false);
  setInterval(poll, 3000);
  setInterval(() => { if(document.visibilityState === "visible") loadFiles(false); }, 30000);
})();
</script>
</body>
</html>
"""

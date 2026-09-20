'use strict';
/* 用户端与管理台共用的 UI 基座：DOM 构造、表格、提示层、时间格式化、取号与 REST 调用。
   两份页面脚本此前各自抄了一份这些工具，改一处忘另一处的代价已经真实发生过一次
   （提示条改 toast 时要在两个文件里做同样的编辑）。
   以普通同源脚本加载（页面 CSP 为 default-src 'self'，不允许内联脚本），
   在 app.js / admin.js 之前以 defer 顺序执行，故两者可直接取用全局 UI。 */
window.UI = (() => {
  const $ = id => document.getElementById(id);

  /* 服务端所有文本长度限制都是 strlen，也就是 UTF-8 字节数；而 JS 的 .length 和 HTML 的
     maxlength 数的是 UTF-16 码元。一个汉字 3 字节：标题框 maxlength=120 时打满 120 个汉字，
     浏览器一路放行，到服务端按 360 字节拒绝，用户只看到一句不带原因的 400。
     长度口径全站只留这一份，两端脚本都从这里取，避免又写出第二个数。 */
  const UTF8_ENCODER = new TextEncoder();
  function byteLen(s) { return UTF8_ENCODER.encode(s || '').length; }
/* 样式表里的 prefers-reduced-motion 兜不住这里：JS 传 behavior:'smooth' 时
   滚动动画照样播，键盘用户按「编辑」会被平滑滚动带着一整页跑。 */
function scrollTo(el, block) {
  const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: block || 'start' });
}
  /* 超长时返回可直接显示的提示（含实测字节数与汉字换算），未超长返回 ''。max 是服务端的字节上限。 */
  function overBytes(s, max, label) {
    const n = byteLen(s);
    if (n <= max) return '';
    const detail = '最长 ' + max + ' 字节，当前 ' + n + ' 字节（一个汉字占 3 字节，约可写 ' + Math.floor(max / 3) + ' 个）';
    return label ? label + '：' + detail : detail;
  }

  /* 按「码点」截断，不是按 UTF-16 码元：String.prototype.slice 会把代理对
     （emoji、以及 CJK 扩展 B 区的生僻字）从中间切成两半，界面上就是一个替换符。
     返回 {text, clipped}，让调用方自己决定被截掉的部分怎么找回——
     全站需要省略号的地方都走这里，不要再各写一份 length + slice。 */
  function clip(text, n) {
    const s = text || '';
    const cps = Array.from(s);
    if (cps.length <= n) return { text: s, clipped: false };
    return { text: cps.slice(0, n).join('') + '…', clipped: true };
  }

  /* 表格里「备注」这一列的通用渲染：截断（按码点，见 clip）+ 原生 <details> 原地展开。
     用户端的「我的记录」和管理台的「待审批预约」都要它——前者是核对，后者是审批依据，
     两页各写一份迟早会漂移，所以收在共用出口里。
     用 <details> 而不是自建展开状态：键盘聚焦、展开语义、读屏播报都是浏览器给的。 */
  const NOTE_CLIP = 24;
  function noteCell(text, n) {
    if (!text) return '—'; /* 空备注要有显式取值：审批人看到空格子分不清「用户没写」还是「没读到」 */
    const c = clip(text, n || NOTE_CLIP);
    if (!c.clipped) return c.text;
    const box = el('details', undefined, 'cell-more');
    box.append(el('summary', c.text), el('div', text, 'cell-more-body'));
    return box;
  }

  /* 提交前的本地校验：把第一条不满足的规则写进表单自己的错误位（role=alert），
     给对应字段打上 field-invalid + aria-invalid 并把焦点送过去，其余字段复位。
     rules 形如 [[字段名, 值->提示]]，返回 '' 表示该字段通过。
     用它的表单必须声明 novalidate：否则 required/min/max 会让浏览器先弹出原生气泡，
     submit 事件根本不触发，页面写好的中文错误位一次都不会显示
     （实测管理台空提交弹的是 "Please fill out this field."，页面 lang 是 zh-CN）。 */
  function formCheck(form, boxId, rules) {
    const box = $(boxId);
    const fields = [...form.querySelectorAll('input,textarea,select')];
    for (const pair of rules) {
      const field = form.elements[pair[0]];
      const msg = field ? pair[1](field.value, form) : '';
      if (msg) {
        if (box) box.textContent = msg;
        for (const i of fields) {
          i.setAttribute('aria-invalid', String(i === field));
          i.classList.toggle('field-invalid', i === field);
        }
        field.focus();
        return false;
      }
    }
    if (box) box.textContent = '';
    for (const i of fields) { i.removeAttribute('aria-invalid'); i.classList.remove('field-invalid'); }
    return true;
  }

  /* 一次性凭据（API 令牌明文、管理员重置出来的新密码）只在响应里出现这一次，
     服务端只存哈希——错过就永远拿不回来。把它们塞进一条带 × 的 toast 里，
     手一抖关掉就没了，也没有任何可复制的入口。这里给一份常驻面板 + 复制按钮。
     复制必须留退路：navigator.clipboard 和安全上下文绑定，经局域网 http 访问时它是
     undefined（和本页 crypto.randomUUID 是同一个坑），所以降级到隐藏 textarea +
     execCommand('copy')。 */
  function fallbackCopy(text) {
    const ta = el('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.className = 'copy-shim';
    document.body.append(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
    ta.remove();
    return ok;
  }
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).then(() => true, () => fallbackCopy(text));
    }
    return Promise.resolve(fallbackCopy(text));
  }
  function secretBox(mount, lead, text) {
    if (!mount) return;
    mount.replaceChildren();
    const box = el('div', undefined, 'secret');
    const code = el('code', text);
    const btn = el('button', '复制', 'secondary');
    btn.type = 'button';
    btn.addEventListener('click', () => {
      copyText(text).then(ok => {
        btn.textContent = ok ? '已复制到剪贴板' : '复制失败，请手动选中';
        if (!ok) { code.tabIndex = 0; code.focus(); }
        setTimeout(() => { btn.textContent = '复制'; }, 2600);
      });
    });
    box.append(el('p', lead, 'field-note'), code, btn);
    mount.append(box);
  }

  /* crypto.randomUUID 属安全上下文专属 API（HTTPS 或 localhost/127.0.0.1），且 Chrome 92 /
     Firefox 95 / Safari 15.4 之前不存在。经局域网 http 访问时它是 undefined，会让每个写操作
     在生成 request_id 时抛错。此处一次性探测，按需降级到 crypto.getRandomValues
     （非安全上下文亦可用），两者都不可用时才退回 Math.random。 */
  const uid = (() => {
    try { if (typeof crypto === 'object' && typeof crypto.randomUUID === 'function' && /^[0-9a-f-]{36}$/.test(crypto.randomUUID())) return () => crypto.randomUUID(); } catch (e) {}
    const hex = '0123456789abcdef';
    return () => {
      let b = new Uint8Array(16);
      try { crypto.getRandomValues(b); }
      catch (e) { for (let i = 0; i < 16; i++) b[i] = Math.floor(Math.random() * 256); }
      b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
      let s = '';
      for (let i = 0; i < 16; i++) { s += hex[b[i] >> 4] + hex[b[i] & 15]; if (i === 3 || i === 5 || i === 7 || i === 9) s += '-'; }
      return s;
    };
  })();

  const dateCN = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' });
  const timeCN = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false });
  const today = () => dateCN.format(new Date());
  const weekAgo = () => dateCN.format(new Date(Date.now() - 6 * 86400000));
  const time = t => timeCN.format(new Date(Number(t) * 1000));
  const stamp = t => dateCN.format(new Date(Number(t) * 1000)) + ' ' + time(t);

  const STATUSES = { CONFIRMED: '预约成功', PENDING: '待审批', HELD: '限时保留', EXPIRED: '保留超时', CANCELLED: '已取消', WAITING: '候补中', WITHDRAWN: '已退出', PROMOTED: '已补位', SKIPPED: '已跳过' };

  function el(tag, text, cls) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; }
  function action(text, fn, cls) { const b = el('button', text, cls || 'secondary'); b.type = 'button'; b.addEventListener('click', () => fn(b)); return b; }
  function setText(id, text) { $(id).textContent = String(text); }
  function empty(target, text, busy) { target.replaceChildren(el('div', text, 'empty' + (busy ? ' busy' : ''))); target.setAttribute('aria-busy', busy ? 'true' : 'false'); }
  /* 骨架屏：读取中铺出与真实卡片同构的占位，避免内容到达时页面高度跳变。 */
  function skeleton(target, n) { target.replaceChildren(); for (let i = 0; i < n; i++) { const c = el('article', undefined, 'slot skeleton'); c.append(el('i', undefined, 't'), el('i', undefined, 'h'), el('i', undefined, 'm'), el('i', undefined, 'b')); target.append(c); } target.setAttribute('aria-busy', 'true'); }
  /* 失败态带「重试」：读操作幂等，就地重试比让用户自己找刷新按钮快。 */
  function failed(target, text, retryFn) { const box = el('div', undefined, 'failed'); box.append(el('span', text)); if (retryFn) box.append(action('重试', retryFn, 'secondary')); target.replaceChildren(box); target.setAttribute('aria-busy', 'false'); }
  /* 表格：表头可点排序（仅纯数据列，含按钮等节点的列不参与），渲染时给每个单元格写
     data-label，供窄屏下的卡片式表格用 ::before 还原列名。
     排序只作用于当前已加载的这一页，按钮 title 里如实写明，避免让人以为跨页排序。 */
  function sortableColumn(rows, i) {
    for (const r of rows) {
      const v = r[i];
      if (v !== null && v !== undefined && typeof v !== 'string' && typeof v !== 'number') return false;
    }
    return true;
  }
  function cmpCell(x, y) {
    if (x === y) return 0;
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    const nx = Number(x), ny = Number(y);
    if (x !== '' && y !== '' && Number.isFinite(nx) && Number.isFinite(ny)) return nx - ny;
    return String(x).localeCompare(String(y), 'zh-Hans-CN');
  }
  function table(target, headers, rows) {
    if (!rows.length) { empty(target, '暂无数据'); return; }
    const wrap = el('div', undefined, 'table-wrap'), t = el('table'), head = el('thead'), htr = el('tr'), body = el('tbody');
    let sortCol = -1, dir = 1;
    headers.forEach((h, i) => {
      const th = el('th', undefined, 'th-' + i); th.scope = 'col';
      if (sortableColumn(rows, i)) {
        const b = el('button', h, 'th-sort');
        b.type = 'button';
        b.title = '按「' + h + '」排序（仅当前页 ' + rows.length + ' 条）';
        b.addEventListener('click', () => { if (sortCol === i) dir = -dir; else { sortCol = i; dir = 1; } render(); });
        th.append(b);
      } else {
        th.append(el('span', h, 'th-plain'));
      }
      htr.append(th);
    });
    head.append(htr); t.append(head);
    function render() {
      const data = rows.map((r, i) => i);
      if (sortCol >= 0) data.sort((a, b) => dir * cmpCell(rows[a][sortCol], rows[b][sortCol]));
      body.replaceChildren();
      for (const idx of data) {
        const r = el('tr');
        rows[idx].forEach((v, ci) => {
          const cell = el('td');
          cell.dataset.label = headers[ci] === undefined ? '' : String(headers[ci]);
          if (v instanceof Node) cell.append(v); else cell.textContent = v == null ? '—' : String(v);
          r.append(cell);
        });
        body.append(r);
      }
      head.querySelectorAll('th').forEach((th, i) => {
        const b = th.querySelector('.th-sort'); if (!b) return;
        if (i === sortCol) { th.setAttribute('aria-sort', dir > 0 ? 'ascending' : 'descending'); b.classList.add(dir > 0 ? 'asc' : 'desc'); }
        else { th.removeAttribute('aria-sort'); b.classList.remove('asc', 'desc'); }
      });
      target.setAttribute('aria-busy', 'false');
    }
    t.append(body); wrap.append(t); target.replaceChildren(wrap); render();
  }

  const TOAST_MAX = 4;
  /* 提示为右下角堆叠 toast：不是一条常驻横幅（长页面下方看不见结果），也不覆盖上一条
     （连续两次失败会丢掉中间那条真正要排查的信息）。最多堆叠 4 条、逐条手动关闭；
     不做定时自动消失——最后一条操作结果必须始终可见可核对。
     容器仍是 #notice[role=status][aria-live=polite]，读屏播报与既有 e2e 断言口径不变。 */
  function dismissToast(t) { if (!t || !t.parentNode) return; t.classList.add('out'); setTimeout(() => { if (t.parentNode) t.parentNode.remove(); if (!$('notice').querySelector('.toast')) $('notice').hidden = true; }, 220); }
  function note(text, bad) {
    const box = $('notice'); box.hidden = false;
    const t = el('div', undefined, 'toast' + (bad ? ' bad' : ''));
    t.append(el('span', text));
    const x = el('button', '×', 'toast-x'); x.type = 'button'; x.setAttribute('aria-label', '关闭这条提示');
    x.addEventListener('click', () => dismissToast(t)); t.append(x);
    box.append(t);
    while (box.children.length > TOAST_MAX) { const first = box.firstElementChild; if (!first) break; first.remove(); }
  }

  class ApiError extends Error { constructor(message, status, code, data) { super(message); this.status = status || 0; this.code = code || 'NETWORK'; this.data = data || null; } }

  /* 两页的 REST 调用只差 401 的处理方式（用户端回登录页并保留待重试编号，
     管理台直接提示并跳回用户端），故把差异留成 onUnauthorized 钩子。 */
  function makeApi(csrf, onUnauthorized) {
    return async function api(path, body) {
      let r;
      try {
        r = await fetch(path, { method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() }, body: body === undefined ? undefined : JSON.stringify(body) });
      } catch (e) { throw new ApiError('网络连接失败，请检查服务是否运行。'); }
      let j;
      try { j = await r.json(); } catch (e) { throw new ApiError('服务返回了无法识别的响应。', r.status === 503 ? 503 : 0); }
      if (r.status === 401 && onUnauthorized) onUnauthorized(r);
      if (!r.ok || j.code !== 'OK') throw new ApiError(j.message || '请求失败，请稍后重试。', r.status, j.code, j.data || null);
      return j.data;
    };
  }

  function syncThemeToggle() {
    const b = $('theme-toggle'); if (!b || !window.labTheme) return;
    const dark = window.labTheme.current() === 'dark';
    b.setAttribute('aria-pressed', String(dark));
    b.setAttribute('title', dark ? '当前深色，点击切换到浅色' : '当前浅色，点击切换到深色');
    b.textContent = dark ? '☀ 浅色' : '◐ 深色';
  }
  function wireThemeToggle() { const b = $('theme-toggle'); if (b) b.addEventListener('click', () => { if (window.labTheme) { window.labTheme.toggle(); syncThemeToggle(); } }); syncThemeToggle(); }

  /* ARIA 页签的键盘契约：一旦声明 role=tab，读屏与键盘用户就按 W3C APG 的预期操作——
     Tab 键只进入页签组一次（组内仅选中项可聚焦），左右方向键在页签间移动焦点，Home/End 到首尾。
     两页此前只同步了 aria-selected，12 个管理页签全部留在 Tab 序列里且无方向键：
     声明了 role 却不履行其键盘语义，比不声明更糟。
     方向键只移动焦点、不切换面板（手动激活）：面板加载会发请求，自动激活会让一次连按
     方向键打出十几次请求。Enter/Space 由 button 原生合成 click，故激活只需绑 click。 */
  function wireTabs(list, onSelect) {
    const tabs = Array.from(list.querySelectorAll('[role="tab"]'));
    function sync(name) {
      let hit = null;
      for (const t of tabs) {
        const on = t.dataset.tab === name;
        t.setAttribute('aria-selected', String(on));
        t.tabIndex = on ? 0 : -1;
        if (on) { t.setAttribute('aria-current', 'page'); hit = t; }
        else t.removeAttribute('aria-current');
      }
      /* 窄屏页签条是横向滚动的：切到第 12 个页签后若它还在视口外，用户看不到自己在哪 */
      if (hit) hit.scrollIntoView({block: 'nearest', inline: 'nearest'});
      return hit;
    }
    for (const t of tabs) {
      t.addEventListener('click', () => onSelect(t.dataset.tab, t));
      t.addEventListener('keydown', e => {
        const i = tabs.indexOf(t);
        const n = e.key === 'ArrowRight' ? (i + 1) % tabs.length
          : e.key === 'ArrowLeft' ? (i - 1 + tabs.length) % tabs.length
          : e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : null;
        if (n === null) return;
        e.preventDefault();
        tabs[n].focus();
      });
    }
    sync(tabs.length ? (tabs.find(t => t.getAttribute('aria-selected') === 'true') || tabs[0]).dataset.tab : '');
    return {tabs, sync};
  }

  return { $, el, action, setText, empty, skeleton, failed, table, uid, dateCN, timeCN, today, weekAgo, time, stamp, STATUSES, TOAST_MAX, dismissToast, note, ApiError, makeApi, syncThemeToggle, wireThemeToggle, wireTabs, byteLen, overBytes, clip, noteCell, formCheck, copyText, secretBox, scrollTo };
})();

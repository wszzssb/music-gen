/* skin.js —— 面板配色切换（强调色 + 明/暗），2026-10-19
 *
 * 用户："把主题颜色可以更改，页面优化一下"。
 * 做法：**只改 CSS 变量**（不写死颜色），页面里所有用到 `--accent` / `--surface-*` 的地方
 * 自动跟着变；选择存 `localStorage`，刷新后保持。
 * ⚠ 不改后端、不改数据 —— 纯前端偏好。
 */
(function () {
  var KEY = 'bgm_skin_v1';
  var SKINS = [
    { id: 'blue', cn: '靛蓝', acc: 'oklch(0.66 0.17 268)', hov: 'oklch(0.71 0.16 268)',
      a2: 'oklch(0.58 0.20 300)', a3: 'oklch(0.72 0.15 200)',
      fg: 'oklch(0.84 0.10 268)', fgL: 'oklch(0.40 0.14 268)' },
    { id: 'violet', cn: '紫罗兰', acc: 'oklch(0.67 0.18 305)', hov: 'oklch(0.72 0.17 305)',
      a2: 'oklch(0.60 0.20 330)', a3: 'oklch(0.72 0.15 265)',
      fg: 'oklch(0.85 0.10 305)', fgL: 'oklch(0.41 0.15 305)' },
    { id: 'green', cn: '青绿', acc: 'oklch(0.70 0.15 160)', hov: 'oklch(0.75 0.14 160)',
      a2: 'oklch(0.62 0.18 190)', a3: 'oklch(0.74 0.15 140)',
      fg: 'oklch(0.86 0.10 160)', fgL: 'oklch(0.40 0.12 160)' },
    { id: 'amber', cn: '琥珀', acc: 'oklch(0.78 0.15 75)', hov: 'oklch(0.82 0.14 75)',
      a2: 'oklch(0.70 0.18 45)', a3: 'oklch(0.80 0.15 100)',
      fg: 'oklch(0.90 0.10 75)', fgL: 'oklch(0.45 0.13 75)' },
    { id: 'rose', cn: '玫红', acc: 'oklch(0.68 0.19 15)', hov: 'oklch(0.73 0.18 15)',
      a2: 'oklch(0.62 0.20 350)', a3: 'oklch(0.74 0.15 30)',
      fg: 'oklch(0.86 0.10 15)', fgL: 'oklch(0.43 0.16 15)' }
  ];
  var LIGHT = {
    '--bg': 'oklch(0.975 0.002 265)',
    '--surface-1': 'oklch(0.955 0.003 265)',
    '--surface-2': 'oklch(0.925 0.004 265)',
    '--surface-3': 'oklch(0.895 0.005 265)',
    '--border': 'oklch(0 0 0 / 10%)',
    '--border-strong': 'oklch(0 0 0 / 18%)',
    '--text': 'oklch(0.24 0.012 265)',
    '--text-2': 'oklch(0.44 0.012 265)',
    '--text-3': 'oklch(0.58 0.012 265)',
    /* **canvas 专用**（卷帘/波形/自动化，2026-10-19）：以前写死深色，浅色主题下整块是黑的 */
    '--roll-bg': '#f7f9fc',
    '--roll-grid': 'rgba(0,0,0,.03)',
    '--roll-grid2': 'rgba(0,0,0,.06)',
    '--roll-line': 'rgba(0,0,0,.10)',
    '--roll-text': '#5b6172',
    '--roll-sub': '#7a8194',
    '--roll-overlay': 'rgba(247,249,252,.82)',
    '--roll-beat': 'rgba(20,140,90,.08)',
    '--roll-accent-dim': 'rgba(60,120,220,.12)',
    '--roll-wave': '#9aa6bb',
    '--roll-label': '#3a4152'
  };
  var DARK = {
    '--bg': 'oklch(0.145 0.004 265)',
    '--surface-1': 'oklch(0.183 0.006 265)',
    '--surface-2': 'oklch(0.216 0.007 265)',
    '--surface-3': 'oklch(0.255 0.008 265)',
    '--border': 'oklch(1 0 0 / 8%)',
    '--border-strong': 'oklch(1 0 0 / 15%)',
    '--text': 'oklch(0.98 0 0)',
    '--text-2': 'oklch(0.74 0.01 265)',
    '--text-3': 'oklch(0.58 0.012 265)',
    '--roll-bg': '#0f1117',
    '--roll-grid': 'rgba(255,255,255,.02)',
    '--roll-grid2': 'rgba(255,255,255,.05)',
    '--roll-line': 'rgba(255,255,255,.08)',
    '--roll-text': '#8b90a4',
    '--roll-sub': '#6f7690',
    '--roll-overlay': 'rgba(15,17,23,.72)',
    '--roll-beat': 'rgba(87,209,139,.07)',
    '--roll-accent-dim': 'rgba(92,200,255,.12)',
    '--roll-wave': '#3a4152',
    '--roll-label': '#c9cedd'
  };

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { return {}; }
  }
  function save(s) {
    try { localStorage.setItem(KEY, JSON.stringify(s)); } catch (e) { /* 隐私模式忽略 */ }
  }
  function apply(s) {
    var skin = SKINS.filter(function (x) { return x.id === s.skin; })[0] || SKINS[0];
    var st = document.documentElement.style;
    var vars = (s.mode === 'light') ? LIGHT : DARK;
    Object.keys(vars).forEach(function (k) { st.setProperty(k, vars[k]); });
    st.setProperty('--accent', skin.acc);
    st.setProperty('--accent-hover', skin.hov);
    st.setProperty('--accent-dim', skin.acc.replace(/\)$/, (s.mode === 'light' ? ' / 22%)' : ' / 16%)')));
    st.setProperty('--accent-2', skin.a2);
    st.setProperty('--accent-3', skin.a3);
    st.setProperty('--accent-fg', (s.mode === 'light') ? skin.fgL : skin.fg);
    // canvas 里的"强调色"（段落边界线等）也要跟着换
    st.setProperty('--roll-accent', skin.acc);
    try { document.dispatchEvent(new CustomEvent('bgm-skin')); } catch (e) {}
    // **旧面板（引擎页 `style.css`）用的是另一套变量名**（`--acc` / `--panel` / `--fg`…），
    // 这里一并写一份，保证两个页面换色同步（2026-10-19）。
    st.setProperty('--acc', skin.acc);
    if (s.mode === 'light') {
      st.setProperty('--bg', '#f6f7fb'); st.setProperty('--panel', '#ffffff');
      st.setProperty('--panel2', '#eef1f8');
      st.setProperty('--line', '#dfe3ee'); st.setProperty('--fg', '#20232c');
      st.setProperty('--dim', '#5b6172');
    } else {
      st.setProperty('--bg', '#12141a'); st.setProperty('--panel', '#1b1e27');
      st.setProperty('--panel2', '#1b1f26');
      st.setProperty('--line', '#2c3040'); st.setProperty('--fg', '#e6e8ef');
      st.setProperty('--dim', '#8b90a4');
    }
    document.documentElement.setAttribute('data-mode', s.mode || 'dark');
    document.documentElement.setAttribute('data-skin', skin.id);
  }
  function control(box, s) {
    var html = ['<span class="skin-lbl">配色</span>'];
    SKINS.forEach(function (k) {
      html.push('<button type="button" class="skin-dot" data-skin="' + k.id + '" title="' + k.cn +
        '" style="background:' + k.acc + '"></button>');
    });
    html.push('<span class="sep-d"></span>');
    html.push('<button type="button" class="skin-mode" data-mode="dark" title="深色">🌙</button>');
    html.push('<button type="button" class="skin-mode" data-mode="light" title="浅色">☀</button>');
    box.innerHTML = html.join('');
    function sync() {
      box.querySelectorAll('.skin-dot').forEach(function (b) {
        b.classList.toggle('on', b.dataset.skin === s.skin);
      });
      box.querySelectorAll('.skin-mode').forEach(function (b) {
        b.classList.toggle('on', b.dataset.mode === s.mode);
      });
    }
    box.querySelectorAll('.skin-dot').forEach(function (b) {
      b.onclick = function () { s.skin = b.dataset.skin; apply(s); save(s); sync(); };
    });
    box.querySelectorAll('.skin-mode').forEach(function (b) {
      b.onclick = function () { s.mode = b.dataset.mode; apply(s); save(s); sync(); };
    });
    sync();
  }

  var s = load();
  s.skin = s.skin || 'blue';
  s.mode = s.mode || 'dark';
  apply(s);
  document.addEventListener('DOMContentLoaded', function () {
    var box = document.getElementById('skinBox');
    if (box) { control(box, s); }
  });
  window.BGM_SKIN = { get: function () { return s; }, set: function (x) { s = x; apply(s); save(s); } };
})();

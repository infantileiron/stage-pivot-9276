import {api, post} from './api/client.js';
import {state} from './stores/session.js';
import {routes, page} from './router/index.js';
import {values, feedback} from './hooks/form.js';
import {esc} from './utils/html.js';
import * as settlements from './pages/settlements.js';
import * as backfill from './pages/backfill.js';
import * as layout from './pages/layout.js';
import * as pivot from './pages/pivot.js';
import * as reviews from './pages/reviews.js';

const root = document.querySelector('#app');
const PAGES = [settlements, backfill, layout, pivot, reviews];

async function load() {
  [state.settlements, state.pivots, state.reviews, state.vouchers, state.backfills] =
    await Promise.all(['/settlements', '/pivots', '/reviews', '/vouchers', '/backfills'].map(x => api(x)));
  state.selected = state.pivots.find(r => r.id === state.selected?.id) || state.pivots[0] || null;
  if (state.job) {
    try { state.job = await api('/backfills/' + state.job.id); } catch (e) { state.job = null; }
  }
}

function login() {
  root.innerHTML = `<div class="login"><section class="cover"><div class="eyebrow">YUN MU / SETTLEMENT PROGRAMME 012</div><h1>散场之后，<br>每一笔都有回声。</h1><p>云幕剧院 · 场次结算节目单<br>明细 → 凭证补账 → 冻结透视 → 差异签核<br>三处场馆 / 180场演出 / 分币守恒</p></section><section><div class="eyebrow">STAGE PIVOT</div><h2>登录结算节目单</h2><form id="login"><label>工作邮箱<input name="email" type="email" value="analyst@demo.local" required></label><label>密码<input name="password" type="password" value="Demo123!" required></label><button class="primary">进入场次结算</button></form><p id="error" class="error"></p><p>admin / analyst / viewer @demo.local<br>密码：Demo123!</p></section></div>`;
  document.querySelector('form').onsubmit = e => {
    e.preventDefault();
    run(async () => {
      let d = await post('/auth/login', values(e.target));
      localStorage.setItem('token', d.token);
      state.user = d.user;
      await load();
      render();
    });
  };
}

function render() {
  const n = Math.min(PAGES.length - 1, Math.max(0, page()));
  root.innerHTML = `<header><div class="brand">云幕 / 场次结算节目单 <span class="eyebrow">STAGE PIVOT</span></div><div>${esc(state.user.email)} · ${esc(state.user.role)} <button id="logout">退出</button></div></header><div class="layout"><nav>${routes.map((r, i) => `<button data-nav="${i}" class="${i === n ? 'active' : ''}">0${i + 1} ${r}</button>`).join('')}<span class="eyebrow">THEATRE ACCOUNTS / 2026</span></nav><main><div class="intro"><div><div class="eyebrow">SETTLEMENT PROGRAMME / 0${n + 1}</div><h1>${routes[n]}</h1><p>让票款、退款与渠道费回到具体场次。缺失票款由交叉凭证推导，唯一解才入账；每份透视保留原始明细，差异由财务负责人签核。</p></div><span class="tag">3 场馆 / 散场结算</span></div><p id="error" class="error"></p>${PAGES[n].render()}</main></div>`;
  bind();
  if (state.user.role === 'viewer') {
    document.querySelectorAll('form button,#draft-review,[data-bf-solve],[data-bf-post]').forEach(b => {
      b.disabled = true;
      if (b.tagName === 'BUTTON' && !b.closest('[data-bf]')) b.textContent = '观察员只读';
    });
  }
}

async function run(fn) {
  try { await fn(); } catch (e) { feedback(e.message); }
}

function toNumber(d, keys) {
  for (const k of keys) if (k in d && d[k] !== '') d[k] = Number(d[k]);
  return d;
}

function bind() {
  document.querySelectorAll('[data-nav]').forEach(b => b.onclick = () => location.hash = b.dataset.nav);
  const logout = document.querySelector('#logout');
  if (logout) logout.onclick = () => { localStorage.removeItem('token'); state.user = null; state.job = null; login(); };

  const sf = document.querySelector('#settlement');
  if (sf) sf.onsubmit = e => {
    e.preventDefault();
    run(async () => {
      const d = toNumber(values(sf), ['gross_cents', 'refund_cents', 'fee_cents', 'seats', 'checked']);
      const record = d.record_id;
      delete d.record_id;
      for (const k of ['gross_cents']) if (k in d && d[k] === '') d[k] = null;
      if (record) await api('/settlements/' + record, {method: 'PUT', body: JSON.stringify(d)});
      else if (d.gross_cents === null) await post('/settlements/missing', d);
      else await post('/settlements', d);
      await load();
      render();
    });
  };

  const vf = document.querySelector('#voucher');
  if (vf) vf.onsubmit = e => {
    e.preventDefault();
    run(async () => {
      const d = toNumber(values(vf), ['total_cents']);
      d.cover_codes = String(d.cover_codes || '').split(/[,，\s]+/).filter(Boolean);
      const record = d.record_id;
      delete d.record_id;
      if (record) await api('/vouchers/' + record, {method: 'PUT', body: JSON.stringify(d)});
      else await post('/vouchers', d);
      await load();
      render();
    });
  };

  const bf = document.querySelector('#backfill');
  if (bf) {
    const scope = () => {
      const d = values(bf);
      d.codes = String(d.codes || '').split(/[,，\s]+/).filter(Boolean);
      d.voucher_ids = String(d.voucher_ids || '').split(/[,，\s]+/).filter(Boolean).map(Number);
      return d;
    };
    bf.onsubmit = e => {
      e.preventDefault();
      run(async () => {
        const d = scope();
        const created = await post('/backfills', d);
        state.job = await post('/backfills/' + created.id + '/solve', {});
        render();
        pollBackfill(created.id);
      });
    };
    const previewBtn = bf.querySelector('button[data-action=preview]');
    if (previewBtn) previewBtn.onclick = () => run(async () => {
      const d = scope();
      const p = await post('/backfills/preview', {...d, preview_only: true, note: ''});
      state.job = {...p, id: null, name: d.name, state: 'preview', attempts: 0};
      render();
    });
  }

  document.querySelectorAll('[data-bf]').forEach(b => b.onclick = () => run(async () => {
    state.job = await api('/backfills/' + b.dataset.bf);
    render();
  }));
  document.querySelectorAll('[data-bf-solve]').forEach(b => b.onclick = () => run(async () => {
    state.job = await post('/backfills/' + b.dataset.bfSolve + '/solve', {});
    render();
    pollBackfill(b.dataset.bfSolve);
  }));
  document.querySelectorAll('[data-bf-post]').forEach(b => b.onclick = () => run(async () => {
    state.job = await post('/backfills/' + b.dataset.bfPost + '/post', {});
    await load();
    render();
  }));
  const dl = document.querySelector('#download-backfill');
  if (dl) dl.onclick = () => run(async () => {
    const d = await api('/backfills/' + state.job.id + '/export');
    download(d, 'stage-backfill-' + state.job.id + '.json');
  });

  const pf = document.querySelector('#pivot');
  if (pf) pf.onsubmit = e => {
    e.preventDefault();
    run(async () => {
      const created = await post('/pivots', values(pf));
      state.selected = created;
      state.drill = null;
      state.chosen = null;
      await load();
      if (location.hash !== '#3') location.hash = '3'; else render();
    });
  };
  const rf = document.querySelector('#review');
  if (rf) rf.onsubmit = e => {
    e.preventDefault();
    run(async () => {
      const d = toNumber(values(rf), ['pivot_id', 'expected_cents']);
      await post('/reviews', d);
      await load();
      render();
    });
  };

  document.querySelectorAll('[data-pivot]').forEach(b => b.onclick = () => run(async () => {
    state.selected = await api('/pivots/' + b.dataset.pivot);
    state.drill = null;
    state.chosen = null;
    if (location.hash !== '#3') location.hash = '3'; else render();
  }));
  document.querySelectorAll('[data-drill-row]').forEach(b => b.onclick = () => run(async () => {
    state.drill = await api('/pivots/' + state.selected.id + '/drill?' + new URLSearchParams({row_key: b.dataset.drillRow, col_key: b.dataset.drillCol}));
    render();
    document.querySelector('#draft-review')?.scrollIntoView({block: 'center'});
  }));
  const draft = document.querySelector('#draft-review');
  if (draft) draft.onclick = () => { state.chosen = state.drill.cell; location.hash = '4'; };
  document.querySelectorAll('[data-approve]').forEach(b => b.onclick = () => run(async () => {
    await post('/reviews/' + b.dataset.approve + '/approve', {});
    await load();
    render();
  }));
  const refresh = document.querySelector('#refresh');
  if (refresh) refresh.onclick = () => run(async () => { await load(); render(); });
  const downloadBtn = document.querySelector('#download');
  if (downloadBtn) downloadBtn.onclick = () => run(async () => {
    const d = await api('/pivots/' + state.selected.id + '/export');
    download(d, 'stage-pivot-' + d.id + '.json');
  });
}

function download(d, name) {
  const u = URL.createObjectURL(new Blob([JSON.stringify(d, null, 2)], {type: 'application/json'}));
  const a = document.createElement('a');
  a.href = u;
  a.download = name;
  a.click();
  URL.revokeObjectURL(u);
}

let pollTimer = null;
function pollBackfill(id) {
  clearInterval(pollTimer);
  let ticks = 0;
  pollTimer = setInterval(() => run(async () => {
    ticks += 1;
    state.job = await api('/backfills/' + id);
    if (['queued', 'running'].includes(state.job.state) && ticks < 40) { render(); return; }
    clearInterval(pollTimer);
    await load();
    render();
  }), 1200);
}

window.onhashchange = () => { if (state.user) { render(); window.scrollTo(0, 0); } };
if (localStorage.getItem('token')) {
  run(async () => {
    try { state.user = await api('/me'); await load(); render(); }
    catch (e) { localStorage.removeItem('token'); login(); feedback(e.message); }
  });
} else login();

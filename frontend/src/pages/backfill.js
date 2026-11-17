import {state} from '../stores/session.js';
import {esc} from '../utils/html.js';
import {badge} from '../components/common/status.js';
import {field} from '../components/common/fields.js';
import {BACKFILL_STATES, VERDICTS} from '../constants/enums.js';
import {money} from '../utils/format.js';

const stateBadge = s => `<span class="tag" data-state="${esc(s)}">${esc(BACKFILL_STATES[s] || s || '—')}</span>`;

function voucherPanel() {
  return `<div class="split"><section class="panel"><h2>交叉汇总凭证</h2>
<p>每条凭证给出若干场次的票款合计。凭证覆盖场次清单与总额共同约束缺失票款；覆盖清单外场次的凭证整条不参与求解。</p>
${state.vouchers.map(v => `<article class="rec"><h3>#${v.id} ${esc(v.code)} · 总额 ${money(v.total_cents)} 元</h3>
<p>覆盖 ${v.cover_codes.length} 场：${esc(v.cover_codes.join('、'))}${v.note ? ' · ' + esc(v.note) : ''}</p>
<p class="eyebrow">修订号 rev ${v.rev}</p></article>`).join('') || '<p>尚无凭证。先在右侧录入覆盖场次与总额。</p>'}
</section><aside class="side"><h2>录入 / 修订凭证</h2>
<form id="voucher">${field('凭证ID（修订填写）', 'record_id', 'number', 'min=1')}${field('凭证编号', 'code', 'text', 'required')}${field('凭证总额 / 分', 'total_cents', 'number', 'required min=0 step=1')}${field('覆盖场次编号（逗号分隔）', 'cover_codes', 'text', 'required placeholder="YM-181,YM-182"')}<label>备注<textarea name="note" rows="3" maxlength="2000"></textarea></label><button class="primary">保存凭证</button></form>
<p class="notice">凭证修订会递增 rev；已求解但未入账的补账将被标记过期，需重新求解。</p>
</aside></div>`;
}

function jobPanel() {
  const j = state.job;
  if (!j) return `<section class="panel"><h2>补账推导</h2><p class="notice">选择场次清单与凭证范围，先做草稿预演，再求解。唯一解才允许填回缺失票款。</p></section>`;
  const r = j.rows || [];
  const conflicts = j.conflicts;
  const preview = j.state === 'preview' || j.id === null || j.id === undefined;
  const canSolve = !preview && !['posted', 'queued', 'running'].includes(j.state);
  const canPost = !preview && j.state === 'solved' && j.verdict === 'unique';
  return `<section class="panel"><div class="result-head"><h2>${preview ? '预演' : '#' + j.id} ${esc(j.name)} ${preview ? '' : stateBadge(j.state)} ${j.verdict ? `<span class="tag" data-verdict="${esc(j.verdict)}">${esc(VERDICTS[j.verdict] || j.verdict)}</span>` : ''}</h2>
${preview ? '<span class="tag">预演 · 未建任务</span>' : '<button id="download-backfill">下载补账结果 JSON</button>'}</div>
${j.error ? `<p class="error">${esc(j.error)}</p>` : ''}
${j.notes ? `<p class="notice">${esc(j.notes.join('；'))}</p>` : ''}
<p class="eyebrow">快照 checksum ${esc(j.snapshot_checksum || '—')} · 求解 ${esc(j.solver || '—')} · 尝试 ${j.attempts} 次</p>
${j.stale ? `<p class="error">已过期：${esc((j.stale.changes || []).map(c => c.kind + ':' + (c.code || '')).join('；'))}</p>` : ''}
${j.summary ? `<div class="metrics"><div><strong>${j.summary.scope_rows}</strong>清单场次</div><div><strong>${j.summary.missing_rows}</strong>缺失票款</div><div><strong>${j.summary.variable_rows}</strong>可推导</div><div><strong>${j.summary.free_rows}</strong>无凭证覆盖</div><div><strong>${j.summary.voucher_count}</strong>参与凭证</div></div>` : ''}
${r.length ? `<div class="scroll"><table><thead><tr><th>场次</th><th>退款/分</th><th>渠道费/分</th><th>下限/分</th><th>上限/分</th><th>入账/分</th><th>凭证</th></tr></thead><tbody>${r.map(x => `<tr><td>${esc(x.code)}${x.unconstrained ? ' <span class="tag">无覆盖</span>' : ''}${x.known ? ' <span class="tag">已知</span>' : ' <span class="tag">缺失</span>'}</td><td>${esc(x.refund_cents)}</td><td>${esc(x.fee_cents)}</td><td>${esc(x.known ? x.gross_cents : x.min_cents)}</td><td>${esc(x.known ? x.gross_cents : x.max_cents)}</td><td>${esc(x.resolved_cents === null || x.resolved_cents === undefined ? '—' : x.resolved_cents)}</td><td>${esc((x.cover_voucher_codes || []).join('、') || '—')}</td></tr>`).join('')}</tbody></table></div>` : ''}
${(j.vouchers || []).length ? `<h3>凭证依据</h3><div class="scroll"><table><thead><tr><th>凭证</th><th>总额/分</th><th>已知票款/分</th><th>缺失待定/分</th><th>方程</th></tr></thead><tbody>${j.vouchers.map(v => `<tr><td>${esc(v.code)} <small>rev ${v.rev}</small></td><td>${esc(v.total_cents)}</td><td>${esc(v.known_sum_cents)}</td><td>${esc(v.residual_for_missing_cents)}</td><td>${esc(v.equation)}</td></tr>`).join('')}</tbody></table></div>` : ''}
${conflicts ? `<h3>冲突凭证集（不可再缩减：${conflicts.irreducible ? '是' : '否'}）</h3>
<p class="error">${esc(conflicts.reason)}</p>
<div class="scroll"><table><thead><tr><th>凭证</th><th>总额/分</th><th>已知票款/分</th><th>覆盖场次</th></tr></thead><tbody>${conflicts.vouchers.map(v => `<tr><td>${esc(v.code)}</td><td>${esc(v.total_cents)}</td><td>${esc(v.known_sum_cents)}</td><td>${esc(v.cover_codes.join('、'))}</td></tr>`).join('')}</tbody></table></div>
<h4>剔除依据（每次剔除后的可行见证）</h4><div class="scroll"><table><thead><tr><th>剔除凭证</th><th>剔除后可行</th><th>可行解见证</th></tr></thead><tbody>${conflicts.removal_evidence.map(e => `<tr><td>${esc(e.removed_voucher_code)}</td><td>${e.feasible_without_it ? '是' : '否'}</td><td>${esc(e.witness.map(w => w.code + '=' + w.cents).join('，'))}</td></tr>`).join('')}</tbody></table></div>
${(conflicts.bound_conflicts || []).length ? `<h4>场次下限冲突</h4><p class="error">${esc(conflicts.bound_conflicts.map(c => c.code + '：' + c.reason).join('；'))}</p>` : ''}` : ''}
${j.posting ? `<h3>已入账</h3><p>写入 ${j.posting.row_count} 场，合计 ${money(j.posting.total_cents)} 元（整数分）。</p><div class="scroll"><table><thead><tr><th>场次</th><th>入账/分</th><th>修订号</th></tr></thead><tbody>${j.posting.written.map(w => `<tr><td>${esc(w.code)}</td><td>${esc(w.cents)}</td><td>${w.before_rev}→${w.after_rev}</td></tr>`).join('')}</tbody></table></div>` : ''}
${preview ? `<p class="notice">这是预演结果；如确认无误，用右上方表单「创建草稿并求解」生成可入账任务。</p>` : `<div class="rec"><button class="primary" data-bf-solve="${j.id}" ${canSolve ? '' : 'disabled'}>${j.state === 'stale' || j.state === 'failed' ? '沿原任务重试求解' : '求解'}</button>
<button data-bf-post="${j.id}" ${canPost ? '' : 'disabled'}>唯一解整批入账</button>
<span class="eyebrow">${canPost ? '唯一解可入账' : j.verdict === 'multi' ? '多解不可填回，仅列上下界' : j.verdict === 'infeasible' ? '矛盾，不可入账' : ''}</span></div>`}
</section>`;
}

function createPanel() {
  return `<aside class="side"><h2>新建补账草稿</h2>
<form id="backfill">${field('任务名称', 'name', 'text', 'required minlength=2 value="11月缺失票款补账"')}${field('场次清单（逗号分隔，可留空=全部）', 'codes', 'text', 'placeholder="YM-181,YM-182"')}${field('凭证ID范围（逗号分隔，可留空=全部）', 'voucher_ids', 'text', 'placeholder="1,2"')}
<button class="primary" data-action="solve">创建草稿并求解</button><button data-action="preview" type="button">仅预演，不改账</button></form>
<p>场次清单用于限定范围并排除越界凭证；凭证范围留空表示全部凭证。预演直接返回推导结果，不建任务、不改账。</p>
${state.backfills.map(b => `<button class="study" data-bf="${b.id}">#${b.id} ${esc(b.name)} ${stateBadge(b.state)} ${b.verdict ? `<span class="tag">${esc(VERDICTS[b.verdict] || b.verdict)}</span>` : ''}</button>`).join('') || '<p>尚无补账任务。</p>'}
</aside>`;
}

export function render() {
  return `<div class="split"><section class="panel"><h2>缺失票款，交叉推导</h2>
<p class="notice">金额为整数分；场次票款不得低于退款加渠道费。已知票款不覆盖、缺失与真实零元区分。只有唯一解才填回，多解列出每场可行上下界，矛盾给出不可再缩减的冲突凭证集。求解期间可继续修订凭证与场次，过期结果拒绝入账；入账整批原子替换且不重复写账。</p>
${jobPanel()}</section>${createPanel()}</div><div class="split"><section class="panel">${voucherPanel()}</section></div>`;
}

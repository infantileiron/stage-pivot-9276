"""缺失票款补账：交叉汇总凭证的整数分推导、入账与凭证校验。

输入为若干凭证（每条由覆盖场次清单与总额构成）、这些场次上已知的票款，以及
缺失票款（gross_cents 为 NULL，区别于真实零元 0）。场次票款为整数分，且必须
不低于退款加渠道费。每个凭证给出其覆盖场次的票款合计，因此待求变量满足

    sum_{场次 in 凭证} 票款 = 凭证总额

已知票款作为常数移入右端；缺失票款为整数变量。只有唯一解才允许填回。多解时
逐场给出可行上下界；无解时用删除过滤求不可再缩减的冲突凭证集，并为每次剔除
保留一个可行见证，作为冲突依据。求解使用 scipy.optimize.milp（HiGHS），整数
规划精确到分，不做四舍五入。
"""
import hashlib
import json
import random
from datetime import datetime, timezone

import numpy as np
from fastapi import HTTPException
from scipy.optimize import Bounds, LinearConstraint, milp

from src.models.entities import Backfill, Settlement, Voucher, VoucherCover

CAP_CENTS = 100_000_000  # 与场次录入上限一致
SOLVER_NAME = 'scipy.optimize.milp / HiGHS'
TIME_LIMIT = 30.0
MAX_VARIABLES = 400  # 逐变量求上下界需 2n 次 MILP；超出规模请拆分场次清单


class SolverError(Exception):
    pass


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def checksum(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def _code_sort_key(item):
    return (len(item[0]), item[0])


# ---------------------------------------------------------------------------
# 凭证读取
# ---------------------------------------------------------------------------

def cover_map(s, voucher_ids=None):
    q = s.query(VoucherCover)
    if voucher_ids is not None:
        if not voucher_ids:
            return {}
        q = q.filter(VoucherCover.voucher_id.in_(voucher_ids))
    out = {}
    for c in q.order_by(VoucherCover.voucher_id, VoucherCover.settlement_id).all():
        out.setdefault(c.voucher_id, []).append(c.settlement_id)
    return out


def voucher_dict(s, v, covers=None, code_of=None):
    covers = cover_map(s, [v.id]) if covers is None else covers
    ids = covers.get(v.id, [])
    if code_of is None:
        rows = s.query(Settlement).filter(Settlement.id.in_(ids)).all() if ids else []
        code_of = {r.id: r.code for r in rows}
    return {
        'id': v.id, 'code': v.code, 'total_cents': v.total_cents, 'note': v.note or '',
        'rev': v.rev, 'created': v.created, 'updated': v.updated,
        'cover_ids': list(ids), 'cover_codes': [code_of[i] for i in ids if i in code_of],
    }


# ---------------------------------------------------------------------------
# 求解快照
# ---------------------------------------------------------------------------

def build_snapshot(s, voucher_ids=None, codes=None):
    """冻结求解输入。凭证覆盖与场次修订号一并记录，供入账前做过期校验。"""
    codes = list(dict.fromkeys(c.strip() for c in (codes or []) if c and c.strip()))
    all_rows = s.query(Settlement).order_by(Settlement.id).all()
    code_of = {r.id: r.code for r in all_rows}
    row_of_code = {r.code: r for r in all_rows}
    if codes:
        missing = [c for c in codes if c not in row_of_code]
        if missing:
            raise HTTPException(422, detail={'message': '场次清单包含不存在的编号', 'codes': missing})

    all_vouchers = s.query(Voucher).order_by(Voucher.id).all()
    by_id = {v.id: v for v in all_vouchers}
    if voucher_ids:
        ids = list(dict.fromkeys(int(i) for i in voucher_ids))
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise HTTPException(404, detail={'message': '凭证不存在', 'ids': missing})
        selected = [by_id[i] for i in ids]
    else:
        selected = all_vouchers

    covers = cover_map(s, [v.id for v in selected])
    included, excluded = [], []
    for v in selected:
        cov = covers.get(v.id, [])
        out_of_scope = [sid for sid in cov if code_of.get(sid) not in codes] if codes else []
        unknown_missing = [sid for sid in out_of_scope
                           if sid not in code_of
                           or row_of_code[code_of[sid]].gross_cents is None]
        if unknown_missing:
            # 越界场次本身也缺票款：它的值不在清单内，凭证无法作为常数约束使用
            excluded.append({
                'id': v.id, 'code': v.code, 'rev': v.rev, 'total_cents': v.total_cents,
                'covers': sorted(cov),
                'cover_codes': [code_of[sid] for sid in cov if sid in code_of],
                'out_of_scope_codes': [code_of.get(sid, str(sid)) for sid in out_of_scope],
                'reason': '凭证覆盖的场次含清单外且票款缺失的场次，整条约束不参与求解',
            })
        else:
            # 越界场次都是已知票款时，按常数并入右端，凭证依然有效
            included.append(v)

    scope_ids = set()
    for v in included:
        scope_ids.update(covers.get(v.id, []))
    if codes:
        scope_ids.update(row_of_code[c].id for c in codes)

    rows = []
    for r in sorted((row_of_code[code_of[i]] for i in scope_ids), key=lambda x: x.code):
        rows.append({
            'id': r.id, 'code': r.code, 'rev': r.rev,
            'gross_cents': r.gross_cents,
            'refund_cents': r.refund_cents, 'fee_cents': r.fee_cents,
            'in_list': not codes or r.code in codes,
        })
    scope_code = {r['id']: r['code'] for r in rows}

    voucher_rows = []
    for v in sorted(included, key=lambda x: x.id):
        cov = [sid for sid in covers.get(v.id, []) if sid in scope_code]
        voucher_rows.append({
            'id': v.id, 'code': v.code, 'rev': v.rev, 'total_cents': v.total_cents,
            'covers': sorted(cov),
            'cover_codes': [scope_code[sid] for sid in sorted(cov)],
            'out_of_scope_codes': [code_of[sid] for sid in covers.get(v.id, [])
                                   if codes and code_of.get(sid) not in codes],
        })

    snapshot = {
        'version': 1,
        'generated_at': now_iso(),
        'codes': codes,
        'requested_voucher_ids': list(voucher_ids or []),
        'all_codes': not codes,
        'all_vouchers': not voucher_ids,
        # 基线：求解开始时的全部场次 id 与凭证 id，用于识别范围为“全部”时的新增记录
        'baseline_settlement_ids': sorted(code_of),
        'baseline_voucher_ids': sorted(by_id),
        'rows': rows,
        'vouchers': voucher_rows,
        'excluded': excluded,
        'cap_cents': CAP_CENTS,
    }
    snapshot['checksum'] = checksum({'rows': rows, 'vouchers': voucher_rows})
    return snapshot


# ---------------------------------------------------------------------------
# 整数规划模型
# ---------------------------------------------------------------------------

class Model:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.rows = snapshot.get('rows') or []
        self.vouchers = snapshot.get('vouchers') or []
        self.row_by_id = {r['id']: r for r in self.rows}
        self.var_rows = []
        self.free_rows = []
        covered = set()
        for v in self.vouchers:
            for sid in v['covers']:
                if sid in self.row_by_id:
                    covered.add(sid)
        for r in self.rows:
            if r['gross_cents'] is None:
                (self.var_rows if r['id'] in covered else self.free_rows).append(r)
        self.var_index = {r['id']: i for i, r in enumerate(self.var_rows)}
        self.n = len(self.var_rows)
        self.lb = [max(0, int(r['refund_cents']) + int(r['fee_cents'])) for r in self.var_rows]
        self.cover_totals = [[] for _ in range(self.n)]
        for v in self.vouchers:
            for sid in v['covers']:
                j = self.var_index.get(sid)
                if j is not None:
                    self.cover_totals[j].append(int(v['total_cents']))
        self.A, self.b = [], []
        self.voucher_known = []
        self.voucher_missing = []
        for v in self.vouchers:
            row = [0.0] * self.n
            known = 0
            missing = []
            for sid in v['covers']:
                r = self.row_by_id.get(sid)
                if r is None:
                    continue
                if r['gross_cents'] is None:
                    j = self.var_index.get(sid)
                    if j is not None:
                        row[j] = 1.0
                        missing.append(sid)
                else:
                    known += int(r['gross_cents'])
            self.A.append(row)
            self.b.append(int(v['total_cents']) - known)
            self.voucher_known.append(known)
            self.voucher_missing.append(missing)

    # -- 上下界：上限取覆盖凭证总额的最小上界，杜绝无界变量 ---------------
    def subset_bounds(self, keep):
        lb, ub = list(self.lb), [CAP_CENTS] * self.n
        cover_totals = [[] for _ in range(self.n)]
        for i in keep:
            for sid in self.vouchers[i]['covers']:
                j = self.var_index.get(sid)
                if j is not None:
                    cover_totals[j].append(int(self.vouchers[i]['total_cents']))
        for j in range(self.n):
            if cover_totals[j]:
                ub[j] = min(CAP_CENTS, max(cover_totals[j]))
        return lb, ub

    def free_lb_ub(self):
        out = []
        for r in self.free_rows:
            out.append((r, max(0, int(r['refund_cents']) + int(r['fee_cents'])), CAP_CENTS))
        return out

    def solve(self, keep, objective=None):
        """在给定凭证子集上求 MILP。返回 (可行, 解 dict 或 None)。

        可行解 dict 覆盖所有缺失场次；不受所选凭证约束的缺失场次取下界，
        不影响可行性判断。
        """
        lb, ub = self.subset_bounds(keep)
        bad = [(self.var_rows[j]['code'], lb[j], ub[j]) for j in range(self.n) if lb[j] > ub[j]]
        if bad:
            return False, None
        free_bad = [(r['code'], lo) for r, lo, hi in self.free_lb_ub() if lo > hi]
        if free_bad:
            return False, None
        if self.n == 0:
            if any(self.b[i] != 0 for i in keep):
                return False, None
            return True, {}
        A = np.asarray([self.A[i] for i in keep], dtype=float) if keep else np.zeros((0, self.n))
        b = np.asarray([self.b[i] for i in keep], dtype=float)
        c = np.zeros(self.n) if objective is None else np.asarray(objective, dtype=float)
        constraints = LinearConstraint(A, b, b) if len(keep) else None
        res = milp(c=c, constraints=constraints, integrality=np.ones(self.n),
                   bounds=Bounds(np.asarray(lb, dtype=float), np.asarray(ub, dtype=float)),
                   options={'time_limit': TIME_LIMIT, 'mip_rel_gap': 0.0})
        if res.status == 2:
            return False, None
        if res.status != 0 or res.x is None:
            raise SolverError('整数规划未得到可行解：%s' % (res.message or res.status))
        x = [int(round(v)) for v in res.x]
        self._validate(keep, x)
        witness = {self.var_rows[j]['code']: x[j] for j in range(self.n)}
        for r, lo, hi in self.free_lb_ub():
            witness[r['code']] = lo
        return True, witness

    def _validate(self, keep, x):
        for j, v in enumerate(x):
            if v < self.lb[j] or v > CAP_CENTS:
                raise SolverError('求解结果越界：%s=%s' % (self.var_rows[j]['code'], v))
        for i in keep:
            total = sum(self.A[i][j] * x[j] for j in range(self.n))
            if abs(total - self.b[i]) > 1e-6:
                raise SolverError('求解结果未满足凭证 %s 的合计约束' % self.vouchers[i]['code'])

    def solution_map(self, x):
        return {self.var_rows[j]['code']: int(x[j]) for j in range(self.n)}


# ---------------------------------------------------------------------------
# 校验与依据
# ---------------------------------------------------------------------------

def voucher_basis(model, keep=None):
    out = []
    for i, v in enumerate(model.vouchers):
        if keep is not None and i not in keep:
            continue
        out.append({
            'id': v['id'], 'code': v['code'], 'rev': v['rev'],
            'total_cents': int(v['total_cents']),
            'known_sum_cents': int(model.voucher_known[i]),
            'missing_count': len(model.voucher_missing[i]),
            'residual_for_missing_cents': int(model.b[i]),
            'cover_codes': list(v['cover_codes']),
            'equation': '%s = 凭证总额 %d − 已知票款 %d' % (
                ' + '.join('票款(%s)' % c for c in v['cover_codes']) or '0',
                int(v['total_cents']), int(model.voucher_known[i])),
        })
    return out


def _witness_list(keep, witness):
    return [{'code': c, 'cents': int(v)} for c, v in sorted(witness.items(), key=lambda kv: _code_sort_key(kv))]


def bound_conflicts(model):
    lb, ub = model.subset_bounds(list(range(len(model.vouchers))))
    out = []
    for j in range(model.n):
        if lb[j] > ub[j]:
            row = model.var_rows[j]
            out.append({
                'code': row['code'], 'settlement_id': row['id'],
                'lower_bound_cents': lb[j], 'upper_bound_cents': ub[j],
                'reason': '退款加渠道费 %d 分已高于覆盖凭证总额允许的上限 %d 分' % (lb[j], ub[j]),
            })
    for r, lo, hi in model.free_lb_ub():
        if lo > hi:
            out.append({'code': r['code'], 'settlement_id': r['id'],
                        'lower_bound_cents': lo, 'upper_bound_cents': hi,
                        'reason': '退款加渠道费高于金额上限'})
    return out


def irreducible_conflicts(model):
    """删除过滤求不可再缩减冲突凭证集（IIS），并为每次剔除保留可行见证。"""
    keep = list(range(len(model.vouchers)))
    evidence = []
    for i in list(keep):
        trial = [k for k in keep if k != i]
        ok, witness = model.solve(trial)
        if not ok:
            keep = trial
        else:
            evidence.append({
                'removed_voucher_id': model.vouchers[i]['id'],
                'removed_voucher_code': model.vouchers[i]['code'],
                'feasible_without_it': True,
                'witness': _witness_list(trial, witness),
                'witness_checksum': checksum(_witness_list(trial, witness)),
            })
    # 最终确认：再移除冲突集中任意一条都应恢复可行，证明不可再缩减
    minimal = True
    verification = []
    for i in keep:
        trial = [k for k in keep if k != i]
        ok, witness = model.solve(trial)
        verification.append({
            'voucher_id': model.vouchers[i]['id'], 'voucher_code': model.vouchers[i]['code'],
            'removing_it_restores_feasibility': bool(ok),
            'witness': _witness_list(trial, witness) if ok else None,
        })
        if not ok:
            minimal = False
    return keep, evidence, verification, minimal


# ---------------------------------------------------------------------------
# 求解主流程
# ---------------------------------------------------------------------------

def solve_snapshot(snapshot):
    model = Model(snapshot)
    if model.n > MAX_VARIABLES:
        raise SolverError('缺失票款 %d 场超过单次求解上限 %d 场，请按凭证拆分为多批'
                          % (model.n, MAX_VARIABLES))
    base = {
        'snapshot_checksum': snapshot.get('checksum'),
        'computed_at': now_iso(),
        'solver': SOLVER_NAME,
        'integer_cents': True,
        'excluded': snapshot.get('excluded') or [],
    }
    code_of = {r['id']: r['code'] for r in model.rows}
    cover_codes = {}
    for v in model.vouchers:
        for sid in v['covers']:
            cover_codes.setdefault(sid, []).append(v['code'])
    for sid in cover_codes:
        cover_codes[sid] = sorted(cover_codes[sid], key=lambda c: (len(c), c))
    for r in model.var_rows:
        r['_cover_codes'] = cover_codes.get(r['id'], [])
    for r in model.free_rows:
        r['_cover_codes'] = []

    if not any(r['gross_cents'] is None for r in model.rows):
        return {**base, 'verdict': 'empty', 'summary': {
            'scope_rows': len(model.rows), 'missing_rows': 0, 'variable_rows': 0,
            'free_rows': 0, 'voucher_count': len(model.vouchers)},
            'rows': _row_basis(model, None, None), 'vouchers': voucher_basis(model),
            'conflicts': None, 'notes': ['清单内没有缺失票款，无需补账']}

    keep_all = list(range(len(model.vouchers)))
    feasible, _ = model.solve(keep_all)
    if not feasible:
        iis, evidence, verification, minimal = irreducible_conflicts(model)
        conflicts = {
            'kind': 'infeasible',
            'irreducible': bool(minimal),
            'vouchers': [voucher_basis(model, [i])[0] for i in iis],
            'removal_evidence': evidence,
            'minimality_verification': verification,
            'bound_conflicts': bound_conflicts(model),
            'reason': '所选凭证给出的交叉合计与已知票款、缺失票款下限无法同时满足；'
                      '下列凭证集去掉任意一条即可恢复可行，故不可再缩减。',
        }
        return {**base, 'verdict': 'infeasible', 'summary': {
            'scope_rows': len(model.rows),
            'missing_rows': len(model.var_rows) + len(model.free_rows),
            'variable_rows': model.n, 'free_rows': len(model.free_rows),
            'voucher_count': len(model.vouchers),
            'conflict_voucher_count': len(iis),
        }, 'rows': _row_basis(model, None, None), 'vouchers': voucher_basis(model),
            'conflicts': conflicts, 'notes': [conflicts['reason']]}

    # 逐变量最小/最大整数可行值
    mins, maxs = {}, {}
    for j in range(model.n):
        e = [0.0] * model.n
        e[j] = 1.0
        ok, witness = model.solve(keep_all, e)
        if not ok:
            raise SolverError('可行域在求下界时失效，结果不可信')
        mins[model.var_rows[j]['code']] = int(round(sum(e[k] * witness[model.var_rows[k]['code']]
                                                     for k in range(model.n))))
        e[j] = -1.0
        ok, witness = model.solve(keep_all, e)
        if not ok:
            raise SolverError('可行域在求上界时失效，结果不可信')
        maxs[model.var_rows[j]['code']] = int(round(-sum(e[k] * witness[model.var_rows[k]['code']]
                                                      for k in range(model.n))))
    # free rows 只能给通用上下界
    free_bounds = {r['code']: (lo, hi) for r, lo, hi in model.free_lb_ub()}
    multi = bool(model.free_rows) or any(mins[r['code']] != maxs[r['code']] for r in model.var_rows)
    solution = None
    if not multi:
        solution = {r['code']: mins[r['code']] for r in model.var_rows}
        ok, witness = model.solve(keep_all)
        if not ok:
            raise SolverError('唯一解复核失败')
        for code, cents in solution.items():
            if witness.get(code) != cents:
                raise SolverError('唯一解与实际可行解不一致')
    rows = _row_basis(model, mins, maxs, free_bounds, solution)
    notes = []
    if snapshot.get('excluded'):
        notes.append('有 %d 条凭证覆盖了清单外场次，未参与求解；如需纳入请把它们覆盖的场次补进清单。'
                     % len(snapshot['excluded']))
    if multi:
        notes.append('可行域存在多个整数解，按场给出可行上下界；仅唯一解才允许填回。')
    else:
        notes.append('每场缺失票款在整数分上唯一确定，可整批原子入账。')
    if model.free_rows:
        notes.append('部分缺失场次没有任何凭证覆盖，只能给出通用上下界，无法唯一推导。')
    return {**base, 'verdict': 'multi' if multi else 'unique',
            'summary': {
                'scope_rows': len(model.rows),
                'missing_rows': len(model.var_rows) + len(model.free_rows),
                'variable_rows': model.n, 'free_rows': len(model.free_rows),
                'voucher_count': len(model.vouchers),
                'missing_total_floor_cents': sum(model.lb) + sum(lo for _, lo, _ in model.free_lb_ub()),
            },
            'rows': rows, 'vouchers': voucher_basis(model), 'solution': solution,
            'conflicts': None, 'notes': notes}


def _row_basis(model, mins, maxs, free_bounds=None, solution=None):
    out = []
    free_bounds = free_bounds or {}
    for r in model.rows:
        item = {
            'settlement_id': r['id'], 'code': r['code'], 'rev': r['rev'],
            'refund_cents': int(r['refund_cents']), 'fee_cents': int(r['fee_cents']),
            'lower_bound_floor_cents': max(0, int(r['refund_cents']) + int(r['fee_cents'])),
            'cover_voucher_codes': r.get('_cover_codes', []),
            'known': r['gross_cents'] is not None,
            'in_list': r.get('in_list', True),
        }
        if item['known']:
            item.update({'gross_cents': int(r['gross_cents']), 'min_cents': None,
                         'max_cents': None, 'resolved_cents': None, 'unconstrained': False})
        elif r['id'] in model.var_index:
            code = r['code']
            item.update({
                'gross_cents': None,
                'min_cents': mins.get(code) if mins else None,
                'max_cents': maxs.get(code) if maxs else None,
                'resolved_cents': (solution or {}).get(code),
                'unconstrained': False,
            })
        else:
            lo, hi = free_bounds.get(r['code'], (max(0, int(r['refund_cents']) + int(r['fee_cents'])), CAP_CENTS))
            item.update({'gross_cents': None, 'min_cents': lo, 'max_cents': hi,
                         'resolved_cents': None, 'unconstrained': True})
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# 过期校验与入账
# ---------------------------------------------------------------------------

def stale_changes(s, snapshot, lock=False):
    """对比冻结快照与当前数据。lock=True 时对参与记录加行锁，供入账事务使用。"""
    changes = []
    grouped = {}
    for r in snapshot.get('rows') or []:
        grouped.setdefault(r['id'], r)
    ids = list(grouped)
    sq = s.query(Settlement).filter(Settlement.id.in_(ids))
    if lock:
        sq = sq.with_for_update()
    current = {r.id: r for r in (sq.all() if ids else [])}
    for rid, snap in sorted(grouped.items(), key=lambda kv: kv[1]['code']):
        cur = current.get(rid)
        if not cur:
            changes.append({'kind': 'settlement_missing', 'settlement_id': rid, 'code': snap['code']})
            continue
        if cur.code != snap['code']:
            changes.append({'kind': 'settlement_changed', 'settlement_id': rid, 'code': snap['code'],
                            'field': 'code', 'snapshot': snap['code'], 'current': cur.code})
        if cur.rev != snap['rev']:
            changes.append({'kind': 'settlement_revised', 'settlement_id': rid, 'code': cur.code,
                            'field': 'rev', 'snapshot': snap['rev'], 'current': cur.rev})
        for field in ('gross_cents', 'refund_cents', 'fee_cents'):
            if getattr(cur, field) != snap[field]:
                changes.append({'kind': 'settlement_changed', 'settlement_id': rid, 'code': cur.code,
                                'field': field, 'snapshot': snap[field], 'current': getattr(cur, field)})

    snap_v = {v['id']: v for v in snapshot.get('vouchers') or []}
    # 被排除的凭证同样参与过期检查：修订总量或覆盖清单都会改变“纳入后”的结论
    for v in snapshot.get('excluded') or []:
        snap_v.setdefault(v['id'], {'id': v['id'], 'code': v['code'], 'rev': v['rev'],
                                   'total_cents': v['total_cents'],
                                   'covers': sorted(v.get('covers') or [])})
    vids = list(snap_v)
    vq = s.query(Voucher).filter(Voucher.id.in_(vids))
    if lock:
        vq = vq.with_for_update()
    cur_v = {v.id: v for v in (vq.all() if vids else [])}
    covers = cover_map(s, vids)
    for vid, snap in sorted(snap_v.items(), key=lambda kv: kv[1]['code']):
        cur = cur_v.get(vid)
        if not cur:
            changes.append({'kind': 'voucher_missing', 'voucher_id': vid, 'code': snap['code']})
            continue
        if cur.code != snap['code']:
            changes.append({'kind': 'voucher_changed', 'voucher_id': vid, 'code': snap['code'],
                            'field': 'code', 'snapshot': snap['code'], 'current': cur.code})
        if cur.rev != snap['rev']:
            changes.append({'kind': 'voucher_revised', 'voucher_id': vid, 'code': cur.code,
                            'field': 'rev', 'snapshot': snap['rev'], 'current': cur.rev})
        if cur.total_cents != snap['total_cents']:
            changes.append({'kind': 'voucher_changed', 'voucher_id': vid, 'code': cur.code,
                            'field': 'total_cents', 'snapshot': snap['total_cents'],
                            'current': cur.total_cents})
        if snap['covers'] is not None and sorted(covers.get(vid, [])) != sorted(snap['covers']):
            changes.append({'kind': 'voucher_covers_changed', 'voucher_id': vid, 'code': cur.code,
                            'field': 'covers', 'snapshot': snap['covers'],
                            'current': sorted(covers.get(vid, []))})

    # 范围为“全部”时，求解后新增的凭证/场次同样会改变结论；用基线 id 精确比对
    known_vids = set(snap_v)
    if snapshot.get('all_vouchers'):
        newer = [v for v in s.query(Voucher).order_by(Voucher.id).all()
                 if v.id not in known_vids]
        if newer:
            changes.append({'kind': 'voucher_added',
                            'codes': [v.code for v in newer],
                            'ids': [v.id for v in newer]})
    if snapshot.get('all_codes'):
        baseline = set(snapshot.get('baseline_settlement_ids') or [])
        newer_rows = [r for r in s.query(Settlement).order_by(Settlement.id).all()
                      if r.id not in baseline]
        if newer_rows:
            changes.append({'kind': 'settlement_added',
                            'codes': [r.code for r in newer_rows],
                            'ids': [r.id for r in newer_rows]})
    return changes


def mark_stale(s, b, changes):
    when = now_iso()
    b.state = 'stale'
    b.error = '凭证或场次在求解后发生修订，过期结果不可入账'
    b.result = {**(b.result or {}), 'stale': {'at': when, 'changes': changes}}


def post_backfill(s, backfill_id):
    """入账。返回 (任务, 是否幂等命中)。整批写账与状态迁移在同一事务内完成。"""
    b = s.query(Backfill).filter(Backfill.id == backfill_id).with_for_update().first()
    if not b:
        raise HTTPException(404, '补账任务不存在')
    if b.state == 'posted':
        return b, True
    if b.state == 'draft':
        raise HTTPException(409, '补账仍为草稿，请先求解')
    if b.state in ('queued', 'running'):
        raise HTTPException(409, '补账正在求解，请稍后刷新')
    if b.state == 'failed':
        raise HTTPException(409, '上次求解失败，请重新求解后再入账')
    if b.state == 'infeasible':
        raise HTTPException(409, '凭证相互矛盾，无法入账；请先处理冲突凭证并重新求解')
    if b.state == 'stale':
        raise HTTPException(409, '结果已过期，请重新求解后再入账')
    if b.state != 'solved':
        raise HTTPException(409, '当前状态不可入账')

    res = b.result or {}
    if res.get('verdict') != 'unique':
        raise HTTPException(409, '仅唯一解可填回缺失票款；多解请核对凭证后再试')
    snapshot = res.get('snapshot') or {}

    # 加锁读取 → 过期校验 → 整批写账，全部在同一事务中，提交前不释放行锁。
    try:
        changes = stale_changes(s, snapshot, lock=True)
        if changes:
            s.rollback()
            b = s.query(Backfill).filter(Backfill.id == backfill_id).with_for_update().first()
            mark_stale(s, b, changes)
            s.commit()
            raise HTTPException(409, detail={
                'message': '凭证或场次在求解后被修订，过期结果拒绝入账',
                'changes': changes,
            })

        targets = [r for r in res.get('rows') or []
                   if r.get('known') is False and r.get('resolved_cents') is not None]
        if not targets:
            raise HTTPException(409, '没有可填回的缺失票款')

        target_ids = [t['settlement_id'] for t in targets]
        current = {r.id: r for r in s.query(Settlement)
                   .filter(Settlement.id.in_(target_ids))
                   .order_by(Settlement.id).with_for_update().all()}
        for t in targets:
            cur = current.get(t['settlement_id'])
            if not cur:
                changes = [{'kind': 'settlement_missing', 'settlement_id': t['settlement_id'],
                            'code': t['code']}]
                s.rollback()
                b = s.query(Backfill).filter(Backfill.id == backfill_id).with_for_update().first()
                mark_stale(s, b, changes)
                s.commit()
                raise HTTPException(409, detail={
                    'message': '场次已不存在，过期结果拒绝入账', 'changes': changes})
            if cur.gross_cents is not None:
                changes = [{'kind': 'settlement_changed', 'settlement_id': t['settlement_id'],
                            'code': t['code'], 'field': 'gross_cents',
                            'snapshot': None, 'current': cur.gross_cents}]
                s.rollback()
                b = s.query(Backfill).filter(Backfill.id == backfill_id).with_for_update().first()
                mark_stale(s, b, changes)
                s.commit()
                raise HTTPException(409, detail={
                    'message': '场次票款已被并发写入，拒绝覆盖', 'changes': changes})

        written = []
        for t in targets:
            cur = current[t['settlement_id']]
            before = cur.rev
            cur.gross_cents = int(t['resolved_cents'])
            cur.rev = int(before) + 1
            written.append({
                'settlement_id': cur.id, 'code': cur.code, 'cents': int(t['resolved_cents']),
                'before_rev': before, 'after_rev': cur.rev,
            })

        at = now_iso()
        b.state = 'posted'
        b.posted_at = at
        b.error = ''
        b.result = {**res, 'posting': {
            'at': at, 'written': written,
            'row_count': len(written),
            'total_cents': sum(w['cents'] for w in written),
            'snapshot_checksum': snapshot.get('checksum'),
        }}
        s.commit()
    except HTTPException:
        raise
    except Exception:
        s.rollback()
        raise
    return b, False

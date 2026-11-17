"""补账任务的控制器：HTTP 形态、序列化与导出。"""
import json
from datetime import datetime, timezone

from fastapi import HTTPException, Response

from src.models.entities import Backfill
from src.services import backfill_jobs as jobs
from src.services.backfill import cover_map, now_iso


def _codes(s, ids):
    from src.models.entities import Settlement
    if not ids:
        return []
    rows = s.query(Settlement).filter(Settlement.id.in_(ids)).all()
    m = {r.id: r.code for r in rows}
    return [m[i] for i in ids if i in m]


def dump_job(b, include_snapshot=False):
    res = b.result or {}
    out = {
        'id': b.id, 'name': b.name, 'state': b.state, 'error': b.error or '',
        'attempts': int(b.attempts or 0), 'run_token': b.run_token or '',
        'created': b.created, 'created_by': b.created_by, 'solved_at': b.solved_at,
        'posted_at': b.posted_at,
        'verdict': res.get('verdict'), 'summary': res.get('summary'),
        'rows': res.get('rows'), 'vouchers': res.get('vouchers'),
        'solution': res.get('solution'), 'conflicts': res.get('conflicts'),
        'notes': res.get('notes'), 'excluded': res.get('excluded'),
        'posting': res.get('posting'), 'stale': res.get('stale'),
        'computed_at': res.get('computed_at'), 'solver': res.get('solver'),
        'snapshot_checksum': (res.get('snapshot') or {}).get('checksum'),
        'scope_codes': (res.get('snapshot') or {}).get('codes') or [],
        'scope_voucher_ids': (res.get('snapshot') or {}).get('requested_voucher_ids') or [],
    }
    if include_snapshot:
        out['snapshot'] = res.get('snapshot')
    return out


def list_jobs(s):
    return [dump_job(b) for b in s.query(Backfill).order_by(Backfill.id.desc()).all()]


def get_job(s, i):
    b = s.query(Backfill).filter(Backfill.id == i).first()
    if not b:
        raise HTTPException(404, '补账任务不存在')
    return jobs.mark_stale_if_needed(s, b)


def create_job(s, data, user):
    return jobs.create(s, data.name, data.codes, data.voucher_ids, user.email)


def update_job(s, i, data):
    b = s.query(Backfill).filter(Backfill.id == i).first()
    if not b:
        raise HTTPException(404, '补账任务不存在')
    return jobs.update_draft(s, b, data.name, data.codes, data.voucher_ids)


def preview(s, data):
    from src.services.backfill import build_snapshot, solve_snapshot
    snapshot = build_snapshot(s, data.voucher_ids, data.codes)
    result = solve_snapshot(snapshot)
    return {'snapshot_checksum': snapshot['checksum'], 'verdict': result['verdict'],
            'summary': result['summary'], 'rows': result['rows'],
            'vouchers': result['vouchers'], 'conflicts': result['conflicts'],
            'solution': result.get('solution'), 'notes': result['notes'],
            'excluded': result['excluded'], 'solver': result['solver'],
            'scope_codes': snapshot['codes']}


def solve_job(s, i):
    b = get_job(s, i)
    return jobs.solve(s, b)


def retry_job(s, i):
    b = get_job(s, i)
    return jobs.retry(s, b)


def post_job(s, i):
    b = get_job(s, i)
    return jobs.post(s, b)


def export_response(s, i, as_attachment=True):
    b = get_job(s, i)
    payload = {
        'task': dump_job(b, include_snapshot=True),
        'exported_at': now_iso(),
        'disclaimer': '金额单位为整数分；缺失票款仅在唯一解时填回，多解仅给出可行上下界；'
                      '冲突凭证集为不可再缩减集合，依据为每次剔除后的可行见证。',
    }
    headers = {}
    if as_attachment:
        headers['Content-Disposition'] = f'attachment; filename="stage-backfill-{b.id}.json"'
    return Response(json.dumps(payload, ensure_ascii=False, indent=2),
                    media_type='application/json', headers=headers)

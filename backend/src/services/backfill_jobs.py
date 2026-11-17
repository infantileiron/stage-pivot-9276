"""补账任务的状态机与队列。worker 仅在此模块内推进求解状态。

状态：draft → queued → running → solved / infeasible / failed
过期检查发生在入账（services.backfill.post_backfill）；并发修订只会让结果变
stale，不会自动覆盖账目。重试沿用原任务与同一份冻结输入，run_token 保证旧
worker 的迟到结果被丢弃；posted 之后重试直接返回已入账，不重复写账。
"""
import os
import uuid
from fastapi import HTTPException

import redis

from src.models.entities import Backfill
from src.services.backfill import (build_snapshot, now_iso, post_backfill,
                                   solve_snapshot, stale_changes)

QUEUE = 'stage:backfills'


def queue():
    return redis.Redis.from_url(os.environ['REDIS_URL'])


def get(s, i):
    b = s.query(Backfill).filter(Backfill.id == i).first()
    if not b:
        raise HTTPException(404, '补账任务不存在')
    return b


def create(s, name, codes, voucher_ids, actor):
    snapshot = build_snapshot(s, voucher_ids, codes)
    b = Backfill(name=name, state='draft',
                 result={'snapshot': snapshot, 'summary': None,
                         'scope': {'codes': list(codes or []),
                                   'voucher_ids': list(voucher_ids or [])}},
                 created=now_iso(), created_by=actor, attempts=0, run_token='', error='')
    s.add(b)
    s.commit()
    return b


def update_draft(s, b, name, codes, voucher_ids):
    if b.state != 'draft':
        raise HTTPException(409, '仅草稿可修改范围；已求解任务请新建')
    snapshot = build_snapshot(s, voucher_ids, codes)
    b.name = name
    b.result = {'snapshot': snapshot, 'summary': None,
                'scope': {'codes': list(codes or []), 'voucher_ids': list(voucher_ids or [])}}
    b.error = ''
    s.commit()
    return b


def _refreeze(s, b):
    """沿原任务的场次清单与凭证范围，按当前数据重新冻结输入。

    任务 id 与范围不变（即“沿原任务恢复”），但过期草稿/失败重试不会复用
    已知过期的旧快照；入账前仍会再做一次过期校验。
    """
    res = b.result or {}
    scope = res.get('scope') or {}
    if not scope:
        snapshot = res.get('snapshot')
        if not snapshot:
            raise HTTPException(409, '缺少冻结输入，请重新创建草稿')
        scope = {'codes': snapshot.get('codes') or [],
                 'voucher_ids': snapshot.get('requested_voucher_ids') or []}
    snapshot = build_snapshot(s, scope.get('voucher_ids'), scope.get('codes'))
    return snapshot, scope


def _enqueue(b, token, s):
    try:
        queue().rpush(QUEUE, b.id)
    except redis.RedisError:
        b.state = 'failed'
        b.error = '队列暂不可用，任务保留，可稍后重试'
        b.run_token = ''
        s.commit()
        raise HTTPException(503, b.error)


def solve(s, b):
    if b.state == 'posted':
        raise HTTPException(409, '任务已入账，不能重复求解；如需修正请新建补账')
    if b.state in ('queued', 'running'):
        raise HTTPException(409, '任务正在求解，请稍后刷新')
    if b.state not in ('draft', 'solved', 'infeasible', 'failed', 'stale'):
        raise HTTPException(409, '当前状态不可求解')
    snapshot, scope = _refreeze(s, b)
    token = uuid.uuid4().hex
    b.state = 'queued'
    b.run_token = token
    b.error = ''
    b.solved_at = None
    b.result = {'snapshot': snapshot, 'summary': None, 'scope': scope,
                'queued_at': now_iso(), 'solve_token': token}
    b.attempts = int(b.attempts or 0) + 1
    s.commit()
    _enqueue(b, token, s)
    return b


def retry(s, b):
    """失败或过期后沿原任务恢复。posted 直接返回，不重复写账。"""
    if b.state == 'posted':
        return b, True
    if b.state in ('queued', 'running'):
        raise HTTPException(409, '任务正在求解，请稍后刷新')
    if b.state not in ('failed', 'stale'):
        raise HTTPException(409, '仅失败或过期的任务可重试；请先求解')
    return solve(s, b), False

def refresh_staleness(s, b):
    """只读探测：入账前页面可据此提示过期。不改状态。"""
    snapshot = (b.result or {}).get('snapshot') or {}
    if b.state not in ('solved',):
        return []
    return stale_changes(s, snapshot)


def mark_stale_if_needed(s, b):
    if b.state != 'solved':
        return b
    changes = stale_changes(s, (b.result or {}).get('snapshot') or {})
    if changes:
        b.state = 'stale'
        b.error = '凭证或场次在求解后被修订，过期结果不可入账'
        b.result = {**(b.result or {}), 'stale': {'at': now_iso(), 'changes': changes}}
        s.commit()
    return b


def post(s, b):
    return post_backfill(s, b.id)


# ---------------------------------------------------------------------------
# worker 侧
# ---------------------------------------------------------------------------

def process(s, backfill_id):
    """处理一条队列消息。返回 (状态, 说明)。任何不匹配的迟到消息都安全丢弃。

    求解可能耗时数十秒，因此不在求解期间持有 Backfill 行锁：先乐观读出冻结输入
    与令牌，求解完成后重新加锁读回，只有状态仍为 running 且令牌未变才写入结果。
    """
    b = s.query(Backfill).filter(Backfill.id == backfill_id).first()
    if not b:
        return 'skipped', '任务不存在'
    if b.state == 'posted':
        return 'skipped', '任务已入账，忽略重复消息'
    if b.state not in ('queued', 'running'):
        return 'skipped', '任务状态为 %s，忽略过期消息' % b.state
    token = (b.result or {}).get('solve_token') or b.run_token
    snapshot = (b.result or {}).get('snapshot') or {}
    if b.state == 'queued':
        b.state = 'running'
        s.commit()
    try:
        result = solve_snapshot(snapshot)
    except Exception as e:  # 求解异常保留任务，可原任务重试
        s.rollback()
        b = s.query(Backfill).filter(Backfill.id == backfill_id).with_for_update().first()
        if b and b.state == 'running' and _token(b) == token:
            b.state = 'failed'
            b.error = '求解失败：%s' % str(e)[:400]
            b.result = {**(b.result or {}), 'failed_at': now_iso()}
            s.commit()
        return 'failed', (b.error if b else '任务不存在')
    s.rollback()
    b = s.query(Backfill).filter(Backfill.id == backfill_id).with_for_update().first()
    if not b or b.state != 'running' or _token(b) != token:
        s.rollback()
        return 'skipped', '状态或令牌已变化，丢弃过期结果'
    verdict = result.get('verdict')
    state = {'unique': 'solved', 'multi': 'solved', 'infeasible': 'infeasible',
             'empty': 'solved'}.get(verdict, 'failed')
    b.state = state
    b.solved_at = now_iso()
    b.error = '' if state != 'failed' else '求解未得到可入账结论'
    b.result = {**result, 'snapshot': snapshot, 'solve_token': token}
    s.commit()
    return state, verdict


def _token(b):
    return (b.result or {}).get('solve_token') or b.run_token

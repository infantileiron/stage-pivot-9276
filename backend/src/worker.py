import json
import logging
import os
import time
from pathlib import Path

import redis

from src.config.database import Session
from src.models.entities import Backfill, Pivot
from src.services.analysis import pivot
from src.services.backfill_jobs import process as process_backfill

QUEUES = ['stage:pivots', 'stage:backfills']
ARTIFACTS = Path(os.environ.get('ARTIFACTS_DIR', '/artifacts'))


def artifact_path(ident):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    return ARTIFACTS / (str(ident) + '.json')


def recover():
    """单 worker 启动恢复：queued/running 任务重新排队，沿用原任务与冻结输入。"""
    q = redis.Redis.from_url(os.environ['REDIS_URL'])
    with Session() as s:
        for r in s.query(Pivot).filter(Pivot.state.in_(['queued', 'running'])).all():
            r.state = 'queued'
            q.rpush('stage:pivots', r.id)
        for b in s.query(Backfill).filter(Backfill.state.in_(['queued', 'running'])).all():
            b.state = 'queued'
            q.rpush('stage:backfills', b.id)
        s.commit()


def handle_pivot(item):
    with Session() as s:
        r = s.get(Pivot, int(item))
        if not r or r.state != 'queued':
            return
        snapshot = (r.result or {}).get('snapshot') or []
        r.state = 'running'
        s.commit()
        try:
            r.result = {**pivot(snapshot, r.row_dim, r.col_dim), 'snapshot': snapshot}
            r.state = 'done'
            s.commit()
            try:
                artifact_path(r.id).write_text(json.dumps(r.result, ensure_ascii=False))
            except OSError:
                logging.warning('写出透视产物失败，数据库结果仍然有效', exc_info=True)
        except Exception as e:
            s.rollback()
            r = s.get(Pivot, int(item))
            if r:
                r.state = 'failed'
                r.error = str(e)
                s.commit()


def handle_backfill(item):
    with Session() as s:
        process_backfill(s, int(item))


def main():
    recover()
    q = redis.Redis.from_url(os.environ['REDIS_URL'])
    while True:
        Path('/tmp/heartbeat').touch()
        try:
            item = q.blpop(QUEUES, timeout=3)
            if not item:
                continue
            queue_name, raw = item
            if queue_name.decode() == 'stage:pivots':
                handle_pivot(raw)
            else:
                handle_backfill(raw)
        except Exception:
            logging.exception('worker')
            time.sleep(2)


if __name__ == '__main__':
    main()

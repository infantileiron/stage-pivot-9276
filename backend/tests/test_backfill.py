import json
import os
import tempfile

import pytest

os.environ.setdefault('JWT_SECRET', 'test-secret')
_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
os.environ['DATABASE_URL'] = 'sqlite:///' + _db.name
os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')

from fastapi.testclient import TestClient  # noqa: E402

from src.config.database import Base, Session, engine  # noqa: E402
from src.models.entities import Settlement, User, Voucher, VoucherCover  # noqa: E402
from src.main import app  # noqa: E402
from src.services import backfill_jobs as jobs  # noqa: E402
from src.services.backfill import build_snapshot, solve_snapshot  # noqa: E402
from src.utils.security import hash_password  # noqa: E402
from src.worker import handle_backfill  # noqa: E402


class FakeQueue:
    def __init__(self):
        self.items = []

    def rpush(self, name, value):
        self.items.append((name, value))

    def blpop(self, names, timeout=0):
        return self.items.pop(0) if self.items else None


@pytest.fixture(autouse=True)
def clean_db(monkeypatch):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session() as s:
        for role in ('admin', 'analyst', 'viewer'):
            s.add(User(email=role + '@demo.local', role=role, password=hash_password('Demo123!')))
        s.commit()
    fake = FakeQueue()
    monkeypatch.setattr(jobs, 'queue', lambda: fake)
    yield fake


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def auth(client, role='analyst'):
    r = client.post('/auth/login', json={'email': role + '@demo.local', 'password': 'Demo123!'})
    assert r.status_code == 200, r.text
    return {'Authorization': 'Bearer ' + r.json()['token']}


def add_settlement(s, code, gross, refund=0, fee=0, day='2026-11-15'):
    s.add(Settlement(code=code, day=day, venue='云幕大剧场', play='渡口来信', channel='剧院直营',
                     gross_cents=gross, refund_cents=refund, fee_cents=fee, seats=100,
                     checked=80, rev=1))
    s.commit()


def add_voucher(s, code, total, covers):
    v = Voucher(code=code, total_cents=total, note='t', rev=1)
    s.add(v)
    s.flush()
    for c in covers:
        sid = s.query(Settlement).filter_by(code=c).one().id
        s.add(VoucherCover(voucher_id=v.id, settlement_id=sid))
    s.commit()
    return v


def run_job(fake):
    name, ident = fake.items.pop(0)
    assert name == 'stage:backfills'
    handle_backfill(str(ident))


# ---------------------------------------------------------------------------
# 求解器单元测试
# ---------------------------------------------------------------------------

def _snap(rows, vouchers):
    return {'version': 1, 'codes': [], 'rows': rows, 'vouchers': vouchers,
            'excluded': [], 'cap_cents': 100_000_000, 'checksum': 'x'}


def _row(i, code, gross, refund=0, fee=0):
    return {'id': i, 'code': code, 'rev': 1, 'gross_cents': gross,
            'refund_cents': refund, 'fee_cents': fee}


def test_unique_solution_integer_cents():
    rows = [_row(1, 'A', None), _row(2, 'B', None), _row(3, 'C', None)]
    vs = [{'id': 1, 'code': 'V1', 'rev': 1, 'total_cents': 10, 'covers': [1, 2], 'cover_codes': ['A', 'B']},
          {'id': 2, 'code': 'V2', 'rev': 1, 'total_cents': 12, 'covers': [1, 3], 'cover_codes': ['A', 'C']},
          {'id': 3, 'code': 'V3', 'rev': 1, 'total_cents': 14, 'covers': [2, 3], 'cover_codes': ['B', 'C']}]
    r = solve_snapshot(_snap(rows, vs))
    assert r['verdict'] == 'unique'
    assert r['solution'] == {'A': 4, 'B': 6, 'C': 8}


def test_multi_solution_gives_bounds_and_never_fills():
    rows = [_row(1, 'A', None), _row(2, 'B', None)]
    vs = [{'id': 1, 'code': 'V1', 'rev': 1, 'total_cents': 10, 'covers': [1, 2], 'cover_codes': ['A', 'B']}]
    r = solve_snapshot(_snap(rows, vs))
    assert r['verdict'] == 'multi'
    assert r['solution'] is None
    assert [(x['code'], x['min_cents'], x['max_cents']) for x in r['rows']] == [('A', 0, 10), ('B', 0, 10)]


def test_infeasible_returns_irreducible_conflict_set():
    rows = [_row(1, 'A', None), _row(2, 'B', None)]
    vs = [{'id': 1, 'code': 'V1', 'rev': 1, 'total_cents': 5, 'covers': [1, 2], 'cover_codes': ['A', 'B']},
          {'id': 2, 'code': 'V2', 'rev': 1, 'total_cents': 9, 'covers': [1, 2], 'cover_codes': ['A', 'B']}]
    r = solve_snapshot(_snap(rows, vs))
    assert r['verdict'] == 'infeasible'
    conf = r['conflicts']
    assert conf['irreducible'] is True
    assert [v['code'] for v in conf['vouchers']] == ['V1', 'V2']
    assert all(e['feasible_without_it'] for e in conf['removal_evidence'])
    assert all(v['removing_it_restores_feasibility'] for v in conf['minimality_verification'])


def test_floor_refund_plus_fee_creates_conflict_with_basis():
    rows = [_row(1, 'A', None, refund=20), _row(2, 'B', None)]
    vs = [{'id': 1, 'code': 'V1', 'rev': 1, 'total_cents': 10, 'covers': [1, 2], 'cover_codes': ['A', 'B']}]
    r = solve_snapshot(_snap(rows, vs))
    assert r['verdict'] == 'infeasible'
    assert [v['code'] for v in r['conflicts']['vouchers']] == ['V1']
    assert r['conflicts']['bound_conflicts'][0]['code'] == 'A'


def test_empty_scope_is_not_zero():
    rows = [_row(1, 'A', 0)]
    vs = [{'id': 1, 'code': 'V1', 'rev': 1, 'total_cents': 0, 'covers': [1], 'cover_codes': ['A']}]
    r = solve_snapshot(_snap(rows, vs))
    assert r['verdict'] == 'empty'
    assert r['rows'][0]['known'] is True


# ---------------------------------------------------------------------------
# 端到端：草稿 → 求解 → 入账
# ---------------------------------------------------------------------------

def test_draft_solve_post_flow(client, clean_db):
    h = auth(client)
    with Session() as s:
        add_settlement(s, 'YM-001', 1000)
        add_settlement(s, 'YM-002', 2000)
        add_settlement(s, 'YM-003', None)
        add_settlement(s, 'YM-004', None)
        add_settlement(s, 'YM-005', None)
        # 已知 YM-001/YM-002 作为常数移入右端；三个缺失场次由三条交叉凭证唯一确定
        add_voucher(s, 'PV-001', 1000 + 2000 + 4000 + 5000, ['YM-001', 'YM-002', 'YM-003', 'YM-004'])
        add_voucher(s, 'PV-002', 5000 + 6000, ['YM-004', 'YM-005'])
        add_voucher(s, 'PV-003', 4000 + 6000, ['YM-003', 'YM-005'])
        known2 = s.query(Settlement).filter_by(code='YM-002').one().gross_cents
    body = {'name': '11月补账', 'codes': [], 'voucher_ids': [1, 2, 3]}
    r = client.post('/backfills', json=body, headers=h)
    assert r.status_code == 200, r.text
    job = r.json()
    assert job['state'] == 'draft'
    r = client.post(f"/backfills/{job['id']}/solve", headers=h)
    assert r.status_code == 200
    assert r.json()['state'] == 'queued'
    run_job(clean_db)
    d = client.get(f"/backfills/{job['id']}", headers=h).json()
    assert d['state'] == 'solved' and d['verdict'] == 'unique'
    assert d['solution'] == {'YM-003': 4000, 'YM-004': 5000, 'YM-005': 6000}
    r = client.post(f"/backfills/{job['id']}/post", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()['state'] == 'posted'
    with Session() as s:
        assert s.query(Settlement).filter_by(code='YM-003').one().gross_cents == 4000
        assert s.query(Settlement).filter_by(code='YM-004').one().gross_cents == 5000
        assert s.query(Settlement).filter_by(code='YM-005').one().gross_cents == 6000
        assert s.query(Settlement).filter_by(code='YM-002').one().gross_cents == known2
    # 重复入账不重复写账
    r = client.post(f"/backfills/{job['id']}/post", headers=h)
    assert r.status_code == 200 and r.json()['idempotent'] is True
    with Session() as s:
        assert s.query(Settlement).filter_by(code='YM-003').one().rev == 2


def test_multi_solution_cannot_post(client, clean_db):
    h = auth(client)
    with Session() as s:
        add_settlement(s, 'YM-001', None)
        add_settlement(s, 'YM-002', None)
        add_voucher(s, 'PV-001', 100, ['YM-001', 'YM-002'])
    r = client.post('/backfills', json={'name': '多解', 'codes': [], 'voucher_ids': [1]}, headers=h)
    jid = r.json()['id']
    client.post(f'/backfills/{jid}/solve', headers=h)
    run_job(clean_db)
    d = client.get(f'/backfills/{jid}', headers=h).json()
    assert d['verdict'] == 'multi'
    r = client.post(f'/backfills/{jid}/post', headers=h)
    assert r.status_code == 409
    with Session() as s:
        assert s.query(Settlement).filter_by(code='YM-001').one().gross_cents is None


def test_infeasible_conflict_job(client, clean_db):
    h = auth(client)
    with Session() as s:
        add_settlement(s, 'YM-001', None)
        add_settlement(s, 'YM-002', None)
        add_voucher(s, 'PV-001', 5, ['YM-001', 'YM-002'])
        add_voucher(s, 'PV-002', 9, ['YM-001', 'YM-002'])
    r = client.post('/backfills', json={'name': '冲突', 'codes': [], 'voucher_ids': [1, 2]}, headers=h)
    jid = r.json()['id']
    client.post(f'/backfills/{jid}/solve', headers=h)
    run_job(clean_db)
    d = client.get(f'/backfills/{jid}', headers=h).json()
    assert d['state'] == 'infeasible'
    assert d['conflicts']['irreducible'] is True
    assert len(d['conflicts']['removal_evidence']) > 0
    r = client.post(f'/backfills/{jid}/post', headers=h)
    assert r.status_code == 409


def test_stale_result_rejected(client, clean_db):
    h = auth(client)
    with Session() as s:
        add_settlement(s, 'YM-001', 1000)
        add_settlement(s, 'YM-002', None)
        add_settlement(s, 'YM-003', None)
        add_voucher(s, 'PV-001', 1000 + 4000 + 5000, ['YM-001', 'YM-002', 'YM-003'])
        sid = s.query(Settlement).filter_by(code='YM-002').one().id
    r = client.post('/backfills', json={'name': '过期', 'codes': [], 'voucher_ids': [1]}, headers=h)
    jid = r.json()['id']
    client.post(f'/backfills/{jid}/solve', headers=h)
    run_job(clean_db)
    assert client.get(f'/backfills/{jid}', headers=h).json()['state'] == 'solved'
    checksum_before = client.get(f'/backfills/{jid}', headers=h).json()['snapshot_checksum']
    # 并发修订凭证
    r = client.put('/vouchers/1', json={'code': 'PV-001', 'total_cents': 10000, 'note': '改',
                                        'cover_codes': ['YM-001', 'YM-002', 'YM-003']}, headers=h)
    assert r.status_code == 200, r.text
    r = client.post(f'/backfills/{jid}/post', headers=h)
    assert r.status_code == 409
    d = client.get(f'/backfills/{jid}', headers=h).json()
    assert d['state'] == 'stale'
    assert d['stale']['changes']
    with Session() as s:
        assert s.query(Settlement).filter_by(code='YM-002').one().gross_cents is None
    # 重试沿原任务恢复
    r = client.post(f'/backfills/{jid}/retry', headers=h)
    assert r.status_code == 200
    run_job(clean_db)
    d = client.get(f'/backfills/{jid}', headers=h).json()
    assert d['state'] == 'solved'
    assert d['attempts'] == 2
    assert d['snapshot_checksum'] != checksum_before


def test_missing_settlement_excluded_from_pivot(client, clean_db):
    h = auth(client)
    r = client.post('/settlements/missing', json={
        'code': 'YM-900', 'day': '2026-11-15', 'venue': '云幕大剧场', 'play': '渡口来信',
        'channel': '剧院直营', 'seats': 100, 'checked': 80}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()['gross_cents'] is None
    with Session() as s:
        snap = build_snapshot(s, [], ['YM-900'])
    assert snap['rows'][0]['gross_cents'] is None


def test_voucher_covers_validation(client, clean_db):
    h = auth(client)
    with Session() as s:
        add_settlement(s, 'YM-001', 1000)
    r = client.post('/vouchers', json={'code': 'PV-X', 'total_cents': 1000, 'note': '',
                                       'cover_codes': ['NOPE']}, headers=h)
    assert r.status_code == 422
    r = client.post('/vouchers', json={'code': 'PV-X', 'total_cents': 1000, 'note': '',
                                       'cover_codes': ['YM-001', 'YM-001']}, headers=h)
    assert r.status_code == 422


def test_viewer_cannot_write(client, clean_db):
    h = auth(client, 'viewer')
    r = client.post('/backfills', json={'name': '只读', 'codes': ['YM-001'], 'voucher_ids': []}, headers=h)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# 并发、恢复与透视一致性
# ---------------------------------------------------------------------------

def test_concurrent_settlement_revision_rejects_post(client, clean_db):
    h = auth(client)
    with Session() as s:
        add_settlement(s, 'YM-001', None)
        add_settlement(s, 'YM-002', None)
        add_voucher(s, 'PV-001', 9000, ['YM-001', 'YM-002'])
        add_voucher(s, 'PV-002', 1000, ['YM-001'])
    r = client.post('/backfills', json={'name': '并发场次', 'codes': [], 'voucher_ids': [1, 2]}, headers=h)
    jid = r.json()['id']
    client.post(f'/backfills/{jid}/solve', headers=h)
    run_job(clean_db)
    assert client.get(f'/backfills/{jid}', headers=h).json()['verdict'] == 'unique'
    # 求解后并发修订已知场次
    with Session() as s:
        sid = s.query(Settlement).filter_by(code='YM-001').one().id
    r = client.put(f'/settlements/{sid}', json={
        'code': 'YM-001', 'day': '2026-11-15', 'venue': '云幕大剧场', 'play': '渡口来信',
        'channel': '剧院直营', 'gross_cents': 1000, 'refund_cents': 0, 'fee_cents': 0,
        'seats': 100, 'checked': 80}, headers=h)
    assert r.status_code == 200, r.text
    r = client.post(f'/backfills/{jid}/post', headers=h)
    assert r.status_code == 409
    assert client.get(f'/backfills/{jid}', headers=h).json()['state'] == 'stale'
    with Session() as s:
        assert s.query(Settlement).filter_by(code='YM-002').one().gross_cents is None


def test_failed_job_retry_uses_same_task_and_writes_once(client, clean_db, monkeypatch):
    h = auth(client)
    with Session() as s:
        add_settlement(s, 'YM-001', None)
        add_settlement(s, 'YM-002', None)
        add_voucher(s, 'PV-001', 8000, ['YM-001'])
        add_voucher(s, 'PV-002', 2000, ['YM-002'])
    r = client.post('/backfills', json={'name': '失败恢复', 'codes': [], 'voucher_ids': [1, 2]}, headers=h)
    jid = r.json()['id']
    client.post(f'/backfills/{jid}/solve', headers=h)
    # 注入一次求解失败
    import src.services.backfill_jobs as jobs_mod
    original = jobs_mod.solve_snapshot
    monkeypatch.setattr(jobs_mod, 'solve_snapshot', lambda snap: (_ for _ in ()).throw(RuntimeError('boom')))
    run_job(clean_db)
    d = client.get(f'/backfills/{jid}', headers=h).json()
    assert d['state'] == 'failed' and 'boom' in d['error']
    monkeypatch.setattr(jobs_mod, 'solve_snapshot', original)
    r = client.post(f'/backfills/{jid}/retry', headers=h)
    assert r.status_code == 200
    run_job(clean_db)
    d = client.get(f'/backfills/{jid}', headers=h).json()
    assert d['state'] == 'solved' and d['verdict'] == 'unique' and d['attempts'] == 2
    assert client.post(f'/backfills/{jid}/post', headers=h).json()['state'] == 'posted'
    with Session() as s:
        assert s.query(Settlement).filter_by(code='YM-001').one().gross_cents == 8000
        assert s.query(Settlement).filter_by(code='YM-002').one().gross_cents == 2000
        revs = {r.code: r.rev for r in s.query(Settlement).all()}
    # 重试已入账任务不重复写账
    r = client.post(f'/backfills/{jid}/retry', headers=h)
    assert r.status_code == 200 and r.json()['idempotent'] is True
    with Session() as s:
        assert {x.code: x.rev for x in s.query(Settlement).all()} == revs


def test_retry_rejected_while_queued(client, clean_db):
    h = auth(client)
    with Session() as s:
        add_settlement(s, 'YM-001', None)
        add_voucher(s, 'PV-001', 100, ['YM-001'])
    r = client.post('/backfills', json={'name': '排队', 'codes': [], 'voucher_ids': [1]}, headers=h)
    jid = r.json()['id']
    client.post(f'/backfills/{jid}/solve', headers=h)
    r = client.post(f'/backfills/{jid}/retry', headers=h)
    assert r.status_code == 409
    r = client.post(f'/backfills/{jid}/post', headers=h)
    assert r.status_code == 409


def test_pivot_rejects_batch_with_missing_gross(client, clean_db, monkeypatch):
    monkeypatch.setattr('src.routes.api.redis.Redis.from_url', lambda *a, **k: clean_db)
    h = auth(client)
    with Session() as s:
        add_settlement(s, 'YM-001', 1000)
        add_settlement(s, 'YM-002', None)
    r = client.post('/pivots', json={'name': '半批', 'row_dim': 'venue', 'col_dim': 'channel'}, headers=h)
    assert r.status_code == 200, r.text
    from src.worker import handle_pivot
    from src.models.entities import Pivot
    with Session() as s:
        pid = s.query(Pivot).order_by(Pivot.id.desc()).first().id
    handle_pivot(str(pid))
    d = client.get(f'/pivots/{pid}', headers=h).json()
    assert d['state'] == 'failed'
    assert '缺失票款' in d['error']


def test_out_of_scope_known_voucher_is_kept_as_constant(client, clean_db):
    h = auth(client)
    with Session() as s:
        add_settlement(s, 'YM-001', None)
        add_settlement(s, 'YM-002', None)
        add_settlement(s, 'YM-003', 500)
        add_settlement(s, 'YM-004', None)
        add_voucher(s, 'PV-001', 1000, ['YM-001', 'YM-002'])
        # 越界场次 YM-003 已知：按常数并入右端，凭证仍然有效
        add_voucher(s, 'PV-002', 1500, ['YM-001', 'YM-003'])
        # 越界场次 YM-004 缺失：无法作为常数，整条排除
        add_voucher(s, 'PV-003', 2000, ['YM-002', 'YM-004'])
    r = client.post('/backfills', json={'name': '限清单', 'codes': ['YM-001', 'YM-002'],
                                        'voucher_ids': [1, 2, 3]}, headers=h)
    jid = r.json()['id']
    client.post(f'/backfills/{jid}/solve', headers=h)
    run_job(clean_db)
    d = client.get(f'/backfills/{jid}', headers=h).json()
    assert [v['id'] for v in d['excluded']] == [3]
    assert d['excluded'][0]['out_of_scope_codes'] == ['YM-004']
    assert d['summary']['voucher_count'] == 2
    kept = {v['code']: v for v in d['vouchers']}
    assert kept['PV-002']['known_sum_cents'] == 500
    assert kept['PV-002']['residual_for_missing_cents'] == 1000


def test_export_contains_basis(client, clean_db):
    h = auth(client)
    with Session() as s:
        add_settlement(s, 'YM-001', None)
        add_settlement(s, 'YM-002', None)
        add_voucher(s, 'PV-001', 500, ['YM-001', 'YM-002'])
        add_voucher(s, 'PV-002', 900, ['YM-001', 'YM-002'])
    r = client.post('/backfills', json={'name': '导出', 'codes': [], 'voucher_ids': [1, 2]}, headers=h)
    jid = r.json()['id']
    client.post(f'/backfills/{jid}/solve', headers=h)
    run_job(clean_db)
    r = client.get(f'/backfills/{jid}/export', headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body['task']['conflicts']['irreducible'] is True
    assert body['task']['snapshot']['checksum']


def test_preview_creates_no_job_and_writes_nothing(client, clean_db):
    h = auth(client)
    with Session() as s:
        add_settlement(s, 'YM-001', None)
        add_settlement(s, 'YM-002', None)
        add_voucher(s, 'PV-001', 1000, ['YM-001', 'YM-002'])
    r = client.post('/backfills/preview', json={'name': '只预演', 'codes': [], 'voucher_ids': [1],
                                                'preview_only': True, 'note': ''}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()['verdict'] == 'multi'
    assert client.get('/backfills', headers=h).json() == []
    with Session() as s:
        assert s.query(Settlement).filter(Settlement.gross_cents.is_(None)).count() == 2


def test_pivot_after_backfill_is_conserved_and_old_pivot_frozen(client, clean_db, monkeypatch):
    monkeypatch.setattr('src.routes.api.redis.Redis.from_url', lambda *a, **k: clean_db)
    h = auth(client)
    with Session() as s:
        add_settlement(s, 'YM-001', 1000)
        add_settlement(s, 'YM-002', None)
        add_voucher(s, 'PV-001', 3000, ['YM-001', 'YM-002'])
    r = client.post('/backfills', json={'name': '补账', 'codes': [], 'voucher_ids': [1]}, headers=h)
    jid = r.json()['id']
    client.post(f'/backfills/{jid}/solve', headers=h)
    run_job(clean_db)
    assert client.post(f'/backfills/{jid}/post', headers=h).json()['state'] == 'posted'
    r = client.post('/pivots', json={'name': '补账后', 'row_dim': 'venue', 'col_dim': 'channel'}, headers=h)
    pid = r.json()['id']
    from src.worker import handle_pivot
    import pathlib
    pathlib.Path('/tmp/artifacts').mkdir(exist_ok=True)
    handle_pivot(str(pid))
    d = client.get(f'/pivots/{pid}', headers=h).json()
    assert d['state'] == 'done' and d['result']['conserved'] is True, d.get('error')
    assert d['result']['total']['gross_cents'] == 3000
    frozen = d['result']['checksum']
    with Session() as s:
        sid = s.query(Settlement).filter_by(code='YM-001').one().id
    client.put(f'/settlements/{sid}', json={
        'code': 'YM-001', 'day': '2026-11-15', 'venue': '云幕大剧场', 'play': '渡口来信',
        'channel': '剧院直营', 'gross_cents': 5000, 'refund_cents': 0, 'fee_cents': 0,
        'seats': 100, 'checked': 80}, headers=h)
    again = client.get(f'/pivots/{pid}', headers=h).json()
    assert again['result']['checksum'] == frozen
    assert again['result']['total']['gross_cents'] == 3000


def test_all_scope_does_not_flag_uncovered_existing_rows(client, clean_db):
    h = auth(client)
    with Session() as s:
        add_settlement(s, 'YM-001', 1000)
        add_settlement(s, 'YM-002', 2000)
        add_settlement(s, 'YM-003', None)
        add_settlement(s, 'YM-004', None)
        add_voucher(s, 'PV-001', 9000, ['YM-003', 'YM-004'])
        add_voucher(s, 'PV-002', 4000, ['YM-003'])
    r = client.post('/backfills', json={'name': '全部范围', 'codes': [], 'voucher_ids': []}, headers=h)
    jid = r.json()['id']
    client.post(f'/backfills/{jid}/solve', headers=h)
    run_job(clean_db)
    assert client.get(f'/backfills/{jid}', headers=h).json()['state'] == 'solved'
    r = client.post(f'/backfills/{jid}/post', headers=h)
    assert r.status_code == 200, r.text
    assert r.json()['state'] == 'posted'


def test_new_settlement_after_solve_invalidates_all_scope(client, clean_db):
    h = auth(client)
    with Session() as s:
        add_settlement(s, 'YM-001', 1000)
        add_settlement(s, 'YM-002', None)
        add_voucher(s, 'PV-001', 3000, ['YM-002'])
    r = client.post('/backfills', json={'name': '新增场次', 'codes': [], 'voucher_ids': []}, headers=h)
    jid = r.json()['id']
    client.post(f'/backfills/{jid}/solve', headers=h)
    run_job(clean_db)
    # 求解后新增一条缺失场次（范围内全部），结果必须视为过期
    client.post('/settlements/missing', json={
        'code': 'YM-900', 'day': '2026-11-15', 'venue': '云幕大剧场', 'play': '渡口来信',
        'channel': '剧院直营', 'seats': 100, 'checked': 10}, headers=h)
    d = client.get(f'/backfills/{jid}', headers=h).json()
    assert d['state'] == 'stale'
    assert d['stale']['changes'][0]['kind'] == 'settlement_added'
    r = client.post(f'/backfills/{jid}/post', headers=h)
    assert r.status_code == 409


def test_excluded_voucher_cover_edit_invalidates(client, clean_db):
    h = auth(client)
    with Session() as s:
        add_settlement(s, 'YM-001', None)
        add_settlement(s, 'YM-002', None)
        add_settlement(s, 'YM-003', None)
        add_settlement(s, 'YM-004', None)
        add_voucher(s, 'PV-001', 9000, ['YM-001', 'YM-002'])
        # 覆盖清单外缺失场次 YM-003，整条被排除
        add_voucher(s, 'PV-002', 4500, ['YM-001', 'YM-003'])
    r = client.post('/backfills', json={'name': '排除凭证', 'codes': ['YM-001', 'YM-002'],
                                        'voucher_ids': [1, 2]}, headers=h)
    jid = r.json()['id']
    client.post(f'/backfills/{jid}/solve', headers=h)
    run_job(clean_db)
    d = client.get(f'/backfills/{jid}', headers=h).json()
    assert d['verdict'] == 'multi'
    assert d['summary']['voucher_count'] == 1
    assert [v['id'] for v in d['excluded']] == [2]
    # 修改被排除凭证的覆盖清单，使其落入清单范围，结论可能变化
    r = client.put('/vouchers/2', json={'code': 'PV-002', 'total_cents': 4500, 'note': 'x',
                                        'cover_codes': ['YM-002']}, headers=h)
    assert r.status_code == 200, r.text
    d = client.get(f'/backfills/{jid}', headers=h).json()
    assert d['state'] == 'stale'
    assert any(c['kind'] == 'voucher_covers_changed' for c in d['stale']['changes'])

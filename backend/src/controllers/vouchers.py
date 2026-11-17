"""凭证的录入、修订与查询。修订递增 rev，供补账过期校验使用。"""
from fastapi import HTTPException

from src.models.entities import Settlement, Voucher, VoucherCover
from src.services.backfill import now_iso, voucher_dict
from src.constants.enums import CHANNELS, VENUES
from sqlalchemy import func


def _resolve_covers(s, codes):
    codes = [c.strip() for c in codes if c and c.strip()]
    if not codes:
        raise HTTPException(422, '凭证至少要覆盖一场')
    if len(set(codes)) != len(codes):
        raise HTTPException(422, '凭证覆盖场次不能重复')
    rows = s.query(Settlement).filter(Settlement.code.in_(codes)).all()
    found = {r.code: r for r in rows}
    missing = [c for c in codes if c not in found]
    if missing:
        raise HTTPException(422, detail={'message': '凭证覆盖了不存在的场次编号', 'codes': missing})
    return [found[c] for c in codes]


def _write_covers(s, v, rows):
    s.query(VoucherCover).filter(VoucherCover.voucher_id == v.id).delete()
    s.flush()
    for r in rows:
        s.add(VoucherCover(voucher_id=v.id, settlement_id=r.id))


def _payload(s, v):
    rows = (s.query(Settlement).join(VoucherCover, VoucherCover.settlement_id == Settlement.id)
            .filter(VoucherCover.voucher_id == v.id).order_by(Settlement.id).all())
    return voucher_dict(s, v, {v.id: [r.id for r in rows]}, {r.id: r.code for r in rows})


def list_vouchers(s):
    vouchers = s.query(Voucher).order_by(Voucher.id).all()
    from src.services.backfill import cover_map
    covers = cover_map(s, [v.id for v in vouchers])
    rows = s.query(Settlement).all()
    code_of = {r.id: r.code for r in rows}
    return [voucher_dict(s, v, covers, code_of) for v in vouchers]


def get_one(s, i):
    v = s.get(Voucher, i)
    if not v:
        raise HTTPException(404, '凭证不存在')
    return _payload(s, v)


def create(s, data):
    if s.query(Voucher).filter_by(code=data.code).first():
        raise HTTPException(409, '凭证编号重复')
    rows = _resolve_covers(s, data.cover_codes)
    v = Voucher(code=data.code, total_cents=data.total_cents, note=data.note or '',
                rev=1, created=now_iso(), updated=now_iso())
    s.add(v)
    s.flush()
    _write_covers(s, v, rows)
    s.commit()
    return _payload(s, v)


def update(s, i, data):
    v = s.get(Voucher, i)
    if not v:
        raise HTTPException(404, '凭证不存在')
    dup = s.query(Voucher).filter(Voucher.code == data.code, Voucher.id != i).first()
    if dup:
        raise HTTPException(409, '凭证编号重复')
    rows = _resolve_covers(s, data.cover_codes)
    v.code = data.code
    v.total_cents = data.total_cents
    v.note = data.note or ''
    v.rev = int(v.rev or 0) + 1
    v.updated = now_iso()
    _write_covers(s, v, rows)
    s.commit()
    return _payload(s, v)

from src.config.database import Base, engine, Session
from src.models.entities import Settlement, User, Voucher, VoucherCover
from src.utils.security import hash_password
from src.constants.enums import CHANNELS, VENUES
import random
from datetime import date, timedelta


def seed():
    Base.metadata.create_all(engine)
    rng = random.Random(3112)
    with Session() as s:
        for role in ('admin', 'analyst', 'viewer'):
            email = role + '@demo.local'
            if not s.query(User).filter_by(email=email).first():
                s.add(User(email=email, role=role, password=hash_password('Demo123!')))
        for i in range(180):
            code = f'YM-{i+1:03}'
            seats = [800, 240, 120][i % 3]
            checked = rng.randint(seats // 2, seats)
            gross = checked * rng.choice([8000, 12000, 18000])
            refund = rng.randint(0, checked // 10) * 8000
            fee = int((gross - refund) * [0, .07, .03][i % 3])
            if not s.query(Settlement).filter_by(code=code).first():
                s.add(Settlement(code=code, day=(date(2026, 9, 1) + timedelta(days=i // 3)).isoformat(),
                                 venue=VENUES[i % 3], play=['渡口来信', '星河漫游', '一间书房', '冬日序曲'][i % 4],
                                 channel=CHANNELS[(i // 3) % 3], gross_cents=gross, refund_cents=refund,
                                 fee_cents=fee, seats=seats, checked=checked, rev=1))
        # 演示缺失票款与交叉汇总凭证：仅当尚无缺失场次时创建，重复执行不覆盖人工修订
        if not s.query(Settlement).filter(Settlement.gross_cents.is_(None)).first():
            specs = [
                ('YM-181', '2026-11-15', VENUES[0], '渡口来信', CHANNELS[0], 100, 80),
                ('YM-182', '2026-11-15', VENUES[1], '星河漫游', CHANNELS[1], 240, 200),
                ('YM-183', '2026-11-16', VENUES[2], '一间书房', CHANNELS[2], 120, 96),
            ]
            for code, day, venue, play, channel, seats, checked in specs:
                if not s.query(Settlement).filter_by(code=code).first():
                    s.add(Settlement(code=code, day=day, venue=venue, play=play, channel=channel,
                                     gross_cents=None, refund_cents=0, fee_cents=0,
                                     seats=seats, checked=checked, rev=1))
            s.commit()
            known = sum(r.gross_cents for r in s.query(Settlement).filter(
                Settlement.code.in_(['YM-001', 'YM-002', 'YM-003'])).all())
            # 三条交叉凭证让 YM-181/182/183 唯一可解；只选 PV-001 预演则呈现多解上下界
            vouchers = [
                ('PV-001', known + 50000 + 60000, '11月15日跨场馆票房回单',
                 ['YM-001', 'YM-002', 'YM-003', 'YM-181', 'YM-182']),
                ('PV-002', 60000 + 70000, '11月16日小剧场与排练厅回单',
                 ['YM-182', 'YM-183']),
                ('PV-003', 50000 + 70000, '11月15日与16日跨场次汇总',
                 ['YM-181', 'YM-183']),
            ]
        else:
            vouchers = []
        for code, total, note, covers in vouchers:
            if s.query(Voucher).filter_by(code=code).first():
                continue
            v = Voucher(code=code, total_cents=total, note=note, rev=1)
            s.add(v)
            s.flush()
            rows = s.query(Settlement).filter(Settlement.code.in_(covers)).all()
            for r in rows:
                s.add(VoucherCover(voucher_id=v.id, settlement_id=r.id))
        s.commit()
        print({'users': s.query(User).count(), 'settlements': s.query(Settlement).count(),
               'vouchers': s.query(Voucher).count(),
               'missing': s.query(Settlement).filter(Settlement.gross_cents.is_(None)).count()})


if __name__ == '__main__':
    seed()

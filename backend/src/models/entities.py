from sqlalchemy import Column,Integer,BigInteger,String,JSON,Text,ForeignKey
from src.config.database import Base
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True);email=Column(String(100),unique=True,nullable=False);password=Column(String(200));role=Column(String(20))
class Settlement(Base):
 __tablename__='settlements'
 id=Column(Integer,primary_key=True);code=Column(String(60),unique=True,nullable=False);day=Column(String(10));venue=Column(String(40));play=Column(String(60));channel=Column(String(30));gross_cents=Column(BigInteger);refund_cents=Column(BigInteger);fee_cents=Column(BigInteger);seats=Column(Integer);checked=Column(Integer);rev=Column(Integer,nullable=False,default=0,server_default='0')
class Voucher(Base):
 __tablename__='vouchers'
 id=Column(Integer,primary_key=True);code=Column(String(60),unique=True,nullable=False);total_cents=Column(BigInteger,nullable=False);note=Column(Text,default='');rev=Column(Integer,nullable=False,default=0,server_default='0');created=Column(String(40));updated=Column(String(40))
class VoucherCover(Base):
 __tablename__='voucher_covers'
 voucher_id=Column(Integer,ForeignKey('vouchers.id'),primary_key=True);settlement_id=Column(Integer,ForeignKey('settlements.id'),primary_key=True,index=True)
class Backfill(Base):
 __tablename__='backfills'
 id=Column(Integer,primary_key=True);name=Column(String(100));state=Column(String(20),default='draft');run_token=Column(String(64),default='');attempts=Column(Integer,nullable=False,default=0,server_default='0');result=Column(JSON,nullable=True);error=Column(Text,default='');created=Column(String(40));created_by=Column(String(100));solved_at=Column(String(40));posted_at=Column(String(40))
class Pivot(Base):
 __tablename__='pivots'
 id=Column(Integer,primary_key=True);name=Column(String(100));row_dim=Column(String(30));col_dim=Column(String(30));state=Column(String(20),default='queued');result=Column(JSON,nullable=True);error=Column(Text,default='')
class Review(Base):
 __tablename__='reviews'
 id=Column(Integer,primary_key=True);pivot_id=Column(Integer,ForeignKey('pivots.id'));row_key=Column(String(100));col_key=Column(String(100));book_cents=Column(BigInteger);expected_cents=Column(BigInteger);difference_cents=Column(BigInteger);note=Column(Text);status=Column(String(20),default='draft')
class Audit(Base):
 __tablename__='audits'
 id=Column(Integer,primary_key=True);actor=Column(String(100));action=Column(String(200));created=Column(String(40))

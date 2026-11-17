from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from src.models.entities import Settlement
from src.services.business import dump
def _code_day(v):return v.isoformat() if hasattr(v,'isoformat') else v
def create_missing(s,data):
 if (data.get('refund_cents') or 0)+(data.get('fee_cents') or 0)>0:raise HTTPException(422,'票款缺失时不能带退款或渠道费；请先录入完整金额')
 if s.query(Settlement).filter_by(code=data['code']).first():raise HTTPException(409,'场次编号重复，一场次仅一条结算')
 r=Settlement(code=data['code'],day=_code_day(data['day']),venue=data['venue'],play=data['play'],channel=data['channel'],gross_cents=None,refund_cents=0,fee_cents=0,seats=data.get('seats',0),checked=data.get('checked',0),rev=1)
 s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'场次编号重复，一场次仅一条结算')
 return dump(r)
def save(s,data,i=None):
 r=s.get(Settlement,i) if i else Settlement()
 if not r:raise HTTPException(404,'结算场次不存在')
 d=data.model_dump();d['day']=d['day'].isoformat()
 if i and d.get('gross_cents') is None and d['refund_cents']+d['fee_cents']>0:raise HTTPException(422,'票款缺失时不能带退款或渠道费；请先录入完整金额')
 before=r.rev or 0
 for k,v in d.items():setattr(r,k,v)
 r.rev=before+1
 s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'场次编号重复，一场次仅一条结算')
 return dump(r)

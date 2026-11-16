from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from src.models.entities import Settlement
from src.services.business import dump
def save(s,data,i=None):
 r=s.get(Settlement,i) if i else Settlement()
 if not r:raise HTTPException(404,'结算场次不存在')
 d=data.model_dump();d['day']=d['day'].isoformat()
 for k,v in d.items():setattr(r,k,v)
 s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'场次编号重复，一场次仅一条结算')
 return dump(r)

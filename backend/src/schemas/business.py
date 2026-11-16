from pydantic import BaseModel,Field,model_validator
from typing import Literal
from datetime import date
Dimension=Literal['venue','play','channel','month']
class Login(BaseModel):email:str;password:str
class SettlementInput(BaseModel):
 code:str=Field(min_length=1,max_length=60)
 day:date
 venue:Literal['云幕大剧场','河畔小剧场','星光排练厅']
 play:str=Field(min_length=1,max_length=60)
 channel:Literal['剧院直营','票务合作','团体专场']
 gross_cents:int=Field(ge=0,le=100000000)
 refund_cents:int=Field(ge=0,le=100000000)
 fee_cents:int=Field(ge=0,le=100000000)
 seats:int=Field(ge=0,le=10000)
 checked:int=Field(ge=0,le=10000)
 @model_validator(mode='after')
 def consistent(self):
  if self.refund_cents+self.fee_cents>self.gross_cents:raise ValueError('退款与渠道费合计不得超过票款')
  if self.checked>self.seats:raise ValueError('核验人数不得超过座位数')
  return self
class PivotInput(BaseModel):
 name:str=Field(min_length=2,max_length=100)
 row_dim:Dimension='venue'
 col_dim:Dimension='channel'
 @model_validator(mode='after')
 def dimensions_differ(self):
  if self.row_dim==self.col_dim:raise ValueError('行列维度不能相同')
  return self
class ReviewInput(BaseModel):
 pivot_id:int=Field(gt=0)
 row_key:str=Field(min_length=1,max_length=100)
 col_key:str=Field(min_length=1,max_length=100)
 expected_cents:int=Field(ge=0,le=1000000000000)
 note:str=Field(min_length=8,max_length=2000)
class ReviewEdit(BaseModel):
 expected_cents:int=Field(ge=0,le=1000000000000)
 note:str=Field(min_length=8,max_length=2000)

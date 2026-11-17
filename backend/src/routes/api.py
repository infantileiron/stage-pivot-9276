import os,json
import redis
from fastapi import APIRouter,Depends,HTTPException,Response
from src.config.database import db
from src.models.entities import User,Settlement,Pivot,Review,Audit
from src.schemas.business import Login,SettlementInput,SettlementMissing,PivotInput,ReviewInput,ReviewEdit,VoucherInput,BackfillCreate,BackfillUpdate
from src.middlewares.auth import current,editor,admin
from src.utils.security import verify,token
from src.services.business import audit,dump
from src.services.settlements import save,create_missing
from src.services.analysis import key
from src.controllers.business import ensure_pivot,ensure_review,cell
from src.controllers import vouchers as vc
from src.controllers import backfills as bf
router=APIRouter()
@router.post('/auth/login')
def login(data:Login,s=Depends(db)):
 u=s.query(User).filter_by(email=data.email).first()
 if not u or not verify(data.password,u.password):raise HTTPException(401,'账号或密码错误')
 return {'token':token(u),'user':dump(u)}
@router.get('/me')
def me(u=Depends(current)):return dump(u)
@router.get('/settlements')
def settlements(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Settlement).order_by(Settlement.id).all()]
@router.post('/settlements')
def create(data:SettlementInput,u=Depends(editor),s=Depends(db)):
 r=save(s,data);audit(s,u,'录入结算 '+r['code']);return r
@router.post('/settlements/missing')
def create_missing_settlement(data:SettlementMissing,u=Depends(editor),s=Depends(db)):
 r=create_missing(s,data.model_dump());audit(s,u,'登记缺失票款 '+r['code']);return r
@router.put('/settlements/{i}')
def edit(i:int,data:SettlementInput,u=Depends(editor),s=Depends(db)):
 r=save(s,data,i);audit(s,u,'修订结算 '+str(i));return r
@router.get('/vouchers')
def vouchers(u=Depends(current),s=Depends(db)):return vc.list_vouchers(s)
@router.post('/vouchers')
def create_voucher(data:VoucherInput,u=Depends(editor),s=Depends(db)):
 r=vc.create(s,data);audit(s,u,'录入凭证 '+r['code']);return r
@router.put('/vouchers/{i}')
def edit_voucher(i:int,data:VoucherInput,u=Depends(editor),s=Depends(db)):
 r=vc.update(s,i,data);audit(s,u,'修订凭证 '+str(i));return r
@router.get('/vouchers/{i}')
def voucher_detail(i:int,u=Depends(current),s=Depends(db)):return vc.get_one(s,i)
@router.get('/backfills')
def backfills(u=Depends(current),s=Depends(db)):return [bf.dump_job(b) for b in s.query(bf.Backfill).order_by(bf.Backfill.id.desc()).all()]
@router.post('/backfills')
def create_backfill(data:BackfillCreate,u=Depends(editor),s=Depends(db)):return bf.dump_job(bf.create_job(s,data,u))
@router.post('/backfills/preview')
def preview_backfill(data:BackfillCreate,u=Depends(editor),s=Depends(db)):return bf.preview(s,data)
@router.get('/backfills/{i}')
def backfill_detail(i:int,u=Depends(current),s=Depends(db)):return bf.dump_job(bf.get_job(s,i),include_snapshot=True)
@router.put('/backfills/{i}')
def update_backfill(i:int,data:BackfillUpdate,u=Depends(editor),s=Depends(db)):
 b=bf.update_job(s,i,data);audit(s,u,'修改补账草稿 '+str(i));return bf.dump_job(b)
@router.post('/backfills/{i}/solve')
def solve_backfill(i:int,u=Depends(editor),s=Depends(db)):
 b=bf.solve_job(s,i);audit(s,u,'提交补账求解 '+str(i));return bf.dump_job(b)
@router.post('/backfills/{i}/retry')
def retry_backfill(i:int,u=Depends(editor),s=Depends(db)):
 b,idem=bf.retry_job(s,i);audit(s,u,'重试补账 '+str(i));return {**bf.dump_job(b),'idempotent':idem}
@router.post('/backfills/{i}/post')
def post_backfill(i:int,u=Depends(editor),s=Depends(db)):
 b,idem=bf.post_job(s,i);audit(s,u,'入账补账 '+str(i)+('（重复请求，未重复写账）' if idem else ''));return {**bf.dump_job(b),'idempotent':idem}
@router.get('/backfills/{i}/export')
def export_backfill(i:int,u=Depends(current),s=Depends(db)):return bf.export_response(s,i,as_attachment=True)
@router.get('/pivots')
def pivots(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Pivot).order_by(Pivot.id.desc()).all()]
@router.post('/pivots')
def submit(data:PivotInput,u=Depends(editor),s=Depends(db)):
 rows=[dump(r) for r in s.query(Settlement).order_by(Settlement.id).all()]
 r=Pivot(**data.model_dump(),result={'snapshot':rows});s.add(r);s.commit()
 try:redis.Redis.from_url(os.environ['REDIS_URL']).rpush('stage:pivots',r.id)
 except redis.RedisError:r.state='failed';r.error='队列暂不可用，请恢复后新建透视';s.commit();raise HTTPException(503,r.error)
 audit(s,u,'提交透视 '+str(r.id));return dump(r)
@router.get('/pivots/{i}')
def detail(i:int,u=Depends(current),s=Depends(db)):return dump(ensure_pivot(s,i))
@router.get('/pivots/{i}/drill')
def drill(i:int,row_key:str,col_key:str,u=Depends(current),s=Depends(db)):
 r,c=cell(s,i,row_key,col_key)
 return {'cell':c,'rows':[x for x in r.result['snapshot'] if key(x,r.row_dim)==row_key and key(x,r.col_dim)==col_key]}
@router.get('/pivots/{i}/export')
def export(i:int,u=Depends(current),s=Depends(db)):
 r=ensure_pivot(s,i)
 if r.state!='done':raise HTTPException(409,'仅成功透视可以导出')
 return Response(json.dumps(dump(r),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="stage-pivot-{i}.json"'})
@router.get('/reviews')
def reviews(u=Depends(current),s=Depends(db)):return [dump(r) for r in s.query(Review).order_by(Review.id.desc()).all()]
@router.post('/reviews')
def review(data:ReviewInput,u=Depends(editor),s=Depends(db)):
 run,c=cell(s,data.pivot_id,data.row_key,data.col_key)
 r=Review(**data.model_dump(),book_cents=c['net_cents'],difference_cents=data.expected_cents-c['net_cents']);s.add(r);s.commit();audit(s,u,'起草差异复核 '+str(r.id));return dump(r)
@router.put('/reviews/{i}')
def edit_review(i:int,data:ReviewEdit,u=Depends(editor),s=Depends(db)):
 r=ensure_review(s,i)
 if r.status!='draft':raise HTTPException(409,'已签核复核单不可覆盖，请新建复核')
 r.expected_cents=data.expected_cents;r.difference_cents=data.expected_cents-r.book_cents;r.note=data.note;s.commit();audit(s,u,'修改差异复核 '+str(i));return dump(r)
@router.post('/reviews/{i}/approve')
def approve(i:int,u=Depends(admin),s=Depends(db)):
 r=ensure_review(s,i)
 if r.status!='draft':raise HTTPException(409,'已签核复核单不可重复签核')
 r.status='approved';s.commit();audit(s,u,'签核差异复核 '+str(i));return dump(r)
@router.get('/audits')
def audits(u=Depends(admin),s=Depends(db)):return [dump(r) for r in s.query(Audit).order_by(Audit.id.desc()).limit(100)]

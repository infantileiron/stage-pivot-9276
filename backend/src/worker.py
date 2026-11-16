import os,time,json,logging
from pathlib import Path
import redis
from src.config.database import Session
from src.models.entities import Pivot
from src.services.analysis import pivot
q=redis.Redis.from_url(os.environ['REDIS_URL'])
with Session() as s:
 for r in s.query(Pivot).filter(Pivot.state.in_(['queued','running'])).all():r.state='queued';q.rpush('stage:pivots',r.id)
 s.commit()
while True:
 Path('/tmp/heartbeat').touch()
 try:
  item=q.blpop('stage:pivots',timeout=3)
  if not item:continue
  with Session() as s:
   r=s.get(Pivot,int(item[1]))
   if not r or r.state!='queued':continue
   snapshot=r.result['snapshot'];r.state='running';s.commit()
   try:
    r.result={**pivot(snapshot,r.row_dim,r.col_dim),'snapshot':snapshot};r.state='done';Path('/artifacts/'+str(r.id)+'.json').write_text(json.dumps(r.result,ensure_ascii=False))
   except Exception as e:r.state='failed';r.error=str(e)
   s.commit()
 except Exception:logging.exception('worker');time.sleep(2)

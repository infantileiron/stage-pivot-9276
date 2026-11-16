import json,hashlib
DIMENSIONS={'venue','play','channel','month'}
SUMS=('gross_cents','refund_cents','fee_cents','seats','checked')
def key(row,dim):return row['day'][:7] if dim=='month' else row[dim]
def aggregate(rows):
 out={k:sum(r[k] for r in rows) for k in SUMS};out['count']=len(rows)
 out['net_cents']=out['gross_cents']-out['refund_cents']-out['fee_cents']
 out['occupancy']=out['checked']/out['seats'] if out['seats'] else None
 return out
def pivot(rows,row_dim,col_dim):
 if row_dim not in DIMENSIONS or col_dim not in DIMENSIONS or row_dim==col_dim:raise ValueError('请选择两个不同的白名单维度')
 if not rows:raise ValueError('没有结算记录，请先录入场次')
 if len({r['code'] for r in rows})!=len(rows):raise ValueError('场次编号重复，无法保证结算守恒')
 row_keys=sorted({key(r,row_dim) for r in rows});col_keys=sorted({key(r,col_dim) for r in rows})
 cells=[{'row_key':a,'col_key':b,**aggregate([r for r in rows if key(r,row_dim)==a and key(r,col_dim)==b])} for a in row_keys for b in col_keys]
 row_totals=[{'key':a,**aggregate([r for r in rows if key(r,row_dim)==a])} for a in row_keys]
 col_totals=[{'key':b,**aggregate([r for r in rows if key(r,col_dim)==b])} for b in col_keys]
 total=aggregate(rows)
 conserved=all(sum(x[k] for x in cells)==sum(x[k] for x in row_totals)==sum(x[k] for x in col_totals)==total[k] for k in (*SUMS,'net_cents','count'))
 if not conserved:raise ValueError('行列总计不守恒，请核对输入')
 return dict(row_keys=row_keys,col_keys=col_keys,cells=cells,row_totals=row_totals,col_totals=col_totals,total=total,conserved=conserved,checksum=hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),method='金额以整数分求和；净结算=票款−退款−渠道费。上座率=总核验人数/总座位，零座位显示未定义；不平均各场百分比。每条为独立场次，渠道是主售票渠道，不拆分同场座位。月份按业务日期YYYY-MM；下钻始终读取冻结快照。')

export const money=c=>(c===null||c===undefined?'缺失':(c/100).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}));export const pct=v=>v===null?'未定义':(v*100).toFixed(1)+'%';
export const cents=c=>c===null||c===undefined?'缺失':String(c);

export const money=c=>(c/100).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});export const pct=v=>v===null?'未定义':(v*100).toFixed(1)+'%';

export function money(value,currency="GBP"){
 if(value===null||value===undefined||!Number.isFinite(Number(value)))return "—";
 return new Intl.NumberFormat("en-GB",{style:"currency",currency,maximumFractionDigits:2}).format(Number(value));
}
export function pct(value){
 return value===null||value===undefined||!Number.isFinite(Number(value))?"—":(value>=0?"+":"")+Number(value).toFixed(2)+"%";
}
export function GrowthChart({points=[],currency="GBP",height=260}){
 const rows=points.filter(p=>Number.isFinite(Number(p.value))).slice(-365);
 if(rows.length<2)return <div className="chart-empty"><span className="empty-icon">⌁</span><b>Your growth graph starts here</b><p>Record holdings, then refresh prices. Your daily history will build automatically.</p></div>;
 const w=900,h=height,pad=32;
 const values=rows.map(p=>Number(p.value));
 const min=Math.min(...values)*.98,max=Math.max(...values)*1.02;
 const scale=(x)=>h-pad-((x-min)/(max-min||1))*(h-2*pad);
 const pts=rows.map((x,i)=>[(i/(rows.length-1))*(w-2*pad)+pad,scale(Number(x.value))]);
 const path=pts.map(([x,y],i)=>(i?"L":"M")+x.toFixed(2)+" "+y.toFixed(2)).join(" ");
 const area=path+" L "+(w-pad)+" "+(h-pad)+" L "+pad+" "+(h-pad)+" Z";
 return <div className="chart-wrap">
  <svg viewBox={"0 0 "+w+" "+h} role="img" aria-label={"Value from "+rows[0].date+" to "+rows.at(-1).date} preserveAspectRatio="none">
   <defs><linearGradient id="curve-gradient" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="#2cdacb" stopOpacity=".25"/><stop offset="100%" stopColor="#2cdacb" stopOpacity="0"/></linearGradient></defs>
   {[.25,.5,.75].map(p=><line key={p} x1={pad} x2={w-pad} y1={pad+(h-2*pad)*p} y2={pad+(h-2*pad)*p} stroke="#27354b" strokeDasharray="4 7"/>)}
   <path d={area} fill="url(#curve-gradient)"/><path d={path} fill="none" stroke="#2cdacb" strokeWidth="3" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke"/>
   <circle cx={pts.at(-1)[0]} cy={pts.at(-1)[1]} r="5" fill="#2cdacb"/>
  </svg>
  <div className="chart-labels"><span>{rows[0].date}</span><strong>{money(rows.at(-1).value,currency)}</strong><span>{rows.at(-1).date}</span></div>
 </div>;
}

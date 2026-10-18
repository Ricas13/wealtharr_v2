// Aggregate equally denominated daily portfolio snapshots, carrying the latest
// known value per account. Never treat a missing account as zero.
export function aggregateSeries(snapshots,portfolioIds){
 const ids=new Set(portfolioIds);
 if(!ids.size)return [];
 const days=[...new Set(snapshots.map(s=>String(s.as_of).slice(0,10)))].sort();
 const latest=new Map(),result=[];
 for(const day of days){
   for(const s of snapshots){
     if(String(s.as_of).slice(0,10)===day&&ids.has(s.portfolio_id))latest.set(s.portfolio_id,Number(s.value));
   }
   if(latest.size===ids.size)result.push({date:day,value:[...latest.values()].reduce((a,b)=>a+b,0)});
 }
 return result;
}
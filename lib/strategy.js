// Conservative 9Sig-style advisory model, not a transcription of every workbook
// branch. No trades are placed, and suggestions are always reviewed by the owner.
export const GROWTH=0.09, SAFE=0.40, RESERVE=0.10, FALL=0.30;
const n=(v)=>Number(v||0);
const r=(v)=>Math.round(v*100)/100;
export function quarterIndex(date){
 const d=new Date(date+"T12:00:00Z");
 return d.getUTCFullYear()*4+Math.floor(d.getUTCMonth()/3);
}
export function previousQuarterKey(asOf) {
 const i=quarterIndex(asOf)-1;
 return Math.floor(i/4)+"-Q"+(i%4+1);
}
export function generateAdvice({portfolio,state,valuation,equityPrice,safePrice,history,asOf,priorSignals=[]}){
 if(!valuation||!Number.isFinite(equityPrice)||!Number.isFinite(safePrice)||equityPrice<=0||safePrice<=0)return [];
 const advice=[];
 const opening=state.openingStockValue||0;
 const startDate=state.strategyStartDate || new Date(portfolio.created_at).toISOString().slice(0,10);
 const currentQ=quarterIndex(asOf),startQ=quarterIndex(startDate);
 const closes=history.filter(x=>x.date<=asOf&&x.date>=startDate)
    .map(x=>({date:x.date,price:n(x.price)})).filter(x=>x.price>0);
 if(closes.length>=2){
   const peak=closes.reduce((a,b)=>b.price>a.price?b:a,closes[0]);
   const latest=closes[closes.length-1];
   const drawdown=1-latest.price/peak.price;
   const below=drawdown>=FALL && latest.date>peak.date;
   const signalKey="30down:"+peak.date;
   if(below&&!priorSignals.some(x=>x.signal_key===signalKey)&&state.equity.quantity>0){
     const quantity=Math.floor(state.equity.quantity*5000)/10000;
     if(quantity>0)advice.push({kind:"THIRTY_DOWN",key:signalKey,
       title:"30% drop: review a half-position sale",
       explanation:"Reference market close is "+r(drawdown*100)+"% below its highest observed close since this portfolio began ("+peak.date+"). Spreadsheet-style 30-down warning. Verify the reference instrument, the recovery rules and costs before trading.",
       actions:[{side:"SELL",symbol:portfolio.equity_symbol,quantity,estimatedAmount:r(quantity*equityPrice)}]});
   }
 }
 if(currentQ<=startQ||opening<=0)return advice;
 const key="quarter:"+previousQuarterKey(asOf);
 if(priorSignals.some(x=>x.signal_key===key))return advice;
 const steps=currentQ-startQ, target=opening*Math.pow(1+GROWTH,steps/4);
 const difference=target-valuation.equityValue;
 const tolerance=Math.max(1,valuation.total*0.005);
 let actions=[], explain="";
 if(Math.abs(difference)<=tolerance){
   explain="Equity sleeve is within 0.5% of portfolio value (minimum 1 currency unit) of the 9% annual compound target. No trade suggested.";
 } else if(difference<0){
   const q=Math.floor((-difference/equityPrice)*10000)/10000;
   if(q>0) actions=[{side:"SELL",symbol:portfolio.equity_symbol,quantity:q,estimatedAmount:r(q*equityPrice)}];
   explain="Equity sleeve is above its quarterly signal line. Review selling the excess and allocating the proceeds to the safe sleeve.";
 } else {
   const cashAvailable=Math.max(0,state.cash);
   const safeAvailable=Math.max(0,valuation.safeValue*(1-RESERVE));
   const fund=Math.min(difference,cashAvailable+safeAvailable);
   const safeSell=Math.max(0,fund-cashAvailable);
   if(safeSell>0){
     const q=Math.floor(safeSell/safePrice*10000)/10000;
     if(q>0)actions.push({side:"SELL",symbol:portfolio.safe_symbol,quantity:q,estimatedAmount:r(q*safePrice)});
   }
   const budget=cashAvailable+actions.reduce((a,x)=>a+x.estimatedAmount,0);
   const q=Math.floor(Math.min(difference,budget)/equityPrice*10000)/10000;
   if(q>0)actions.push({side:"BUY",symbol:portfolio.equity_symbol,quantity:q,estimatedAmount:r(q*equityPrice)});
   explain=fund<difference?"Equity is below the quarterly signal line, but available cash and the 10% protected safe-asset reserve limit the proposed buy.":"Equity is below the quarterly signal line. Review funding a buy from available cash and the safe sleeve.";
 }
 advice.push({kind:"QUARTERLY",key,title:"Quarterly 9Sig review · "+previousQuarterKey(asOf),
  explanation:explain+" Signal line: "+r(target)+" "+portfolio.currency+
    ". Model assumptions: 9% compounded annually, 40% initial safe sleeve, 10% safe reserve. This is advisory, not an exact implementation of all spreadsheet exceptions.",
  actions});
 return advice;
}

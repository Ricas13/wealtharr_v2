import "server-only";
import {db} from "./db.js";
const VALID=/^[A-Z0-9^][A-Z0-9.^=\-]{0,24}$/;
export function validSymbol(v){return VALID.test(v);}
export async function yahooDaily(symbol, start="2010-01-01"){
 if(!validSymbol(symbol))throw new Error("Invalid market ticker");
 const url=new URL("https://query1.finance.yahoo.com/v8/finance/chart/"+encodeURIComponent(symbol));
 url.searchParams.set("interval","1d");
 url.searchParams.set("period1",String(Math.floor(Date.parse(start+"T00:00:00Z")/1000)));
 url.searchParams.set("period2",String(Math.floor(Date.now()/1000)+86400));
 const res=await fetch(url,{headers:{Accept:"application/json","User-Agent":"WealtharrPersonalTracker/0.1"},signal:AbortSignal.timeout(14000),cache:"no-store"});
 if(!res.ok)throw new Error("Market data provider HTTP "+res.status);
 const data=await res.json();
 const chart=data?.chart?.result?.[0];
 if(!chart||!chart.timestamp?.length)throw new Error("Market data provider returned no prices");
 const rawCurrency=chart.meta?.currency;
 const currency=rawCurrency==="GBp"||rawCurrency==="GBX"?"GBP":rawCurrency;
 if(!["GBP","USD"].includes(currency))throw new Error("Unsupported quote currency");
 const divisor=rawCurrency==="GBp"||rawCurrency==="GBX"?100:1;
 const series=chart.timestamp.map((time,i)=>({
   date:new Date(time*1000).toISOString().slice(0,10),
   price:Number(chart.indicators?.quote?.[0]?.close?.[i])/divisor
 })).filter(p=>Number.isFinite(p.price)&&p.price>0);
 if(!series.length)throw new Error("No valid closing prices returned");
 const byDay=new Map(series.map(p=>[p.date,p]));
 return {symbol,currency,series:[...byDay.values()].sort((a,b)=>a.date.localeCompare(b.date))};
}
export async function updateSymbol(symbol,{history=false}={}){
 const sql=db();
 const [cached]=await sql`SELECT * FROM price_quotes WHERE symbol=${symbol}`;
 const [historyCount]=history?await sql`SELECT count(*)::int AS count FROM market_daily WHERE symbol=${symbol}`:[{count:1}];
 if(cached&&Date.now()-new Date(cached.fetched_at).getTime()<12*60*60*1000&&historyCount.count>0)return;
 const fresh=await yahooDaily(symbol,history?"2010-01-01":new Date(Date.now()-30*86400000).toISOString().slice(0,10));
 const latest=fresh.series.at(-1);
 await sql`INSERT INTO price_quotes(symbol,price,currency,as_of,fetched_at) VALUES(${symbol},${latest.price},${fresh.currency},${latest.date},now()) ON CONFLICT(symbol) DO UPDATE SET price=excluded.price,currency=excluded.currency,as_of=excluded.as_of,fetched_at=now()`;
 if(history){
   for(let i=0;i<fresh.series.length;i+=350){
     const parts=fresh.series.slice(i,i+350).map(p=>[symbol,p.date,p.price]);
     await sql`INSERT INTO market_daily(symbol,as_of,price) VALUES ${sql(parts)} ON CONFLICT(symbol,as_of) DO UPDATE SET price=excluded.price`;
   }
 }
}

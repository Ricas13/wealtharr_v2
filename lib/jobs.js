import "server-only";
import {db} from "./db.js";
import {updateSymbol} from "./market.js";
import {portfolioState} from "./portfolio.js";
import {generateAdvice} from "./strategy.js";
import {deliverPending} from "./notifications.js";
export async function syncOne(portfolio){
 const sql=db();
 // Any missing quote or currency mismatch prevents a valuation and signal.
 await updateSymbol(portfolio.equity_symbol);
 await updateSymbol(portfolio.safe_symbol);
 await updateSymbol("TQQQ",{history:true});
 const {state,valuation,quotes}=await portfolioState(portfolio);
 if(!valuation)throw new Error("Quote missing, stale, or quoted in a different currency");
 const today=new Date().toISOString().slice(0,10);
 const start=new Date(portfolio.created_at).toISOString().slice(0,10);
 const reference=await sql`SELECT as_of AS date,price FROM market_daily WHERE symbol='TQQQ' AND as_of>=${start} ORDER BY as_of`;
 if(!reference.length)throw new Error("Missing Nasdaq 3x reference history");
 const historical=reference.map(x=>({date:new Date(x.date).toISOString().slice(0,10),price:Number(x.price)}));
 const previous=await sql`SELECT signal_key FROM signals WHERE portfolio_id=${portfolio.id}`;
 const advice=generateAdvice({portfolio,state,valuation,
   equityPrice:Number(quotes[portfolio.equity_symbol].price),
   safePrice:Number(quotes[portfolio.safe_symbol].price),
   history:historical,asOf:valuation.asOf,priorSignals:previous});
 await sql.begin(async tx=>{
   await tx`INSERT INTO snapshots(portfolio_id,as_of,value,net_invested,cash,equity_value,safe_value) VALUES(${portfolio.id},${valuation.asOf},${valuation.total},${valuation.invested},${state.cash},${valuation.equityValue},${valuation.safeValue}) ON CONFLICT(portfolio_id,as_of) DO UPDATE SET value=excluded.value,net_invested=excluded.net_invested,cash=excluded.cash,equity_value=excluded.equity_value,safe_value=excluded.safe_value`;
   const [owner]=await tx`SELECT u.id,u.notify_email,u.notify_discord FROM users u WHERE u.id=${portfolio.user_id}`;
   for(const a of advice){
     const created=await tx`INSERT INTO signals(portfolio_id,kind,signal_key,as_of,title,explanation,suggested_actions) VALUES(${portfolio.id},${a.kind},${a.key},${valuation.asOf},${a.title},${a.explanation},${tx.json(a.actions)}) ON CONFLICT(portfolio_id,signal_key) DO NOTHING RETURNING id`;
     if(created[0]){
       for(const [channel,enabled] of [["email",owner.notify_email],["discord",owner.notify_discord]]){
         if(enabled)await tx`INSERT INTO deliveries(signal_id,user_id,channel) VALUES(${created[0].id},${owner.id},${channel}) ON CONFLICT DO NOTHING`;
       }
     }
   }
 });
 return {asOf:valuation.asOf,valuation,alerts:advice.length,checkedAt:today};
}
export async function runAll(){
 const sql=db(), portfolios=await sql`SELECT * FROM portfolios WHERE archived_at IS NULL ORDER BY id`;
 const [setting]=await sql`SELECT value FROM app_settings WHERE key='nine_sig_enabled'`;
 if(setting?.value==="false")return {paused:true};
 let synced=0,failed=0;
 for(const p of portfolios){
   try{await syncOne(p);synced++;}
   catch(e){console.error("Portfolio sync failed",p.id,e?.name||"Error");failed++;}
 }
 const notifications=await deliverPending();
 return {synced,failed,...notifications};
}

import "server-only";
import {db} from "./db.js";
import {replay,valuePortfolio} from "./ledger.js";
export async function ownedPortfolio(user,id){
 const sql=db(); const found=await sql`SELECT * FROM portfolios WHERE id=${id} AND user_id=${user.id} AND archived_at IS NULL LIMIT 1`;
 return found[0]||null;
}
export async function portfolioState(portfolio){
 const sql=db();
 const rows=await sql`SELECT * FROM ledger WHERE portfolio_id=${portfolio.id} ORDER BY trade_date,id`;
 const state=replay(rows,portfolio.equity_symbol,portfolio.safe_symbol);
 state.openingStockValue=rows.filter(t=>t.kind==="OPENING"&&t.symbol===portfolio.equity_symbol)
   .reduce((sum,t)=>sum+Number(t.quantity)*Number(t.unit_price),0);
 const symbols=[portfolio.equity_symbol,portfolio.safe_symbol];
 const quoteRows=await sql`SELECT * FROM price_quotes WHERE symbol IN ${sql(symbols)}`;
 const quotes=Object.fromEntries(quoteRows.map(q=>[q.symbol,{price:Number(q.price),currency:q.currency,as_of:String(q.as_of).slice(0,10)}]));
 const fresh=Object.values(quotes).length===2 &&
   Object.values(quotes).every(q=>q.currency===portfolio.currency && ((Date.now()-Date.parse(q.as_of+"T00:00:00Z"))/86400000)<8);
 return {rows,state,quotes,valuation:fresh?valuePortfolio(state,quotes,portfolio.equity_symbol,portfolio.safe_symbol):null};
}

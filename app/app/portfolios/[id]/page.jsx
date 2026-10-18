import Link from "next/link";
import {notFound} from "next/navigation";
import {requireUser} from "@/lib/auth.js";
import {ownedPortfolio,portfolioState} from "@/lib/portfolio.js";
import {db} from "@/lib/db.js";
import {GrowthChart,money,pct} from "@/components/chart.jsx";
import {addTransactionAction,acknowledgeSignalAction,refreshPortfolioAction} from "@/app/actions.js";
export default async function PortfolioPage({params,searchParams}){
 const user=await requireUser(),{id}=await params,q=await searchParams;
 const p=await ownedPortfolio(user,id);if(!p)notFound();
 const {rows,state,quotes,valuation}=await portfolioState(p),sql=db();
 const history=await sql`SELECT as_of AS date,value FROM snapshots WHERE portfolio_id=${p.id} ORDER BY as_of`;
 const signals=await sql`SELECT * FROM signals WHERE portfolio_id=${p.id} ORDER BY created_at DESC LIMIT 20`;
 const currency=p.currency;
 return <><div className="page-heading"><div><p className="eyebrow">9SIG / {p.account_type}</p><h1>{p.name}</h1><p>{p.equity_symbol} + {p.safe_symbol} · {currency} · Manually executed trades</p></div><form action={refreshPortfolioAction}><input type="hidden" name="portfolio_id" value={p.id}/><button className="btn btn-outline" type="submit">⟳ Refresh prices</button></form></div>
 {q?.error&&<div className="alert error">{q.error}</div>}{q?.saved&&<div className="alert success">Transaction recorded.</div>}{q?.synced&&<div className="alert success">Market data sync completed.</div>}
 {!valuation&&<div className="alert warning">Waiting for current valid prices for both tickers. Use Refresh prices. If this continues, check that each ticker exists on Yahoo and is quoted in {currency}; no synthetic or cross-currency valuation will be shown.</div>}
 <div className="kpi-grid"><div className="metric-card"><span>Account value</span><strong>{money(valuation?.total,currency)}</strong><small>Quotes as of {valuation?.asOf||"not yet available"}</small></div>
 <div className="metric-card"><span>Profit / loss</span><strong className={valuation?.gain>=0?"positive":"negative"}>{money(valuation?.gain,currency)}</strong><small>{pct(valuation?.gainPct)} excluding deposits</small></div>
 <div className="metric-card"><span>Available cash</span><strong>{money(state.cash,currency)}</strong><small>Based on recorded transactions</small></div>
 <div className="metric-card"><span>Monthly plan</span><strong>{money(p.monthly_contribution,currency)}</strong><small>Reminder amount, not an automatic transfer</small></div></div>
 <section className="panel"><div className="panel-heading"><div><h2>Account performance</h2><p>Daily tracking history</p></div><span className="muted">Last {history.length} price observations</span></div><GrowthChart points={history} currency={currency}/></section>
 <div className="two-panel"><section className="panel"><div className="panel-heading"><h2>Holdings</h2><span className="muted">Shares and cash</span></div>
 {[{title:"Leveraged equity",symbol:p.equity_symbol,data:state.equity,value:valuation?.equityValue},{title:"Safe asset",symbol:p.safe_symbol,data:state.safe,value:valuation?.safeValue}].map(a=><div key={a.symbol} className="holding-row"><div className="holding-icon">{a.symbol[0]}</div><div><strong>{a.symbol}</strong><small>{a.title} · {Number(a.data.quantity).toLocaleString("en-GB",{maximumFractionDigits:4})} shares</small></div><div className="holding-amount"><strong>{money(a.value,currency)}</strong><small>Avg cost {a.data.quantity?money(a.data.cost/a.data.quantity,currency):"—"}</small></div></div>)}
 <div className="holding-row"><div className="holding-icon cash-icon">£</div><div><strong>Cash</strong><small>Uninvested buying power</small></div><div className="holding-amount"><strong>{money(state.cash,currency)}</strong></div></div>
 <p className="data-note">Quote currency and timestamps must match; daily closing prices may be delayed.</p></section>
 <section className="panel"><div className="panel-heading"><h2>Strategy instructions</h2><span className="mini-badge">Review only</span></div>
 {signals.length?signals.map(s=><div className="signal-item" key={s.id}><div className="signal-title"><span className={s.status==="OPEN"?"signal-indicator":"signal-muted"}>!</span><strong>{s.title}</strong><span className="muted">{s.status==="OPEN"?"Open":"Acknowledged"}</span></div>
 <p>{s.explanation}</p>{Array.isArray(s.suggested_actions)&&s.suggested_actions.length>0&&<div className="actions-preview">{s.suggested_actions.map((a,i)=><div key={i}><b>{a.side}</b> {a.quantity} × {a.symbol} <span>≈ {money(a.estimatedAmount,currency)}</span></div>)}</div>}
 {s.status==="OPEN"&&<form action={acknowledgeSignalAction}><input type="hidden" name="signal_id" value={s.id}/><button className="btn btn-small btn-outline" type="submit">Mark reviewed</button></form>}</div>):<div className="all-clear"><span>◌</span><b>No strategy actions yet</b><p>Quarterly reviews and Nasdaq reference 30%-drop alerts will appear after verified market data and the next eligible strategy event.</p></div>}
 </section></div>
 <div className="two-panel"><section className="panel"><div className="panel-heading"><div><h2>Record a transaction</h2><p>We never trade on your behalf.</p></div></div>
 <form action={addTransactionAction} className="form-stack compact-form"><input type="hidden" name="portfolio_id" value={p.id}/><div className="form-grid"><label>Transaction<select name="kind"><option value="DEPOSIT">Deposit cash</option><option value="BUY">Buy shares</option><option value="SELL">Sell shares</option><option value="WITHDRAWAL">Withdraw cash</option><option value="DIVIDEND">Dividend / income</option><option value="FEE">Account fee</option></select></label>
 <label>Asset (used for buys / sells)<select name="symbol"><option value={p.equity_symbol}>{p.equity_symbol} · Equity</option><option value={p.safe_symbol}>{p.safe_symbol} · Safe</option></select></label>
 <label>Cash amount (cash movements only)<input name="amount" type="number" min="0" step="0.01" defaultValue="0"/></label><label>Number of shares (trades only)<input name="quantity" type="number" min="0" step="0.00000001" defaultValue="0"/></label>
 <label>Execution price / share<input name="price" type="number" min="0" step="0.00000001" defaultValue="0"/></label><label>Trade fee<input name="fees" type="number" min="0" step="0.01" defaultValue="0"/></label>
 <label>Execution date<input name="date" type="date" defaultValue={new Date().toISOString().slice(0,10)} required/></label><label>Note (optional)<input name="notes" maxLength="250" placeholder="Trading 212 fill"/></label></div>
 <button type="submit" className="btn btn-primary">Save transaction →</button></form></section>
 <section className="panel"><div className="panel-heading"><h2>Transaction history</h2><span className="muted">{rows.length} recorded</span></div>
 <div className="table-scroll"><table className="ledger-table"><thead><tr><th>Date</th><th>Type</th><th>Instrument</th><th>Quantity</th><th>Amount</th></tr></thead><tbody>
 {[...rows].reverse().slice(0,35).map(t=><tr key={t.id}><td>{String(t.trade_date).slice(0,10)}</td><td>{t.kind}</td><td>{t.symbol||"Cash"}</td><td>{Number(t.quantity)?Number(t.quantity).toFixed(4):"—"}</td><td>{money(t.kind==="BUY"||t.kind==="SELL"?Number(t.quantity)*Number(t.unit_price)+Number(t.fees):t.amount,currency)}</td></tr>)}
 </tbody></table></div>
 {!rows.length&&<p className="empty-inline">Your first recorded transaction will appear here.</p>}</section></div>
 <p className="data-note">9Sig proposals use a documented preliminary rule set inspired by the attached spreadsheet. It is not an independently certified replication of every exceptional rule. Verify quantities and instrument eligibility with your broker before trading.</p></>;
}
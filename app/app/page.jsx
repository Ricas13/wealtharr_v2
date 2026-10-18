import Link from "next/link";
import {requireUser} from "@/lib/auth.js";
import {db} from "@/lib/db.js";
import {portfolioState} from "@/lib/portfolio.js";
import {aggregateSeries} from "@/lib/series.js";
import {GrowthChart,money,pct} from "@/components/chart.jsx";
export default async function Overview(){
 const user=await requireUser(),sql=db();
 const portfolios=await sql`SELECT * FROM portfolios WHERE user_id=${user.id} AND archived_at IS NULL ORDER BY created_at,id`;
 const states=await Promise.all(portfolios.map(async p=>({...p,...await portfolioState(p)})));
 const currencies=[...new Set(portfolios.map(p=>p.currency))],currency=currencies[0]||"GBP",mixed=currencies.length>1;
 const allPriced=states.length>0&&states.every(p=>p.valuation);
 const total=allPriced&&!mixed?states.reduce((sum,p)=>sum+p.valuation.total,0):null;
 const invested=allPriced&&!mixed?states.reduce((sum,p)=>sum+p.valuation.invested,0):null;
 const open=await sql`SELECT s.id,s.portfolio_id,s.title,s.created_at,p.name FROM signals s JOIN portfolios p ON p.id=s.portfolio_id WHERE p.user_id=${user.id} AND s.status='OPEN' ORDER BY s.created_at DESC LIMIT 6`;
 const history=await sql`SELECT s.* FROM snapshots s JOIN portfolios p ON p.id=s.portfolio_id WHERE p.user_id=${user.id} ORDER BY s.as_of`;
 const chartData=mixed?[]:aggregateSeries(history,portfolios.map(p=>p.id));
 return <><div className="page-heading"><div><p className="eyebrow">COMMAND CENTRE</p><h1>Overview</h1><p>Everything you need to know about your investments.</p></div><Link className="btn btn-primary" href="/app/portfolios/new">＋ Add strategy</Link></div>
 {!portfolios.length&&<div className="onboarding-banner"><div className="onboarding-symbol">◇</div><div><h2>Ready to track your first strategy?</h2><p>Create a 9Sig account, enter the shares you already own (or begin with cash), and Wealtharr will take it from there.</p></div><Link className="btn btn-primary" href="/app/portfolios/new">Set up 9Sig →</Link></div>}
 {mixed&&<div className="alert warning">Your portfolios use different currencies. Individual values are shown, but Wealtharr will not add pounds and dollars together without verified exchange rates.</div>}
 <div className="kpi-grid"><div className="metric-card"><span>Portfolio value</span><strong>{money(total,currency)}</strong><small>{!allPriced?"Waiting for verified prices":mixed?"Mixed currencies":portfolios.length+" strategy account(s)"}</small></div>
 <div className="metric-card"><span>Total profit / loss</span><strong className={total!==null&&total-invested>=0?"positive":"negative"}>{total===null?"—":money(total-invested,currency)}</strong><small>{total===null?"After initial market sync":pct(invested>0?(total/invested-1)*100:null)} net of contributions</small></div>
 <div className="metric-card"><span>Action needed</span><strong>{open.length}</strong><small>Unacknowledged strategy notices</small></div>
 <div className="metric-card"><span>My strategy accounts</span><strong>{portfolios.length}</strong><small>Separate ISA / general portfolios</small></div></div>
 <section className="panel"><div className="panel-heading"><div><h2>Total portfolio growth</h2><p>Tracked daily valuations, not historical simulation</p></div><span className="muted">Since tracking began</span></div><GrowthChart points={chartData} currency={currency}/></section>
 <div className="two-panel"><section className="panel"><div className="panel-heading"><h2>My strategies</h2><Link href="/app/portfolios/new" className="text-link">Add new →</Link></div>
 {states.length?states.map(p=><Link className="strategy-row" key={p.id} href={"/app/portfolios/"+p.id}><span className="strategy-icon">9</span><span className="strategy-summary"><strong>{p.name}</strong><small>9Sig · {p.account_type} · {p.currency}</small></span><span className="strategy-price"><b>{p.valuation?money(p.valuation.total,p.currency):"Prices pending"}</b><small className={p.valuation?.gain>=0?"positive":"negative"}>{p.valuation?pct(p.valuation.gainPct):"Refresh required"}</small></span><span>›</span></Link>):<p className="empty-inline">No strategies created yet.</p>}
 </section><section className="panel"><div className="panel-heading"><h2>Needs your attention</h2><span className="muted">Review only</span></div>
 {open.length?open.map(s=><Link href={"/app/portfolios/"+s.portfolio_id} key={s.id} className="notice-row"><span className="notice-bell">!</span><div><strong>{s.title}</strong><small>{s.name} · {new Date(s.created_at).toLocaleDateString("en-GB")}</small></div><span>›</span></Link>):<div className="all-clear"><span>✓</span><b>All caught up</b><p>New quarterly and 30%-drop reviews appear here.</p></div>}
 </section></div><p className="data-note">Prices are sourced from unofficial Yahoo Finance chart data and may be delayed or unavailable. No recommendations are trade orders.</p></>;
}
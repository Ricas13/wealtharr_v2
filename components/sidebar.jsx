import Link from "next/link";
import {logoutAction} from "@/app/actions.js";
const icons={overview:"▦",strategy:"◈",settings:"⚙",admin:"⌘",plus:"＋",home:"⌂"};
export default function Sidebar({user,portfolios}){
 return <aside className="sidebar">
  <Link href="/" className="brand"><span className="brand-mark">◈</span><span>Wealth<span className="brand-accent">arr</span><small>PERSONAL STRATEGY TRACKER</small></span></Link>
  <nav className="side-nav">
   <div className="nav-label">WORKSPACE</div>
   <Link href="/app" className="side-link"><span>{icons.overview}</span> Overview</Link>
   <div className="nav-label space-above">MY STRATEGIES <span className="count">{portfolios.length}</span></div>
   {portfolios.map(p=><Link className="side-link" href={"/app/portfolios/"+p.id} key={p.id}><span>{icons.strategy}</span><span className="nav-item-text">{p.name}<small>9Sig · {p.account_type}</small></span></Link>)}
   <Link href="/app/portfolios/new" className="side-link side-add"><span>{icons.plus}</span> Add strategy</Link>
   <div className="nav-label space-above">ACCOUNT</div>
   <Link href="/app/settings" className="side-link"><span>{icons.settings}</span> Notifications & preferences</Link>
   {user.role==="admin"&&<details className="admin-menu" open><summary><span>⌘</span> Administration</summary>
     <Link href="/app/admin">Control centre</Link>
     <Link href="/app/admin#channels">Email & Discord</Link>
     <Link href="/app/admin#users">Users</Link>
     <Link href="/app/admin#stripe">Stripe <span className="muted">Later</span></Link>
   </details>}
  </nav>
  <div className="sidebar-bottom"><div className="user-info"><span className="avatar">{user.email[0].toUpperCase()}</span><span className="nav-item-text">{user.email}<small>{user.role==="admin"?"Administrator":"Member"}</small></span></div>
  <form action={logoutAction}><button type="submit" className="logout-button">↪ Sign out</button></form></div>
 </aside>;
}
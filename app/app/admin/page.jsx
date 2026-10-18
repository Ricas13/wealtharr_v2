import {requireAdmin} from "@/lib/auth.js";
import {db} from "@/lib/db.js";
import {notificationAvailability} from "@/lib/notifications.js";
import {toggleNineSigAction,createMemberAction} from "@/app/actions.js";
export default async function Admin({searchParams}){
 await requireAdmin();const sql=db(),q=await searchParams,channels=notificationAvailability();
 const [[enabled],users,counts]=await Promise.all([
   sql`SELECT value FROM app_settings WHERE key='nine_sig_enabled'`,
   sql`SELECT id,email,role,created_at FROM users ORDER BY created_at`,
   sql`SELECT (SELECT count(*)::int FROM portfolios) AS portfolios,(SELECT count(*)::int FROM signals WHERE status='OPEN') AS pending,(SELECT count(*)::int FROM deliveries WHERE status='failed') AS delivery_failures`
 ]);
 return <><div className="page-heading"><div><p className="eyebrow">ADMINISTRATION</p><h1>Control centre</h1><p>Personal operations now. Ready for more users later.</p></div><span className="mini-badge">ADMIN ONLY</span></div>
 {q?.error&&<div className="alert error">{q.error}</div>}{q?.saved&&<div className="alert success">Changes saved.</div>}
 <div className="kpi-grid"><div className="metric-card"><span>Users</span><strong>{users.length}</strong></div><div className="metric-card"><span>Strategy accounts</span><strong>{counts[0].portfolios}</strong></div><div className="metric-card"><span>Open strategy notices</span><strong>{counts[0].pending}</strong></div><div className="metric-card"><span>Failed deliveries</span><strong>{counts[0].delivery_failures}</strong></div></div>
 <section className="panel admin-section"><div className="panel-heading"><div><h2>Strategies</h2><p>Enable or pause the 9Sig template globally.</p></div></div>
 <div className="setting-option"><div><h3>9Sig · Nasdaq 3× quarterly target</h3><p>9% annual growth line, 40% safe baseline, reference 30%-drop watch.</p></div><form action={toggleNineSigAction}><input type="hidden" name="enabled" value={enabled?.value==="false"?"true":"false"}/><button className="btn btn-outline">{enabled?.value==="false"?"Enable 9Sig":"Pause 9Sig"}</button></form></div></section>
 <section className="panel admin-section" id="channels"><div className="panel-heading"><div><h2>Email & Discord</h2><p>Delivery credentials are managed as private environment secrets.</p></div></div>
 <div className="integration-row"><span className="integration-icon">✉</span><div><b>SMTP email</b><p>Set SMTP_HOST, SMTP_PORT, SMTP_FROM, SMTP_USER and SMTP_PASSWORD.</p></div><span className={channels.email?"status-on":"status-off"}>{channels.email?"Configured":"Not configured"}</span></div>
 <div className="integration-row"><span className="integration-icon">#</span><div><b>Discord webhook</b><p>Set DISCORD_WEBHOOK_URL. Only official Discord webhook hosts are accepted.</p></div><span className={channels.discord?"status-on":"status-off"}>{channels.discord?"Configured":"Not configured"}</span></div>
 <p className="data-note">Credentials and webhook URLs are never displayed to browser users. Delivery retry limit: 3. New signals enqueue notifications once per channel.</p></section>
 <section className="panel admin-section" id="users"><div className="panel-heading"><div><h2>Users</h2><p>Invite-only; no public signup.</p></div></div>
 {users.map(u=><div className="integration-row" key={u.id}><span className="avatar">{u.email[0].toUpperCase()}</span><div><b>{u.email}</b><p>{u.role} · Joined {new Date(u.created_at).toLocaleDateString("en-GB")}</p></div></div>)}
 <form action={createMemberAction} className="form-grid member-form"><label>New member email<input name="email" type="email" required placeholder="member@example.com"/></label><label>Initial password (12+ characters)<input name="password" type="password" minLength="12" required/></label><button className="btn btn-primary" type="submit">Create member</button></form></section>
 <section className="panel admin-section" id="stripe"><div className="panel-heading"><h2>Stripe</h2><span className="mini-badge">NOT ENABLED</span></div><p className="subdued">Billing and subscriptions will be added only when you decide to turn Wealtharr into a SaaS. No payment credentials or checkout flows are configured.</p></section>
 </>;
}
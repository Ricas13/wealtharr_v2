import {requireUser} from "@/lib/auth.js";
import {db} from "@/lib/db.js";
import Sidebar from "@/components/sidebar.jsx";
export const dynamic="force-dynamic";
export default async function AppLayout({children}){
 const user=await requireUser(),sql=db();
 const portfolios=await sql`SELECT id,name,account_type FROM portfolios WHERE user_id=${user.id} AND archived_at IS NULL ORDER BY created_at,id`;
 return <div className="app-shell"><Sidebar user={user} portfolios={portfolios}/><div className="app-content"><header className="app-topbar"><span className="breadcrumb">◈ <span>Wealtharr</span> / Workspace</span><div className="topbar-right"><span className="status-dot"/> Monitoring available <span className="topbar-avatar">{user.email[0].toUpperCase()}</span></div></header><main className="main-content">{children}</main></div></div>;
}
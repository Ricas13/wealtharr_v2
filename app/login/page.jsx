import Link from "next/link";
import {currentUser} from "@/lib/auth.js";
import {redirect} from "next/navigation";
import {loginAction} from "@/app/actions.js";
export default async function Login({searchParams}){
 if(await currentUser())redirect("/app");
 const query=await searchParams;
 return <div className="auth-screen"><div className="login-panel">
 <Link href="/" className="brand center-brand"><span className="brand-mark">◈</span><span>Wealth<span className="brand-accent">arr</span></span></Link>
 <p className="eyebrow">SECURE SIGN IN</p><h1>Welcome back.</h1><p className="subdued">Your strategies are waiting.</p>
 {query?.error&&<div className="alert error">{query.error}</div>}
 <form action={loginAction} className="form-stack"><label>Email<input name="email" type="email" autoComplete="username" required placeholder="you@example.com"/></label>
 <label>Password<input name="password" type="password" autoComplete="current-password" required/></label>
 <button className="btn btn-primary full" type="submit">Sign in →</button></form>
 <p className="fineprint">Accounts are created by the administrator. Public registration is disabled.</p>
 </div></div>;
}
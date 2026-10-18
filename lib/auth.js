import "server-only";
import {cookies} from "next/headers";
import {createHash,randomBytes} from "node:crypto";
import {redirect} from "next/navigation";
import {db} from "./db.js";
const cookieName="wealtharr_session";
const hash=(s)=>createHash("sha256").update(s).digest("hex");
export async function currentUser(){
 const jar=await cookies(), token=jar.get(cookieName)?.value;
 if(!token)return null;
 const sql=db();
 const rows=await sql`SELECT u.id,u.email,u.role,u.notify_email,u.notify_discord FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=${hash(token)} AND s.expires_at>now() LIMIT 1`;
 return rows[0]||null;
}
export async function requireUser(){const u=await currentUser();if(!u)redirect("/login");return u;}
export async function requireAdmin(){const u=await requireUser();if(u.role!=="admin")redirect("/app");return u;}
export async function signIn(userId){
 const token=randomBytes(32).toString("base64url"),sql=db();
 await sql`INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(${hash(token)},${userId},now()+interval '30 days')`;
 const jar=await cookies();jar.set(cookieName,token,{httpOnly:true,secure:process.env.NODE_ENV==="production",sameSite:"lax",path:"/",maxAge:60*60*24*30});
}
export async function signOut(){
 const jar=await cookies(), token=jar.get(cookieName)?.value;
 if(token){const sql=db();await sql`DELETE FROM sessions WHERE token_hash=${hash(token)}`;}
 jar.delete(cookieName);
}

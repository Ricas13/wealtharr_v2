"use server";
import bcrypt from "bcryptjs";
import {redirect} from "next/navigation";
import {db} from "@/lib/db.js";
import {requireUser,requireAdmin,signIn,signOut} from "@/lib/auth.js";
import {ownedPortfolio} from "@/lib/portfolio.js";
import {replay} from "@/lib/ledger.js";
import {syncOne} from "@/lib/jobs.js";
import {validSymbol} from "@/lib/market.js";
const str=(fd,k)=>String(fd.get(k)||"").trim();
const num=(fd,k)=>Number(str(fd,k)||"0");
const today=()=>new Date().toISOString().slice(0,10);
const positive=(v)=>Number.isFinite(v)&&v>=0&&v<1e12;
function failure(path,msg){redirect(path+"?error="+encodeURIComponent(msg));}
export async function loginAction(fd){
 const email=str(fd,"email").toLowerCase(),pass=str(fd,"password");
 if(!email||!pass)return failure("/login","Email or password incorrect");
 const sql=db();
 const [attempt]=await sql`SELECT failures,locked_until FROM login_attempts WHERE email=${email}`;
 if(attempt?.locked_until && new Date(attempt.locked_until)>new Date())return failure("/login","Try again in 15 minutes");
 const [user]=await sql`SELECT id,password_hash FROM users WHERE email=${email}`;
 const matched=user?await bcrypt.compare(pass,user.password_hash):false;
 if(!matched){
   await sql`INSERT INTO login_attempts(email,failures,locked_until) VALUES(${email},1,NULL) ON CONFLICT(email) DO UPDATE SET failures=login_attempts.failures+1,locked_until=CASE WHEN login_attempts.failures>=4 THEN now()+interval '15 minutes' ELSE NULL END,updated_at=now()`;
   return failure("/login","Email or password incorrect");
 }
 await sql`DELETE FROM login_attempts WHERE email=${email}`;
 await signIn(user.id);redirect("/app");
}
export async function logoutAction(){await signOut();redirect("/");}
export async function createPortfolioAction(fd){
 const user=await requireUser(),sql=db();
 const name=str(fd,"name"),account=str(fd,"account_type"),currency=str(fd,"currency");
 const equity=str(fd,"equity_symbol").toUpperCase(),safe=str(fd,"safe_symbol").toUpperCase();
 const cash=num(fd,"cash"),monthly=num(fd,"monthly"),eqQty=num(fd,"equity_qty"),
  eqPrice=num(fd,"equity_price"),safeQty=num(fd,"safe_qty"),safePrice=num(fd,"safe_price");
 if(name.length<2||name.length>80||!["ISA","GIA","SIPP","OTHER"].includes(account)
  ||!["GBP","USD"].includes(currency)||!validSymbol(equity)||!validSymbol(safe)||equity===safe
  ||![cash,monthly,eqQty,eqPrice,safeQty,safePrice].every(positive)
  ||(eqQty>0&&eqPrice<=0)||(safeQty>0&&safePrice<=0))
   return failure("/app/portfolios/new","Check the portfolio name, symbols and positive amounts");
 const [setting]=await sql`SELECT value FROM app_settings WHERE key='nine_sig_enabled'`;
 if(setting?.value==="false")return failure("/app/portfolios/new","9Sig is temporarily disabled");
 const p=await sql.begin(async tx=>{
  const [created]=await tx`INSERT INTO portfolios(user_id,name,account_type,currency,equity_symbol,safe_symbol,monthly_contribution) VALUES(${user.id},${name},${account},${currency},${equity},${safe},${monthly}) RETURNING id`;
  if(cash>0)await tx`INSERT INTO ledger(portfolio_id,kind,amount,trade_date) VALUES(${created.id},'DEPOSIT',${cash},${today()})`;
  if(eqQty>0)await tx`INSERT INTO ledger(portfolio_id,kind,symbol,quantity,unit_price,trade_date) VALUES(${created.id},'OPENING',${equity},${eqQty},${eqPrice},${today()})`;
  if(safeQty>0)await tx`INSERT INTO ledger(portfolio_id,kind,symbol,quantity,unit_price,trade_date) VALUES(${created.id},'OPENING',${safe},${safeQty},${safePrice},${today()})`;
  return created.id;
 });
 redirect("/app/portfolios/"+p);
}
export async function addTransactionAction(fd){
 const user=await requireUser(),id=str(fd,"portfolio_id"),p=await ownedPortfolio(user,id);
 if(!p)redirect("/app");
 const kind=str(fd,"kind"),symbol=str(fd,"symbol").toUpperCase(),quantity=num(fd,"quantity"),
  price=num(fd,"price"),fees=num(fd,"fees"),amount=num(fd,"amount"),day=str(fd,"date")||today(),notes=str(fd,"notes").slice(0,250);
 const url="/app/portfolios/"+p.id;
 if(!["DEPOSIT","WITHDRAWAL","BUY","SELL","DIVIDEND","FEE"].includes(kind) ||
    ![quantity,price,fees,amount].every(positive) || !/^\d{4}-\d{2}-\d{2}$/.test(day) ||
    !Number.isFinite(Date.parse(day)) || day>today() ||
    ((kind==="BUY"||kind==="SELL") && (![p.equity_symbol,p.safe_symbol].includes(symbol)||quantity<=0||price<=0))||
    (!(kind==="BUY"||kind==="SELL")&&amount<=0))return failure(url,"Check the date and amounts");
 const sql=db();
 try{
  await sql.begin(async tx=>{
   await tx`SELECT id FROM portfolios WHERE id=${p.id} FOR UPDATE`;
   const existing=await tx`SELECT * FROM ledger WHERE portfolio_id=${p.id} ORDER BY trade_date,id`;
   const proposed={id:Number.MAX_SAFE_INTEGER,trade_date:day,kind,symbol:kind==="BUY"||kind==="SELL"?symbol:null,quantity:kind==="BUY"||kind==="SELL"?quantity:0,unit_price:kind==="BUY"||kind==="SELL"?price:0,fees:kind==="BUY"||kind==="SELL"?fees:0,amount:kind==="BUY"||kind==="SELL"?0:amount};
   replay([...existing,proposed],p.equity_symbol,p.safe_symbol);
   await tx`INSERT INTO ledger(portfolio_id,kind,symbol,quantity,unit_price,fees,amount,trade_date,notes) VALUES(${p.id},${kind},${proposed.symbol},${proposed.quantity},${proposed.unit_price},${proposed.fees},${proposed.amount},${day},${notes})`;
  });
 }catch{return failure(url,"Insufficient shares or cash, or an invalid transaction order");}
 redirect(url+"?saved=1");
}
export async function acknowledgeSignalAction(fd){
 const user=await requireUser(),sql=db(),id=str(fd,"signal_id");
 const rows=await sql`UPDATE signals s SET status='ACKNOWLEDGED' FROM portfolios p WHERE s.id=${id} AND s.portfolio_id=p.id AND p.user_id=${user.id} RETURNING p.id`;
 redirect(rows[0]?"/app/portfolios/"+rows[0].id:"/app");
}
export async function refreshPortfolioAction(fd){
 const user=await requireUser(),id=str(fd,"portfolio_id"),p=await ownedPortfolio(user,id);
 if(!p)redirect("/app");
 try{await syncOne(p);redirect("/app/portfolios/"+p.id+"?synced=1");}
 catch{return failure("/app/portfolios/"+p.id,"Unable to refresh: market quotes unavailable or currency mismatch");}
}
export async function savePreferencesAction(fd){
 const user=await requireUser(),sql=db();
 await sql`UPDATE users SET notify_email=${str(fd,'email')==='on'},notify_discord=${str(fd,'discord')==='on'} WHERE id=${user.id}`;
 redirect("/app/settings?saved=1");
}
export async function toggleNineSigAction(fd){
 await requireAdmin();const sql=db();
 await sql`UPDATE app_settings SET value=${str(fd,'enabled')==='true'?'true':'false'} WHERE key='nine_sig_enabled'`;
 redirect("/app/admin?saved=1");
}
export async function createMemberAction(fd){
 await requireAdmin();const sql=db(),email=str(fd,"email").toLowerCase(),pass=str(fd,"password");
 if(!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)||pass.length<12)return failure("/app/admin","Valid email and a 12+ character password required");
 const hash=await bcrypt.hash(pass,12);
 try{await sql`INSERT INTO users(email,password_hash,role) VALUES(${email},${hash},'member')`;}
 catch{return failure("/app/admin","An account with that email already exists");}
 redirect("/app/admin?saved=1");
}

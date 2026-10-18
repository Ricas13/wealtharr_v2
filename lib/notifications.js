import "server-only";
import nodemailer from "nodemailer";
import {db} from "./db.js";
function discordConfigured(){
 try { const u=new URL(process.env.DISCORD_WEBHOOK_URL||"");
   return u.protocol==="https:"&&["discord.com","discordapp.com"].includes(u.hostname)&&
    /^\/api\/webhooks\/\d+\/[A-Za-z0-9_\-]+$/.test(u.pathname);
 } catch{return false;}
}
export function notificationAvailability(){
 return {email:!!(process.env.SMTP_HOST&&process.env.SMTP_FROM),discord:discordConfigured()};
}
export async function deliverPending(){
 const sql=db(), channels=notificationAvailability();
 const pending=await sql`SELECT d.id,d.channel,d.attempts,s.title,s.explanation,s.suggested_actions,p.name,p.currency,u.email FROM deliveries d JOIN signals s ON s.id=d.signal_id JOIN portfolios p ON p.id=s.portfolio_id JOIN users u ON u.id=d.user_id WHERE d.status IN ('pending','failed') AND d.attempts<3 ORDER BY d.id LIMIT 40`;
 let sent=0,failed=0;
 for(const d of pending){
   if(!channels[d.channel])continue;
   const lines=Array.isArray(d.suggested_actions)?d.suggested_actions:[];
   const body=d.name+"\n"+d.title+"\n"+d.explanation+"\n"+
     lines.map(a=>a.side+" "+a.quantity+" "+a.symbol+" (approx "+a.estimatedAmount+" "+d.currency+")").join("\n")+
     "\nReview only. No orders have been placed.";
   try {
     if(d.channel==="email"){
       const transport=nodemailer.createTransport({host:process.env.SMTP_HOST,
         port:Number(process.env.SMTP_PORT||587),secure:Number(process.env.SMTP_PORT||587)===465,
         auth:process.env.SMTP_USER?{user:process.env.SMTP_USER,pass:process.env.SMTP_PASSWORD}:undefined});
       await transport.sendMail({from:process.env.SMTP_FROM,to:d.email,
         subject:"Wealtharr: "+d.title,text:body});
       transport.close();
     }else{
       const response=await fetch(process.env.DISCORD_WEBHOOK_URL,{
         method:"POST",headers:{"content-type":"application/json"},
         body:JSON.stringify({content:body.slice(0,1900),allowed_mentions:{parse:[]}}),
         signal:AbortSignal.timeout(12000)});
       if(!response.ok)throw new Error("Discord rejected the message");
     }
     await sql`UPDATE deliveries SET status='sent',attempts=attempts+1,last_error=NULL,sent_at=now() WHERE id=${d.id}`;sent++;
   } catch(err){
     await sql`UPDATE deliveries SET status='failed',attempts=attempts+1,last_error=${String(err?.name||'delivery failure').slice(0,80)} WHERE id=${d.id}`;failed++;
   }
 }
 return {sent,failed};
}

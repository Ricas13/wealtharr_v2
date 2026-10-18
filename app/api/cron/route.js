import {timingSafeEqual} from "node:crypto";
import {runAll} from "@/lib/jobs.js";
export const runtime="nodejs";
export const dynamic="force-dynamic";
export async function POST(request){
 const expected=process.env.CRON_TOKEN||"";
 const provided=(request.headers.get("authorization")||"").replace(/^Bearer /,"");
 if(!expected||expected.length<24||Buffer.byteLength(provided)!==Buffer.byteLength(expected)||
    !timingSafeEqual(Buffer.from(provided),Buffer.from(expected)))
   return Response.json({error:"Unauthorized"},{status:401});
 try{return Response.json(await runAll());}
 catch(e){console.error("Scheduled sync failed",e?.name||"Error");return Response.json({error:"Sync error"},{status:503});}
}

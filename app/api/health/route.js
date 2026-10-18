import {db} from "@/lib/db.js";
export const dynamic="force-dynamic";
export async function GET(){
 try{const sql=db();await sql`SELECT 1`;return Response.json({status:"ok"});}
 catch{return Response.json({status:"unhealthy"},{status:503});}
}

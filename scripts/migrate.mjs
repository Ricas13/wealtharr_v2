import fs from "node:fs/promises";
import postgres from "postgres";
if (!process.env.DATABASE_URL) throw new Error("DATABASE_URL is required");
const db = postgres(process.env.DATABASE_URL,{max:1});
try {
  await db.unsafe(await fs.readFile(new URL("../database/schema.sql",import.meta.url),"utf8")).simple();
  console.log("Database schema ready");
} finally {await db.end();}

import postgres from "postgres";
let client;
export function db() {
  if(!process.env.DATABASE_URL) throw new Error("DATABASE_URL is missing");
  if(!client) client=postgres(process.env.DATABASE_URL,{max:10,idle_timeout:30,connect_timeout:10});
  return client;
}
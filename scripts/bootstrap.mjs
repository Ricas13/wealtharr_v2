import bcrypt from "bcryptjs";
import postgres from "postgres";
const {DATABASE_URL,BOOTSTRAP_ADMIN_EMAIL,BOOTSTRAP_ADMIN_PASSWORD}=process.env;
if (!DATABASE_URL || !BOOTSTRAP_ADMIN_EMAIL || !BOOTSTRAP_ADMIN_PASSWORD || BOOTSTRAP_ADMIN_PASSWORD.length<12)
  throw new Error("Set DATABASE_URL, BOOTSTRAP_ADMIN_EMAIL and a password of at least 12 characters");
const db=postgres(DATABASE_URL,{max:1});
try {
  const email=BOOTSTRAP_ADMIN_EMAIL.trim().toLowerCase();
  const existing=await db`SELECT id,role FROM users WHERE email=${email}`;
  if (existing.length) {console.log("Admin account already exists; credentials were not overwritten");}
  else {
    const hash=await bcrypt.hash(BOOTSTRAP_ADMIN_PASSWORD,12);
    await db`INSERT INTO users(email,password_hash,role) VALUES(${email},${hash},'admin')`;
    console.log("Admin account created");
  }
} finally {await db.end();}

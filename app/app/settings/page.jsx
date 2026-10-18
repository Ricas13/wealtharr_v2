import {requireUser} from "@/lib/auth.js";
import {savePreferencesAction} from "@/app/actions.js";
export default async function Settings({searchParams}){
 const user=await requireUser(),q=await searchParams;
 return <><div className="page-heading"><div><p className="eyebrow">ACCOUNT</p><h1>Notifications</h1><p>Choose how you want to hear about strategy reviews.</p></div></div>
 {q?.saved&&<div className="alert success">Preferences saved.</div>}
 <form action={savePreferencesAction} className="form-panel settings-form"><div className="setting-option"><div><h3>Email alerts</h3><p>Quarterly recommendations and 30%-drop notices to {user.email}.</p></div><input type="checkbox" name="email" defaultChecked={user.notify_email}/></div>
 <div className="setting-option"><div><h3>Discord alerts</h3><p>Send the same notices through the administrator's configured Discord webhook.</p></div><input type="checkbox" name="discord" defaultChecked={user.notify_discord}/></div>
 <button className="btn btn-primary" type="submit">Save preferences</button></form></>;
}
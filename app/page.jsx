import Link from "next/link";
import {currentUser} from "@/lib/auth.js";
export default async function Home(){
 const user=await currentUser();
 return <div className="landing"><header className="landing-header"><a className="brand" href="/"><span className="brand-mark">◈</span><span>Wealth<span className="brand-accent">arr</span></span></a><Link className="btn btn-outline" href={user?"/app":"/login"}>{user?"Open dashboard":"Sign in"} →</Link></header>
  <main className="hero"><div className="eyebrow"><span className="live-dot"/>YOUR STRATEGY. ONE CLEAR VIEW.</div>
   <h1>Invest with a plan.<br/><span>Track without the noise.</span></h1>
   <p>One place for your holdings, portfolio performance and upcoming strategy reviews. Start with 9Sig. Add more accounts whenever you need.</p>
   <Link href={user?"/app":"/login"} className="btn btn-primary btn-large">{user?"Go to dashboard":"Open Wealtharr"} <span>↗</span></Link>
   <div className="hero-visual"><div className="preview-top"><span>◈ &nbsp; Portfolio overview</span><span className="preview-dots">● ● ●</span></div><div className="preview-body">
     <div><small>TOTAL PORTFOLIO</small><strong>Your accounts, together.</strong><span>Clear values. Real holdings. No made-up returns.</span></div>
     <div className="preview-graph"><svg viewBox="0 0 600 160" preserveAspectRatio="none"><path d="M0 135 L60 123 L120 128 L180 93 L245 108 L310 78 L378 92 L455 44 L510 58 L600 16" stroke="#2cdacb" strokeWidth="4" fill="none"/><path d="M0 135 L60 123 L120 128 L180 93 L245 108 L310 78 L378 92 L455 44 L510 58 L600 16 L600 160 L0 160 Z" fill="#2cdacb" opacity=".08"/></svg></div></div>
    </div>
  </main><footer className="landing-footer">Private-first by design · No automatic trading · Not financial advice</footer></div>;
}
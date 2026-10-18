// Pure accounting engine. Ledger is the source of truth, not manually-edited balances.
export const number = (v) => Number(v||0);
export const round = (v) => Math.round((v + Number.EPSILON)*1e6)/1e6;
export function replay(rows, equitySymbol, safeSymbol) {
  const pos={};
  let cash=0, invested=0, realized=0;
  const sorted=[...rows].sort((a,b)=>new Date(a.trade_date).toISOString().localeCompare(new Date(b.trade_date).toISOString())||number(a.id)-number(b.id));
  for(const t of sorted){
    const q=number(t.quantity), price=number(t.unit_price), fee=number(t.fees), amt=number(t.amount), sym=t.symbol;
    const p=sym?(pos[sym]??={quantity:0,cost:0}):null;
    switch(t.kind){
      case "OPENING": p.quantity+=q;p.cost+=q*price+fee;invested+=q*price+fee;break;
      case "DEPOSIT": cash+=amt;invested+=amt;break;
      case "WITHDRAWAL": cash-=amt;invested-=amt;break;
      case "BUY": cash-=q*price+fee;p.quantity+=q;p.cost+=q*price+fee;break;
      case "SELL": {
        if(!p||p.quantity+1e-7<q) throw new Error("Ledger contains an oversold position");
        const cost=p.cost*(q/p.quantity);p.quantity-=q;p.cost-=cost;cash+=q*price-fee;
        realized+=q*price-fee-cost; if(p.quantity<1e-7){p.quantity=0;p.cost=0;}
        break;
      }
      case "DIVIDEND":cash+=amt;break;
      case "FEE":cash-=amt;break;
      default: throw new Error("Unknown ledger transaction");
    }
    if(cash < -0.00001 && t.kind!=="OPENING") throw new Error("Ledger contains a negative cash balance");
  }
  return {cash:round(cash),invested:round(invested),realized:round(realized),
    positions:pos,equity:pos[equitySymbol]||{quantity:0,cost:0},
    safe:pos[safeSymbol]||{quantity:0,cost:0}};
}
export function valuePortfolio(state,quotes,equitySymbol,safeSymbol){
  const e=quotes[equitySymbol],s=quotes[safeSymbol];
  if(!e||!s) return null;
  const eq=round(state.equity.quantity*number(e.price));
  const safe=round(state.safe.quantity*number(s.price));
  const total=round(state.cash+eq+safe);
  return {cash:state.cash,equityValue:eq,safeValue:safe,total,invested:state.invested,
    gain:round(total-state.invested),gainPct:state.invested>0?round((total/state.invested-1)*100):null,
    asOf:e.as_of<s.as_of?e.as_of:s.as_of};
}

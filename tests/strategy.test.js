import test from "node:test";
import assert from "node:assert/strict";
import {generateAdvice,quarterIndex,previousQuarterKey} from "../lib/strategy.js";
const p={equity_symbol:"TQQQ",safe_symbol:"AGG",currency:"USD",created_at:"2026-01-10T00:00:00Z"};
const state={cash:100,equity:{quantity:10},safe:{quantity:5},openingStockValue:500};
const value={cash:100,equityValue:400,safeValue:500,total:1000};
test("quarter rollover handles calendar years",()=>{
 assert.equal(quarterIndex("2026-01-01")-quarterIndex("2025-12-31"),1);
 assert.equal(previousQuarterKey("2026-01-03"),"2025-Q4");
});
test("no quarterly signal is generated before the initial quarter ends",()=>{
 assert.equal(generateAdvice({portfolio:p,state,valuation:value,equityPrice:40,safePrice:100,history:[],asOf:"2026-02-10"}).length,0);
});
test("quarterly advice includes a funding-constrained stock action",()=>{
 const a=generateAdvice({portfolio:p,state,valuation:value,equityPrice:40,safePrice:100,history:[],asOf:"2026-04-03"});
 const q=a.find(x=>x.kind==="QUARTERLY");
 assert.equal(q.key,"quarter:2026-Q1");assert.ok(q.actions.some(x=>x.side==="BUY"));
 assert.ok(q.actions.every(x=>x.quantity>0));
});
test("30% peak drop creates a deduplicated half-sale review",()=>{
 const hist=[{date:"2026-01-11",price:100},{date:"2026-03-15",price:65}];
 const first=generateAdvice({portfolio:p,state,valuation:value,equityPrice:40,safePrice:100,history:hist,asOf:"2026-03-15"});
 const alert=first.find(x=>x.kind==="THIRTY_DOWN");assert.equal(alert.actions[0].quantity,5);
 const repeat=generateAdvice({portfolio:p,state,valuation:value,equityPrice:40,safePrice:100,history:hist,asOf:"2026-03-15",priorSignals:[{signal_key:alert.key}]});
 assert.ok(!repeat.some(x=>x.kind==="THIRTY_DOWN"));
});
test("quarter reviews are idempotent",()=>{
 const a=generateAdvice({portfolio:p,state,valuation:value,equityPrice:40,safePrice:100,history:[],asOf:"2026-04-03",priorSignals:[{signal_key:"quarter:2026-Q1"}]});
 assert.ok(!a.some(x=>x.kind==="QUARTERLY"));
});
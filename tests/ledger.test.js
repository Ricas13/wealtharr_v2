import test from "node:test";
import assert from "node:assert/strict";
import {replay,valuePortfolio} from "../lib/ledger.js";
const symbols=["TQQQ","AGG"];
test("opening positions are not paid from cash twice",()=>{
 const s=replay([{id:1,trade_date:"2026-01-01",kind:"OPENING",symbol:"TQQQ",quantity:10,unit_price:20,fees:0},{id:2,trade_date:"2026-01-01",kind:"DEPOSIT",amount:1000}],...symbols);
 assert.equal(s.cash,1000);assert.equal(s.invested,1200);assert.equal(s.equity.quantity,10);
});
test("fees and partial sale preserve cost basis and realized profit",()=>{
 const s=replay([{id:1,trade_date:"2026-01-01",kind:"DEPOSIT",amount:1000},
 {id:2,trade_date:"2026-01-02",kind:"BUY",symbol:"TQQQ",quantity:10,unit_price:20,fees:1},
 {id:3,trade_date:"2026-01-03",kind:"SELL",symbol:"TQQQ",quantity:4,unit_price:30,fees:2}],...symbols);
 assert.equal(s.cash,917);assert.equal(s.equity.quantity,6);
 assert.equal(s.equity.cost,120.6);assert.equal(s.realized,37.6);
 const val=valuePortfolio(s,{TQQQ:{price:25,as_of:"2026-01-03"},AGG:{price:100,as_of:"2026-01-03"}},...symbols);
 assert.equal(val.total,1067);assert.equal(val.gain,67);
});
test("overselling and overspending throw, never silently change balances",()=>{
 assert.throws(()=>replay([{id:1,trade_date:"2026-01-01",kind:"SELL",symbol:"TQQQ",quantity:1,unit_price:1}],...symbols),/oversold/i);
 assert.throws(()=>replay([{id:1,trade_date:"2026-01-01",kind:"BUY",symbol:"TQQQ",quantity:1,unit_price:1}],...symbols),/negative cash/i);
});
test("valuation is absent when a quote is absent",()=>{
 assert.equal(valuePortfolio(replay([],...symbols),{},...symbols),null);
});
import test from "node:test";
import assert from "node:assert/strict";
import {aggregateSeries} from "../lib/series.js";
test("historical sums never drop a portfolio with no same-day update",()=>{
 const points=aggregateSeries([{portfolio_id:"a",as_of:"2026-01-01",value:100},{portfolio_id:"a",as_of:"2026-01-02",value:110},{portfolio_id:"b",as_of:"2026-01-02",value:50},{portfolio_id:"b",as_of:"2026-01-03",value:70}],["a","b"]);
 assert.deepEqual(points,[{date:"2026-01-02",value:160},{date:"2026-01-03",value:180}]);
});
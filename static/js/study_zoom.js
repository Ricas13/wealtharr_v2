/* Read-only Study zoom. The source is the current hypothetical Study replay,
 * never real holdings. Server computes selected-period returns and drawdown. */
(function () {
  "use strict";
  var root = document.querySelector("[data-study-zoom]");
  var dataNode = document.getElementById("study-zoom-data");
  if (!root || !dataNode) return;
  var history = JSON.parse(dataNode.textContent);
  var values = history.rows || [];
  var dates = history.point_dates_iso || [];
  var invested = history.put_in_points || [];
  if (dates.length < 3 || invested.length !== dates.length ||
      values.some(function (r) { return r.points.length !== dates.length; })) return;
  var svg = root.querySelector("[data-study-chart]");
  var mini = root.querySelector("[data-study-mini]");
  var track = root.querySelector("[data-study-track]");
  var windowEl = root.querySelector("[data-study-window]");
  var startEl = root.querySelector('[data-study-handle="start"]');
  var endEl = root.querySelector('[data-study-handle="end"]');
  var reset = root.querySelector("[data-study-reset]");
  var tip = root.querySelector("[data-study-tip]");
  var status = root.querySelector("[data-study-status]");
  var NS = "http://www.w3.org/2000/svg";
  var last = dates.length - 1, view = {start:0,end:last};
  var log = root.dataset.studyScale === "log";
  var currency = root.dataset.studyCurrency;
  var drag = null, currentRequest = 0, timer = null;
  var original = {}, originalLabels = {};
  var kpis = {}, rows = {}, bestLabels = [];
  var fmt = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });
  function money(v) { return v == null ? "—" : currency + fmt.format(v); }
  function signedMoney(v) {
    if (v == null) return "—";
    return (v >= 0 ? "+" : "−") + currency + fmt.format(Math.abs(v));
  }
  function percentage(v) {
    return v == null ? "—" : (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(1) + "%";
  }
  function number(v) { return Math.min(last, Math.max(0, Math.round(v))); }
  function full() { return view.start === 0 && view.end === last; }
  function day(i) {
    var d = new Date(dates[i] + "T12:00:00Z");
    return new Intl.DateTimeFormat("en-GB", {day:"numeric",month:"short",year:"numeric",timeZone:"UTC"}).format(d);
  }
  function month(i) {
    var d = new Date(dates[i] + "T12:00:00Z");
    return new Intl.DateTimeFormat("en-GB", {month:"short",year:"numeric",timeZone:"UTC"}).format(d);
  }
  function tone(el, value) {
    el.classList.remove("w2-pos", "w2-neg", "w2-positive", "w2-negative");
    if (value != null) el.classList.add(value >= 0 ? "w2-pos" : "w2-neg");
  }
  root.querySelectorAll("[data-study-primary-metric]").forEach(function (el) {
    var key = el.dataset.studyPrimaryMetric;
    kpis[key] = el;
    original["kpi:" + key] = { html: el.innerHTML, cls: el.className };
  });
  root.querySelectorAll("[data-study-row]").forEach(function (row) {
    var key = row.dataset.studyRow;
    rows[key] = {};
    row.querySelectorAll("[data-study-cell]").forEach(function (el) {
      var field = el.dataset.studyCell;
      rows[key][field] = el;
      original[key + ":" + field] = {html:el.innerHTML,cls:el.className};
    });
  });
  ["[data-study-value-label]", "[data-study-capital-label]",
   "[data-study-capital-heading]", "[data-study-comparison-title]"].forEach(function (selector) {
    originalLabels[selector] = root.querySelector(selector).textContent;
  });
  root.querySelectorAll("[data-study-best]").forEach(function (el) { bestLabels.push(el); });
  function restore() {
    Object.keys(original).forEach(function (key) {
      var parts = key.split(":");
      var el = parts[0] === "kpi" ? kpis[parts[1]] : rows[parts[0]][parts[1]];
      el.innerHTML = original[key].html;
      el.className = original[key].cls;
    });
    Object.keys(originalLabels).forEach(function (selector) {
      root.querySelector(selector).textContent = originalLabels[selector];
    });
    bestLabels.forEach(function (b) { b.hidden = false; });
    status.textContent = "Full Study period";
    root.querySelector("[data-study-rows]").setAttribute("aria-busy", "false");
  }
  function setText(selector, val) { root.querySelector(selector).textContent = val; }
  function paint(data) {
    if (!data || !data.rows) return;
    var name = values.find(function (r) { return r.key === history.primary; });
    var primary = data.rows[history.primary];
    var span = day(view.start) + " – " + day(view.end);
    setText("[data-study-comparison-title]", "Compared over " + span);
    setText("[data-study-value-label]", (name ? name.label : "Strategy") + " value at end");
    setText("[data-study-capital-label]", "Opening value + top-ups");
    setText("[data-study-capital-heading]", "Opening + top-ups");
    if (primary) {
      kpis.final.textContent = money(primary.final);
      kpis.put_in.textContent = money(primary.put_in);
      kpis.gain.textContent = signedMoney(primary.gain) + "  (" + percentage(primary.gain_pct) + ")";
      kpis.annual.textContent = percentage(primary.annual);
      kpis.drawdown.textContent = percentage(primary.drawdown);
      kpis.from.textContent = day(view.start);
      tone(kpis.gain, primary.gain);
      tone(kpis.annual, primary.annual);
    }
    Object.keys(rows).forEach(function (key) {
      var item = data.rows[key];
      Object.keys(rows[key]).forEach(function (field) {
        var el = rows[key][field];
        if (!item) { el.textContent = "—"; return; }
        var v = item[field];
        el.textContent = field === "final" || field === "put_in" ? money(v) :
          field === "gain" ? signedMoney(v) : percentage(v);
        if (["gain", "gain_pct", "annual", "drawdown"].includes(field)) tone(el, v);
      });
    });
    bestLabels.forEach(function (b) { b.hidden = true; }); // historical full-run "Best" no longer describes a zoom
    status.textContent = "Selected-period figures use each approach's opening value and subsequent top-ups; trade counts remain whole-run totals.";
    root.querySelector("[data-study-rows]").setAttribute("aria-busy", "false");
  }
  function placeholder() {
    Object.keys(rows).forEach(function (key) {
      Object.keys(rows[key]).forEach(function (field) { rows[key][field].textContent = "—"; });
    });
    Object.keys(kpis).forEach(function (key) {
      if (key !== "from") kpis[key].textContent = "—";
    });
    kpis.from.textContent = day(view.start);
    setText("[data-study-comparison-title]", "Compared over " + day(view.start) + " – " + day(view.end));
    setText("[data-study-capital-label]", "Opening value + top-ups");
    setText("[data-study-capital-heading]", "Opening + top-ups");
    bestLabels.forEach(function (b) { b.hidden = true; });
    root.querySelector("[data-study-rows]").setAttribute("aria-busy", "true");
    status.textContent = "Calculating selected-period results…";
  }
  function updateFigures() {
    clearTimeout(timer);
    var request = ++currentRequest;
    if (full()) { restore(); return; }
    placeholder();
    timer = setTimeout(function () {
      var q = new URLSearchParams({
        amount: root.dataset.studyAmount, monthly: root.dataset.studyMonthly,
        range: root.dataset.studyRange, fill: root.dataset.studyFill,
        window_start: String(view.start), window_end: String(view.end)
      });
      fetch(root.dataset.studyWindowUrl + "?" + q.toString(),
            {headers:{Accept:"application/json"}})
        .then(function (response) { if (!response.ok) throw new Error("Period unavailable"); return response.json(); })
        .then(function (data) {
          if (request !== currentRequest || data.start_index !== view.start ||
              data.end_index !== view.end) return;
          paint(data);
        })
        .catch(function () {
          if (request !== currentRequest) return;
          status.textContent = "Selected-period figures unavailable. Adjust the range or reset zoom.";
          root.querySelector("[data-study-rows]").setAttribute("aria-busy", "false");
        });
    }, 140);
  }
  function samples(a, b, limit) {
    var out=[], step=Math.max(1,Math.ceil((b-a)/Math.max(1,limit-1)));
    for(var i=a;i<=b;i+=step)out.push(i);
    if(out[out.length-1]!==b)out.push(b);
    return out;
  }
  function line(target, arr, ticks, a, b, y, cls) {
    var p=document.createElementNS(NS,"polyline");
    p.setAttribute("class", cls);
    p.setAttribute("points", ticks.map(function(i) {
      return ((i-a)/Math.max(1,b-a)*800).toFixed(1)+","+y(arr[i]).toFixed(1);
    }).join(" "));
    target.appendChild(p);
  }
  function plot(target,a,b,height,limit) {
    target.querySelectorAll("polyline").forEach(function(n) { n.remove(); });
    var lines=values.map(function(row) {return {css:row.css,pts:row.points};});
    lines.push({css:"w2-c4",pts:invested});
    var min=Infinity,max=-Infinity;
    lines.forEach(function(r) {
      for(var i=a;i<=b;i++){
        var v=r.pts[i];
        if(v>0&&Number.isFinite(v)){min=Math.min(min,v);max=Math.max(max,v);}
      }
    });
    if(!Number.isFinite(max))return;
    var f=log?Math.log10:function(x){return x;};
    var low=log?f(min):0,high=f(max),span=(high-low)||1;
    var y=function(v){return height-(f(Math.max(v,0.000001))-low)/span*height;};
    var ticks=samples(a,b,limit);
    lines.forEach(function(r){line(target,r.pts,ticks,a,b,y,r.css);});
    if(target===svg){
      var fmtY=function(v){return currency+fmt.format(log?Math.pow(10,v):v);};
      root.querySelector('[data-study-y="0"]').textContent=fmtY(high);
      root.querySelector('[data-study-y="1"]').textContent=fmtY(low+span/2);
      root.querySelector('[data-study-y="2"]').textContent=fmtY(low);
      var axis=root.querySelector("[data-study-ticks]");
      axis.textContent="";
      for(var t=0;t<5;t++){
        var label=document.createElement("span");
        label.textContent=month(Math.round(a+(b-a)*t/4));
        axis.appendChild(label);
      }
    }
  }
  function redraw() {
    plot(svg,view.start,view.end,200,800);
    // Repaint miniature only when scale changes, not on every handle motion.
    var left=view.start/last*100,right=view.end/last*100;
    windowEl.style.left=left+"%";
    windowEl.style.width=(right-left)+"%";
    startEl.style.left=left+"%";
    endEl.style.left=right+"%";
    startEl.style.transform=view.start===0?"translateX(0)":"translateX(-50%)";
    endEl.style.transform=view.end===last?"translateX(-100%)":"translateX(-50%)";
    root.querySelector("[data-study-before]").style.width=left+"%";
    var shade=root.querySelector("[data-study-after]");
    shade.style.left=right+"%";
    shade.style.width=(100-right)+"%";
    setText("[data-study-dates]", day(view.start)+" – "+day(view.end));
    reset.disabled=full();
    [[startEl,view.start,0,view.end-2,day(view.start)],
     [endEl,view.end,view.start+2,last,day(view.end)],
     [windowEl,view.start,0,last-(view.end-view.start),
      day(view.start)+" to "+day(view.end)]].forEach(function(item) {
      item[0].setAttribute("aria-valuemin",item[2]);
      item[0].setAttribute("aria-valuemax",item[3]);
      item[0].setAttribute("aria-valuenow",item[1]);
      item[0].setAttribute("aria-valuetext",item[4]);
    });
    hideTip();
  }
  function setView(a,b) {
    a=Math.max(0,Math.min(last-2,Math.round(a)));
    b=Math.max(a+2,Math.min(last,Math.round(b)));
    if(a===view.start&&b===view.end)return;
    view={start:a,end:b};
    redraw();
    updateFigures();
  }
  function pointerIndex(e) {
    var r=track.getBoundingClientRect();
    return number((e.clientX-r.left)/Math.max(1,r.width)*last);
  }
  track.addEventListener("pointerdown",function(e){
    if(e.button!==0)return;
    var handle=e.target.closest("[data-study-handle]");
    var within=e.target.closest("[data-study-window]");
    var kind=handle?handle.dataset.studyHandle:"window";
    if(!handle&&!within){
      var width=view.end-view.start;
      var start=Math.max(0,Math.min(last-width,pointerIndex(e)-Math.round(width/2)));
      setView(start,start+width);
    }
    drag={id:e.pointerId,kind:kind,origin:pointerIndex(e),start:view.start,end:view.end};
    track.setPointerCapture(e.pointerId);e.preventDefault();
  });
  track.addEventListener("pointermove",function(e){
    if(!drag||drag.id!==e.pointerId)return;
    var n=pointerIndex(e);
    if(drag.kind==="start")setView(Math.min(n,view.end-2),view.end);
    else if(drag.kind==="end")setView(view.start,Math.max(n,view.start+2));
    else{
      var len=drag.end-drag.start;
      var start=Math.max(0,Math.min(last-len,drag.start+n-drag.origin));
      setView(start,start+len);
    }
  });
  function stop(e){if(drag&&drag.id===e.pointerId)drag=null;}
  track.addEventListener("pointerup",stop);
  track.addEventListener("pointercancel",stop);
  track.addEventListener("lostpointercapture",stop);
  [startEl,endEl,windowEl].forEach(function(el){
    el.addEventListener("keydown",function(e){
      if(!["ArrowLeft","ArrowRight","Home","End"].includes(e.key))return;
      var d=e.key==="ArrowRight"?(e.shiftKey?10:1):-(e.shiftKey?10:1);
      var kind=el.dataset.studyHandle||"window";
      if(kind==="start")setView(e.key==="Home"?0:e.key==="End"?view.end-2:view.start+d,view.end);
      else if(kind==="end")setView(view.start,e.key==="Home"?view.start+2:e.key==="End"?last:view.end+d);
      else{
        var len=view.end-view.start;
        var a=e.key==="Home"?0:e.key==="End"?last-len:
          Math.max(0,Math.min(last-len,view.start+d));
        setView(a,a+len);
      }
      e.preventDefault();
    });
  });
  reset.addEventListener("click",function(){setView(0,last);});
  var cursor=document.createElementNS(NS,"line");
  cursor.setAttribute("class","study-zoom-cursor");
  cursor.setAttribute("y1","0");cursor.setAttribute("y2","200");
  function hideTip(){tip.hidden=true;if(cursor.parentNode)cursor.remove();}
  function showTip(clientX) {
    var r=svg.getBoundingClientRect();
    var fraction=Math.min(1,Math.max(0,(clientX-r.left)/Math.max(1,r.width)));
    var i=Math.round(view.start+fraction*(view.end-view.start));
    var x=(i-view.start)/Math.max(1,view.end-view.start)*800;
    cursor.setAttribute("x1",x.toFixed(1));cursor.setAttribute("x2",x.toFixed(1));
    svg.appendChild(cursor);
    tip.textContent="";
    function add(label,val,css) {
      var line=document.createElement("div");
      line.className="study-zoom-tip-row";
      var a=document.createElement("span");a.textContent=label;a.className=css||"";
      var b=document.createElement("strong");b.textContent=money(val);
      line.appendChild(a);line.appendChild(b);tip.appendChild(line);
    }
    var title=document.createElement("strong");
    title.textContent=day(i);title.className="study-zoom-tip-date";tip.appendChild(title);
    values.forEach(function(row){add(row.label,row.points[i],row.css);});
    add("Money put in by then",invested[i]);
    tip.hidden=false;
    var left=fraction*r.width;
    var width=tip.offsetWidth;
    tip.style.left=Math.max(0,Math.min(r.width-width,
      left>r.width/2?left-width-12:left+12))+"px";
  }
  svg.addEventListener("pointermove",function(e){showTip(e.clientX);});
  svg.addEventListener("pointerdown",function(e){showTip(e.clientX);});
  svg.addEventListener("pointerleave",function(e){if(e.pointerType==="mouse")hideTip();});
  root.querySelectorAll('[aria-label="Scale"] a').forEach(function(a){
    a.addEventListener("click",function(e){
      e.preventDefault();
      log=a.textContent.trim()==="Log";
      root.querySelectorAll('[aria-label="Scale"] a').forEach(function(other){
        other.classList.toggle("w2-on",other===a);
        if(other===a)other.setAttribute("aria-current","true");
        else other.removeAttribute("aria-current");
      });
      plot(mini,0,last,60,220);
      redraw();
    });
  });
  plot(mini,0,last,60,220);
  redraw();
  restore();
})();
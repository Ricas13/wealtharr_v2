/* Small progressive enhancements; charts and navigation work without a framework. */
(function () {
  'use strict';
  var root=document.documentElement;
  var toggle=document.querySelector('[data-theme-toggle]');
  var label=document.querySelector('.ws-theme-label');
  function apply(name){
    var light=name==='light';
    root.dataset.theme=light?'light':'dark';
    if(toggle){toggle.setAttribute('aria-pressed',String(light));
      toggle.setAttribute('aria-label',light?'Switch to dark theme':'Switch to light theme');}
    if(label)label.textContent=light?'Dark mode':'Light mode';
  }
  var theme='dark';
  try{theme=localStorage.getItem('wealtharr-theme')||'dark';}catch(e){}
  apply(theme);
  if(toggle)toggle.addEventListener('click',function(){
    theme=root.dataset.theme==='light'?'dark':'light';
    try{localStorage.setItem('wealtharr-theme',theme);}catch(e){}
    apply(theme);
  });
  var chart=document.querySelector('#wealth-chart, #strategy-chart');
  var source=document.querySelector('#wealth-chart-points, #strategy-chart-points');
  if(!chart||!source)return;
  var points=[];
  try{points=JSON.parse(source.textContent||'[]');}catch(e){}
  points.sort(function(a,b){return a.date.localeCompare(b.date);});
  var formatter=new Intl.NumberFormat('en-GB',{style:'currency',currency:'GBP'});
  var active='all';
  function visible(){
    if(active==='all')return points;
    var cutoff=new Date();
    if(active==='1m')cutoff.setMonth(cutoff.getMonth()-1);
    if(active==='3m')cutoff.setMonth(cutoff.getMonth()-3);
    if(active==='1y')cutoff.setFullYear(cutoff.getFullYear()-1);
    return points.filter(function(p){return new Date(p.date+'T12:00:00')>=cutoff;});
  }
  function enhance(){
    var rows=visible();
    var circles=Array.from(chart.querySelectorAll('svg [data-point]'));
    circles.forEach(function(circle,i){
      if(!rows[i])return;
      circle.setAttribute('tabindex','0');
      circle.setAttribute('role','img');
      circle.setAttribute('aria-label',rows[i].date+': value '+formatter.format(Number(rows[i].value))+', contributions '+formatter.format(Number(rows[i].contributed)));
      circle.addEventListener('focus',function(){
        circle.dispatchEvent(new Event('mouseenter'));
        var box=chart.getBoundingClientRect();
        circle.dispatchEvent(new MouseEvent('mousemove',{clientX:box.left+box.width/2,clientY:box.top+box.height/2}));
      });
      circle.addEventListener('blur',function(){circle.dispatchEvent(new Event('mouseleave'));});
      circle.addEventListener('keydown',function(e){
        if(e.key==='ArrowRight'&&circles[i+1]){e.preventDefault();circles[i+1].focus();}
        if(e.key==='ArrowLeft'&&circles[i-1]){e.preventDefault();circles[i-1].focus();}
        if(e.key==='Escape')circle.blur();
      });
    });
  }
  var controls=Array.from(document.querySelectorAll('.chart-controls [data-range]'));
  controls.forEach(function(button){
    button.addEventListener('click',function(){
      active=button.dataset.range;
      controls.forEach(function(other){other.setAttribute('aria-pressed',String(other===button));});
      // site.js registers its redraw handler first and has already rebuilt SVG.
      enhance();
    });
  });
  enhance();
})();
/* PulseGuard console behaviour. Vanilla JS, no dependencies. */
(function(){
"use strict";
var $=function(s,r){return (r||document).querySelector(s)},$$=function(s,r){return Array.prototype.slice.call((r||document).querySelectorAll(s))};
var store={get:function(k){try{return localStorage.getItem(k)}catch(e){return null}},set:function(k,v){try{localStorage.setItem(k,v)}catch(e){}}};
var reduce=window.matchMedia&&matchMedia("(prefers-reduced-motion:reduce)").matches;
var NS="http://www.w3.org/2000/svg";
function el(n,a){var e=document.createElementNS(NS,n);for(var k in a)e.setAttribute(k,a[k]);return e}

/* theme */
var root=document.documentElement,t0=store.get("pg-theme");if(t0)root.setAttribute("data-theme",t0);
function isLight(){var t=root.getAttribute("data-theme");return t?t==="light":matchMedia("(prefers-color-scheme:light)").matches}
$$("[data-theme-toggle]").forEach(function(b){b.addEventListener("click",function(){var n=isLight()?"dark":"light";root.setAttribute("data-theme",n);store.set("pg-theme",n);toast("ok","Theme set to "+n)})});

/* sidebar */
var app=$(".app");
if(app){
  if(store.get("pg-collapsed")==="1")app.classList.add("is-collapsed");
  $$("[data-collapse]").forEach(function(b){b.addEventListener("click",function(){app.classList.toggle("is-collapsed");store.set("pg-collapsed",app.classList.contains("is-collapsed")?"1":"0")})});
  $$("[data-menu]").forEach(function(b){b.addEventListener("click",function(){app.classList.toggle("drawer")})});
  document.addEventListener("click",function(e){if(app.classList.contains("drawer")&&!e.target.closest(".side")&&!e.target.closest("[data-menu]"))app.classList.remove("drawer")});
}

/* toast */
function toast(kind,msg){var h=$(".toasts");if(!h){h=document.createElement("div");h.className="toasts";h.setAttribute("aria-live","polite");document.body.appendChild(h)}
  var t=document.createElement("div");t.className="toast "+kind;t.innerHTML='<svg class="i"><use href="#'+(kind==="bad"?"x-circle":"check-circle")+'"/></svg><span></span>';t.lastChild.textContent=msg;h.appendChild(t);setTimeout(function(){t.style.transition="opacity .3s";t.style.opacity=0;setTimeout(function(){t.remove()},300)},2800)}
window.pgToast=toast;

/* copy */
document.addEventListener("click",function(e){var b=e.target.closest("[data-copy]");if(!b)return;var v=b.getAttribute("data-copy");
  if(navigator.clipboard)navigator.clipboard.writeText(v).catch(function(){});toast("ok","Copied to clipboard")});

/* count up */
function countUp(n){var to=parseFloat(n.getAttribute("data-count")),dec=+(n.getAttribute("data-dec")||0),suf=n.getAttribute("data-suf")||"";
  function set(v){n.innerHTML=v.toFixed(dec)+(suf?"<small>"+suf+"</small>":"")}
  if(reduce){set(to);return}var s=performance.now(),d=900;
  (function f(t){var p=Math.min(1,(t-s)/d);set(to*(1-Math.pow(1-p,3)));if(p<1)requestAnimationFrame(f)})(s)}
$$("[data-count]").forEach(countUp);

/* sparkline: data-spark="1,2,3" */
function spark(n){var v=n.getAttribute("data-spark").split(",").map(Number).filter(function(x){return !isNaN(x)});if(v.length<2)return;var w=120,h=36,mn=Math.min.apply(0,v),mx=Math.max.apply(0,v),r=(mx-mn)||1;
  var pts=v.map(function(y,i){return [i*(w/(v.length-1)),h-3-((y-mn)/r)*(h-8)]});
  var d=pts.map(function(p,i){return(i?"L":"M")+p[0].toFixed(1)+" "+p[1].toFixed(1)}).join(" ");
  var id="s"+Math.random().toString(36).slice(2,7);
  var s=el("svg",{viewBox:"0 0 "+w+" "+h,preserveAspectRatio:"none","aria-hidden":"true"});
  var g=el("linearGradient",{id:id,x1:0,y1:0,x2:0,y2:1});g.appendChild(el("stop",{offset:"0","stop-color":"currentColor","stop-opacity":".28"}));g.appendChild(el("stop",{offset:"1","stop-color":"currentColor","stop-opacity":"0"}));
  var df=el("defs",{});df.appendChild(g);s.appendChild(df);
  s.appendChild(el("path",{d:d+" L"+w+" "+h+" L0 "+h+" Z",fill:"url(#"+id+")"}));
  s.appendChild(el("path",{d:d,fill:"none",stroke:"currentColor","stroke-width":"1.8","stroke-linecap":"round","stroke-linejoin":"round","vector-effect":"non-scaling-stroke"}));
  n.appendChild(s)}
$$("[data-spark]").forEach(spark);

/* uptime bars: data-bar="o48" pattern string: o=ok x=down g=gap w=warn */
$$("[data-bar]").forEach(function(b){var p=b.getAttribute("data-bar"),map={o:"",x:"bad",g:"gap",w:"warn"},lab={o:"Operational",x:"Down",g:"No data",w:"Degraded"};
  for(var i=0;i<p.length;i++){var s=document.createElement("i");if(map[p[i]])s.className=map[p[i]];s.title=lab[p[i]];b.appendChild(s)}});

/* countdown rings */
$$("[data-cd]").forEach(function(n){var tot=+n.getAttribute("data-total")||60,left=+n.getAttribute("data-cd");var lab=$("span",n);
  function paint(){n.style.setProperty("--p",((tot-left)/tot*100).toFixed(1));lab.textContent=left>=100?Math.round(left/60)+"m":left+"s"}paint();
  setInterval(function(){left--;if(left<0){left=tot;n.classList.add("pulse")}paint()},1000)});

/* tabs filter: [data-tabs] buttons data-f ; targets [data-st] */
$$("[data-tabs]").forEach(function(g){var tg=$(g.getAttribute("data-tabs"));
  $$("button",g).forEach(function(b){b.addEventListener("click",function(){$$("button",g).forEach(function(x){x.setAttribute("aria-selected",x===b)});var f=b.getAttribute("data-f");
    $$("[data-st]",tg).forEach(function(c){c.hidden=!(f==="all"||c.getAttribute("data-st")===f||(f==="attn"&&/down|warn/.test(c.getAttribute("data-st"))))})})})});
$$("[data-search]").forEach(function(i){var tg=$(i.getAttribute("data-search"));i.addEventListener("input",function(){var q=i.value.toLowerCase();$$("[data-st]",tg).forEach(function(c){c.hidden=q&&c.textContent.toLowerCase().indexOf(q)<0})})});

/* switches */
$$(".switch").forEach(function(s){s.addEventListener("click",function(){var on=s.getAttribute("aria-checked")!=="true";s.setAttribute("aria-checked",on);var box=document.getElementById(s.getAttribute("aria-controls")||"");if(box)box.checked=on;toast("ok",(s.getAttribute("data-label")||"Setting")+(on?" enabled":" disabled"))})});

/* busy buttons: data-busy="msg" */
$$("[data-busy]").forEach(function(b){b.addEventListener("click",function(e){e.preventDefault();if(b.classList.contains("is-busy"))return;b.classList.add("is-busy");setTimeout(function(){b.classList.remove("is-busy");toast("ok",b.getAttribute("data-busy"))},1100)})});
$$("[data-toast]").forEach(function(b){b.addEventListener("click",function(e){if(b.tagName==="A"&&b.getAttribute("href")==="#")e.preventDefault();toast(b.getAttribute("data-kind")||"ok",b.getAttribute("data-toast"))})});

/* dialogs */
function open(id){var o=document.getElementById(id);if(o){o.classList.add("on");var i=$("input",o);if(i)setTimeout(function(){i.focus()},30)}}
function close(){$$(".ov.on").forEach(function(o){o.classList.remove("on")})}
$$("[data-open]").forEach(function(b){b.addEventListener("click",function(){open(b.getAttribute("data-open"))})});
$$(".ov").forEach(function(o){o.addEventListener("mousedown",function(e){if(e.target===o)close()})});
$$("[data-close]").forEach(function(b){b.addEventListener("click",close)});

/* palette */
var pal=$("#pal");
if(pal){var inp=$("input",pal),list=$(".pal-list",pal),items=$$("li",list),sel=0;
  function recent(){try{return JSON.parse(store.get("pg-recent")||"[]")}catch(e){return []}}
  function remember(link){if(!link)return;var href=link.getAttribute("href"),text=(link.textContent||"").trim();if(!href)return;var next=[{href:href,text:text}].concat(recent().filter(function(item){return item.href!==href})).slice(0,5);store.set("pg-recent",JSON.stringify(next))}
  function show(){var q=inp.value.toLowerCase(),rec=recent(),vis=items.filter(function(li){var link=$("a",li),href=link?link.getAttribute("href"):"",text=li.textContent.toLowerCase(),m;if(q)m=text.indexOf(q)>=0;else if(rec.length)m=rec.some(function(item){return item.href===href});else m=li.getAttribute("data-page")==="1";li.hidden=!m;return m});sel=0;vis.forEach(function(li,i){li.setAttribute("aria-selected",i===0)});pal._v=vis}
  list.addEventListener("click",function(e){var link=e.target.closest?e.target.closest("a"):null;remember(link)});
  inp.addEventListener("input",show);
  inp.addEventListener("keydown",function(e){var v=pal._v||items;if(e.key==="ArrowDown"||e.key==="ArrowUp"){e.preventDefault();sel=(sel+(e.key==="ArrowDown"?1:-1)+v.length)%v.length;v.forEach(function(li,i){li.setAttribute("aria-selected",i===sel)})}
    if(e.key==="Enter"&&v[sel]){var link=$("a",v[sel]);if(link)location.href=link.getAttribute("href")}});
  document.addEventListener("keydown",function(e){if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==="k"){e.preventDefault();open("pal");inp.value="";show()}});
}
document.addEventListener("keydown",function(e){if(e.key==="Escape")close()});

/* activity log */
var log=$("[data-log]");
function paintLog(events){if(!log||!events||!events.length)return;log.textContent="";
  events.slice(0,10).forEach(function(ev){var r=document.createElement("div"),when=new Date(ev.at||""),ts="";
    if(!isNaN(when.getTime()))ts=when.toLocaleTimeString([],{hour:"numeric",minute:"2-digit"});
    r.innerHTML='<span class="ts"></span> <span class="lv"></span> <span></span>';
    r.children[0].textContent=ts;r.children[1].textContent=(ev.state||"info").toUpperCase();
    r.children[1].className="lv "+(ev.state==="down"?"bad":ev.state==="up"?"ok":"wn");
    r.children[2].textContent=ev.text||"";log.appendChild(r)});}
function loadLog(){if(!log)return;fetch("/api/activity",{headers:{Accept:"application/json"}}).then(function(r){return r.ok?r.json():null}).then(function(body){if(body)paintLog(body.events||[])}).catch(function(){})}
if(log){loadLog();if(!reduce)setInterval(loadLog,15000)}

/* probe */
$$("[data-probe]").forEach(function(b){b.addEventListener("click",function(){var box=$(b.getAttribute("data-probe")),form=b.closest("form");if(!box||!form)return;
  if(window.pgSync)window.pgSync(form);b.classList.add("is-busy");box.className="result";box.textContent="Testing…";
  fetch("/monitors/probe",{method:"POST",body:new FormData(form),headers:{Accept:"application/json"}}).then(function(r){return r.json().then(function(j){return {ok:r.ok,body:j}})}).then(function(res){
    b.classList.remove("is-busy");
    if(!res.ok||res.body.error){box.className="result on down";box.textContent=res.body.error||"Check failed";return}
    box.className="result on "+(res.body.ok?"up":"down");box.textContent=(res.body.latency_ms==null?"":res.body.latency_ms+" ms")+(res.body.message?" · "+res.body.message:"")}).catch(function(){b.classList.remove("is-busy");box.className="result on down";box.textContent="Check failed"})})});

/* wizard */
var wiz=$("[data-wizard]");
if(wiz){var secs=$$(".sect",wiz.parentNode),steps=$$(".w",wiz),cur=0;
  var kind=wiz.getAttribute("data-initial")||"http";
  function go(n){cur=Math.max(0,Math.min(secs.length-1,n));secs.forEach(function(s,i){s.classList.toggle("on",i===cur)});steps.forEach(function(s,i){s.classList.toggle("on",i===cur);s.classList.toggle("done",i<cur)});
    if(cur===secs.length-1)review()}
  $$("[data-next]").forEach(function(b){b.addEventListener("click",function(){
    if(cur===1){var t=$("#f-target"),n=$("#f-name"),bad=false;[n,t].forEach(function(i){var f=i.closest(".field"),v=i.value.trim();var ok=i===n?/^[A-Za-z0-9_-]{1,128}$/.test(v):(/^(tcp|dns|tls)$/.test(kind)?/^[A-Za-z0-9.-]+$/.test(v):/^https?:\/\//.test(v));if(kind==="heartbeat")ok=i===n?ok:true;f.classList.toggle("bad",!ok);if(!ok)bad=true});if(bad)return}
    go(cur+1)})});
  $$("[data-prev]").forEach(function(b){b.addEventListener("click",function(){go(cur-1)})});
  function applyKind(name){kind=name;var hid=$("#f-kind");if(hid)hid.value=name;$$(".kind").forEach(function(x){x.setAttribute("aria-pressed",x.getAttribute("data-kind")===name)});
  $$("[data-only]").forEach(function(f){f.hidden=f.getAttribute("data-only").split(" ").indexOf(kind)<0});
  var t=$("#f-target");if(t){t.placeholder=/^(tcp|dns|tls)$/.test(kind)?"example.com":"https://example.com"}}
$$(".kind").forEach(function(k){k.addEventListener("click",function(){applyKind(k.getAttribute("data-kind"))})});applyKind(kind);
  function review(){var set=function(id,v){var n=document.getElementById(id);if(n)n.textContent=v};var iv=$("#f-int"),rv=$("[name=retries]");set("r-kind",kind);set("r-name",($("#f-name")||{}).value||"-");set("r-target",($("#f-target")||{}).value||"-");set("r-int",(iv?iv.value:"60")+" s");set("r-ret",(rv?rv.value:"3")+" attempts")}
  $$(".stepper").forEach(function(s){var o=$("input",s)||$("output",s),b=$$("button",s),mn=+s.getAttribute("data-min"),mx=+s.getAttribute("data-max"),st=+s.getAttribute("data-step")||1;
    function read(){return +(o.value!=null&&o.tagName==="INPUT"?o.value:o.textContent)||0}
    function write(v){if(o.tagName==="INPUT")o.value=String(v);else o.textContent=String(v)}
    b[0].addEventListener("click",function(){write(Math.max(mn,read()-st))});b[1].addEventListener("click",function(){write(Math.min(mx,read()+st))})});
  window.pgSync=function(){};
  window.pgInt=function(){var n=$("#f-int");return n?n.value:"60"};
  go(0)}

/* chart */
var ch=$("[data-chart]");
if(ch){var tip=$(".tip",ch.parentNode),raw=[],node=document.getElementById("chart-data");
  try{raw=node?JSON.parse(node.textContent):[]}catch(e){raw=[]}
  var W=1000,H=280,P={l:46,r:12,t:16,b:26},cur2="24h";
  function hours(k){return k==="1h"?1:k==="7d"?24*7:k==="30d"?24*30:24}
  function series(){var cut=Date.now()-hours(cur2)*3600000;var rows=raw.filter(function(row){var t=new Date(row[0]).getTime();return !isNaN(t)&&t>=cut});return rows.length?rows:raw.slice(-Math.min(raw.length,96))}
  function draw(){var rows=series(),vals=rows.map(function(row){return row[1]==null?null:Number(row[1])}),nums=vals.filter(function(v){return v!=null&&!isNaN(v)});
    ch.textContent="";if(!nums.length){var empty=el("text",{x:W/2,y:H/2,"text-anchor":"middle"});empty.textContent="no data";ch.appendChild(empty);var sp=document.getElementById("c-p50");if(sp){sp.textContent="—";var sq=document.getElementById("c-p95");if(sq)sq.textContent="—"}return}
    var n=vals.length,mx=Math.max(1,Math.ceil(Math.max.apply(0,nums)/50)*50);
    var X=function(i){return P.l+(n<2?0:i*(W-P.l-P.r)/(n-1))},Y=function(v){return H-P.b-(v/mx)*(H-P.t-P.b)};
    var g=el("g",{"class":"grid"});for(var j=0;j<=4;j++){var y=Y(mx*j/4);g.appendChild(el("line",{x1:P.l,x2:W-P.r,y1:y,y2:y}));var tx=el("text",{x:P.l-8,y:y+4,"text-anchor":"end"});tx.textContent=Math.round(mx*j/4);g.appendChild(tx)}ch.appendChild(g);
    var path="",open=false;vals.forEach(function(v,i){if(v==null||isNaN(v)){open=false;return}path+=(open?"L":"M")+X(i).toFixed(1)+" "+Y(v).toFixed(1)+" ";open=true});
    vals.forEach(function(v,i){if(v==null){var x0=X(i),x1=X(Math.min(n-1,i+1));ch.appendChild(el("rect",{x:x0,y:P.t,width:Math.max(2,x1-x0),height:H-P.t-P.b,fill:"var(--line)",opacity:".7"}))}});
    var sorted=nums.slice().sort(function(a,b){return a-b}),p50=sorted[Math.floor((sorted.length-1)*.5)],p95=sorted[Math.min(sorted.length-1,Math.floor((sorted.length-1)*.95))];
    ch.appendChild(el("line",{"class":"p50",x1:P.l,x2:W-P.r,y1:Y(p50),y2:Y(p50)}));ch.appendChild(el("line",{"class":"p95",x1:P.l,x2:W-P.r,y1:Y(p95),y2:Y(p95)}));
    if(path){var ln=el("path",{"class":"line",d:path});ch.appendChild(ln)}
    var s50=document.getElementById("c-p50");if(s50){s50.textContent=Math.round(p50)+" ms";var s95=document.getElementById("c-p95");if(s95)s95.textContent=Math.round(p95)+" ms"}}
  $$("[data-range]").forEach(function(b){b.addEventListener("click",function(){$$("[data-range]").forEach(function(x){x.setAttribute("aria-selected",x===b)});cur2=b.getAttribute("data-range");draw()})});
  draw()}

/* SLA */
var sla=$("[data-sla]");
if(sla){var a=$("#s-t"),dn=$("#s-d"),pr=$("#s-p");
  function calc(){var tgt=parseFloat(a.value),days={"30 days":30,"90 days":90,"365 days":365}[pr.value]||30,mins=days*1440,allowed=mins*(1-tgt/100),down=parseFloat(dn.value)||0,ach=100*(1-down/mins);
    var ok=ach>=tgt,C=2*Math.PI*84,frac=Math.max(0,Math.min(1,(ach-90)/10));
    $("#s-allow").textContent=allowed.toFixed(1)+" min";$("#s-ach").textContent=ach.toFixed(3)+"%";$("#s-left").textContent=Math.max(0,allowed-down).toFixed(1)+" min";
    var g=$(".gauge .val"),box=$(".gauge");g.setAttribute("stroke-dasharray",C);g.setAttribute("stroke-dashoffset",C*(1-frac));box.style.setProperty("--c",ok?"var(--ok)":"var(--danger)");$("#s-g").textContent=ach.toFixed(2)+"%";
    var v=$("#s-v");v.className="pill "+(ok?"up":"down");v.textContent=ok?"Target met":"Target missed"}
  [a,dn,pr].forEach(function(i){i.addEventListener("input",calc)});$$("[data-t]").forEach(function(b){b.addEventListener("click",function(){a.value=b.getAttribute("data-t");$$("[data-t]").forEach(function(x){x.setAttribute("aria-pressed",x===b)});calc()})});calc()}

/* api key reveal / generate token */
$$("[data-gen]").forEach(function(b){b.addEventListener("click",function(){var form=b.closest("form");if(form)form.requestSubmit?form.requestSubmit():form.submit()})});

/* drop zone */
$$(".drop").forEach(function(d){["dragover","dragenter"].forEach(function(ev){d.addEventListener(ev,function(e){e.preventDefault();d.classList.add("over")})});["dragleave","drop"].forEach(function(ev){d.addEventListener(ev,function(e){e.preventDefault();d.classList.remove("over");if(ev==="drop"&&e.dataTransfer&&e.dataTransfer.files.length){var input=$("input[type=file]",d);if(input)input.files=e.dataTransfer.files;var name=e.dataTransfer.files[0].name;var label=$("b",d);if(label)label.textContent=name}})})});

/* local times, meter widths, and live KPI numbers */
function paintTimes(){$$("time[datetime]").forEach(function(node){var when=new Date(node.getAttribute("datetime")||"");if(isNaN(when.getTime()))return;var text=node.getAttribute("data-clock")==="hms"?when.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"}):when.toLocaleString(undefined,{month:"short",day:"numeric",hour:"numeric",minute:"2-digit"});if(node.textContent!==text)node.textContent=text})}
function paintMeters(){$$(".meter i").forEach(function(n){var w=n.getAttribute("data-w");if(w)n.style.width=w})}
function paintKpis(body){if(!body)return;var total=(body.counts.up||0)+(body.counts.down||0)+(body.counts.unknown||0)+(body.counts.paused||0);
  var map={monitors:total,uptime:body.uptime,incidents:body.open_incidents,checks:body.checks_today,latency:body.avg_ms,cert:body.next_cert};
  Object.keys(map).forEach(function(name){var n=document.querySelector('[data-kpi="'+name+'"]');if(!n||map[name]==null||map[name]==="")return;n.textContent=map[name]});
  (body.monitors||[]).forEach(function(card){$$(".mon").forEach(function(el){var name=el.querySelector(".mon-name");if(!name||name.textContent!==card.name)return;var msg=el.querySelector(".mon-msg");if(msg&&card.plain)msg.textContent=card.plain})})}
paintTimes();paintMeters();
if($("[data-kpis]")){setInterval(function(){fetch("/api/dashboard",{headers:{Accept:"application/json"}}).then(function(r){return r.ok?r.json():null}).then(function(body){paintKpis(body);if(body&&body.activity)paintLog(body.activity)}).catch(function(){})},5000)}
var kindFilter=$("[data-kind-filter]");
if(kindFilter){kindFilter.addEventListener("change",function(){var v=kindFilter.value;$$("#mons [data-kind]").forEach(function(el){el.hidden=v!=="all"&&el.getAttribute("data-kind")!==v})})}
var navMons=$("[data-nav-mons]");
if(navMons){var toggle=$("[data-nav-toggle]",navMons);if(store.get("pg-mon-list")==="0"){navMons.classList.add("is-shut");if(toggle)toggle.setAttribute("aria-expanded","false")}
  if(toggle)toggle.addEventListener("click",function(){var shut=navMons.classList.toggle("is-shut");toggle.setAttribute("aria-expanded",shut?"false":"true");store.set("pg-mon-list",shut?"0":"1")})}
$$("[data-switcher]").forEach(function(s){s.addEventListener("change",function(){if(s.value)location.href=s.value})});
var chords={d:"/dashboard",m:"/monitors",i:"/incidents",c:"/certificates"},armed="";
document.addEventListener("keydown",function(e){var tag=e.target&&e.target.tagName;if(tag&&/INPUT|TEXTAREA|SELECT/.test(tag))return;if(e.metaKey||e.ctrlKey||e.altKey)return;var key=e.key.toLowerCase();
  if(key==="n"){location.href="/monitors/new";return}if(key==="g"){armed="g";return}if(armed==="g"&&chords[key]){location.href=chords[key];return}armed=""});
})();

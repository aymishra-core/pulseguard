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

/* next-check rings follow the snapshot time. A late server shows "due now". */
function paintDue(now){$$("[data-due]").forEach(function(n){var due=new Date(n.getAttribute("data-due")||"").getTime(),tot=+n.getAttribute("data-total")||60,lab=$("span",n),text=pgLive.dueLabel(due,now),left=isNaN(due)?0:Math.round((due-now)/1000);
  if(text==="due now"){n.style.setProperty("--p","100");n.classList.add("pulse")}
  else{n.classList.remove("pulse");n.style.setProperty("--p",((tot-Math.min(left,tot))/tot*100).toFixed(1))}
  if(lab)lab.textContent=text})}
function paintOpenFor(now){$$("[data-opened]").forEach(function(n){var start=new Date(n.getAttribute("data-opened")||"").getTime();if(isNaN(start))return;var seconds=Math.max(0,Math.floor((now-start)/1000)),sec=seconds%60,minutes=Math.floor(seconds/60),hours=Math.floor(minutes/60);minutes%=60;function pad(v){return (v<10?"0":"")+v}n.textContent=pad(hours)+":"+pad(minutes)+":"+pad(sec)})}
paintDue(Date.now());paintOpenFor(Date.now());
setInterval(function(){var now=Date.now();paintDue(now);paintOpenFor(now)},1000);

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
/* The log starts from the snapshot. Later rows arrive from /api/activity?since. */

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
  $$("[data-next]").forEach(function(b){b.addEventListener("click",function(){if(cur===1&&!validateTarget())return;go(cur+1)})});
  $$("[data-prev]").forEach(function(b){b.addEventListener("click",function(){go(cur-1)})});
  var port=$("#port");
  if(port)port.addEventListener("input",function(){port._auto=false});
  function applyKind(name){kind=name;var hid=$("#f-kind");if(hid)hid.value=name;$$(".kind").forEach(function(x){x.setAttribute("aria-pressed",x.getAttribute("data-kind")===name)});
  $$("[data-only]").forEach(function(f){f.hidden=f.getAttribute("data-only").split(" ").indexOf(kind)<0});
  var t=$("#f-target"),msg=pgForm.messages[kind]||"";if(t)t.placeholder=pgForm.placeholders[kind]||"";
  var hint=$("[data-target-hint]"),err=$("[data-target-err]");if(hint)hint.textContent=msg;if(err)err.textContent=msg;
  if(port){if(kind==="tls"&&port.value.trim()===""){port.value="443";port._auto=true}else if(kind==="tcp"&&port._auto){port.value="";port._auto=false}}
  paintFix()}
  function paintFix(){var t=$("#f-target"),b=$("[data-fix]");if(!t||!b)return;var found=pgForm.urlFix(kind,t.value);b.hidden=!found;b.textContent=found?found.label:""}
  function validateTarget(){["#f-name","#f-target","#port"].forEach(function(id){var node=$(id);if(node&&node.closest(".field"))node.closest(".field").classList.remove("bad")});
    var name=$("#f-name"),target=$("#f-target"),problems=pgForm.stepProblems({kind:kind,name:name?name.value:"",editing:name&&name.hasAttribute("readonly"),target:target?target.value:"",port:port?port.value:""});
    problems.forEach(function(item){var node=item.field==="name"?name:item.field==="port"?port:target;if(!node)return;var field=node.closest(".field");if(!field)return;field.classList.add("bad");var err=$(".err",field);if(err)err.textContent=item.message});
    paintFix();return problems.length===0}
  var targetInput=$("#f-target");
  if(targetInput)targetInput.addEventListener("input",paintFix);
  var fix=$("[data-fix]");
  if(fix)fix.addEventListener("click",function(){var t=$("#f-target"),found=t?pgForm.urlFix(kind,t.value):null;if(!t||!found)return;t.value=found.host;if((kind==="tcp"||kind==="tls")&&port){port.value=String(found.port);port._auto=false}var field=t.closest(".field");if(field)field.classList.remove("bad");if(port&&pgForm.portOk(port.value)){var pf=port.closest(".field");if(pf)pf.classList.remove("bad")}paintFix()});
  $$(".kind").forEach(function(k){k.addEventListener("click",function(){applyKind(k.getAttribute("data-kind"))})});applyKind(kind);
  wireDeps();
  function review(){var set=function(id,v){var n=document.getElementById(id);if(n)n.textContent=v};var iv=$("#f-int"),rv=$("[name=retries]");set("r-kind",kind);set("r-name",($("#f-name")||{}).value||"-");set("r-target",($("#f-target")||{}).value||"-");set("r-int",(iv?iv.value:"60")+" s");set("r-ret",(rv?rv.value:"3")+" attempts")}
  $$(".stepper").forEach(function(s){var o=$("input",s)||$("output",s),b=$$("button",s),mn=+s.getAttribute("data-min"),mx=+s.getAttribute("data-max"),st=+s.getAttribute("data-step")||1;
    function read(){return +(o.value!=null&&o.tagName==="INPUT"?o.value:o.textContent)||0}
    function write(v){if(o.tagName==="INPUT")o.value=String(v);else o.textContent=String(v)}
    b[0].addEventListener("click",function(){write(Math.max(mn,read()-st))});b[1].addEventListener("click",function(){write(Math.min(mx,read()+st))})});
  window.pgSync=function(){};
  window.pgInt=function(){var n=$("#f-int");return n?n.value:"60"};
  go(+(wiz.getAttribute("data-step")||0))}
function wireDeps(){var root=$("[data-dep]");if(!root)return;var toggle=$(".dep-btn",root),pop=$("[data-dep-pop]",root),list=$(".dep-list",root),chips=$("[data-dep-chips]",root),fields=$("[data-dep-fields]",root),search=$(".dep-search",root),limit=$("[data-dep-limit]",root),max=+(root.getAttribute("data-max")||8),sel=0;
  function chosen(){return $$("input",fields).map(function(n){return n.value})}
  function visible(){return $$("[role=option]",list).filter(function(li){return !li.hidden})}
  function paintLabel(){var count=chosen().length;toggle.textContent=pgForm.menuLabel(count);toggle.disabled=count>=max;if(limit)limit.hidden=count<max}
  function hideChosen(){var have={};chosen().forEach(function(name){have[name]=true});$$("[role=option]",list).forEach(function(li){var q=search?search.value.trim().toLowerCase():"";li.hidden=!!have[li.getAttribute("data-name")]||(q&&li.textContent.toLowerCase().indexOf(q)<0)})}
  function mark(){var rows=visible();rows.forEach(function(li,i){li.setAttribute("aria-selected",i===sel)});if(rows[sel])rows[sel].scrollIntoView({block:"nearest"})}
  function open(){if(toggle.disabled)return;hideChosen();sel=0;pop.hidden=false;toggle.setAttribute("aria-expanded","true");mark();if(search)search.focus();else if(visible()[0])visible()[0].focus()}
  function close(){pop.hidden=true;toggle.setAttribute("aria-expanded","false");toggle.focus()}
  function add(name){if(!name)return;var next=pgForm.addChoice(chosen(),name,max);if(next.length===chosen().length)return;var chip=document.createElement("button");chip.type="button";chip.className="dep-chip";chip.setAttribute("data-dep-remove",name);chip.setAttribute("aria-label","Remove "+name);chip.textContent=name;var x=document.createElement("span");x.setAttribute("aria-hidden","true");x.textContent="×";chip.appendChild(x);chips.appendChild(chip);var input=document.createElement("input");input.type="hidden";input.name="depends_on";input.value=name;fields.appendChild(input);var row=$("[data-name='"+name.replace(/'/g,"\\'")+"']",list);if(row)row.hidden=true;paintLabel();close()}
  function remove(name){$$("[data-dep-remove]",chips).forEach(function(chip){if(chip.getAttribute("data-dep-remove")===name)chip.remove()});$$("input",fields).forEach(function(input){if(input.value===name)input.remove()});var row=$("[data-name='"+name.replace(/'/g,"\\'")+"']",list);if(row)row.hidden=false;paintLabel()}
  toggle.addEventListener("click",function(){if(pop.hidden)open();else close()});
  if(search)search.addEventListener("input",function(){sel=0;hideChosen();mark()});
  list.addEventListener("click",function(e){var li=e.target.closest?e.target.closest("[role=option]"):null;if(li&&!li.hidden)add(li.getAttribute("data-name"))});
  chips.addEventListener("click",function(e){var chip=e.target.closest?e.target.closest("[data-dep-remove]"):null;if(chip)remove(chip.getAttribute("data-dep-remove"))});
  root.addEventListener("keydown",function(e){var rows=visible();if(e.key==="Escape"){if(!pop.hidden){e.preventDefault();close()}return}if(e.key==="ArrowDown"){e.preventDefault();if(pop.hidden)open();else{sel=Math.min(rows.length-1,sel+1);mark()}}if(e.key==="ArrowUp"){e.preventDefault();if(!pop.hidden){sel=Math.max(0,sel-1);mark()}}if(e.key==="Enter"&&!pop.hidden){e.preventDefault();if(rows[sel])add(rows[sel].getAttribute("data-name"))}});
  document.addEventListener("click",function(e){if(!pop.hidden&&!root.contains(e.target))close()});
  paintLabel()}

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
function paintTimes(){$$("time[datetime]").forEach(function(node){var when=new Date(node.getAttribute("datetime")||"");if(isNaN(when.getTime()))return;var mode=node.getAttribute("data-clock"),text;if(mode==="hms")text=when.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"});else if(mode==="hm")text=when.toLocaleTimeString([],{hour:"2-digit",minute:"2-digit",hourCycle:"h23"});else text=when.toLocaleString(undefined,{month:"short",day:"numeric",hour:"numeric",minute:"2-digit"});if(node.textContent!==text)node.textContent=text})}
function paintMeters(){$$(".meter i").forEach(function(n){var w=n.getAttribute("data-w");if(w)n.style.width=w})}
function applySnapshot(snap){if(!snap||!snap.counts)return;var open=(snap.open_incidents||[]).length,latency=snap.mean_latency_ms==null?"—":String(snap.mean_latency_ms),down=snap.counts.down||0,total=snap.counts.total||0,up=snap.counts.up||0;
  $$("[data-snap=checks]").forEach(function(n){n.textContent=String(snap.checks_today)});
  $$("[data-snap=latency]").forEach(function(n){n.textContent=latency});
  $$("[data-snap=open]").forEach(function(n){n.textContent=String(open)});
  var sweep=$("[data-snap=sweep]");if(sweep&&snap.last_sweep_at)sweep.setAttribute("datetime",snap.last_sweep_at);
  var sub=$("[data-snap=incident-sub]");
  if(sub){sub.textContent="";if(!open)sub.textContent="None open";else{var when=document.createElement("time");when.setAttribute("datetime",snap.open_incidents[0].opened_at);when.setAttribute("data-clock","hm");when.textContent=snap.incident_hm||"";sub.appendChild(document.createTextNode("since "));sub.appendChild(when)}}
  var aside=$("[data-snap=aside]");
  if(aside){aside.textContent="";if(open){var small=document.createElement("small");small.textContent="Open for";var bold=document.createElement("b");bold.setAttribute("data-opened",snap.open_incidents[0].opened_at);bold.textContent=snap.open_for||"";aside.appendChild(small);aside.appendChild(bold)}else{var note=document.createElement("b");note.textContent=snap.history_note||"";aside.appendChild(note)}}
  var chip=$("[data-snap=chip]"),chipLabel=$("[data-snap=chip-label]");
  if(chip&&chipLabel){chip.classList.remove("down","warn","ok");var failing=(snap.nav_items||[]).filter(function(item){return item.dot==="down"});
    if(down){chip.classList.add("down");chipLabel.textContent=down+" failing";chip.setAttribute("href",down===1&&failing[0]?"/monitors/"+encodeURIComponent(failing[0].name)+"/view":"/monitors?status=down")}
    else if(open){chip.classList.add("warn");chipLabel.textContent=open+" open";chip.setAttribute("href","/incidents?status=open")}
    else if(total){chip.classList.add(up===total?"ok":"warn");chipLabel.textContent=up+"/"+total+" healthy";chip.setAttribute("href","/monitors")}
    else{chip.classList.add("warn");chipLabel.textContent="Awaiting first sweep";chip.setAttribute("href","/dashboard")}}
  setBadge("fail","Monitors",down,false);setBadge("open","Incidents",open,false);setBadge("cert","Certificates",snap.counts.expiring||0,true);
  var title=$("[data-snap=hero-title]");if(title&&snap.hero_title)title.textContent=snap.hero_title;
  var eye=$("[data-snap=hero-eye]");if(eye)eye.textContent=snap.hero_state?"Service state · "+snap.hero_state:"Service state";
  var mix=$("[data-snap=mix]");if(mix)mix.textContent=up+" up · "+down+" down";
  var totalNode=$("[data-snap=total]");if(totalNode)totalNode.textContent=String(total);
  $$("[data-snap=latency]").forEach(function(n){var small=n.nextElementSibling;if(small&&small.tagName==="SMALL")small.hidden=latency==="—"});
  (snap.nav_items||[]).forEach(function(item){$$(".nav-mon").forEach(function(link){var label=link.querySelector("span:last-child");if(!label||label.textContent!==item.name)return;var dot=link.querySelector(".dot-s");if(dot)dot.className="dot-s "+(item.dot||"")})});
  (snap.monitors||[]).forEach(function(card){$$(".mon").forEach(function(el){var name=el.querySelector(".mon-name");if(!name||name.textContent!==card.name)return;var msg=el.querySelector(".mon-msg");if(msg&&card.plain)msg.textContent=card.plain;var due=el.querySelector("[data-due]");if(due&&card.next_check_at)due.setAttribute("data-due",card.next_check_at)})});
  paintFeed(snap);paintAttention(snap);paintTimes();paintDue(Date.now());paintOpenFor(Date.now())}
function setBadge(key,word,count,warn){var el=$("[data-snap=badge-"+key+"]");if(!count){if(el)el.remove();return}if(!el){var host=null;$$(".nav a").forEach(function(link){var label=link.querySelector("span");if(label&&label.textContent===word)host=link});if(!host)return;el=document.createElement("span");el.className=warn?"badge warn":"badge";el.setAttribute("data-snap","badge-"+key);host.appendChild(el)}el.textContent=String(count)}
function paintFeed(snap){var feed=$("[data-feed]");if(!feed||!snap.activity)return;var rows=snap.activity.slice().sort(function(a,b){return String(b.at).localeCompare(String(a.at))}).slice(0,50);feed.textContent="";if(!rows.length){var empty=document.createElement("li");empty.className="gap";var mark=document.createElement("span");mark.className="d";var hold=document.createElement("div");var strong=document.createElement("b");strong.textContent="No activity yet";var quiet=document.createElement("small");quiet.textContent="State changes show up here.";hold.appendChild(strong);hold.appendChild(quiet);empty.appendChild(mark);empty.appendChild(hold);feed.appendChild(empty);return}rows.forEach(function(event){var li=document.createElement("li");li.className=event.state==="down"?"bad":event.state==="up"?"ok":"gap";li.setAttribute("data-event-id",event.id);var d=document.createElement("span");d.className="d";var wrap=document.createElement("div");var link=document.createElement("a");var incident=snap.open_ids&&snap.open_ids[event.monitor];link.href=event.state==="down"&&incident?"/incidents/"+incident:"/monitors/"+encodeURIComponent(event.monitor)+"/view";var b=document.createElement("b");b.textContent=event.text||"";link.appendChild(b);var small=document.createElement("small");small.textContent=event.state||"";wrap.appendChild(link);wrap.appendChild(small);var time=document.createElement("time");time.setAttribute("datetime",event.at||"");time.textContent=event.at||"";li.appendChild(d);li.appendChild(wrap);li.appendChild(time);feed.appendChild(li)})}
function paintAttention(snap){var rows=snap.attention||[],box=$("[data-attention]");if(!rows.length){if(box)box.remove();return}if(!box){box=document.createElement("section");box.className="card attn";box.setAttribute("data-attention","");box.setAttribute("aria-label","Needs attention");var head=document.createElement("div");head.className="card-head";var h2=document.createElement("h2");h2.textContent="Needs attention";head.appendChild(h2);var list=document.createElement("ul");list.setAttribute("data-attention-list","");box.appendChild(head);box.appendChild(list);var hero=$(".hero");if(hero&&hero.parentNode)hero.parentNode.insertBefore(box,hero.nextSibling)}var ul=$("[data-attention-list]",box);if(!ul)return;ul.textContent="";rows.forEach(function(row){var li=document.createElement("li");var pill=document.createElement("span");pill.className="pill "+(row.tone||"");pill.textContent=row.pill||"";var name=document.createElement("a");name.href="/monitors/"+encodeURIComponent(row.name)+"/view";name.textContent=row.name||"";var plain=document.createElement("span");plain.textContent=row.plain||"";var age=document.createElement("span");age.className="age";age.textContent=row.age||"";var mon=document.createElement("a");mon.href=name.href;mon.textContent="Monitor";li.appendChild(pill);li.appendChild(name);li.appendChild(plain);li.appendChild(age);li.appendChild(mon);if(row.incident_id){var inc=document.createElement("a");inc.href="/incidents/"+row.incident_id;inc.textContent="Incident";li.appendChild(inc)}ul.appendChild(li)})}
function paintLive(){var node=$("[data-live]");if(!node)return;var stale=pgLive.isStale(Date.now()-liveState.lastOk);node.classList.toggle("is-stale",stale);var label=$("[data-live-label]",node);if(label)label.textContent=pgLive.liveLabel(liveState.generated,Date.now(),stale)}
function appendLog(rows){var box=$("[data-log]");if(!box)return;var fresh=pgLive.newerRows(rows,liveState.logId);if(!fresh.length)return;$$(".row",box).forEach(function(row){if(!row.getAttribute("data-log-id"))row.remove()});fresh.forEach(function(row){if(row.id>liveState.logId)liveState.logId=row.id;var line=document.createElement("div");line.className="row";line.setAttribute("data-log-id",row.id);var ts=document.createElement("span");ts.className="ts";var time=document.createElement("time");time.setAttribute("datetime",row.at||"");time.textContent=row.at||"";ts.appendChild(time);var lv=document.createElement("span");lv.className="lv "+(row.mark==="FAIL"?"bad":row.mark==="PASS"?"ok":"wn");lv.textContent=row.mark||"";var name=document.createElement("b");name.textContent=row.name||"";var text=document.createElement("span");text.textContent=(row.plain||"")+(row.mark==="GAP"?"":" · "+row.latency+" ms");line.appendChild(ts);line.appendChild(document.createTextNode(" "));line.appendChild(lv);line.appendChild(document.createTextNode(" "));line.appendChild(name);line.appendChild(document.createTextNode(" "));line.appendChild(text);box.insertBefore(line,box.firstChild)});while(box.children.length>50)box.removeChild(box.lastChild);paintTimes()}
var liveState={generated:0,lastOk:Date.now(),fails:0,timer:0,etag:"",logId:0,down:0,open:0};
function scheduleLive(){clearTimeout(liveState.timer);if(document.hidden)return;var wait=liveState.fails?pgLive.backoffMs(liveState.fails-1):pgLive.pollMs(liveState.down,liveState.open);liveState.timer=setTimeout(pollLive,wait)}
function pollLive(){if(document.hidden){scheduleLive();return}var offset=-new Date().getTimezoneOffset();
  fetch("/api/dashboard?offset="+offset,{headers:{Accept:"application/json","If-None-Match":liveState.etag}}).then(function(r){var tag=r.headers.get("ETag");if(tag)liveState.etag=tag;if(r.status!==304&&!r.ok){liveState.fails+=1;return null}liveState.lastOk=Date.now();liveState.fails=0;if(r.status===304)return null;return r.json()}).then(function(body){if(body&&pgLive.shouldApply(liveState.generated,body.generated_ms)){applySnapshot(body);liveState.generated=body.generated_ms;liveState.down=(body.counts&&body.counts.down)||0;liveState.open=(body.open_incidents||[]).length}return fetch("/api/activity?since="+liveState.logId,{headers:{Accept:"application/json"}})}).then(function(r){return r&&r.ok?r.json():null}).then(function(body){if(body)appendLog(body.events||[])}).catch(function(){liveState.fails+=1}).then(function(){paintLive();scheduleLive()})}
paintTimes();paintMeters();
if($("[data-kpis]")){var liveNode=$("[data-live]");if(liveNode)liveState.generated=+liveNode.getAttribute("data-generated")||0;$$("[data-log-id]").forEach(function(n){var id=+n.getAttribute("data-log-id");if(id>liveState.logId)liveState.logId=id});setInterval(paintLive,1000);document.addEventListener("visibilitychange",function(){if(!document.hidden)pollLive()});scheduleLive()}
var runAll=$("[data-run-all]");
if(runAll)runAll.addEventListener("submit",function(e){e.preventDefault();var note=$("[data-run-note]");if(note)note.textContent="Checking…";fetch(runAll.action,{method:"POST",body:new FormData(runAll),headers:{Accept:"application/json"}}).then(function(r){return r.json()}).then(function(body){if(note)note.textContent="Ran "+body.ran+" of "+body.total;pollLive()}).catch(function(){if(note)note.textContent="Check failed"})});
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

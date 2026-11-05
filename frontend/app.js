var token = localStorage.getItem("tap_token") || "";
var principal = localStorage.getItem("tap_principal") || "";
function v(id){ return document.getElementById(id).value; }
function setOut(t){ document.getElementById("out").textContent = t; }
function show(name){
  document.querySelectorAll(".view").forEach(function(e){ e.style.display="none"; });
  document.getElementById("view-"+name).style.display="block";
  document.querySelectorAll("nav button").forEach(function(b){ b.classList.remove("active"); });
  var n=document.getElementById("nav-"+name); if(n) n.classList.add("active");
}
async function api(path, body, method){
  var opts={method: method||(body?"POST":"GET"), headers:{"Content-Type":"application/json"}};
  if(token) opts.headers["Authorization"]="Bearer "+token;
  if(body) opts.body=JSON.stringify(body);
  var r=await fetch(path, opts);
  var j=await r.json();
  if(!r.ok) throw new Error(j.error||("HTTP "+r.status));
  return j;
}
function saveToken(t, p){ token=t; principal=p; localStorage.setItem("tap_token", t); localStorage.setItem("tap_principal", p); document.getElementById("who").textContent="Signed in as "+p; }
async function setupOwner(){ try{ await api("/api/setup-owner", {password: v("pw")}); setOut("Owner password set. Now sign in as owner."); }catch(e){ setOut("Error: "+e.message); } }
async function loginOwner(){ try{ var j=await api("/api/login-owner", {password: v("pw")}); saveToken(j.token, j.principal); setOut("Signed in as owner."); }catch(e){ setOut("Error: "+e.message); } }
async function loginArtist(){ try{ var j=await api("/api/login-artist", {artist_id: v("artistId"), password: v("artistPw")}); saveToken(j.token, j.principal); setOut("Signed in as artist "+j.principal+"."); }catch(e){ setOut("Error: "+e.message); } }
async function logout(){ try{ await api("/api/logout", {}); }catch(e){} token=""; principal=""; localStorage.removeItem("tap_token"); localStorage.removeItem("tap_principal"); setOut("Signed out."); }
async function loadTiers(){ try{ document.getElementById("homeInfo").textContent=JSON.stringify(await api("/api/tiers"), null, 2); }catch(e){ setOut("Error: "+e.message); } }
async function loadPayments(){ try{ document.getElementById("homeInfo").textContent=JSON.stringify(await api("/api/payments"), null, 2); }catch(e){ setOut("Error: "+e.message); } }
function skillsOf(s){ return s.split(",").map(function(x){return x.trim();}).filter(Boolean); }
async function addArtist(){ try{ var j=await api("/api/artists", {name: v("aName"), skills: skillsOf(v("aSkills")), location: v("aLoc"), tier: v("aTier")}); setOut("Artist created: "+j.artist_id+" — "+j.name); document.getElementById("aName").value=""; }catch(e){ setOut("Error: "+e.message); } }
async function setArtistPw(){ try{ var aid=document.getElementById("artistId").value; if(!aid) throw new Error("Type the artist ID into the Artist ID box on Home first."); await api("/api/artists/"+encodeURIComponent(aid)+"/password", {password: v("aNewPw")}); setOut("Password set for "+aid+"."); }catch(e){ setOut("Error: "+e.message); } }
async function addOpp(){ try{ var j=await api("/api/opportunities", {title: v("oTitle"), kind: v("oKind"), skills: skillsOf(v("oSkills")), location: v("oLoc"), date: v("oDate"), pay_cents: parseInt(v("oPay"),10)}); setOut("Opportunity created: "+j.opp_id); }catch(e){ setOut("Error: "+e.message); } }
async function listOpps(){ try{ document.getElementById("oppList").textContent=JSON.stringify(await api("/api/opportunities"), null, 2); }catch(e){ setOut("Error: "+e.message); } }
async function offerBooking(){ try{ var j=await api("/api/bookings/offer", {artist_id: v("bArtist"), opp_id: v("bOpp")}); setOut("Booking offered: "+j.booking_id+" state "+j.state); }catch(e){ setOut("Error: "+e.message); } }
async function bookingAction(a){ try{ var j=await api("/api/bookings/"+a, {booking_id: v("bId")}); setOut("Booking "+a+": "+JSON.stringify(j)); }catch(e){ setOut("Error: "+e.message); } }
async function loadCalendar(){ try{ document.getElementById("bookInfo").textContent=JSON.stringify(await api("/api/artists/"+v("bArtist")+"/calendar"), null, 2); }catch(e){ setOut("Error: "+e.message); } }
async function addProduct(){ try{ var j=await api("/api/shop/products", {artist_id: v("sArtist"), name: v("sName"), kind: v("sKind"), price_cents: parseInt(v("sPrice"),10), stock: parseInt(v("sStock"),10)}); setOut("Product created: "+j.product_id); }catch(e){ setOut("Error: "+e.message); } }
async function loadShop(){ try{ document.getElementById("shopList").textContent=JSON.stringify(await api("/api/shop/"+v("sArtist")), null, 2); }catch(e){ setOut("Error: "+e.message); } }
async function orderMerch(){ try{ var j=await api("/api/shop/order", {artist_id: v("sArtist"), product_id: v("sProd"), qty: parseInt(v("sQty"),10)}); setOut("Order created: "+j.order_id+" state "+j.state); }catch(e){ setOut("Error: "+e.message); } }
async function shopAction(a){ try{ var b={order_id: v("sOrder")}; if(a==="clear") b.cleared_cents=parseInt(v("sCleared"),10); if(a==="fulfill") b.shipping_note=""; var j=await api("/api/shop/"+a, b); setOut("Order "+a+": "+JSON.stringify(j)); }catch(e){ setOut("Error: "+e.message); } }
async function restock(){ try{ var j=await api("/api/shop/restock", {product_id: v("sProd"), qty: parseInt(v("sRestock"),10)}); setOut("Restocked: "+JSON.stringify(j)); }catch(e){ setOut("Error: "+e.message); } }
async function addCampaign(){ try{ var j=await api("/api/campaigns", {artist_id: v("cArtist"), title: v("cTitle"), channels: skillsOf(v("cChan")), budget_cap_cents: parseInt(v("cCap"),10)}); setOut("Campaign created: "+j.campaign_id); }catch(e){ setOut("Error: "+e.message); } }
async function campaignSpend(){ try{ var j=await api("/api/campaigns/spend", {campaign_id: v("cId"), amount_cents: parseInt(v("cSpend"),10), note: v("cNote")}); setOut("Campaign spend: "+JSON.stringify(j)); }catch(e){ setOut("Error: "+e.message); } }
async function draftPost(){ try{ var j=await api("/api/campaigns/draft", {campaign_id: v("cId"), text: v("cPost")}); setOut("Draft saved (not posted): "+JSON.stringify(j)); }catch(e){ setOut("Error: "+e.message); } }
async function approvePost(){ try{ var j=await api("/api/campaigns/approve", {campaign_id: v("cId"), index: parseInt(v("cIdx"),10)}); setOut("Draft approved: "+JSON.stringify(j)); }catch(e){ setOut("Error: "+e.message); } }
async function listCampaigns(){ try{ document.getElementById("campList").textContent=JSON.stringify(await api("/api/campaigns?artist_id="+encodeURIComponent(v("cArtist"))), null, 2); }catch(e){ setOut("Error: "+e.message); } }
async function addRelease(){ try{ var j=await api("/api/releases", {artist_id: v("pArtist"), title: v("pTitle")}); setOut("Release created: "+j.release_id); }catch(e){ setOut("Error: "+e.message); } }
async function startProduction(){ try{ var j=await api("/api/productions", {artist_id: v("pArtist"), release_id: v("pRel")}); setOut("Production started: "+j.production_id+" stage "+j.stage); }catch(e){ setOut("Error: "+e.message); } }
async function advanceProduction(){ try{ var j=await api("/api/productions/advance", {production_id: v("pProd"), note: v("pNote")}); setOut("Production: "+JSON.stringify(j)); }catch(e){ setOut("Error: "+e.message); } }
async function productionNote(){ try{ var j=await api("/api/productions/note", {production_id: v("pProd"), text: v("pNote")}); setOut("Note added."); }catch(e){ setOut("Error: "+e.message); } }
async function approveRelease(){ try{ var j=await api("/api/releases/approve", {release_id: v("pRel")}); setOut("Release approved: "+JSON.stringify(j)); }catch(e){ setOut("Error: "+e.message); } }
async function exportRelease(){ try{ var j=await api("/api/releases/export", {release_id: v("pRel")}); setOut("Release export (local only): "+JSON.stringify(j)); }catch(e){ setOut("Error: "+e.message); } }
async function listProductions(){ try{ document.getElementById("prodList").textContent=JSON.stringify(await api("/api/productions?artist_id="+encodeURIComponent(v("pArtist"))), null, 2); }catch(e){ setOut("Error: "+e.message); } }
async function addTask(){ try{ var j=await api("/api/tasks", {artist_id: v("tArtist"), kind: v("tKind"), title: v("tTitle"), due: v("tDue")}); setOut("Task created: "+j.task_id); }catch(e){ setOut("Error: "+e.message); } }
async function completeTask(){ try{ await api("/api/tasks/complete", {task_id: v("tId")}); setOut("Task completed."); }catch(e){ setOut("Error: "+e.message); } }
async function loadTasks(){ try{ document.getElementById("taskList").textContent=JSON.stringify(await api("/api/tasks?artist_id="+encodeURIComponent(v("tArtist"))), null, 2); }catch(e){ setOut("Error: "+e.message); } }
async function dailyBrief(){ try{ document.getElementById("taskList").textContent=JSON.stringify(await api("/api/brief", {artist_id: v("tArtist")}), null, 2); }catch(e){ setOut("Error: "+e.message); } }
async function scheduleCall(){ try{ var j=await api("/api/calls", {artist_id: v("tArtist"), contact: v("callContact"), purpose: v("callPurpose"), date: v("callDate")}); setOut("Call scheduled (log only): "+j.call_id); }catch(e){ setOut("Error: "+e.message); } }
async function logCall(){ try{ var j=await api("/api/calls/log", {call_id: v("callId"), outcome: v("callOutcome"), notes: v("callNotes")}); setOut("Call logged: "+JSON.stringify(j)); }catch(e){ setOut("Error: "+e.message); } }
async function listCalls(){ try{ document.getElementById("taskList").textContent=JSON.stringify(await api("/api/calls?artist_id="+encodeURIComponent(v("tArtist"))), null, 2); }catch(e){ setOut("Error: "+e.message); } }
async function addExpense(){ try{ var j=await api("/api/expenses", {artist_id: v("tArtist"), category: v("expCat"), amount_cents: parseInt(v("expAmt"),10), note: v("expNote")}); setOut("Expense added: "+JSON.stringify(j)); }catch(e){ setOut("Error: "+e.message); } }
var lastReceipt=null;
async function collect(){ try{ lastReceipt=await api("/api/bookings/collect", {booking_id: v("colBid"), cleared_cents: parseInt(v("colAmt"),10), stage: v("colStage")}); document.getElementById("receiptOut").textContent=JSON.stringify(lastReceipt, null, 2); setOut("Collected (cleared only). Receipt kept for the cut step."); }catch(e){ setOut("Error: "+e.message); } }
async function payCut(){ try{ if(!lastReceipt) throw new Error("Collect first, then pay a cut from that receipt."); var j=await api("/api/bookings/pay-cut", {receipt: lastReceipt, cut_cents: parseInt(v("cutAmt"),10), payee: v("cutPayee")}); document.getElementById("receiptOut").textContent=JSON.stringify(j, null, 2); setOut("Cut paid from V8 only."); }catch(e){ setOut("Error: "+e.message); } }
async function calc11vt(){ try{ var j=await api("/api/calc-11vt", {cleared_cents: parseInt(v("calcGross"),10), cut_cents: parseInt(v("calcCut"),10), cleared: v("calcCleared")==="true"}); document.getElementById("calcOut").textContent=JSON.stringify(j, null, 2); }catch(e){ setOut("Error: "+e.message); document.getElementById("calcOut").textContent="REFUSED: "+e.message; } }
async function loadDiscover(){ try{ document.getElementById("discList").textContent=JSON.stringify(await api("/api/discover/artists"), null, 2); }catch(e){ setOut("Error: "+e.message); } }
async function publishProfile(){ try{ var j=await api("/api/discover/publish", {artist_id: v("dArtist"), bio: v("dBio")}); setOut("Profile published: "+JSON.stringify(j)); }catch(e){ setOut("Error: "+e.message); } }
async function unpublishProfile(){ try{ await api("/api/discover/unpublish", {artist_id: v("dArtist")}); setOut("Profile unpublished."); }catch(e){ setOut("Error: "+e.message); } }
async function addSample(){ try{ var j=await api("/api/discover/sample", {artist_id: v("dArtist"), title: v("dSample"), kind: v("dKind"), notes: v("dNotes")}); setOut("Sample added (provided by the artist): "+j.title); }catch(e){ setOut("Error: "+e.message); } }
async function requestConnection(){ try{ var j=await api("/api/discover/connect", {artist_id: v("cArtist2"), seeker_name: v("cName"), seeker_contact: v("cContact"), message: v("cMsg")}); setOut("Connection request pending: "+j.request_id); }catch(e){ setOut("Error: "+e.message); } }
async function decideConnection(a){ try{ var j=await api("/api/discover/decide", {request_id: v("cReq"), accept: a}); setOut("Request decided: "+JSON.stringify(j)); }catch(e){ setOut("Error: "+e.message); } }
if(principal) document.getElementById("who").textContent="Signed in as "+principal;
show("home"); setOut("Ready. Sign in to begin — the app starts empty.");

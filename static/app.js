const SW={background:"#0e1530",color:"#e8f0ff",confirmButtonColor:"#35e0ff",cancelButtonColor:"#444"};
if(window.Swal)window.alert=m=>Swal.fire({...SW,text:String(m),confirmButtonText:"OK"});
const ask=async m=>window.Swal?(await Swal.fire({...SW,text:m,icon:"warning",showCancelButton:true,confirmButtonText:"Yes",cancelButtonText:"Cancel"})).isConfirmed:confirm(m);
const $=i=>document.getElementById(i);
const tok=()=>localStorage.getItem("ta_token")||"";
async function api(path,opt={}){opt.headers={...(opt.headers||{}),Authorization:tok(),"Content-Type":"application/json"};
const r=await fetch("/api/"+path,opt);const d=await r.json();if(r.status===401&&!location.pathname.endsWith("/")){logout();return}
if(!r.ok)throw new Error(d.detail);return d}
const esc=s=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function logout(){try{await api("logout",{method:"POST"})}catch(e){}localStorage.removeItem("ta_token");location.href="/"}
async function delAccount(){if(!(await ask("Delete your account and ALL your designs and answers forever?")))return;await api("account",{method:"DELETE"});localStorage.clear();location.href="/"}
function guard(){if(!tok())location.href="/"}

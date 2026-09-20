'use strict';
(function(){
/* DOM 构造、表格、提示层、时间格式化与 request_id 取号统一来自 web/ui.js（与用户端共用一份实现）。 */
/* UI 里叫 time/stamp，管理台历史命名为 fmtTime/fmtStamp，用解构重命名保持既有调用点不变。 */
const {$,el,empty,failed,action,table,setText,uid,today,weekAgo,dateCN,timeCN,ApiError,note,syncThemeToggle,overBytes,formCheck,time:fmtTime,stamp:fmtStamp}=UI;
const state={user:null,csrf:'',labs:[],tab:'labs',tabs:null,page:1,hasMore:false,userPage:1,userHasMore:false,sentPage:1,sentHasMore:false};
const statuses=UI.STATUSES;
const showStatus=r=>r.status==='CANCELLED'&&r.cancel_reason==='NO_SHOW'?'已爽约':(statuses[r.status]||r.status);
const slotRange=r=>fmtStamp(r.start_at)+' – '+fmtTime(r.end_at);
const sourceName=r=>r.source==='WAITLIST'?'候补补位':r.source==='DIRECT'?'直接预约':(r.source||'—');
/* 各文本字段的字节上限，逐个抄自服务端：实验室 src/http.c 的 text_ok(name/loc/desc)，
   资源与通知在 src/service.c，查询参数在 src/http.c 的 records 与 users 分支。
   为什么光有框上的 maxlength 不够：它数 UTF-16 码元，服务端 strlen 数 UTF-8 字节，
   一个汉字 3 字节，位置框 180 码元能写出 540 字节，浏览器放行而请求吃一句裸 400。
   查询参数更隐蔽：服务端先把 q 读进固定缓冲区截断、再判长度，
   超长中文前缀被静默切碎成另一个词，返回「查无此人」而不是报错。 */
const LIMITS={labName:180,labLoc:180,labDesc:1000,assetName:180,assetSpec:300,
              noticeTitle:120,noticeBody:500,qUser:64,fAction:32,fUser:64};
function over(v,max,label){const bad=overBytes(v,max,label);if(bad)note(bad,true);return !!bad;}
/* 一条规则一个函数，交给 UI.formCheck 在提交前跑。管理台这些字段首尾空格无意义，
   所以先 trim 再量——与用户端密码/用户名「校验什么串就提交什么串」相反，这里是刻意的。
   日期与容量这类跨字段、非字节的规则也放进来，报错才统一：都落在表单自己的 role=alert 区。 */
function lenRule(label,max,required){return v=>{const t=v.trim();if(!t)return required?label+'不能为空':'';return overBytes(t,max,label);};}
function intRule(label,min,max){return v=>{const n=Math.floor(Number(v));
  return !Number.isFinite(n)||n<min||n>max?label+'需为 '+min+'..'+max+' 的整数。':'';};}
const FORM_RULES={
 'lab-form':[['name',lenRule('实验室名称',LIMITS.labName,true)],['location',lenRule('实验室位置',LIMITS.labLoc,true)],
             ['description',lenRule('实验室说明',LIMITS.labDesc,false)]],
 'asset-form':[['name',lenRule('资源名称',LIMITS.assetName,true)],['spec',lenRule('资源规格',LIMITS.assetSpec,false)],
                ['total',intRule('资源数量',1,999)]],
 'notify-form':[['title',lenRule('通知标题',LIMITS.noticeTitle,true)],['body',lenRule('通知正文',LIMITS.noticeBody,false)],
                 ['username',(v,f)=>f.elements.target.value==='user'&&!v.trim()?'请选择或填写要通知的用户名。':'']],
 'publish-form':[['lab_id',v=>!v?'请先在「实验室管理」中新增实验室，再来发布场次。':''],
                  ['capacity',intRule('每场次容量',1,200)],
                  ['end_date',v=>!v?'请选择结束日期。':''],
                  ['start_date',(v,f)=>{const e=f.elements.end_date.value;
                    if(!v||!e)return '请选择开始与结束日期。';
                    const days=(Date.parse(e+'T00:00:00Z')-Date.parse(v+'T00:00:00Z'))/86400000+1;
                    return !Number.isFinite(days)||days<1?'结束日期不能早于开始日期。'
                      :days>14?'单次最多发布 14 天，请缩小日期范围。':'';}]]
};
const fmtUptime=v=>{const s=Number(v);if(!Number.isFinite(s))return '—';const h=Math.floor(s/3600),m=Math.floor(s%3600/60);return (h>0?h+' 小时 ':'')+m+' 分';};
let kicked=false;
function kick(){if(kicked)return;kicked=true;location.replace('/');}
/* 管理台的 401：提示后跳回用户端并终止本次调用（用户端是回登录页并保留待重试编号）。 */
const api=UI.makeApi(()=>state.csrf,()=>{note('登录已过期，即将返回用户端。',true);kick();throw new ApiError('登录已过期。',401,'UNAUTHORIZED');});
function gate(msg){if(kicked)return;kicked=true;note(msg,true);setTimeout(()=>location.replace('/'),900);}
async function mutate(path,body,okMsg,button,onErr){if(button)button.disabled=true;try{const d=await api(path,Object.assign({},body,{request_id:uid()}));note(typeof okMsg==='function'?okMsg(d):okMsg);return d;}catch(e){note(e.message+(e.status===503?' 数据库暂忙，可安全重试。':''),true);if(onErr)onErr(e);return null;}finally{if(button)button.disabled=false;}}

/* -- r41 管理员强制操作按钮（E2E case8 集成点：data-rid + data-force-cancel 属性） -- */
function buildForceActions(r){
  const wrap=el('span');wrap.dataset.rid=r.id;
  const st=r.status;
  if(st!=='CONFIRMED'&&st!=='HELD'&&st!=='PENDING')return wrap;
  const fc=action('强制取消',()=>{
    if(!confirm('确认强制取消预约 #'+r.id+'（'+r.username+'）？名额将按候补规则释放。'))return;
    mutate('/api/admin/reservations/'+encodeURIComponent(r.id)+'/force-cancel',{},'预约 #'+r.id+' 已强制取消。',fc).then(d=>{if(d!==null)loadRecords();});
  },'secondary');
  fc.dataset.forceCancel='1';fc.setAttribute('data-force-cancel','');
  wrap.append(fc);
  if(st==='CONFIRMED'&&r.checked_in_at){
    const fcomp=action('代签退',()=>{
      mutate('/api/admin/reservations/'+encodeURIComponent(r.id)+'/force-complete',{},'预约 #'+r.id+' 已代签退（强制完成）。',fcomp).then(d=>{if(d!==null)loadRecords();});
    },'secondary');
    fcomp.setAttribute('data-force-complete','');
    wrap.append(fcomp);
  }
  return wrap;
}

/* -- r22 待审批专页签：只拉 PENDING 预约（不传 date 即不过滤日期，覆盖跨天场次），支持就地批准/拒绝 -- */
function updateApprovalBadge(n){const b=$('approvals-badge');if(!b)return;b.textContent=n>0?String(n):'';b.hidden=n<=0;}
function buildApprovalActions(r){const box=el('span',undefined,'actions');
 box.append(action('批准',b=>mutate('/api/reservations/'+encodeURIComponent(r.id)+'/approve',{},'已批准预约 #'+r.id+'。',b).then(ok=>{if(ok!==null)loadApprovals();}),'primary'));
 box.append(action('拒绝',b=>mutate('/api/reservations/'+encodeURIComponent(r.id)+'/reject',{},'已拒绝预约 #'+r.id+'，已通知用户。',b).then(ok=>{if(ok!==null)loadApprovals();}),'secondary'));
 return box;}
/* 备注就是审批依据：用户在预约时写的实验项目/组号/器材需求只存在 reservations.note 里，
   接口也一直把它随 /api/admin/records 返回（见 src/service.c 的 records()），可这张表此前
   根本没有这一列——管理员对着「实验室 + 用户名 + 时段」批准或拒绝，看不到用户写过什么。
   渲染走 UI.noteCell：与用户端「我的记录」同一份截断+原地展开实现。 */
async function loadApprovals(){const box=$('admin-approvals');empty($('admin-approvals'),'正在读取待审批预约…',true);try{const d=await api('/api/admin/records?status=PENDING&page=1&page_size=50');const list=d.reservations||[];setText('c-pending',list.length);updateApprovalBadge(list.length);if(!list.length){empty(box,'当前没有待审批的预约。');return;}table(box,['ID','实验室','用户名','时段','来源','备注','操作'],list.map(r=>[r.id,r.lab_name,r.username,slotRange(r),sourceName(r),UI.noteCell(r.note),buildApprovalActions(r)]));}catch(e){empty(box,'待审批加载失败：'+e.message);}}
async function exportClaims(){const b=$('export-claims');b.disabled=true;try{const end=today(),start=new Date(Date.now()-30*86400000).toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'});const d=await api('/api/admin/asset-claims/export?start_date='+encodeURIComponent(start)+'&end_date='+encodeURIComponent(end));const blob=new Blob([d.content],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=d.filename||'asset-claims.csv';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);note('声明占用报表已导出：'+(d.filename||'asset-claims.csv'));}catch(e){note(e.message,true);}finally{b.disabled=false;}}
async function loadClaims(){const end=today(),start=new Date(Date.now()-30*86400000).toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'});empty($('admin-claims'),'正在读取声明占用…');try{const d=await api('/api/admin/asset-claims?start_date='+encodeURIComponent(start)+'&end_date='+encodeURIComponent(end));const rows=(d.claims||[]).map(c=>[c.asset_id,c.asset_name,c.claims,c.last_start?fmtStamp(c.last_start):'—']);table($('admin-claims'),['资源ID','资源','声明次数','最近声明时段'],rows);}catch(e){empty($('admin-claims'),'声明占用加载失败：'+e.message);}}
async function loadAssets(){const labId=$('assets-lab').value;empty($('admin-assets'),labId?'正在读取资源…':'请先在「实验室管理」中新增实验室。');if(!labId)return;try{const d=await api('/api/labs/'+encodeURIComponent(labId)+'/assets');const names={AVAILABLE:'可用',MAINTENANCE:'维修中',DISABLED:'停用'};table($('admin-assets'),['名称','规格','数量','状态','操作'],(d.assets||[]).map(a=>{const edit=action('编辑',()=>{const f=$('asset-form');f.elements.id.value=a.id;f.elements.name.value=a.name||'';f.elements.spec.value=a.spec||'';f.elements.total.value=a.total??1;f.elements.status.value=a.status||'AVAILABLE';$('asset-form-title').textContent='编辑资源：'+a.name;UI.scrollTo(f);});return [a.name,a.spec||'—',a.total??1,el('span',names[a.status]||a.status,'tag'+(a.status==='AVAILABLE'?'':a.status==='MAINTENANCE'?' warn':' gray')),edit];}));}catch(e){empty($('admin-assets'),'资源加载失败：'+e.message);}
 loadClaims();}
$('refresh-assets').addEventListener('click',loadAssets);
$('assets-lab').addEventListener('change',loadAssets);
$('reset-asset').addEventListener('click',()=>{const f=$('asset-form');f.reset();f.elements.id.value='';f.elements.total.value=1;$('asset-form-title').textContent='新增资源';});
$('asset-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget,id=f.elements.id.value,labId=$('assets-lab').value,b=f.querySelector('button[type="submit"]');if(!labId){note('请先在「实验室管理」中新增实验室。',true);return;}if(!formCheck(f,'asset-error',FORM_RULES['asset-form']))return;const body={name:f.elements.name.value.trim(),spec:f.elements.spec.value.trim(),total:String(Math.floor(Number(f.elements.total.value))),status:f.elements.status.value};const d=await mutate(id?'/api/admin/assets/'+encodeURIComponent(id)+'/update':'/api/admin/labs/'+encodeURIComponent(labId)+'/assets',body,'资源已保存。',b);if(d!==null){$('reset-asset').click();await loadAssets();}});
async function loadOverview(){const day=today();$('health-line').textContent='正在读取服务状态…';try{const[s,m,h,pend]=await Promise.all([api('/api/admin/stats?start_date='+encodeURIComponent(day)+'&end_date='+encodeURIComponent(day)),api('/api/admin/metrics'),api('/api/health'),api('/api/admin/records?status=PENDING&page=1&page_size=50')]);const row=(s.stats||[]).find(x=>x.date===day)||{};const pn=((pend&&pend.reservations)||[]).length;setText('c-pending',pn);updateApprovalBadge(pn);setText('c-slots',row.slots??0);setText('c-confirmed',row.confirmed??0);setText('c-waiting',row.waiting??0);setText('c-checked',row.checked_in??0);const c=m.counters||{},total=Number(c.requests_total)||0;setText('c-rate',total?Math.round((Number(c.ok_2xx)||0)/total*1000)/10+'%':'—');$('c-rate-sub').textContent='5xx 错误 '+(c.err_5xx??0)+' · 503 数据库忙碌 '+(c.db_busy_503??0)+' · 登录 '+(c.logins??0)+' 次';$('health-line').textContent='服务版本 '+(h.version||'—')+' · 已连续运行 '+fmtUptime(h.uptime_s)+' · 日期与时间均为北京时间（UTC+8）';}catch(e){$('health-line').textContent='概览加载失败：'+e.message;}}
async function loadLabs(){try{const d=await api('/api/labs');state.labs=d.labs||[];renderLabs();renderLabOptions();}catch(e){empty($('admin-labs'),'实验室加载失败：'+e.message);}}
function renderLabs(){table($('admin-labs'),['名称','位置','说明','状态','操作'],state.labs.map(l=>{const st=el('span',undefined,'tag-row');
 /* 「预约需审批」是 lab.require_approval 的直接投影。这个开关此前只能写不能读：
    列在数据库里、服务端也接受，管理台却无从判断某个实验室的为什么要排队等批准。 */
 st.append(el('span',l.enabled?'启用':'停用','tag'+(l.enabled?'':' gray')));
 if(l.require_approval)st.append(el('span','预约需审批','tag warn'));
 return [l.name,l.location||'—',l.description||'—',st,action('编辑',()=>{const f=$('lab-form');f.elements.id.value=l.id;f.elements.name.value=l.name||'';f.elements.location.value=l.location||'';f.elements.description.value=l.description||'';f.elements.enabled.checked=!!l.enabled;f.elements.require_approval.checked=!!l.require_approval;$('lab-form-title').textContent='编辑实验室：'+l.name;UI.scrollTo(f);})];}));}
function fillLabSelect(sel){const old=sel.value;sel.replaceChildren();if(!state.labs.length){const o=el('option','暂无实验室，请先在「实验室管理」中新增');o.value='';sel.append(o);return;}state.labs.forEach(l=>{const o=el('option',l.name+(!l.enabled?'（已停用）':''));o.value=l.id;sel.append(o);});if(state.labs.some(l=>l.id===old))sel.value=old;}
function renderLabOptions(){fillLabSelect($('publish-lab'));fillLabSelect($('slots-lab'));fillLabSelect($('assets-lab'));}
async function loadRecords(){const p=state.page;const ea=$('events-action').value.trim(),eu=$('events-user').value.trim();if(over(ea,LIMITS.fAction,'日志动作筛选')||over(eu,LIMITS.fUser,'日志操作者'))return;empty($('admin-reservations'),'正在读取记录…',true);empty($('admin-waitlist'),'正在读取记录…',true);empty($('admin-events'),'正在读取记录…',true);try{const d=await api('/api/admin/records?date='+encodeURIComponent($('admin-date').value)+'&page='+p+'&page_size=20'+(ea?'&action='+encodeURIComponent(ea):'')+(eu?'&user='+encodeURIComponent(eu):''));
const resCols=['ID','实验室','用户名','时段','状态','来源','备注','签到时间','操作'];
const resRows=(d.reservations||[]).map(function(r){
  const row=[r.id,r.lab_name,r.username,slotRange(r),showStatus(r),sourceName(r),UI.noteCell(r.note),r.checked_in_at?fmtStamp(r.checked_in_at):'—'];
  row.push(buildForceActions(r));
  return row;
});
table($('admin-reservations'),resCols,resRows);
table($('admin-waitlist'),['ID','实验室','用户名','时段','状态','队列位置'],(d.waitlist||[]).map(r=>[r.id,r.lab_name,r.username,slotRange(r),statuses[r.status]||r.status,r.status==='WAITING'&&r.position?'第 '+r.position+' 位':'—']));table($('admin-events'),['时间','操作者','动作','对象','请求编号'],(d.events||[]).map(v=>[fmtStamp(v.created_at),v.actor,v.action,v.entity_id,v.request_id]));state.hasMore=!!d.has_more;$('admin-page').textContent='第 '+p+' 页'+(d.has_more?'（还有更多）':'');$('admin-prev').disabled=p<=1;$('admin-next').disabled=!d.has_more;}catch(e){empty($('admin-reservations'),'记录加载失败：'+e.message);empty($('admin-waitlist'),'请刷新重试');empty($('admin-events'),'请刷新重试');}}
async function loadUsers(){const p=state.userPage;const badq=overBytes($('users-q').value.trim(),LIMITS.qUser,'用户名前缀');if(badq){note(badq,true);empty($('admin-users'),'搜索词超长：'+badq+'。服务端会先把过长的查询串截断再比对，直接发出去只会得到一个查无此人的错误结果。');return;}empty($('admin-users'),'正在读取用户…',true);try{const q=$('users-q').value.trim();const d=await api('/api/admin/users?page='+p+'&page_size=20'+(q?'&q='+encodeURIComponent(q):''));const rows=(d.users||[]).map(u=>{const ns=u.no_show_count??0;return [u.id,u.username,u.role==='ADMIN'?'管理员':'用户',el('span',u.enabled?'启用':'停用','tag'+(u.enabled?'':' gray')),u.reservations??0,u.waitlisted??0,ns>=2?el('span','受限中','tag late'):ns,buildUserActions(u)];});table($('admin-users'),['ID','用户名','角色','状态','有效预约','候补中','近7天爽约','操作'],rows);state.userHasMore=!!d.has_more;$('users-page').textContent='第 '+p+' 页'+(d.has_more?'（还有更多）':'');$('users-prev').disabled=p<=1;$('users-next').disabled=!d.has_more;}catch(e){empty($('admin-users'),'用户加载失败：'+e.message);}}
function buildUserActions(u){const box=el('span',undefined,'actions');
 if(u.enabled)box.append(action('停用',b=>mutate('/api/admin/users/'+u.id+'/disable',{},'账号 '+u.username+' 已停用并下线。',b).then(ok=>{if(ok!==null)loadUsers();}),'secondary'));
 else box.append(action('启用',b=>mutate('/api/admin/users/'+u.id+'/enable',{},'账号 '+u.username+' 已启用。',b).then(ok=>{if(ok!==null)loadUsers();}),'secondary'));
 box.append(action('重置密码',b=>api('/api/admin/users/'+u.id+'/reset-password',{request_id:uid()}).then(d=>{UI.secretBox($('pw-secret'),'用户 '+u.username+' 的新密码只显示这一次，请立即复制并转交本人：',d.password);loadUsers();}).catch(e=>note(e.message,true)),'secondary'));
 return box;}
async function loadSent(){const p=state.sentPage;empty($('admin-sent'),'正在读取通知历史…',true);try{const d=await api('/api/admin/notifications/sent?page='+p+'&page_size=20');table($('admin-sent'),['时间','类型','接收人','标题','正文','已读时间'],(d.sent||[]).map(n=>[fmtStamp(n.created_at),n.kind,n.username,n.title,n.body||'—',n.read_at?fmtStamp(n.read_at):'未读']));state.sentHasMore=!!d.has_more;$('sent-page').textContent='第 '+p+' 页 / 共 '+(d.total??'—')+' 条'+(d.has_more?'（还有更多）':'');$('sent-prev').disabled=p<=1;$('sent-next').disabled=!d.has_more;}catch(e){empty($('admin-sent'),'通知历史加载失败：'+e.message);}}
async function loadLogs(){const box=$('admin-logs');empty(box,'正在读取日志…',true);try{const lines=Number($('logs-lines').value)||100,level=$('logs-level').value;const d=await api('/api/admin/logs?lines='+lines+(level?'&level='+level:''));const list=d.lines||[];if(!list.length){empty(box,'日志为空。');return;}const pre=el('pre',list.join('\n'),'log-view');pre.id='log-pre';box.replaceChildren(pre,el('p','显示最近 '+list.length+' 行'+(d.truncated?'（日志文件较大，仅读取尾部）':'')+' · 文件大小 '+((d.file_size||0)/1024).toFixed(1)+' KB','muted'));}catch(e){empty(box,'日志加载失败：'+e.message);}}
/* -- r12 纯 SVG 柱状图（零依赖）：有效预约 / 已签到 双序列 --
   配色一律走 CSS 令牌（class），不在 JS 里写死色值：写死后无法随主题切换，
   暗色下坐标文字实测仅 3.84:1、图例文字 1.21:1（等于看不见）。
   CSP 又禁内联样式，class 是唯一可用通道。 */
function svgBarChart(rows){
 const NS='http://www.w3.org/2000/svg';
 const W=720,H=260,PL=42,PB=44,PT=16,PR=8;
 const max=Math.max(1,...rows.map(r=>Math.max(r[1],r[2])));
 const iw=(W-PL-PR)/rows.length,bw=Math.min(34,iw*0.55);
 const svg=document.createElementNS(NS,'svg');
 svg.setAttribute('viewBox','0 0 '+W+' '+H);svg.setAttribute('width','100%');svg.setAttribute('role','img');
 svg.setAttribute('aria-label','每日预约趋势柱状图：绿色柱为有效预约，金色柱为已签到，每组左柱为有效预约、右柱为已签到');
 const mk=(tag,cls,attrs,text,parent)=>{const n=document.createElementNS(NS,tag);if(cls)n.setAttribute('class',cls);for(const k in attrs)n.setAttribute(k,attrs[k]);if(text!==undefined)n.textContent=text;(parent||svg).append(n);return n;};
 const yv=v=>H-PB-(v/max)*(H-PB-PT);
 for(let g=0;g<=4;g++){const v=max*g/4,y=yv(v);
  mk('line','chart-grid',{x1:PL,y1:y,x2:W-PR,y2:y});
  mk('text','chart-text',{x:PL-6,y:y+4,'text-anchor':'end','font-size':'10'},String(Math.round(v*10)/10));}
 rows.forEach((r,i)=>{
  const cx=PL+iw*i+iw/2;
  const bar=(cls,v,x,label)=>{mk('title',null,{},label,mk('rect',cls,{x,y:yv(v),width:bw,height:H-PB-yv(v),rx:3}));};
  bar('chart-bar-1',r[1],cx-bw-2,r[0]+' 有效预约 '+r[1]);
  bar('chart-bar-2',r[2],cx+2,r[0]+' 已签到 '+r[2]);
  mk('text','chart-text',{x:cx,y:H-PB+16,'text-anchor':'middle','font-size':'10'},r[0].slice(5));
 });
 mk('rect','chart-bar-1',{x:PL,y:4,width:12,height:12});
 mk('text','chart-text',{x:PL+16,y:14,'font-size':'11'},'有效预约');
 mk('rect','chart-bar-2',{x:PL+78,y:4,width:12,height:12});
 mk('text','chart-text',{x:PL+94,y:14,'font-size':'11'},'已签到');
 return svg;
}
async function loadFairness(){const box=document.getElementById("admin-fairness");if(!box)return;try{const d=await api("/api/admin/fairness?days=28");const cols=["用户","入队","转正","退出","跳过","平均等待(s)","获得预约"];const rows=(d.users||[]).filter(u=>u.joined||u.granted).map(u=>[u.username,u.joined,u.promoted,u.withdrawn,u.skipped,u.avg_wait_s==null?"—":Math.round(u.avg_wait_s),u.granted]);table(box,cols,rows);const note=document.getElementById("fairness-note");if(note)note.textContent="Jain 公平指数（近 28 天，按用户获得预约数）："+d.jain_index.toFixed(4);}catch(e){empty(box,"公平性数据加载失败："+e.message);}}
async function loadStats(){const start=$('stats-start').value,end=$('stats-end').value;if(!start||!end){note('请先选择统计的起止日期。',true);return;}empty($('admin-stats'),'正在读取统计…');const chartBox=$('stats-chart');chartBox.replaceChildren();empty($('admin-util'),'正在读取利用率…');try{const d=await api('/api/admin/stats?start_date='+encodeURIComponent(start)+'&end_date='+encodeURIComponent(end));const list=d.stats||[];if(!list.length){empty($('admin-stats'),'所选日期范围内暂无统计数据。');return;}const rows=list.map(r=>[r.date,r.slots,r.confirmed,r.cancelled,r.no_show,r.checked_in,r.waiting]);const t=d.totals||{};rows.push(['合计',t.slots??'—',t.confirmed??'—',t.cancelled??'—',t.no_show??'—',t.checked_in??'—',t.waiting??'—']);table($('admin-stats'),['日期','开放场次','有效预约','已取消','已爽约','已签到','候补人数'],rows);if(list.length)chartBox.append(el('h3','预约趋势'),svgBarChart(list.map(r=>[r.date,r.confirmed,r.checked_in])));const u=await api('/api/admin/labs/utilization?start_date='+encodeURIComponent(start)+'&end_date='+encodeURIComponent(end));table($('admin-util'),['实验室','开放场次','总席位','有效预约','已签到','已爽约','利用率%','实机时(分)','实机时利用率%'],(u.utilization||[]).map(r=>[r.lab_name,r.slots,r.seats,r.confirmed,r.checked_in,r.no_show,r.utilization+'%',r.actual_minutes,r.utilization_actual+'%']));}catch(e){empty($('admin-stats'),'统计加载失败：'+e.message);}}
async function exportStats(){const start=$('stats-start').value,end=$('stats-end').value;if(!start||!end){note('请先选择统计的起止日期。',true);return;}const b=$('export-stats');b.disabled=true;try{const d=await api('/api/admin/stats/export?start_date='+encodeURIComponent(start)+'&end_date='+encodeURIComponent(end));const blob=new Blob([d.content],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=d.filename||'stats.csv';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);note('统计报表已导出：'+(d.filename||'stats.csv'));}catch(e){note(e.message,true);}finally{b.disabled=false;}}
async function loadMetrics(){const box=$('admin-metrics');empty(box,'正在读取指标…',true);try{const d=await api('/api/admin/metrics');const c=d.counters||{},l=d.latency_ms||{},cnt=Number(l.count)||0;const wrap=el('div');wrap.append(el('h3','请求计数'));const ct=el('div');table(ct,['指标','数值'],[['请求总数',c.requests_total??0],['成功 2xx',c.ok_2xx??0],['客户端错误 4xx',c.err_4xx??0],['服务端错误 5xx',c.err_5xx??0],['其中 503 数据库忙碌',c.db_busy_503??0],['登录成功次数',c.logins??0]]);wrap.append(ct);const avg=cnt?Math.round((Number(l.sum)||0)/cnt*100)/100:0,mx=Math.round((Number(l.max)||0)*100)/100;wrap.append(el('h3','延迟分布（毫秒 · 样本 '+cnt+' · 平均 '+avg+' · 最大 '+mx+'）'));const bounds=['≤1','≤2','≤5','≤10','≤20','≤50','≤100','≤200','≤500','≤1000','≤2000','>2000'];const lt=el('div');table(lt,['区间 (ms)','请求数','占比'],(l.buckets||[]).map((v,i)=>[bounds[i]||'桶'+i,v,cnt?Math.round(v/cnt*1000)/10+'%':'—']));wrap.append(lt);box.replaceChildren(wrap);}catch(e){empty(box,'指标加载失败：'+e.message);}}
/* 场次容量：服务端规则是「容量 ≥ 已确认(CONFIRMED)+限时保留(HELD) 的人数」，
   而列表接口的 confirmed_count 只统计 CONFIRMED。因此下限按已知数据给出，
   并如实说明服务端可能要求更高，避免界面承诺一个会落空的值。 */
const CAP_NOTE_DEFAULT='先在下方列表中点「修改」选择场次，这里会显示该场次的容量下限。';
function capError(text){const i=$('slot-form').elements.capacity,e=$('slot-cap-error');
 if(!text){e.textContent='';e.hidden=true;i.classList.remove('field-invalid');i.removeAttribute('aria-invalid');return;}
 e.textContent=text;e.hidden=false;i.classList.add('field-invalid');i.setAttribute('aria-invalid','true');i.focus();i.select();}
function renderSlots(list){table($('admin-slots'),['时段','容量','已确认','候补','状态','操作'],list.map(s=>[fmtTime(s.start_at)+' – '+fmtTime(s.end_at),s.capacity,s.confirmed_count??0,s.waiting_count??0,el('span',s.enabled?'启用':'停用','tag'+(s.enabled?'':' gray')),action('修改',()=>editSlot(s))]));}
function editSlot(s){const f=$('slot-form');f.elements.id.value=s.id;f.elements.capacity.value=s.capacity;f.elements.enabled.checked=!!s.enabled;
 const taken=Number(s.confirmed_count)||0;f.dataset.floor=String(Math.max(1,taken));
 $('slot-cap-note').textContent='该场次已确认 '+taken+' 人，容量不得低于 '+Math.max(1,taken)+'；处于「限时保留」的待确认名额服务端同样计入下限，实际下限可能更高。';
 capError('');$('slot-form-title').textContent='修改场次：'+fmtTime(s.start_at)+' – '+fmtTime(s.end_at);UI.scrollTo(f);}
function resetSlotForm(){const f=$('slot-form');f.reset();f.elements.id.value='';delete f.dataset.floor;$('slot-cap-note').textContent=CAP_NOTE_DEFAULT;capError('');$('slot-form-title').textContent='修改场次';}
async function loadSlots(){const lab=$('slots-lab').value,day=$('slots-date').value||today();if(!lab){empty($('admin-slots'),'暂无实验室，请先在「实验室管理」中新增。');return;}empty($('admin-slots'),'正在读取场次…');try{const d=await api('/api/slots?lab_id='+encodeURIComponent(lab)+'&date='+encodeURIComponent(day));renderSlots(d.slots||[]);}catch(e){empty($('admin-slots'),'场次加载失败：'+e.message);}}
async function switchTab(tab){state.tab=tab;for(const t of ['labs','publish','slots','approvals','records','users','assets','stats','metrics','notify','sent','logs'])$(t+'-view').hidden=t!==tab;state.tabs.sync(tab);await refreshCurrent();}
async function refreshCurrent(){try{if(state.tab==='labs'||state.tab==='publish')await loadLabs();if(state.tab==='slots'){if(!state.labs.length)await loadLabs();await loadSlots();}if(state.tab==='approvals')await loadApprovals();if(state.tab==='records')await loadRecords();if(state.tab==='users')await loadUsers();if(state.tab==='assets'){if(!state.labs.length)await loadLabs();await loadAssets();}if(state.tab==='stats'){await loadStats();await loadFairness();}if(state.tab==='metrics')await loadMetrics();if(state.tab==='sent')await loadSent();if(state.tab==='logs')await loadLogs();}catch(e){note(e.message,true);}}
/* 页签的点击、aria-selected/tabindex 同步与方向键键盘导航统一由 UI.wireTabs 承担。 */
state.tabs=UI.wireTabs($('tabs'),name=>switchTab(name));
$('logout').addEventListener('click',async e=>{const b=e.currentTarget;b.disabled=true;try{await api('/api/logout',{request_id:uid()});location.href='/';}catch(err){note(err.message,true);b.disabled=false;}});
$('refresh-overview').addEventListener('click',()=>loadOverview());
$('refresh-labs').addEventListener('click',()=>loadLabs());
$('reset-lab').addEventListener('click',()=>{const f=$('lab-form');f.reset();f.elements.id.value='';$('lab-form-title').textContent='新增实验室';});
$('lab-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget,id=f.elements.id.value,b=f.querySelector('button[type="submit"]');if(!formCheck(f,'lab-error',FORM_RULES['lab-form']))return;const body={name:f.elements.name.value.trim(),location:f.elements.location.value.trim(),description:f.elements.description.value.trim(),require_approval:f.elements.require_approval.checked};if(id)body.enabled=f.elements.enabled.checked;const d=await mutate(id?'/api/admin/labs/'+id+'/update':'/api/admin/labs',body,'实验室信息已保存。',b);if(d!==null){$('reset-lab').click();await loadLabs();}});
$('publish-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget,b=f.querySelector('button[type="submit"]');const start=f.elements.start_date.value,end=f.elements.end_date.value,capacity=Math.floor(Number(f.elements.capacity.value));const wds=[...document.querySelectorAll('#wd-row input')].map(c=>c.checked?'1':'0').join('');if(!formCheck(f,'publish-error',FORM_RULES['publish-form']))return;const d=await mutate('/api/admin/slots/publish',{lab_id:String(f.elements.lab_id.value),start_date:start,end_date:end,capacity,weekdays:wds},r=>r.created>0?('发布完成，新增 '+r.created+' 个场次。'):'发布完成，但没有新增场次：该范围内的场次已全部存在，或已开始（已开始的场次不会创建）。',b);if(d!==null)await loadOverview();});
$('admin-date').addEventListener('change',()=>{state.page=1;loadRecords();});
$('admin-prev').addEventListener('click',()=>{if(state.page>1){state.page--;loadRecords();}});
$('admin-next').addEventListener('click',()=>{if(!state.hasMore)return;state.page++;loadRecords();});
$('refresh-approvals').addEventListener('click',()=>loadApprovals());
$('refresh-records').addEventListener('click',()=>{state.page=1;loadRecords();});
$('events-action').addEventListener('change',()=>{state.page=1;loadRecords();});
$('events-user').addEventListener('change',()=>{state.page=1;loadRecords();});
$('refresh-users').addEventListener('click',()=>{state.userPage=1;loadUsers();});
$('users-query').addEventListener('click',()=>{state.userPage=1;loadUsers();});
$('users-q').addEventListener('change',()=>{state.userPage=1;loadUsers();});
$('users-prev').addEventListener('click',()=>{if(state.userPage>1){state.userPage--;loadUsers();}});
$('users-next').addEventListener('click',()=>{if(state.userHasMore){state.userPage++;loadUsers();}});
$('refresh-sent').addEventListener('click',()=>{state.sentPage=1;loadSent();});
$('refresh-logs').addEventListener('click',()=>loadLogs());
$('logs-lines').addEventListener('change',()=>loadLogs());
$('logs-level').addEventListener('change',()=>loadLogs());
$('sent-prev').addEventListener('click',()=>{if(state.sentPage>1){state.sentPage--;loadSent();}});
$('sent-next').addEventListener('click',()=>{if(state.sentHasMore){state.sentPage++;loadSent();}});
$('refresh-stats').addEventListener('click',()=>loadStats());
$('export-stats').addEventListener('click',()=>exportStats());
async function exportUtil(){const start=$('stats-start').value,end=$('stats-end').value;if(!start||!end){note('请先选择统计的起止日期。',true);return;}const b=$('export-util');b.disabled=true;try{const d=await api('/api/admin/stats/utilization/export?start_date='+encodeURIComponent(start)+'&end_date='+encodeURIComponent(end));const blob=new Blob([d.content],{type:'text/csv;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=d.filename||'utilization.csv';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);note('利用率报表已导出：'+(d.filename||'utilization.csv'));}catch(e){note(e.message,true);}finally{b.disabled=false;}}
$('export-util').addEventListener('click',exportUtil);
$('export-claims').addEventListener('click',exportClaims);
$('refresh-metrics').addEventListener('click',()=>loadMetrics());
$('slots-query').addEventListener('click',()=>loadSlots());
$('refresh-slots').addEventListener('click',()=>loadSlots());
$('slots-date').addEventListener('change',()=>loadSlots());
$('reset-slot').addEventListener('click',()=>resetSlotForm());
$('slot-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget,b=f.querySelector('button[type="submit"]');const id=f.elements.id.value,cap=Math.floor(Number(f.elements.capacity.value));
 if(!id){note('请先在列表中点击「修改」选择场次。',true);return;}
 if(!Number.isFinite(cap)||cap<1||cap>200){capError('容量需为 1..200 的整数。');note('容量需为 1..200 的整数。',true);return;}
 const floor=Number(f.dataset.floor||0);
 if(floor&&cap<floor){capError('该场次已确认 '+floor+' 人，容量不能小于 '+floor+'（已停止提交，未发送请求）。');note('容量小于该场次已确认人数，已停止提交。',true);return;}
 capError('');
 const d=await mutate('/api/admin/slots/'+encodeURIComponent(id)+'/update',{capacity:cap,enabled:f.elements.enabled.checked},'场次已更新。',b,err=>{if(err.status===409)capError(err.message+'（服务端还计入了待确认的保留名额。）');});
 if(d!==null){resetSlotForm();await loadSlots();}});
$('slot-form').elements.capacity.addEventListener('input',()=>capError(''));
document.querySelectorAll('#notify-form [name="target"]').forEach(r=>r.addEventListener('change',()=>{$('notify-user-wrap').hidden=$('notify-form').elements.target.value!=='user';}));
$('notify-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget,b=f.querySelector('button[type="submit"]');const all=f.elements.target.value==='all',title=f.elements.title.value.trim(),body=f.elements.body.value.trim(),username=f.elements.username.value.trim();if(!formCheck(f,'notify-error',FORM_RULES['notify-form']))return;const d=await mutate('/api/admin/notifications',all?{all:true,title,body}:{username,title,body},r=>'通知已发送给 '+(r.sent??0)+' 位用户。',b);if(d!==null){f.reset();$('notify-user-wrap').hidden=true;}});
$('admin-date').value=today();$('publish-form').elements.start_date.value=today();$('publish-form').elements.end_date.value=today();$('stats-start').value=weekAgo();$('stats-end').value=today();$('slots-date').value=today();
(async()=>{try{const r=await fetch('/api/me',{credentials:'same-origin'});if(r.status===401)return gate('请先使用管理员账号登录，即将返回用户端。');const j=await r.json().catch(()=>null);if(!j||j.code!=='OK'||!j.data||!j.data.user)return gate('身份验证失败，即将返回用户端。');if(j.data.user.role!=='ADMIN')return gate('需要管理员权限，即将返回用户端。');state.user=j.data.user;state.csrf=j.data.csrf_token||'';}catch(e){return gate('网络连接失败，请检查服务是否运行，即将返回用户端。');}$('identity').textContent=state.user.username+' · 管理员';$('gate').hidden=true;$('topbar').hidden=false;$('app').hidden=false;await loadOverview();await switchTab('labs');})();
if($('theme-toggle'))$('theme-toggle').addEventListener('click',()=>{if(window.labTheme){window.labTheme.toggle();syncThemeToggle();}});
syncThemeToggle();
})();

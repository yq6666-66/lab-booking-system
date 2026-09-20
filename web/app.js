'use strict';
/* DOM 构造、表格、提示层、时间格式化与 request_id 取号统一来自 web/ui.js（与管理台共用一份实现）。 */
const {$,el,empty,skeleton,failed,action,table,uid,today,stamp,time,dateCN,timeCN,ApiError,note,syncThemeToggle,byteLen,overBytes,noteCell,formCheck}=UI;
const state = {user:null,csrf:'',labs:[],labAssets:[],tab:'book',tabs:null,pending:null,busy:false,query:0,recQuery:0,page:1,checkinWindow:900,checkinTimer:0,holdTimer:0,holdReloaded:false,loginRole:'USER',notifyPage:1,recFilter:'',picker:null};
const statuses = UI.STATUSES;
const cancelReasons = {USER:'主动取消',NO_SHOW:'爽约释放',REJECTED:'审批未通过',PREEMPTED:'被高优先级申请占用'};
const showStatus = r => r.status==='CANCELLED'?(cancelReasons[r.cancel_reason]||'已取消'):(statuses[r.status]||r.status);
const kindLabels = {PROMOTED:'候补补位',NO_SHOW:'爽约提醒',NOTICE:'通知'};
/* 开启审批的实验室，这条预约落库是 PENDING：不占名额、也还没生效。
   回执带 status（POST /api/reservations），措辞先读它——
   统一一句「预约成功」会把没生效的申请说成已确认，用户以为座位已经是他的了。 */
function reserveMsg(d){return d&&d.status==='PENDING'
  ?'申请已提交：该实验室的预约需管理员审批，批准前不占用名额，进度可在我的记录查看。'
  :'预约成功，可在我的记录查看。';}
/* 替代时段与「近期时段」两处的卡片都来自当前所选实验室（require_approval 由 GET /api/labs 下发），
   按钮文案与主列表保持一致，别让同一个动作在两块面板里一个叫「立即预约」一个叫「提交申请」。 */
function labNeedsApproval(){const l=state.labs.find(x=>x.id===$('lab-select').value);return !!(l&&l.require_approval);}
/* 两页 REST 调用只差 401 的处理方式，差异留成 onUnauthorized 钩子。 */
const api = UI.makeApi(() => state.csrf, () => {
  if (state.user) { showLogin(); $('login-error').textContent = '登录已过期，请重新登录；未确认操作的请求编号已保留。'; }
});
function showLogin(){if(state.checkinTimer){clearInterval(state.checkinTimer);state.checkinTimer=0;}state.user=null;state.csrf='';state.pending=null;$('retry-area').hidden=true;$('app-view').hidden=true;$('account').hidden=true;$('login-view').hidden=false;$('register-form').hidden=true;$('login-form').hidden=false;setLoginRole('USER');}
function restorePending(){try{const p=JSON.parse(sessionStorage.getItem('lab-pending')||'null');if(p&&p.user===state.user.id&&typeof p.path==='string'&&p.path.startsWith('/api/')&&p.body&&typeof p.body.request_id==='string')state.pending=p;else state.pending=null;}catch(e){state.pending=null;}$('retry-area').hidden=!state.pending;}
function rememberPending(){try{if(state.pending)sessionStorage.setItem('lab-pending',JSON.stringify(state.pending));else sessionStorage.removeItem('lab-pending');}catch(e){/* In-memory retry remains available. */}$('retry-area').hidden=!state.pending;}
/* 只做两件事：置全局 busy 闸门、给触发按钮打「进行中」标记。
   旧实现对全文档按钮做 disabled 快照与还原：一次写操作要扫上百个节点，
   而且把键盘焦点从用户刚按下的按钮上丢掉（disabled 元素不可聚焦，焦点退回 body）。
   重复提交由 mutation() 开头的 state.busy 早退拦住，不必靠"全部禁用"来表达。 */
function lock(on,trigger){state.busy=on;document.querySelectorAll('.pending').forEach(b=>{b.classList.remove('pending');b.removeAttribute('aria-busy');});if(on&&trigger){trigger.classList.add('pending');trigger.setAttribute('aria-busy','true');}}
async function mutation(path,body,message,button,retry=false,onError=null){if(state.busy){note('上一个操作仍在处理中，请稍候。',true);return;}if(state.pending&&!retry){note('请先重试并确认上次操作的结果，再执行新的操作。',true);return;}if(!retry){state.pending={user:state.user.id,path,body:{...body,request_id:uid()},message};rememberPending();}const p=state.pending;if(!p)return;lock(true,button);let refresh=false,caught=null;try{const d=await api(p.path,p.body);note((typeof p.message==='function'?p.message(d):p.message)||'操作已完成，请在下方对应列表中核对结果。');state.pending=null;rememberPending();refresh=true;}catch(e){caught=e;note(e.message+(e.status===503?' 数据库暂忙，可以安全重试。':''),true);if(e.status!==0&&e.status!==503&&e.status!==500&&e.status!==401){state.pending=null;rememberPending();refresh=true;}}finally{lock(false);}if(refresh)await refreshCurrent();if(caught&&onError)try{onError(caught);}catch(_){/* 展示回调异常不影响主流程 */}}
async function enter(d){state.user=d.user;state.csrf=d.csrf_token;$('identity').textContent=d.user.username+' · '+(d.user.role==='ADMIN'?'管理员':'普通用户');$('admin-console-link').hidden=d.user.role!=='ADMIN';$('login-view').hidden=true;$('app-view').hidden=false;$('account').hidden=false;$('notify-panel').hidden=true;syncThemeToggle();restorePending();if(d.user.role==='ADMIN'){location.href='/admin.html';return;}/* 实验室列表与未读计数互不依赖：并发取回，登录到首屏少一个往返。 */await Promise.all([loadLabs(),refreshNotifyCount()]);await switchTab('book');}
async function loadLabs(){const d=await api('/api/labs');state.labs=d.labs||[];const old=$('lab-select').value;const select=$('lab-select');select.replaceChildren();state.labs.forEach(l=>{const o=el('option',l.name+(!l.enabled?'（已停用）':''));o.value=l.id;select.append(o);});if(state.labs.some(l=>l.id===old))$('lab-select').value=old;}
async function switchTab(tab){hideAlternatives();closeReschedule();state.tab=tab;if(state.checkinTimer){clearInterval(state.checkinTimer);state.checkinTimer=0;}if(tab==='checkin')state.checkinTimer=setInterval(()=>{if(state.tab==='checkin')renderCheckin();},30000);if(state.holdTimer){clearInterval(state.holdTimer);state.holdTimer=0;}if(tab==='mine'){state.holdReloaded=false;state.holdTimer=setInterval(tickHolds,1000);}for(const t of ['book','checkin','mine'])$(t+'-view').hidden=t!==tab;state.tabs.sync(tab);await refreshCurrent();}
async function refreshCurrent(){try{if(state.tab==='book')await loadSlots();if(state.tab==='checkin')await renderCheckin();if(state.tab==='mine'){renderRecFilter();/* 三份数据互不依赖，并发加载；单个失败由各自函数就地降级为占位文案。 */await Promise.all([loadRecords(),loadSessions(),loadTokens()]);}}catch(e){note(e.message,true);}}
/* #alternatives 只服务预约页签的「替代时段」。它是三个页签面板的兄弟节点而非子孙：
   过去嵌在 #book-view 内，别的页签展开它就是 0 矩形（第 9 轮修掉的死路）；
   改期不再复用这块常驻面板，而是就地展开在触发行下方（见 showReschedule）。 */
function hideAlternatives(){const box=$('alternatives');box.hidden=true;box.replaceChildren();}
function openAlternatives(title,noteText){const box=$('alternatives');box.hidden=false;box.replaceChildren();
  const head=el('div',undefined,'alt-head');head.append(el('h3',title),action('收起',hideAlternatives,'quiet'));box.append(head);
  if(noteText)box.append(el('p',noteText,'muted'));return box;}
function revealAlternatives(){const box=$('alternatives');UI.scrollTo(box,'center');box.focus();}
/* 载荷字段就是 {id,start_at,end_at}（见 docs/CONTRACT.md），余位与候补人数服务端都没给，
   所以卡片只报时段——旧实现在这里写死过一句「候补 0 人」。 */
function renderAlternatives(list){const box=openAlternatives('该场次已满，可预约以下近期时段','同一实验室、所选场次之后 7 天内、按时间排序的空闲场次，最多 3 个；提交时会重新校验。');if(!list.length){box.append(el('div','当前 7 天内暂无其他空闲时段，可选择加入候补。','empty'));revealAlternatives();return;}const grid=el('div',undefined,'slot-grid');for(const s of list){const card=el('article',undefined,'slot');card.append(el('span','可预约','tag'),el('div',stamp(s.start_at)+' – '+time(s.end_at),'slot-time'));card.append(action(labNeedsApproval()?'提交预约申请':'立即预约',b=>mutation('/api/reservations',{slot_id:s.id},reserveMsg,b,false,e=>{if(e.code==='SLOT_FULL'&&e.data&&Array.isArray(e.data.alternatives))renderAlternatives(e.data.alternatives);else if(e.code==='SLOT_FULL')renderAlternatives([]);}),'primary'));grid.append(card);}box.append(grid);revealAlternatives();}
async function renderSuggestions(lab,date){const panel=$('suggest-panel'),box=$('suggestions');try{const d=await api('/api/suggestions?lab_id='+encodeURIComponent(lab)+'&date='+encodeURIComponent(date));const list=d.suggestions||[];if(!list.length){panel.hidden=true;return;}panel.hidden=false;box.replaceChildren();const nb=list.filter(x=>x.bookable).length,nj=list.filter(x=>x.joinable).length;$('suggest-count').textContent='（'+(nb||'无')+' 个可预约 · '+(nj||'无')+' 个可候补，展开查看逐场次理由）';for(const s of list){const card=el('article',undefined,'slot');const why=(s.reasons||[]).map(r=>r.message).join('；');let label=s.bookable?'可预约':s.joinable?'可候补':'暂不可约';let cls='tag'+(s.bookable?'':s.joinable?' warn':' gray');card.append(el('span',label,cls),el('div',time(s.start_at)+' – '+time(s.end_at),'slot-time'),el('p','已约 '+s.taken+'/'+s.capacity+' · 候补 '+s.waiting_count+' 人','muted'));if(why)card.append(el('p',why,'muted'));if(s.bookable)card.append(action(labNeedsApproval()?'提交预约申请':'立即预约',b=>mutation('/api/reservations',{slot_id:s.slot_id},reserveMsg,b,false,e=>{if(e.code==='SLOT_FULL'&&e.data&&Array.isArray(e.data.alternatives))renderAlternatives(e.data.alternatives);}),'primary'));else if(s.joinable){const pr=s.promote_probability;card.append(action('加入候补'+(pr==null?'':(pr>0?'（历史转正率约 '+Math.round(pr*100)+'%）':'（该排位近期无转正记录）')),b=>mutation('/api/waitlist',{slot_id:s.slot_id},d=>d&&d.promote_probability!=null?'已加入候补，当前排第 '+(Number(d.queue_ahead)+1)+' 位，历史转正概率约 '+Math.round(d.promote_probability*100)+'%。':'已加入候补，可在我的记录查看顺序。',b),'secondary'));}box.append(card);}}catch(e){panel.hidden=true;}}
async function loadSlots(){const target=$('slots');hideAlternatives();const token=++state.query;const lab=state.labs.find(l=>l.id===$('lab-select').value);if(!lab){empty(target,'暂无实验室，请联系管理员。');return;}$('lab-name').textContent=lab.name;$('lab-description').textContent=[lab.location,lab.description].filter(Boolean).join(' · ');loadLabAssets();if(!$('book-date').value){empty(target,'请选择有效日期。');return;}skeleton(target,8);try{const d=await api('/api/slots?lab_id='+encodeURIComponent(lab.id)+'&date='+encodeURIComponent($('book-date').value));if(token!==state.query)return;target.replaceChildren();target.setAttribute('aria-busy','false');$('slots-updated').textContent='更新于 '+time(Math.floor(Date.now()/1000));renderSuggestions(lab.id,$('book-date').value);if(!d.slots.length){empty(target,'当日暂无开放场次，请选择其他日期或联系管理员发布。');return;}state.checkinWindow=d.checkin_window||900;for(const s of d.slots){const card=el('article',undefined,'slot');const started=Number(s.start_at)*1000<=Date.now();const taken=!s.enabled||!s.lab_enabled;const checkedIn=!!s.my_checked_in_at;const canCheckin=started&&!!s.my_reservation_id&&!checkedIn&&Date.now()/1000<Number(s.start_at)+state.checkinWindow;const full=Number(s.confirmed_count)>=Number(s.capacity);let label=started?(s.my_reservation_id?(checkedIn?'已签到':'待签到'):s.my_waitlist_id?'候补中':'已开始'):taken?'暂不可预约':s.my_reservation_id?'我的预约':s.my_waitlist_id?'我的候补':Number(s.confirmed_count)>=Number(s.capacity)?'已满':'可预约';let tagClass='tag'+(canCheckin?' warn':checkedIn&&s.my_reservation_id?' done':taken&&!started?' gray':Number(s.confirmed_count)>=Number(s.capacity)&&!started&&!s.my_reservation_id?' busy':'');if(canCheckin)card.className='slot checkin';const tags=el('div',undefined,'tag-row');tags.append(el('span',label,tagClass));
 /* 需审批的场次在提交前就得说明：批准前不占名额、也还没生效。等回执再补一句，
    用户已经把「立即预约」按下去了。 */
 if(s.require_approval&&!started&&!s.my_reservation_id)tags.append(el('span','需管理员审批','tag warn'));
 card.append(tags,el('div',time(s.start_at)+' – '+time(s.end_at),'slot-time'),el('p','已约 '+s.confirmed_count+'/'+s.capacity+' · 候补 '+s.waiting_count+' 人','muted'));if(canCheckin)card.append(action('立即签到',b=>mutation('/api/reservations/'+s.my_reservation_id+'/checkin',{},'签到成功，请按时使用实验室。',b),'primary'));else if(checkedIn){card.append(el('p','已签到 '+stamp(s.my_checked_in_at),'muted'));
 const ended=Number(s.start_at)+3600<=Date.now()/1000;
 if(!ended)card.append(action('签退',b=>mutation('/api/reservations/'+s.my_reservation_id+'/checkout',{},'签退成功，实际使用时长已计入资源统计。',b),'secondary'));}
 else if(!started&&s.my_reservation_id)card.append(action('取消预约',confirmAction('取消预约',b=>mutation('/api/reservations/'+s.my_reservation_id+'/cancel',{},'预约已取消，如有候补已自动处理。',b))));else if(!started&&s.my_waitlist_id)card.append(action('退出候补',b=>mutation('/api/waitlist/'+s.my_waitlist_id+'/withdraw',{},'已退出候补。',b)));else if(!started&&!taken){const chosen=new Set();
 if((state.labAssets||[]).length){
 /* 这排芯片是这次预约要声明占用的设备，但整块控件此前一个字都没写：
    唯一的说明是每个按钮上的 title，触屏点不到、键盘聚焦不显示、读屏也不播报。
    上面那条只读的「实验室资源：」反而有文字标签——可交互的没标签，不能点的有。 */
 const pickLabel=el('p','需声明使用的设备（可选，可多选）','field-note slot-assets-label');
 pickLabel.id='asset-pick-hint-'+s.id;card.append(pickLabel);
 const wrap=el('div',undefined,'chips-row slot-assets');state.labAssets.forEach(a=>{const c=el('button',(a.total>1?a.name+' ×'+a.total:a.name),'chips');c.type='button';c.title='声明使用（可选）';c.setAttribute('aria-describedby','asset-pick-hint-'+s.id);
 /* 选没选中的资源就是这次预约要占用的配额，不能只靠底色深浅表达（WCAG 1.4.1），
    也不能只有 class —— 读屏要靠 aria-pressed 才知道这是个切换按钮、当前是选中还是未选中（4.1.2）。
    可见的 ✓ 由 .chips.on::before 画在样式表里，JS 只负责状态。 */
 c.setAttribute('aria-pressed','false');
 c.addEventListener('click',()=>{const on=chosen.has(String(a.id));
   if(on){chosen.delete(String(a.id));}else{chosen.add(String(a.id));}
   c.classList.toggle('on',!on);c.setAttribute('aria-pressed',String(!on));});
 wrap.append(c);});card.append(wrap);}
 let noteInput=null;
 if(!full){noteInput=el('input');noteInput.type='text';noteInput.maxLength=NOTE_MAX;noteInput.placeholder='备注（可选，如实验项目名）';
  noteInput.setAttribute('aria-label','备注（可选，如实验项目名）');
  noteInput.setAttribute('aria-describedby','note-hint-'+s.id);noteInput.className='slot-note';card.append(noteInput);
  /* placeholder 一输入就消失，而「最长多少」是动手之前就该看到的约束；
     它同时充当上面 aria-describedby 指向的说明节点。id 必须带场次号——
     一屏 48 张卡片共用同一个 id 会让描述指向别人的说明。 */
  const hint=el('p','备注最长 '+NOTE_MAX+' 字节（约 '+Math.floor(NOTE_MAX/3)+' 个汉字）','field-note');
  hint.id='note-hint-'+s.id;card.append(hint);}
 card.append(action(full?'加入候补':(s.require_approval?'提交预约申请':'立即预约'),b=>{const nt=noteInput?noteInput.value.trim():'';const ne=overBytes(nt,NOTE_MAX,'备注');if(ne){note(ne,true);return;}mutation(full?'/api/waitlist':'/api/reservations',full?{slot_id:s.id}:{slot_id:s.id,assets:[...chosen],note:nt},full?'已加入候补，可在我的记录查看顺序。':reserveMsg,b,false,full?null:e=>{if(e.code==='SLOT_FULL'&&e.data&&Array.isArray(e.data.alternatives))renderAlternatives(e.data.alternatives);})},full?'secondary':'primary'));}else card.append(el('p',started?'场次已开始':'当前场次不接受新申请','muted'));target.append(card);}}catch(e){if(token===state.query)failed(target,'时段加载失败：'+e.message,loadSlots);}}
async function renderCheckin(){if(!state.user)return;const box=$('checkin-list'),hist=$('checkin-history');empty(box,'正在读取今日场次…',true);try{const d=await api('/api/me/records?page=1&page_size=50');const dayStart=Date.parse(today()+'T00:00:00+08:00')/1000,dayEnd=dayStart+86400,now=Date.now()/1000,win=state.checkinWindow||900;const list=(d.reservations||[]).filter(r=>Number(r.start_at)>=dayStart&&Number(r.start_at)<dayEnd).sort((a,b)=>Number(a.start_at)-Number(b.start_at));if(!list.length)empty(box,'今天没有可签到的场次。');else{box.replaceChildren();for(const r of list){const start=Number(r.start_at),end=Number(r.end_at),checkedIn=!!r.checked_in_at,expired=now>=start+win;const card=el('article',undefined,'slot');let tag,cls='tag',sub=null,btn=null;if(r.status!=='CONFIRMED'){tag=showStatus(r);cls='tag gray';}else if(checkedIn){tag='已签到 ✓';cls='tag done';sub=el('p','签到时间 '+stamp(r.checked_in_at),'muted');}else if(now<start){const s=Math.max(0,Math.floor(start-now)),h=Math.floor(s/3600),m=Math.floor(s%3600/60);tag='未开始';cls='tag gray';sub=el('p',(h?h+' 小时 ':'')+m+' 分后可签','muted');btn=action('立即签到',()=>{},'primary');btn.disabled=true;}else if(!expired){tag='待签到';cls='tag warn';card.className='slot checkin';btn=action('立即签到',b=>mutation('/api/reservations/'+r.id+'/checkin',{},'签到成功，请按时使用实验室。',b),'primary');}else{tag='签到已截止';cls='tag late';sub=el('p','签到窗口已关闭，超时按爽约处理，不可补签。','muted');}card.append(el('span',tag,cls),el('p',r.lab_name,'checkin-lab'),el('div',time(start)+' – '+time(end),'slot-time'));if(sub)card.append(sub);if(btn)card.append(btn);box.append(card);}}const done=(d.reservations||[]).filter(r=>r.checked_in_at).sort((a,b)=>Number(b.checked_in_at)-Number(a.checked_in_at)).slice(0,5);table(hist,['实验室','日期','签到时间'],done.map(r=>[r.lab_name,dateCN.format(Number(r.start_at)*1000),stamp(r.checked_in_at)]));}catch(e){empty(box,'签到信息加载失败：'+e.message);empty(hist,'请刷新重试');}}
/* 危险操作两段确认：首次点击变红色「确认取消？」，3 秒内再次点击才执行，超时自动复位。 */
function confirmAction(text,onConfirm){return b=>{if(b.dataset.confirm==='1'){delete b.dataset.confirm;if(b._confirmTimer){clearTimeout(b._confirmTimer);b._confirmTimer=0;}b.textContent=text;b.classList.remove('btn-danger');onConfirm(b);return;}b.dataset.confirm='1';b.textContent='确认取消？';b.classList.add('btn-danger');b._confirmTimer=setTimeout(()=>{delete b.dataset.confirm;b.textContent=text;b.classList.remove('btn-danger');b._confirmTimer=0;},3000);};}
/* HELD 的截止时间是服务端算好的绝对时刻：只渲染一次文本的话，用户会盯着一个已经过期的
   「请在截止前确认」看下去。这里每秒改一次剩余时间，归零后禁用按钮并拉一次列表，
   让服务端的 EXPIRED 结果回到界面上。 */
function holdLeft(dl){const sec=Math.max(0,Math.floor((Number(dl)-Date.now())/1000));return '剩余 '+Math.floor(sec/60)+' 分 '+String(sec%60).padStart(2,'0')+' 秒';}
/* 备注列的截断与展开渲染收在 UI.noteCell（web/ui.js）：原本这一列写作
   r.note.length>20?r.note.slice(0,20)+'…' —— 数的是 UTF-16 码元，一个 emoji（或 CJK 扩展 B
   区的生僻字）占两个码元，正好落在第 20 位就被切成半个代理对。管理台的待审批列表现在也要
   同一列，所以这里不再留第二份实现。 */
function tickHolds(){
  const cells=document.querySelectorAll('#reservations [data-hold]');
  if(!cells.length){if(state.holdTimer){clearInterval(state.holdTimer);state.holdTimer=0;}return;}
  let expired=false;
  cells.forEach(cell=>{const dl=Number(cell.dataset.hold);
    if(dl>Date.now()){const t=cell.querySelector('.hold-left');if(t)t.textContent=holdLeft(dl);}
    else{expired=true;const b=cell.querySelector('button');if(b){b.disabled=true;b.textContent='保留已超时';}
      const t=cell.querySelector('.hold-left');if(t)t.textContent='名额已释放给下一位候补';}});
  if(expired&&!state.holdReloaded){state.holdReloaded=true;loadRecords();}
}
async function loadRecords(page){closeReschedule();const token=++state.recQuery;const p=page||state.page;state.page=p;empty($('reservations'),'正在读取记录…',true);empty($('waitlist'),'正在读取记录…',true);try{const d=await api('/api/me/records?page='+p+'&page_size=20'+(state.recFilter?'&status='+state.recFilter:''));if(token!==state.recQuery)return;const rows=(d.reservations||[]).map(r=>{const fields=[r.lab_name,stamp(r.start_at),showStatus(r),r.source==='WAITLIST'?'候补补位':r.source==='DIRECT'?'直接预约':r.source,noteCell(r.note)];const canCancel=r.status==='CONFIRMED'&&Number(r.start_at)*1000>Date.now();if(canCancel){const ops=el('span',undefined,'actions');ops.append(action('取消预约',confirmAction('取消预约',b=>mutation('/api/reservations/'+r.id+'/cancel',{},'预约已取消。',b))));ops.append(action('改期',b=>showReschedule(r,b),'secondary'));fields.push(ops);}else if(r.status==='HELD'){const dl=r.hold_deadline?Number(r.hold_deadline)*1000:0;if(dl>Date.now()){const ops=el('span',undefined,'actions');ops.append(action('确认保留',b=>mutation('/api/reservations/'+r.id+'/confirm',{},'已确认，预约生效。',b),'primary'),el('span',holdLeft(dl),'muted hold-left'));ops.dataset.hold=String(dl);fields.push(ops);}else fields.push('保留已超时，名额已释放给候补。');}else if(r.status==='PENDING'){fields.push('待管理员审批，批准前不占用名额');}else fields.push(r.checked_in_at?'已签到 '+time(r.checked_in_at):'—');return fields;});table($('reservations'),['实验室','时间','状态','来源','备注','操作'],rows);table($('waitlist'),['实验室','时间','状态','顺序','操作'],(d.waitlist||[]).map(r=>{const fields=[r.lab_name,stamp(r.start_at),statuses[r.status]||r.status,r.status==='WAITING'&&r.position?'第 '+r.position+' 位':r.status==='SKIPPED'?'暂不可递补（账号停用等）':r.status==='WITHDRAWN'?'已主动退出':'—'];fields.push(r.status==='WAITING'&&Number(r.start_at)*1000>Date.now()?action('退出候补',b=>mutation('/api/waitlist/'+r.id+'/withdraw',{},'已退出候补。',b)):'—');return fields;}));$('mine-page').textContent='第 '+p+' 页'+(d.has_more?'（还有更多）':'');$('mine-prev').disabled=p<=1;$('mine-next').disabled=!d.has_more;}catch(e){if(token!==state.recQuery)return;failed($('reservations'),'记录加载失败：'+e.message,()=>loadRecords());empty($('waitlist'),'请刷新重试');}}
/* 改期候选展开在被点击那一行的正下方，不再与预约页签的替代时段共用 #alternatives。
   共用一块常驻面板的代价实测很具体：候选列表距触发行 1589px，压在修改密码 / 在线会话 /
   API 令牌三块面板之后；且「刷新记录」重画表格后它就没人认领了——残留一份对不上号的旧候选。
   行内展开一次解决这两点：列表跟着行走，表格重渲染时它一起消失。 */
function closeReschedule(){if(state.picker){state.picker.remove();state.picker=null;}}
async function showReschedule(r,btn){const row=btn.closest('tr');closeReschedule();btn.disabled=true;try{const d=await api('/api/slots?lab_id='+encodeURIComponent(r.lab_id)+'&date='+today());const all=(d.slots||[]).filter(x=>String(x.id)!==String(r.slot_id));const day2=new Date(Date.now()+86400000).toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'});const d2=await api('/api/slots?lab_id='+encodeURIComponent(r.lab_id)+'&date='+day2);all.push(...(d2.slots||[]));const targets=all.filter(x=>x.enabled&&x.lab_enabled&&Number(x.confirmed_count)<Number(x.capacity)&&Number(x.start_at)*1000>Date.now()).slice(0,6);
 const tr=el('tr',undefined,'reschedule-row');tr.dataset.rid=String(r.slot_id);
 const td=document.createElement('td');td.colSpan=row.cells.length;td.dataset.label='';
 const box=el('div',undefined,'alt-area');box.tabIndex=-1;
 const head=el('div',undefined,'alt-head');head.append(el('h3','改期：选择同实验室的新时段（原时段自动释放并按顺序补位）'),action('收起',closeReschedule,'quiet'));
 const list=el('div',undefined,'alt-list');list.setAttribute('role','group');list.setAttribute('aria-label','可改期的目标时段');
 if(!targets.length)list.append(el('p','近两天暂无可改的目标场次。','muted'));
 for(const t of targets)list.append(action(time(t.start_at)+' – '+time(t.end_at)+'（已约 '+t.confirmed_count+'/'+t.capacity+'）',async bb=>{bb.disabled=true;try{await mutation('/api/reservations/'+r.id+'/reschedule',{slot_id:String(t.id),request_id:uid()},'改期成功。');closeReschedule();}catch(e){bb.disabled=false;}},'primary'));
 box.append(head,list);td.append(box);tr.append(td);row.after(tr);state.picker=tr;
 UI.scrollTo(tr,'nearest');box.focus();
 }catch(e){note(e.message,true);closeReschedule();}
 /* 「改期」按钮必须在所有路径复位：只在部分分支解禁会让入口永久失效。 */
 finally{btn.disabled=false;}}
function renderRecFilter(){const box=$('rec-filter');box.replaceChildren();for(const pair of [['','全部'],['CONFIRMED','进行中'],['CANCELLED','已取消'],['NO_SHOW','已爽约']]){const on=state.recFilter===pair[0];const b=el('button',pair[1],'chips'+(on?' on':''));b.type='button';
 /* 单选筛选用的是「当前项」而不是「已按下」：点已选中的那一项不会取消筛选。 */
 if(on)b.setAttribute('aria-current','true');
 b.addEventListener('click',()=>{if(state.recFilter===pair[0])return;state.recFilter=pair[0];state.page=1;renderRecFilter();loadRecords();});box.append(b);}}
async function refreshNotifyCount(){try{const d=await api('/api/me/notifications?page=1&page_size=1');const n=Number(d.unread_count||0);$('notify-count').textContent=n>99?'99+':String(n);$('notify-count').hidden=n===0;}catch(e){/* 通知计数失败不阻塞主流程 */}}
async function loadNotifications(){const box=$('notify-list');try{const d=await api('/api/me/notifications?page='+state.notifyPage+'&page_size=20');const list=d.notifications||[];const oldMore=box.querySelector('.notify-more');if(oldMore)oldMore.remove();if(state.notifyPage===1)box.replaceChildren();if(!list.length&&state.notifyPage===1){empty(box,'暂无通知。');return;}for(const n of list){const item=el('article',undefined,'notify-item'+(n.read_at?'':' unread'));item.append(el('span',kindLabels[n.kind]||'通知','tag'),el('h4',n.title),el('p',n.body),el('time',stamp(n.created_at)+(n.read_at?' · 已读':' · 未读')));const actions=el('div',undefined,'actions');if(!n.read_at)actions.append(action('标为已读',()=>markNotifications({ids:[n.id]})));if(n.kind==='PROMOTED'&&n.slot_id)actions.append(action('去看场次',()=>{switchTab('book');}));item.append(actions);box.append(item);}if(d.has_more)box.append(action('加载更多通知',async b=>{b.disabled=true;state.notifyPage++;await loadNotifications();},'secondary notify-more'));}catch(e){empty(box,'通知加载失败：'+e.message);}}
async function markNotifications(body){await mutation('/api/me/notifications/read',body,'通知已标记为已读。');state.notifyPage=1;await loadNotifications();await refreshNotifyCount();}
async function loadSessions(){const box=$('sessions');empty(box,'正在读取会话…',true);try{const d=await api('/api/me/sessions');table(box,['创建时间','过期时间','状态','操作'],(d.sessions||[]).map(s=>[s.created_at?stamp(s.created_at):'未记录',stamp(s.expires_at),s.current?'当前会话':'其他会话',s.current?el('span','—'):action('下线',()=>revokeSession(s.id))]));}catch(e){failed(box,'会话加载失败：'+e.message,loadSessions);}}
async function revokeSession(id){await mutation('/api/me/sessions/'+encodeURIComponent(id)+'/revoke',{},'该会话已下线。');await loadSessions();}
/* 表单校验由页面负责：四个 form 都声明了 novalidate，浏览器不再兜底。
   规则与服务端逐条对齐（src/http.c 的 name_ok/text_ok、src/service.c 的改密与令牌）：
   长度一律按 UTF-8 字节数算，因为服务端用 strlen —— 按 .length 数 UTF-16 码元时，
   22 个汉字（66 字节）会在本地放行、到服务端才被拒；
   且一律不 trim：密码里的空格是有意义的字符，8 个空格是合法口令，
   trim 之后会被误判成「不能为空」。校验只负责拦下必然失败的提交，最终裁决仍在服务端。 */
function hasBlank(v){for(const ch of v){const c=ch.codePointAt(0);if(c<=0x20||c===0x7f)return true;}return false;}
const NAME_MIN = 2, NAME_MAX = 64, PW_MIN = 8, PW_MAX = 128, TOKEN_MAX = 80, NOTE_MAX = 200;
/* 服务端把字节说成「个可见字符」：中文一个字占 3 字节，打了 22 个字的人看到
   「需为 2..64 个字符」只会以为规则没生效。超长一律交给 UI.overBytes 报出实测字节数与
   汉字换算，其余分支各自说清失败原因——四种失败共用一句「需为 2..64」等于什么都没说。 */
function rRegName(v){const n=byteLen(v);return !n?'请填写用户名':n<NAME_MIN?'用户名至少 '+NAME_MIN+' 个字符':n>NAME_MAX?overBytes(v,NAME_MAX,'用户名'):hasBlank(v)?'用户名不能包含空格或控制字符':'';}
/* 登录不做 2 字符下限（服务端也只做非空与长度上限），但同样提前挡掉空格：
   库里不存在含空格的用户名，放行只会换来一句分不清是用户名还是密码错的 401。 */
function rLoginName(v){const n=byteLen(v);return !n?'请填写用户名':n>NAME_MAX?overBytes(v,NAME_MAX,'用户名'):hasBlank(v)?'用户名不能包含空格或控制字符':'';}
function rLoginPw(v){const n=byteLen(v);return !n?'请填写密码':n>PW_MAX?overBytes(v,PW_MAX,'密码'):'';}
function rPw(v){const n=byteLen(v);return n<PW_MIN?'密码至少 '+PW_MIN+' 位':n>PW_MAX?overBytes(v,PW_MAX,'密码'):'';}
function rTokName(v){const n=byteLen(v);return !n?'请填写令牌名称':n>TOKEN_MAX?overBytes(v,TOKEN_MAX,'令牌名称'):'';}
/* 校验结果统一交给 UI.formCheck 落进各自的 role=alert 区。 */

$('login-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget,b=$('login-submit');if(!formCheck(f,'login-error',[['username',rLoginName],['password',rLoginPw]]))return;b.disabled=true;try{const d=await api('/api/login',{username:f.elements.username.value,password:f.elements.password.value,role_hint:state.loginRole});f.elements.password.value='';await enter(d);}catch(err){$('login-error').textContent=err.message;}finally{b.disabled=false;}});
const LOGIN_ROLES={USER:{subtitle:'使用管理员为你创建的账号，或注册新账号',submit:'登录'},ADMIN:{subtitle:'使用管理员账号登录，进入管理控制台',submit:'进入管理台'}};
function setLoginRole(role){state.loginRole=LOGIN_ROLES[role]?role:'USER';const cfg=LOGIN_ROLES[state.loginRole];$('login-subtitle').textContent=cfg.subtitle;$('login-submit').textContent=cfg.submit;$('login-register-entry').hidden=state.loginRole!=='USER';for(const pair of [['USER','login-tab-user'],['ADMIN','login-tab-admin']])$(pair[1]).setAttribute('aria-pressed',String(pair[0]===state.loginRole));$('login-error').textContent='';}
$('login-tab-user').addEventListener('click',()=>setLoginRole('USER'));
$('login-tab-admin').addEventListener('click',()=>setLoginRole('ADMIN'));
$('goto-register').addEventListener('click',e=>{e.preventDefault();$('login-form').hidden=true;$('register-form').hidden=false;$('register-error').textContent='';});
$('goto-login').addEventListener('click',e=>{e.preventDefault();$('register-form').hidden=true;$('login-form').hidden=false;$('login-error').textContent='';});
$('register-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget,b=f.querySelector('button[type="submit"]');if(!formCheck(f,'register-error',[['username',rRegName],['password',rPw],['confirm',rPw]]))return;b.disabled=true;try{if(f.elements.password.value!==f.elements.confirm.value)throw new Error('两次输入的密码不一致');const d=await api('/api/register',{username:f.elements.username.value,password:f.elements.password.value});f.elements.password.value=f.elements.confirm.value='';await enter(d);note('注册成功，欢迎！默认为普通用户角色。');}catch(err){$('register-error').textContent=err.message;}finally{b.disabled=false;}});
$('logout').addEventListener('click',async()=>{try{await api('/api/logout',{});showLogin();}catch(e){note(e.message,true);}});
/* 页签的点击、aria-selected/tabindex 同步与方向键键盘导航统一由 UI.wireTabs 承担。 */
state.tabs = UI.wireTabs(document.querySelector('nav.tabs'), name => switchTab(name));
$('retry').addEventListener('click',()=>mutation(null,null,null,null,true));
$('lab-select').addEventListener('change',loadSlots);
async function loadLabAssets(){const box=$('lab-assets');const labId=$('lab-select').value;if(!labId){box.replaceChildren();return;}try{const d=await api('/api/labs/'+encodeURIComponent(labId)+'/assets');const list=(d.assets||[]);state.labAssets=list.filter(a=>a.status==='AVAILABLE');box.replaceChildren();if(!list.length){box.append(el('span','该实验室暂无资源清单','muted'));return;}box.append(el('span','实验室资源：','muted'));for(const a of list){const maint=a.status==='MAINTENANCE';
  /* 维修中的资源原先借用 .on（也就是「已选中」那套绿色）来表达，还只在 title 里说明：
     鼠标悬停才知道、触屏和键盘用户永远看不到，而且「选中色」用在这里读起来像「已勾选」。
     现在文字里直接写出来，配色换成警示色。 */
  const chip=el('span',(a.total>1?a.name+' ×'+a.total:a.name)+(a.spec?'（'+a.spec+'）':'')+(maint?'（维修中）':''),maint?'chips chip-maint':'chips');
  if(maint)chip.title='该资源维修中，暂不可预约';
  box.append(chip);}}catch(e){box.replaceChildren();}}
$('book-date').addEventListener('change',loadSlots);$('refresh-slots').addEventListener('click',loadSlots);$('refresh-checkin').addEventListener('click',renderCheckin);$('refresh-mine').addEventListener('click',()=>{state.page=1;refreshCurrent();});
$('refresh-tokens').addEventListener('click',loadTokens);
$('token-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget,b=f.querySelector('button[type="submit"]');if(!formCheck(f,'token-error',[['name',rTokName]]))return;b.disabled=true;try{const d=await api('/api/me/tokens',{name:f.elements.name.value});await loadTokens();UI.secretBox($('token-secret'),'令牌明文只显示这一次，服务端不保存原文。请立即复制保存：',d.token);note('令牌已创建，明文见下方常驻面板。');f.reset();}catch(e2){note(e2.message,true);}finally{b.disabled=false;}});
async function loadTokens(){const box=$('tokens');if(!box)return;empty(box,'正在读取令牌…',true);try{const d=await api('/api/me/tokens');const list=d.tokens||[];if(!list.length){empty(box,'暂无令牌。');return;}table(box,['名称','创建时间','操作'],list.map(t=>[t.name,stamp(t.created_at),action('吊销',b=>mutation('/api/me/tokens/'+t.id+'/revoke',{},'令牌已吊销。',b))]));}catch(e){failed(box,'令牌加载失败：'+e.message,loadTokens);}}
$('export-ics').addEventListener('click',async()=>{const b=$('export-ics');b.disabled=true;try{const d=await api('/api/me/calendar/export');const blob=new Blob([d.content],{type:'text/calendar;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=d.filename||'lab-schedule.ics';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);note('日历已导出：'+(d.filename||'lab-schedule.ics'));}catch(e){note(e.message,true);}finally{b.disabled=false;}});$('refresh-sessions').addEventListener('click',loadSessions);$('mine-prev').addEventListener('click',()=>loadRecords(Math.max(1,state.page-1)));$('mine-next').addEventListener('click',()=>loadRecords(state.page+1));
function openNotify(){const panel=$('notify-panel');if(panel.hidden){panel.hidden=false;$('notify-btn').setAttribute('aria-expanded','true');state.notifyPage=1;loadNotifications().then(()=>panel.focus());}else closeNotify();}
function closeNotify(){if($('notify-panel').hidden)return;$('notify-panel').hidden=true;$('notify-btn').setAttribute('aria-expanded','false');$('notify-btn').focus();}
$('theme-toggle').addEventListener('click',()=>{if(window.labTheme){window.labTheme.toggle();syncThemeToggle();}});
syncThemeToggle();
$('notify-btn').addEventListener('click',openNotify);$('notify-close').addEventListener('click',closeNotify);$('notify-read-all').addEventListener('click',()=>markNotifications({all:true}));
document.addEventListener('keydown',e=>{if(e.key==='Escape'){closeNotify();closeReschedule();}});
/* 兜底：任何未捕获的异常都要让用户看见并可刷新，而不是界面静默停在半完成状态。 */
window.addEventListener('error',e=>{if(e&&e.message)note('页面出现未预期的错误：'+e.message+'（刷新页面可恢复；未确认的操作仍可凭原请求编号重试）',true);});
window.addEventListener('unhandledrejection',e=>{const m=e&&e.reason&&e.reason.message?e.reason.message:'未知原因';note('页面出现未预期的错误：'+m+'（刷新页面可恢复；未确认的操作仍可凭原请求编号重试）',true);});
$('password-form').addEventListener('submit',e=>{e.preventDefault();const f=e.currentTarget;if(!formCheck(f,'password-error',[['old_password',rLoginPw],['new_password',rPw]]))return;if(f.elements.old_password.value===f.elements.new_password.value){$('password-error').textContent='新密码不能与原密码相同';f.elements.new_password.focus();return;}mutation('/api/me/password',{old_password:f.elements.old_password.value,new_password:f.elements.new_password.value},'密码已更新，其他设备的登录已失效。',null);f.reset();});
$('book-date').value=today();
(async()=>{try{await enter(await api('/api/me'));}catch(e){showLogin();if(e.status!==401)$('login-error').textContent=e.message;}})();

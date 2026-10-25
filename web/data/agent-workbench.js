/* Connect the supplied standalone workbench to the existing local task API. */
if(location.protocol!=='file:'){
 let pollTimer=null,config=null,loading=false;
 const done=s=>['completed','blocked','failed'].includes(s);
 async function request(path,body){const r=await fetch('/api/'+path,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:undefined);const data=await r.json();if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'请求未通过检查');return data;}
 function controls(){
  $('task-options').hidden=!T.draft;
  $('hint').textContent=config?.live_ready?'已连接本机 Agent。发送会真实执行；任务结束后可补充要求继续。':'已连接任务服务；实况配置未就绪，可回看已有录制。';
  document.querySelector('.send').disabled=loading||(!T.draft&&T.connected&&!done(T.status));
  if(T.connected){$('meta').innerHTML=`<span>${esc(T.mode)} · ${esc(({completed:'已完成',blocked:'待补充',failed:'未完成',running:'执行中',queued:'准备中'})[T.status]||T.status)}</span><span>${fmt(T.created_at)}</span><span>${!done(T.status)&&T.current_stage?'正在 '+esc(STAGE_NAMES[T.current_stage]||T.current_stage):''}</span>`;
   $('replay').hidden=!done(T.status);
  }
 }
 window.workbenchOpened=()=>{clearTimeout(pollTimer);controls();if(T.connected){history.replaceState(null,'','#task/'+T.id);poll(T.id);}};
 async function poll(id){
  if(T.id!==id||!T.connected)return;
  try{
   const data=await request(`tasks/${id}/view`);if(T.id!==id)return;data.title=data.task?.message?.slice(0,24)||data.title;data.stages.forEach(s=>s.summary=s.summary.replaceAll('None','待补充'));
   const signature=JSON.stringify([data.steps.length,data.stages.map(s=>s.done),data.status,data.map.found.length]);
   if(T.signature!==signature&&!playing){
    const thread=$('thread'),atEnd=thread.scrollHeight-thread.scrollTop-thread.clientHeight<80,position=thread.scrollTop;
    const active=TABS.find(t=>!$('p-'+t).hidden)||'map';
    Object.assign(T,data,{connected:true,pending:false,signature,extra:[]});
    renderTasks();renderRail();renderMap();renderDecisions();renderFindings();renderThread();
    TABS.forEach(t=>{$('tab-'+t).setAttribute('aria-selected',t===active);$('p-'+t).hidden=t!==active;});
    thread.scrollTop=atEnd?thread.scrollHeight:position;controls();
   }
   if(!done(data.status))pollTimer=setTimeout(()=>poll(id),1000);
  }catch(e){if(T.id!==id)return;$('hint').textContent=e.message;pollTimer=setTimeout(()=>poll(id),1800);}
 }
 window.startConnectedTask=async text=>{
  if(loading)return;
  if(!config?.live_ready){msg('sys','系统','<span>未执行</span>','<p>请在后端配置模型与高德 key 后再发送。已有录制仍可回看。</p>');return;}
  loading=true;controls();
  try{
   const parent=T.connected?T.id:null;
   const body={area:parent?T.task.area:$('area-select').value,profile:parent?T.task.profile:$('product-select').value,input:text,mode:'live',parent_id:parent};
   const result=await request('tasks',body);
   stopPlay();
   const draft={draft:false,connected:true,id:result.run_id,run_id:result.run_id,title:text.slice(0,22),request:text,product:$('product-select').selectedOptions[0]?.textContent||T.product,area:body.area,task:{profile:body.profile,area:body.area,parent_id:parent},status:'queued',mode:'实况',extra:[],stages:[],steps:[],channels:[],terminals:[],pitches:[],map:{base:[],found:[],zones:[]},counts:{},targets:[],boundary:[],radius:3000,cache:{total:0},route:{first:[]}};
   tasks.unshift(draft);cur=0;selStage='L0';$('ask').value='';toggleBox(false);open();
  }catch(e){msg('sys','系统','<span>未执行</span>',`<p>${esc(e.message)}</p>`);$('thread').scrollTop=$('thread').scrollHeight;}
  finally{loading=false;controls();}
 };
 async function connect(){
  try{
   const [cfg,profiles,areas,records]=await Promise.all([request('tasks/config'),request('profiles'),request('areas'),request('tasks')]);config=cfg;
   $('product-select').innerHTML=profiles.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');$('product-select').value='xinxian-huoquan';
   $('area-select').innerHTML=areas.map(a=>`<option value="${esc(a.id)}">${esc(({shishan:'狮山',guanqian:'观前街',ligongdi:'李公堤'})[a.id]||a.id)}</option>`).join('');$('area-select').value='shishan';
   const restored=records.map(r=>({id:r.run_id,run_id:r.run_id,task:r.task,title:r.task.message.slice(0,24),product:r.task.product,area:r.task.area,request:r.task.message,created_at:r.task.created_at,status:r.status,stopped_at:r.stopped_at,connected:true,pending:true,mode:r.task.mode==='replay'?'回放':'实况',draft:false,extra:[],stages:[],steps:[],channels:[],terminals:[],pitches:[],map:{base:[],found:[],zones:[]},counts:{},targets:[],boundary:[],radius:3000,cache:{total:0},route:{first:[]}}));
   tasks.unshift(...restored.filter(Boolean));
   const id=location.hash.split('/').pop();const index=tasks.findIndex(t=>t.id===id);
   if(index>=0){cur=index;open();}else{$('newtask').click();}
   controls();
  }catch(e){$('hint').textContent='正在连接任务服务；暂可回看录制。';setTimeout(connect,2000);}
 }
 connect();
}

const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('web/data/agent-workbench.js','utf8');
test('reload groups parent chains into one conversation and retains unrelated tasks',()=>{
 const ctx={location:{protocol:'file:'}};vm.createContext(ctx);vm.runInContext(source,ctx);
 const row=(id,parent,date)=>({run_id:id,task:{parent_id:parent,created_at:date,message:id}});
 const groups=ctx.groupTaskRecords([row('second','first','2'),row('other',null,'3'),row('first',null,'1'),row('third','second','4')]);
 assert.equal(groups.length,2);assert.equal(groups[0].root.run_id,'first');assert.equal(groups[0].latest.run_id,'third');
 assert.equal(groups[0].ids.length,3);
});
test('sending a follow-up replaces the current execution, retains messages, and sends parent ID',async()=>{
 const elements=new Map();const sent=[];
 const element=id=>{if(!elements.has(id))elements.set(id,{hidden:false,value:'',selectedOptions:[{textContent:'product'}],click(){},setAttribute(){},scrollHeight:100,scrollTop:0,clientHeight:100});return elements.get(id);};
 const ctx={location:{protocol:'http:',hash:''},history:{replaceState(){}},setTimeout(){},clearTimeout(){},
  window:null,$:element,document:{querySelector:()=>({})},esc:String,fmt:String,STAGE_NAMES:{},TABS:[],playing:false,
  tasks:[],T:{draft:true},cur:0,selStage:'L0',stopPlay(){},toggleBox(){},open(){ctx.T=ctx.tasks[ctx.cur];},msg(){},
  fetch:async(path,options)=>{let data=[];
   if(path.endsWith('config'))data={live_ready:true};
   if(path.endsWith('profiles'))data=[{id:'xinxian-huoquan',name:'product'}];
   if(path.endsWith('areas'))data=[{id:'shishan'}];
   if(options){sent.push(JSON.parse(options.body));data={run_id:'second'};}
   return {ok:true,json:async()=>data};
  }};
 ctx.window=ctx;vm.createContext(ctx);vm.runInContext(source,ctx);
 await new Promise(resolve=>setImmediate(resolve));
 const first={id:'first',connected:true,status:'completed',title:'Original',task:{area:'shishan',profile:'xinxian-huoquan'},steps:[]};
 ctx.T=first;ctx.tasks=[first];ctx.cur=0;
 await ctx.startConnectedTask('把半径改成 2 公里');
 assert.equal(ctx.tasks.length,1);assert.equal(ctx.T.id,'second');
 assert.equal(ctx.T.previousTurns[0].id,'first');assert.equal(ctx.T.title,'Original');
 assert.equal(sent[0].parent_id,'first');assert.equal(sent[0].input,'把半径改成 2 公里');
});

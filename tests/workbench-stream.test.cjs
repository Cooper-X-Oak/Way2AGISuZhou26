const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const html=fs.readFileSync('pipeline/view_template.html','utf8');
for(const [i,match] of [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].entries())new vm.Script(match[1],{filename:'workbench-'+i});
test('generated delivery page has complete replay data and executable scripts',()=>{
 const delivered=fs.readFileSync('web/data/agent-workbench.html','utf8');
 for(const match of delivered.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g))new vm.Script(match[1]);
 const match=delivered.match(/const DATA=([^\n]+);/);
 const data=JSON.parse(match[1]);assert.ok(data.tasks.length>0);
});
const helper=html.slice(html.indexOf('async function streamChat('),html.indexOf('const RUN_STREAMS='));
test('SSE parser preserves UTF8 and processes operation before final chunk',async()=>{
 const encoder=new TextEncoder();let delivered=false;
 const first=encoder.encode('data: {"type":"text_delta","text":"你好"}\n\ndata: {"type":"operation","run_id":"abc"}\n\n');
 const last=encoder.encode('data: {"type":"done","reply":"完成"}\n\n');
 let index=0;
 const ctx={TextDecoder,Uint8Array,fetch:async()=>({ok:true,body:{getReader:()=>({read:async()=>{
  if(index<first.length)return {value:first.slice(index,index+++1),done:false};
  if(index++===first.length){assert.equal(delivered,true);return {value:last,done:false};}
  return {done:true};
 },cancel:async()=>{},releaseLock:()=>{}})}})};
 vm.createContext(ctx);vm.runInContext(helper,ctx);const events=[];
 const result=await ctx.streamChat({},e=>{events.push(e);if(e.type==='operation')delivered=true;});
 assert.equal(events[0].text,'你好');assert.equal(result.reply,'完成');
});

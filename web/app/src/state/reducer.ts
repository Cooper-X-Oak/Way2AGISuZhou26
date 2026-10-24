import type {History,Stage,StageId} from '../data/types';
export const order:StageId[]=['L0','L1','L2','L2C','L3','L3P','L4','PACK'];
export interface Event extends History {type:string;id:string;time:number}
export interface NodeState {phase:'waiting'|'running'|'passed'|'failed'|'gate';attempts:number;backtracks:number;approved_by?:string|null;event?:Event}
export type State=Record<StageId,NodeState>;
export const initial=():State=>Object.fromEntries(order.map(id=>[id,{phase:'waiting',attempts:0,backtracks:0}])) as State;
export function reducer(state:State,event:Event):State{
 if(!order.includes(event.stage))return state;
 const next={...state},old=state[event.stage],node={...old,event};next[event.stage]=node;
 if(event.type==='stage_start'){node.phase='running';node.approved_by=null;}
 if(event.type==='gate_pending')node.phase='gate';
 if(event.type==='gate_approved'){node.phase='passed';node.approved_by=event.approved_by;}
 if(event.type==='result'||event.type==='attempt'){node.phase=event.status==='passed'?'passed':'failed';node.attempts++;node.approved_by=event.approved_by;}
 if(event.type==='violation')node.phase='failed';
 if(event.type==='stage_done')node.phase='passed';
 if(event.type==='backtrack'){
  node.backtracks++;const start=order.indexOf(event.backtrack?.to||'L2');
  for(const id of order.slice(start)){next[id]={...next[id],phase:'waiting',approved_by:null};}
 }
 return next;
}
export function historyEvents(stages:Stage[]):Event[]{
 const history=stages.flatMap(s=>s.history.map(h=>({...h,stage:s.stage}))).sort((a,b)=>Date.parse(a.timestamp)-Date.parse(b.timestamp));
 return history.flatMap((h,i)=>{
  const time=Date.parse(h.timestamp),base={...h,time,id:String(i)};
  if(h.backtrack)return [{...base,type:'backtrack'}];
  if(h.attempt==null)return [];
  const events:Event[]=[{...base,id:i+'-start',type:'stage_start'}];
  if(h.approved_by)events.push({...base,id:i+'-gate',type:'gate_pending'});
  events.push({...base,type:'result'});return events;
 });
}
export function stateAt(events:Event[],index:number){return events.slice(0,index+1).reduce(reducer,initial());}
export function snapshot(stages:Stage[]):State{const state=stateAt(historyEvents(stages),Infinity);for(const s of stages)state[s.stage]={...state[s.stage],phase:s.accepted?'passed':s.available?'failed':'waiting'};return state;}
export function liveEvent(raw:{type:string;stage:StageId;attempt?:number;payload:Partial<History>},id:string):Event{return {...raw.payload,stage:raw.stage,attempt:raw.attempt??raw.payload.attempt,type:raw.type,id,timestamp:raw.payload.timestamp||new Date().toISOString(),time:Date.now()} as Event;}
export const isViolation=(e:Event)=>e.type==='violation'||e.type==='result'&&!!e.violations?.length;
export function hold(e:Event|undefined,reduced:boolean,lineMs:number){if(reduced)return 0;if(e&&isViolation(e))return 2500;if(e?.type==='backtrack')return 1500;if(e?.type==='gate_pending')return 800;return lineMs;}

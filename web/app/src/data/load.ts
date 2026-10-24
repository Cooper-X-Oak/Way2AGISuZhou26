import type {Scenario,Summary,MapData,Stage,Terminals} from './types';
const cache=new Map<string,Promise<unknown>>();
export async function readData<T>(path:string):Promise<T>{
 if(window.__BUNDLE__){if(!(path in window.__BUNDLE__))throw Error(`离线包未包含 ${path}`);return window.__BUNDLE__[path] as T;}
 if(!cache.has(path)){const promise=fetch('./'+path.split('/').map(encodeURIComponent).join('/')).then(r=>{if(!r.ok)throw Error(`${path} · HTTP ${r.status}`);return r.json();});cache.set(path,promise);promise.catch(()=>cache.delete(path));}
 return cache.get(path) as Promise<T>;
}
export async function loadScenario(id:string):Promise<Scenario>{const [summary,map,stages,terminals]=await Promise.all([readData<Summary>(`${id}/summary.json`),readData<MapData>(`${id}/map.json`),readData<{stages:Stage[]}>(`${id}/stages.json`),readData<Terminals>(`${id}/terminals.json`)]);return {id,summary,map,stages:stages.stages,terminals};}
export const names={L0:'立项',L1:'卖给谁',L2:'去哪里',L2C:'对手在哪',L3:'进哪家',L3P:'谁来供货',L4:'怎么说',PACK:'作战包'};
export const channel=(s?:string)=>({seafood:'海鲜',hotpot:'火锅',bbq:'烧烤',banquet:'宴席',canteen:'食堂',late_meal:'夜宵',restaurant_other:'其他餐饮'}[s||'']||s||'—');
export const action=(s?:string)=>({'首访-带样品和桌牌':'带样品首访','首访-了解需求':'先了解需求'}[s||'']||s||'—');
export const text=(v:unknown):string=>v==null?'—':typeof v==='object'?JSON.stringify(v):String(v);
export function distance(a?:{lat:number;lon:number},b?:{lat:number;lon:number}){if(!a||!b)return null;const r=Math.PI/180,h=Math.sin((b.lat-a.lat)*r/2)**2+Math.cos(a.lat*r)*Math.cos(b.lat*r)*Math.sin((b.lon-a.lon)*r/2)**2;return Math.round(6371000*2*Math.atan2(Math.sqrt(h),Math.sqrt(1-h)));}

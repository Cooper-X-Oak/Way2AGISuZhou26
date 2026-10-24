import {describe,it,expect} from 'vitest';
import {readFileSync} from 'node:fs';
import {historyEvents,initial,reducer,stateAt,snapshot,hold,isViolation,liveEvent} from './reducer';
import type {Stage} from '../data/types';
const load=(id:string):Stage[]=>JSON.parse(readFileSync(new URL(`../../../data/${id}/stages.json`,import.meta.url),'utf8')).stages;
describe('real exported histories',()=>{
 for(const id of ['guanqian','shishan','ligongdi','guanqian-compliance','backtrack'])it(`${id}: replay ends at accepted snapshot`,()=>{const stages=load(id);expect(stateAt(historyEvents(stages),Infinity)).toEqual(snapshot(stages));});
 it('compliance retains failure and then passes on retry',()=>{const events=historyEvents(load('guanqian-compliance')),i=events.findIndex(isViolation);expect(i).toBeGreaterThan(0);expect(stateAt(events,i).L4.phase).toBe('failed');expect(stateAt(events,Infinity).L4.attempts).toBe(2);expect(hold(events[i],false,480)).toBe(2500);expect(hold(events[i],true,480)).toBe(0);});
 it('backtracking invalidates downstream acceptance but retains attempts',()=>{const events=historyEvents(load('backtrack')),i=events.findIndex(e=>e.type==='backtrack'),s=stateAt(events,i);expect(s.L2.phase).toBe('waiting');expect(s.L3.backtracks).toBe(1);expect(s.L3.attempts).toBeGreaterThan(0);expect(s.PACK.phase).toBe('waiting');});
 it('gate approval is explicitly sourced',()=>{const events=historyEvents(load('guanqian')),i=events.findIndex(e=>e.type==='gate_pending');expect(stateAt(events,i).L2.phase).toBe('gate');expect(stateAt(events,i+1).L2.approved_by).toBe('auto (demo)');});
 it('live and replay use the same result reduction',()=>{const replay=historyEvents(load('guanqian')).find(e=>e.type==='result')!;const live=liveEvent({type:'attempt',stage:replay.stage,payload:replay},'sse-1');expect(reducer(initial(),live).L0.phase).toBe(reducer(initial(),replay).L0.phase);});
});

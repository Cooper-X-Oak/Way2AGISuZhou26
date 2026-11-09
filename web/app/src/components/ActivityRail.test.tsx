// @vitest-environment jsdom
import {afterEach, describe, expect, it, vi} from 'vitest';
import {act} from 'react';
import {createRoot, Root} from 'react-dom/client';
import {readFileSync} from 'node:fs';
import {ActivityRail} from './ActivityRail';
import {ActivityEvent, activityTokens, groupActivity, parseActivity} from '../data/activity';

const event = (overrides: Partial<ActivityEvent> = {}): ActivityEvent => ({
  id: 'run:1', run_id: 'run', seq: 1, ts: '2026-10-24T09:36:24Z', stage: 'L1',
  actor: 'agent', kind: 'agent.plan', title: '读取产品资料', detail: {summary: '原始详情'},
  refs: [], metrics: {}, status: 'ok', parent_id: null, ...overrides,
});
let root: Root | undefined;
let host: HTMLDivElement;
function render(events: ActivityEvent[]) {
  (globalThis as {IS_REACT_ACT_ENVIRONMENT?: boolean}).IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement('div'); document.body.append(host);
  root = createRoot(host);
  act(() => root!.render(<ActivityRail events={events}/>));
  return host;
}
afterEach(() => {if (root) act(() => root!.unmount()); root = undefined; document.body.replaceChildren(); vi.restoreAllMocks();});

describe('ActivityRail', () => {
  it('groups stages in source order, keeps run-level events, and excludes usage rows', () => {
    const events = [event({id:'r0', stage:null}), event(), event({id:'r2',stage:'L2'}), event({id:'r3'}), event({id:'u',kind:'llm.usage',metrics:{tokens:42}})];
    expect(groupActivity(events).map(g => [g.stage, g.events.length])).toEqual([[null,1],['L1',2],['L2',1]]);
    expect(activityTokens(events)).toBe(42);
    render(events);
    expect(host.querySelectorAll('.activity-stage')).toHaveLength(3);
    expect(host.querySelectorAll('.activity-row')).toHaveLength(4);
    const group = host.querySelector<HTMLDetailsElement>('.activity-stage')!;
    expect(group.open).toBe(true);
    act(() => group.querySelector('summary')!.click());
    expect(group.open).toBe(false);
  });
  it('renders actor symbols, expandable escaped detail, and a violation stamp', () => {
    render([event(), event({id:'p',actor:'program'}),event({id:'h',actor:'human'}),event({id:'v',kind:'check.violation',actor:'program',detail:{rule_id:'brief-function',matched_term:'护血管',html:'<script>bad()</script>'}})]);
    expect([...host.querySelectorAll('.actor')].map(x => x.textContent)).toEqual(['◆','■','●','✕']);
    expect(host.querySelector('.activity-blocked')?.textContent).toContain('brief-function');
    expect(host.querySelector('script')).toBeNull();
    const detail = host.querySelector<HTMLDetailsElement>('.activity-row details')!;
    expect(detail.open).toBe(false);
    act(() => detail.querySelector('summary')!.click());
    expect(detail.open).toBe(true);
    expect(detail.textContent).toContain('原始详情');
  });
  it.each([['确认','confirm'],['退回','return']])('dispatches %s locally once and does not change the source gate', (label, decision) => {
    const listener = vi.fn(); window.addEventListener('pipeline:gate-decision',listener);
    const gate = event({kind:'gate.pending',actor:'human',status:'waiting'});
    render([gate]);
    act(() => [...host.querySelectorAll('button')].find(b => b.textContent === label)!.click());
    expect(listener).toHaveBeenCalledTimes(1);
    expect((listener.mock.calls[0][0] as CustomEvent).detail).toEqual({eventId:'run:1',runId:'run',stage:'L1',decision});
    expect(host.textContent).toContain('仅本地记录');
    expect(host.querySelectorAll('button')).toHaveLength(0);
    expect(gate.status).toBe('waiting');
    window.removeEventListener('pipeline:gate-decision',listener);
  });
  it('reads the exact real sample, normalizes display labels, and preserves its failure', () => {
    const events = parseActivity(readFileSync(new URL('../../public/sample-events.jsonl',import.meta.url),'utf8'));
    expect(events).toHaveLength(98);
    expect(events[0].title).toBe('运行开始');
    expect(events.at(-1)?.status).toBe('blocked');
    expect(events.at(-1)?.detail.error).toBe('agent_stage_budget_exceeded');
    expect(events.filter(e => e.kind==='llm.usage').every(e => e.actor==='system')).toBe(true);
    expect(activityTokens(events)).toBe(19425);
    render(events);
    expect(host.querySelectorAll('.activity-row')).toHaveLength(88);
    expect(host.textContent).toContain('运行失败');
  });
  it('rejects malformed input rather than silently dropping ledger entries', () => {
    expect(() => parseActivity('{"id":"bad"}')).toThrow('第 1 行');
    expect(() => parseActivity(JSON.stringify(event())+'\nnot-json')).toThrow('第 2 行');
  });
});

import {useState} from 'react';
import {ActivityEvent, dispatchGateDecision, groupActivity} from '../data/activity';
import {names} from '../data/load';

function EventRow({event}: {event: ActivityEvent}) {
  const [decision, setDecision] = useState<string>();
  const blocked = event.kind === 'check.violation' || event.status === 'blocked';
  const icon = blocked ? '✕' : {agent: '◆', program: '■', human: '●', system: '◇'}[event.actor];
  const gate = event.kind === 'gate.pending';
  return <li className={`activity-row ${blocked ? 'activity-blocked' : ''} ${gate ? 'activity-gate' : ''}`}>
    <details>
      <summary><span aria-label={blocked ? '被拦' : event.actor} className={`actor actor-${event.actor}`}>{icon}</span><span className="event-title" title={event.title}>{event.title}</span><span className="event-seq">{event.seq}</span></summary>
      <div className="event-detail"><time dateTime={event.ts}>{event.ts}</time><p>{event.kind} · {event.status}</p><pre>{JSON.stringify(event.detail, null, 2)}</pre>{event.refs.length > 0 && <p>关联：{event.refs.join(' · ')}</p>}</div>
    </details>
    {gate && <div className="gate-actions">{decision ? <p role="status">已{decision} · 仅本地记录</p> : <><p>待人工确认 · 本地演示</p><button onClick={() => {dispatchGateDecision(event, 'confirm'); setDecision('确认');}}>确认</button><button onClick={() => {dispatchGateDecision(event, 'return'); setDecision('退回');}}>退回</button></>}</div>}
  </li>;
}

export function ActivityRail({events, error, loading}: {events: ActivityEvent[]; error?: string; loading?: boolean}) {
  return <section className="activity-content" aria-label="Agent 活动流">
    <div className="activity-heading"><h2>Agent 活动流</h2><span className="chip review">独立样例</span></div>
    <p className="activity-note">L1 运行记录 · 与主区商圈快照独立</p>
    {events[0] && <p className="activity-source mono">{events[0].run_id}</p>}
    <div className="activity-legend"><span className="actor-agent">◆ agent</span><span>■ 程序</span><span className="actor-human">● 人</span><span className="actor-blocked">✕ 被拦</span></div>
    {loading && <p role="status">正在读取活动…</p>}
    {error && <p role="alert">{error}</p>}
    {!loading && !error && !events.length && <p>暂无活动</p>}
    {groupActivity(events).map(group => <details className="activity-stage" key={group.stage || 'run'} open>
      <summary><strong>{group.stage ? `${group.stage} · ${names[group.stage as keyof typeof names] || '关卡'}` : '运行'} </strong><span>{group.events.length} 条</span></summary>
      <ol>{group.events.map(event => <EventRow key={event.id} event={event}/>)}</ol>
    </details>)}
  </section>;
}

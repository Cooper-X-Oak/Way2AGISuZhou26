export interface ActivityEvent {
  id: string;
  run_id: string;
  seq: number;
  ts: string;
  stage: string | null;
  actor: 'agent' | 'program' | 'human' | 'system';
  kind: string;
  title: string;
  detail: Record<string, unknown>;
  refs: string[];
  metrics: Record<string, number>;
  status: 'running' | 'ok' | 'warn' | 'blocked' | 'waiting';
  parent_id: string | null;
}

const titles: Record<string, string> = {
  'run.started': '运行开始', 'run.completed': '运行完成', 'run.failed': '运行失败',
  'stage.started': '关卡开始', 'stage.completed': '关卡完成',
  'agent.plan': '制定计划', 'agent.read': '读取资料', 'agent.think': '判断摘要',
  'agent.output': '产出结果', 'agent.retry': '修订重试',
  'tool.call': '调用工具', 'tool.result': '工具返回', 'tool.duplicate': '重复工具调用',
  'context.compacted': '上下文已压缩', 'llm.usage': '模型用量',
  'check.layer': '分层检查', 'check.violation': '规则拦截',
  'flow.backtrack': '流程回退', 'gate.pending': '等待确认', 'gate.resolved': '关口已处理',
  'artifact.ready': '交付物已就绪', 'clarify.question': '待澄清问题', 'clarify.answer': '澄清答复',
};

/** Presentation-only normalization. Never rewrites the source JSONL or invents events. */
export function adaptEvent(event: ActivityEvent): ActivityEvent {
  const detail = event.detail;
  const hint = [detail.summary, detail.name, detail.error].find(v => typeof v === 'string' && v);
  const title = event.title === event.kind
    ? `${titles[event.kind] || event.kind}${hint ? ` · ${hint}` : ''}`
    : event.title;
  return {...event, title: title.replace(/\s+/g, ' '), actor: event.kind === 'llm.usage' ? 'system' : event.actor};
}

export function parseActivity(text: string): ActivityEvent[] {
  return text.split(/\r?\n/).filter(line => line.trim()).map((line, index) => {
    try {
      const event = JSON.parse(line);
      if (!event || typeof event.id !== 'string' || typeof event.run_id !== 'string' ||
          !Number.isInteger(event.seq) || typeof event.ts !== 'string' ||
          !(event.stage === null || typeof event.stage === 'string') ||
          !['agent', 'program', 'human', 'system'].includes(event.actor) ||
          typeof event.kind !== 'string' || typeof event.title !== 'string' ||
          !event.detail || typeof event.detail !== 'object' || Array.isArray(event.detail) ||
          !Array.isArray(event.refs) || !event.metrics || typeof event.metrics !== 'object' ||
          !['running', 'ok', 'warn', 'blocked', 'waiting'].includes(event.status) ||
          !(event.parent_id === null || typeof event.parent_id === 'string')) throw Error('事件信封无效');
      return adaptEvent(event);
    } catch { throw Error(`样例事件第 ${index + 1} 行格式无效`); }
  }).sort((a, b) => a.seq - b.seq);
}

export function groupActivity(events: ActivityEvent[]) {
  const groups = new Map<string | null, ActivityEvent[]>();
  for (const event of events) {
    if (event.kind === 'llm.usage') continue;
    if (!groups.has(event.stage)) groups.set(event.stage, []);
    groups.get(event.stage)!.push(event);
  }
  return [...groups].map(([stage, items]) => ({stage, events: items}));
}

export function activityTokens(events: ActivityEvent[]) {
  return events.filter(e => e.kind === 'llm.usage').reduce((sum, e) => sum + (Number.isFinite(e.metrics.tokens) ? e.metrics.tokens : 0), 0);
}

export type GateDecision = {eventId: string; runId: string; stage: string | null; decision: 'confirm' | 'return'};
export function dispatchGateDecision(event: ActivityEvent, decision: GateDecision['decision']) {
  const detail: GateDecision = {eventId: event.id, runId: event.run_id, stage: event.stage, decision};
  window.dispatchEvent(new CustomEvent<GateDecision>('pipeline:gate-decision', {detail}));
}

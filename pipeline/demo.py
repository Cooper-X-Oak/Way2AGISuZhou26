"""一条命令的黑客松演示：uv run python -m pipeline demo

案例：洞庭山·新鲜活泉（真实产品资料）× 苏州高新区狮山商圈。
目标渠道是茶馆、写字楼、健身房，而狮山缓存里这三类都是 0 家——
规则版流水线停在 L2；Agent 自己决定去高德补采什么，补到的商户经代码核对后进入后续关卡。

模式（全部如实标识）：
  实况  live    有 LLM_* 与 AMAP_KEY 时：真实模型 + 真实高德；成功后自动录制
  回放  replay  有录制文件时：按录制顺序重放模型回复和高德结果，不联网
  自动  auto    能实况就实况，否则回放；两者都不行时说明缺什么
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

CASE = dict(profile='xinxian-huoquan', area='shishan',
            request='苏州高新区狮山商圈周边3公里，推广新鲜活泉，2个地推，做4周')
RECORDING = Path('docs/demo/recordings/xinxian-shishan.json')
WEB_SCENARIO = Path('web/data/xinxian-agent')
REPORTS = Path('.work/reports')


def say(text=''):
    print(text, flush=True)


def banner(text):
    say('\n' + '═' * 64 + f'\n  {text}\n' + '═' * 64)


def live_ready():
    missing = [k for k in ('LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL') if not os.getenv(k)]
    try:
        from pipeline.amap import load_key
        load_key('.')
    except RuntimeError:
        missing.append('AMAP_KEY（写在仓库根目录 .env：AMAP_KEY=...）')
    return missing


class Replayer:
    """Returns recorded model replies in order; refuses to improvise when the flow diverges."""

    def __init__(self, replies, model):
        self.replies, self.i, self.model = replies, 0, f'replay:{model}'

    def __call__(self, payload):
        from pipeline.agent_loop import AgentError
        if self.i >= len(self.replies):
            raise AgentError('回放录制已用完：本次流程与录制时不一致，请重新实况录制')
        reply = self.replies[self.i]
        self.i += 1
        return reply


def build_inputs(agent, **extra):
    from pipeline.__main__ import prepare_data
    from pipeline.judge import Judge
    from pipeline.market_capture import load_market
    from pipeline.profiles import load, parameters
    params = parameters()
    pois, coverage = prepare_data(params, CASE['area'])
    return {'request': CASE['request'], 'site': coverage['site'], 'area_id': CASE['area'], 'pois': pois, 'coverage': coverage,
            'params': params, 'judge': Judge(offline=True), 'profile': load(CASE['profile']), 'demo_replay': None,
            'auto_approve': True, 'no_defaults': False, 'market': load_market(CASE['area']),
            'agent_l1d': agent, 'agent_echo': agent, **extra}


def read(directory, name):
    from pipeline.io import read as verified
    return verified(Path(directory) / name)


def summary(directory, mode):
    """Readable result for the market-expansion manager: where, which stores, why, what to say."""
    profile = read(directory, 'profile.json')
    channel_names = {c['id']: c['name'] for c in profile['channels']}
    pois = {p['id']: p for p in read(directory, 'pois.json')}
    d = read(directory, 'discovery.json')
    zones = [z for z in read(directory, 'zones.json')['items'] if z.get('selected')]
    terminals = read(directory, 'terminals.json')['items']
    materials = read(directory, 'materials.json')['items']
    added = d['added_poi_ids']
    lines = [f"# 演示结果：{profile['product']['name']} × 狮山商圈（{mode}）", '',
             f"- run：`{Path(directory).name}`",
             f"- Agent：模型调用 {d['agent']['model_calls']} 次，工具调用 {d['agent']['tool_calls']} 次，高德请求 {d['agent']['amap_calls']} 次（回放为 0）",
             f"- 补采新增商户 {len(added)} 家：" + '，'.join(f"{channel_names.get(r['channel_id'], r['channel_id'])} {r['candidate_count']} 家（{r['status']}）" for r in d['items']),
             f"- Agent 结论：{d['decision']}", '',
             f"## 去哪里：选中 {len(zones)} 个商圈网格", '']
    lines += [f"- 网格 {z['zone_id']}：排名 {z['rank']}，目标渠道计数 {z['counts']}" for z in zones]
    lines += ['', '## 进哪家：A 级首访名单（前 10）', '', '| # | 门店 | 渠道 | 距中心 | 理由 | 动作 |', '|---|---|---|---|---|---|']
    top = [t for t in terminals if t['grade'] == 'A'][:10] or terminals[:10]
    for i, t in enumerate(top, 1):
        p = pois.get(t['poi_id'], {})
        tag = '（Agent 补采）' if t['poi_id'] in added else ''
        lines.append(f"| {i} | {p.get('name') or '—'}{tag} | {channel_names.get(t['channel_id'], t['channel_id'])} | "
                     f"{round(p.get('distance_m') or 0)} 米 | {t['reason']} | {t['next_action']} |")
    lines += ['', f"共 {len(terminals)} 家终端：A {sum(t['grade']=='A' for t in terminals)} / B {sum(t['grade']=='B' for t in terminals)} / C {sum(t['grade']=='C' for t in terminals)}。", '',
              '## 怎么说：进店话术示例', '']
    shown = set()
    for m in materials:
        target = m['target']; ch = next((t['channel_id'] for t in terminals if t['poi_id'] == target), target)
        if ch in shown: continue
        shown.add(ch)
        lines += [f"**{channel_names.get(ch, ch)}**（合规扫描：{'通过' if m['compliance']['passed'] else '未通过'}）", '', '> ' + m['pitch'], '']
    lines += ['## 如实说明', '',
              '- 只有 L1D（终端发现）由模型决策；其余关卡为规则（`judged_by: rules`），打分、分级由代码计算。',
              '- 补采商户来自高德 POI 基础字段，名称与类型不代表实际经营情况或进货意愿，需走访确认。',
              '- 价格、毛利、首单政策未知，话术中不承诺；竞品与供货信息沿用现有缓存。',
              f"- 模式：{mode}。" + ('回放按录制顺序重放真实模型回复与高德结果，未重新调用。' if mode == '回放' else '真实模型与真实高德。')]
    return '\n'.join(lines) + '\n'


def record(directory, post_log, model, recording=RECORDING):
    pages = []
    for path in sorted((Path(directory) / 'agent' / 'amap').glob('*.json')):
        row = json.loads(path.read_text(encoding='utf-8'))
        pages.append({**row['query'], 'page': row['page'], 'payload': row['payload']})
    from pipeline.io import write
    write(recording, {'note': '实况录制：模型回复按调用顺序保存；高德为基础字段白名单，无联系方式；仅供演示回放。',
                      'recorded_at': datetime.now(timezone.utc).isoformat(), 'run_id': Path(directory).name,
                      'model': model, 'case': CASE, 'model_replies': post_log, 'amap_pages': pages})


def main(mode='auto', *, root=Path('.'), recording=RECORDING, web=WEB_SCENARIO, inject=None, check=live_ready):
    """`inject` / `check` exist for tests (fake transports); the CLI never passes them."""
    root, recording = Path(root), Path(recording)
    from pipeline.agent_loop import http_post
    from pipeline.engine import execute
    from pipeline.__main__ import load_replay_pages
    missing = check()
    if mode == 'auto':
        mode = 'live' if not missing else 'replay' if recording.is_file() else None
    if mode is None or (mode == 'live' and missing):
        say('无法实况运行，缺少：\n  - ' + '\n  - '.join(missing))
        say(f'也没有回放录制（{recording}）。配好上面几项后再运行一次，成功后会自动录制。')
        return 2
    if mode == 'replay' and not recording.is_file():
        say(f'没有回放录制：{recording}。请先实况运行一次。'); return 2
    label = '实况' if mode == 'live' else '回放'

    banner(f'演示：{CASE["request"]}　模式：{label}')
    say('产品：洞庭山·新鲜活泉（真实产品资料）；目标渠道：茶馆 / 写字楼 / 健身房')

    banner('① 对照：规则版（不调用模型、不补采）')
    _, base = execute(build_inputs(False), root=root)
    say(f"结果：{base['status']}，停在 {base.get('stopped_at') or '—'}；" +
        ('L2 没有任何目标渠道商户可选——缓存里只有餐馆。' if base.get('stopped_at') == 'L2' else '规则版已跑通。'))

    banner(f'② Agent 终端发现（{label}）')
    if mode == 'live':
        real = (inject or {}).get('llm_post') or http_post(); log = []
        def post(payload):
            reply = real(payload)
            log.append({'choices': [{'message': reply['choices'][0]['message']}]})
            return reply
        post.model = getattr(real, 'model', 'unknown')
        extra = {**(inject or {}), 'llm_post': post}
    else:
        data = json.loads(recording.read_text(encoding='utf-8'))
        post = Replayer(data['model_replies'], data['model'])
        extra = {'llm_post': post, 'amap_replay': load_replay_pages(data['amap_pages'])}
        say(f"回放录制：{data['recorded_at']}，模型 {data['model']}，run {data['run_id']}")
    directory, state = execute(build_inputs(True, **extra), root=root)
    say(f"\n结果：{state['status']}，完成关卡 {' → '.join(state['completed_stages'])}")
    if state['status'] != 'completed':
        say(f"停在 {state.get('stopped_at')}：{json.dumps(state.get('violations'), ensure_ascii=False)[:600]}")
        say(f'完整记录：{directory}/ledger.jsonl、{directory}/agent/steps.jsonl')
        return 1
    if mode == 'live':
        record(directory, log, post.model, recording)
        say(f'已录制回放：{recording}')

    banner('③ 结果：去哪里、进哪家、怎么说')
    text = summary(directory, label)
    reports = root / REPORTS
    reports.mkdir(parents=True, exist_ok=True)
    report = reports / f'demo-{Path(directory).name}.md'
    report.write_text(text, encoding='utf-8')
    say(text)
    try:
        if web:
            from pipeline.export import export_run
            export_run(directory, web)
            say(f'工作台数据：{web}（npm run dev 后打开 #/replay/{Path(web).name}）')
    except Exception as exc:  # the page is optional for the demo; never hide the main result
        say(f'工作台导出失败（{type(exc).__name__}），不影响上面的结果。')
    say(f'报告已保存：{report}\n运行目录：{directory}')
    return 0

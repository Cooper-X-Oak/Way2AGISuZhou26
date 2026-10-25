"""Make a finished live run the public example on the workbench page (web/data/agent-workbench.html).

    uv run python scripts/refresh_demo_page.py <task id from the page URL, after #>

The page is read per request, so no restart is needed; refresh the browser. The rules-only comparison
task already on the page is kept. Only real, completed runs are accepted.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline import view  # noqa: E402
from pipeline.io import read  # noqa: E402

PAGE = ROOT / 'web' / 'data' / 'agent-workbench.html'


def find(run_id):
    roots = [os.getenv('PUBLIC_DEMO_ROOT'), ROOT / '.work' / 'tmp' / 'public-demo-runtime', ROOT]
    for base in filter(None, roots):
        d = Path(base) / 'runs' / run_id
        if (d / 'status.json').is_file():
            return d
    hits = [p.parent for p in ROOT.glob(f'**/runs/{run_id}/status.json')]
    if hits:
        return hits[0]
    raise SystemExit(f'找不到任务 {run_id} 的运行目录（在 .work/tmp/public-demo-runtime/runs 和 runs 下都没有）')


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    run_id = sys.argv[1].strip().lstrip('#').removeprefix('task/')
    directory = find(run_id)
    status = read(directory / 'status.json')
    if status.get('status') != 'completed':
        raise SystemExit(f"任务还没有完成（当前：{status.get('status')}，停在 {status.get('stopped_at')}），等它跑完再执行")
    html = PAGE.read_text(encoding='utf-8')
    start = html.index('const DATA=') + len('const DATA=')
    old = json.JSONDecoder().raw_decode(html[start:])[0]['tasks']
    keep = [t for t in old if not t.get('steps')]  # the rules-only comparison has no agent steps
    new = view.build(directory, title=None, created_at=status.get('started_at'))
    PAGE.write_text(view.render([new] + keep), encoding='utf-8')
    agent = new.get('counts') or {}
    print(f"已更新：{PAGE.relative_to(ROOT)}")
    print(f"公共示例现在是 {run_id}：补采 {agent.get('added')} 家，首访 {len(new.get('terminals') or [])} 家，话术 {len(new.get('pitches') or [])} 个渠道")
    print('浏览器 Ctrl+Shift+R 刷新即可，不用重启服务。')


if __name__ == '__main__':
    main()

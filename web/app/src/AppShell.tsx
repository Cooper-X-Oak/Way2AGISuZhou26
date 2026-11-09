import {useEffect, useRef, useState} from 'react';
import type {ReactNode} from 'react';
import {Workbench} from './App';
import {ActivityRail} from './components/ActivityRail';
import {activityTokens, parseActivity} from './data/activity';
import type {ActivityEvent} from './data/activity';
import {loadScenario, readData} from './data/load';
import type {Index, Scenario, Summary} from './data/types';

const navigation = [['/', '首页'], ['/runs', '运行'], ['/profiles', '产品档案'], ['/areas', '商圈数据'], ['/rules', '规则库'], ['/tasks', '任务'], ['/settings', '设置']];
const areaNames: Record<string, string> = {guanqian: '观前街', shishan: '狮山', ligongdi: '李公堤'};

function useHash() {
  const [hash, setHash] = useState(location.hash.slice(1) || '/');
  useEffect(() => {const change = () => setHash(location.hash.slice(1) || '/'); window.addEventListener('hashchange', change); return () => window.removeEventListener('hashchange', change);}, []);
  return hash;
}

function HomePage({index}: {index: Index}) {
  const [summaries, setSummaries] = useState<Record<string, Summary>>({});
  const [error, setError] = useState('');
  const [area, setArea] = useState('guanqian');
  const [product, setProduct] = useState('');
  useEffect(() => {
    let active = true;
    Promise.all(index.scenarios.map(async s => [s.id, await readData<Summary>(s.summary)] as const))
      .then(entries => {if (active) {setSummaries(Object.fromEntries(entries)); setProduct(entries[0]?.[1].product_profile.id || '');}})
      .catch(e => {if (active) setError(e.message);});
    return () => {active = false;};
  }, [index]);
  const products = [...new Map(Object.values(summaries).map(s => [s.product_profile.id, s.product_profile])).values()];
  return <div className="home-page">
    <header className="page-intro"><span className="eyebrow">MARKET ENTRY</span><h1>新建市场进入任务</h1><p>从产品与商圈出发，查看每一关的判断、检查与交付。</p></header>
    <form className="panel task-composer" onSubmit={e => {e.preventDefault(); location.hash = `/runs/${area}`;}}>
      <label htmlFor="request">这次想进入怎样的市场？</label>
      <textarea id="request" name="request" rows={4} placeholder="描述目标商圈、目标业态与铺市计划…"/>
      <div className="composer-controls"><label>产品<select value={product} onChange={e => setProduct(e.target.value)}>{products.length ? products.map(p => <option key={p.id} value={p.id}>{p.product.name}</option>) : <option value="">读取产品中…</option>}</select></label><label>商圈<select value={area} onChange={e => setArea(e.target.value)}>{index.scenarios.filter(s => !s.demo_replay).map(s => <option value={s.id} key={s.id}>{areaNames[s.area_id] || s.area_id}</option>)}</select></label><button type="submit" className="primary-button">发起 →</button></div>
      <p className="sub">当前为演示入口，发起后打开所选商圈的已有示例运行。</p>
    </form>
    <section className="recent-runs"><div className="section-heading"><h2>最近运行</h2><a href="#/runs">全部运行 →</a></div>
      {error && <p role="alert">{error}</p>}
      <div className="run-cards">{index.scenarios.map(s => {const summary = summaries[s.id]; return <a href={`#/runs/${s.id}`} className="panel run-card" key={s.id}><div><h3>{areaNames[s.area_id] || s.area_id}</h3><span className="chip">{summary ? ({completed: '已完成', blocked: '被拦截', failed: '失败'}[summary.status.status] || summary.status.status) : '读取中'}</span></div><p>{summary?.product_profile.product.name || '—'}{s.demo_replay ? ` · ${s.demo_replay === 'backtrack' ? '回退演示' : '合规回放'}` : ''}</p><dl><div><dt>A / B 终端</dt><dd className="mono">{summary ? `${summary.counts.grades.A || 0} / ${summary.counts.grades.B || 0}` : '—'}</dd></div><div><dt>判断者</dt><dd>{summary?.judged_by || '—'}</dd></div></dl><span className="sub mono">{summary?.status.run_id || s.id}</span></a>;})}</div>
    </section>
  </div>;
}

function RunPage({id, index, mode}: {id: string; index: Index; mode: string}) {
  const [data, setData] = useState<Scenario>();
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    if (!index.scenarios.some(s => s.id === id)) {setError('未知运行'); return;}
    loadScenario(id).then(d => {if (active) setData(d);}).catch(e => {if (active) setError(e.message);});
    return () => {active = false;};
  }, [id, index]);
  if (error) return <section className="panel section" role="alert"><h1>无法读取运行</h1><p>{error}</p><a href="#/">返回首页</a></section>;
  return data ? <div className="run-workspace" data-ready={id}><Workbench data={data} index={index} mode={mode}/></div> : <p role="status">正在读取场景…</p>;
}

/** Hash route outlet; deliberately keeps the existing React-only dependency surface. */
export function Outlet({path, index, mode}: {path: string; index?: Index; mode: string}) {
  if (!index) return <p role="status">正在读取索引…</p>;
  if (path === '/') return <HomePage index={index}/>;
  const run = /^\/runs\/([^/]+)$/.exec(path);
  if (run) return <RunPage key={`${run[1]}/${mode}`} id={run[1]} index={index} mode={mode}/>;
  const page = navigation.find(([route]) => route === path);
  return <section className="panel section placeholder-page"><h1>{page?.[1] || '页面未找到'}</h1><p>{page ? '待接入' : '请从左侧导航选择页面。'}</p></section>;
}

export default function AppShell() {
  const hash = useHash();
  // Keep existing shared /run and /replay bookmarks usable.
  const [rawPath, query = ''] = hash.split('?');
  const path = rawPath.replace(/^\/(run|replay)\//, '/runs/').replace(/^\/data$/, '/areas');
  const mode = rawPath.startsWith('/replay/') || new URLSearchParams(query).get('mode') === 'replay' ? 'replay' : 'run';
  const [index, setIndex] = useState<Index>();
  const [indexError, setIndexError] = useState('');
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [eventError, setEventError] = useState('');
  const [loading, setLoading] = useState(true);
  const [menu, setMenu] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [readCount, setReadCount] = useState(0);
  const drawerButton = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    let active = true;
    readData<Index>('index.json').then(d => {if (active) setIndex(d);}).catch(e => {if (active) setIndexError(e.message);});
    fetch('./sample-events.jsonl').then(r => {if (!r.ok) throw Error(`样例事件读取失败 · HTTP ${r.status}`); return r.text();})
      .then(parseActivity).then(d => {if (active) setEvents(d);}).catch(e => {if (active) setEventError(e.message);}).finally(() => {if (active) setLoading(false);});
    return () => {active = false;};
  }, []);
  useEffect(() => {setMenu(false); setDrawer(false);}, [hash]);
  useEffect(() => {if (drawer) {dialog.current?.showModal(); setReadCount(events.length);} else if (dialog.current?.open) {dialog.current.close(); drawerButton.current?.focus();}}, [drawer, events.length]);
  useEffect(() => {const mq = matchMedia('(min-width: 1281px)'); const change = () => {if (mq.matches) setDrawer(false);}; mq.addEventListener('change', change); return () => mq.removeEventListener('change', change);}, []);
  const title = path.startsWith('/runs/') ? `运行 / ${areaNames[path.split('/')[2]] || path.split('/')[2]}` : navigation.find(([route]) => route === path)?.[1] || '页面未找到';
  const rail: ReactNode = <ActivityRail events={events} error={eventError} loading={loading}/>;
  return <div className="app-shell">
    <a className="skip-link" href="#main-content" onClick={e => {e.preventDefault(); document.getElementById('main-content')?.focus();}}>跳到主内容</a>
    <aside className="app-sidebar"><div className="sidebar-brand">铺市流水线<span>MARKET ENTRY</span></div><button className="mobile-menu-button" aria-expanded={menu} aria-controls="app-navigation" onClick={() => setMenu(!menu)}>菜单 {menu ? '−' : '+'}</button><nav id="app-navigation" className={menu ? 'navigation menu-open' : 'navigation'} aria-label="主导航">{navigation.map(([route, name], i) => <a className={route === '/settings' ? 'settings-link' : ''} href={`#${route}`} key={route} aria-current={(route === '/' ? path === '/' : path === route || path.startsWith(`${route}/`)) ? 'page' : undefined}><span className="mono">{String(i + 1).padStart(2, '0')}</span>{name}</a>)}</nav></aside>
    <div className="app-body"><header className="app-topbar"><nav aria-label="面包屑"><a href="#/">铺市</a><span> / </span><strong>{title}</strong></nav><div className="topbar-meta"><span className="chip brand">{mode === 'replay' ? '回放' : '快照'}模式</span><span className="mono">样例用量 {activityTokens(events).toLocaleString()} tokens</span><button ref={drawerButton} className="activity-toggle" aria-expanded={drawer} onClick={() => setDrawer(true)}>活动 {Math.max(0, events.length - readCount)}</button></div></header>
      <div className="app-columns"><main id="main-content" tabIndex={-1} className="app-outlet">{indexError ? <p role="alert">{indexError}</p> : <Outlet path={path} index={index} mode={mode}/>}</main><aside className="desktop-activity">{rail}</aside></div>
    </div>
    <dialog ref={dialog} className="activity-drawer" aria-label="Agent 活动流抽屉" onCancel={() => setDrawer(false)} onClose={() => setDrawer(false)} onClick={e => {if (e.target === e.currentTarget) setDrawer(false);}}><div className="drawer-inner"><button className="drawer-close" onClick={() => setDrawer(false)}>关闭活动 ×</button>{drawer && rail}</div></dialog>
  </div>;
}

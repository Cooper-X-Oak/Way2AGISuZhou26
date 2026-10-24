import type {ReactNode} from 'react';
export const Chip=({children,kind='plain'}:{children:ReactNode;kind?:string})=><span className={`chip ${kind}`}>{children}</span>;
export function Status({value}:{value?:string}){return <Chip kind={value==='passed'?'brand':value==='needs_review'?'review':['failed','blocked'].includes(value||'')?'stamp':'plain'}>{{passed:'通过',failed:'未通过',blocked:'阻断',needs_review:'待复核',not_run:'未运行'}[value||'']||value||'—'}</Chip>;}
export function Open({items}:{items:string[]}){return <div className="open"><b>待首访确认 / 待验证</b><ul>{[...new Set(items)].map(s=><li key={s}>{s}</li>)}{!items.length&&<li>—</li>}</ul></div>;}

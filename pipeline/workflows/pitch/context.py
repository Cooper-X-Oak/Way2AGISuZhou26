def active(c):return {r['channel_id']:r for r in c['l1']['items'] if r['priority'] in ('P1','P2')}


ANGLES={'补位':'先了解尚未满足的用水需求，讨论补充陈列。','正面':'围绕本品资料介绍特点，邀请核对样品与进货条件。','避让':'先做需求访谈并记录后续跟进条件，暂不推动替换现有陈列。'}


def angles(c):
    """Profile may override wording per strategy; default keeps the legacy water copy."""
    return {**ANGLES,**(c['profile'].get('material',{}).get('angles') or {})}


def targets(c):
    channels=active(c);zones={r['zone_id']:r for r in c['l2c']['items']}
    result={t['poi_id']:(channels[t['channel_id']],zones[t['zone_id']]['strategy']) for t in c['l3']['items'] if t['grade'] in ('A','B') and t['channel_id'] in channels}
    covered={ch['channel_id'] for ch,_ in result.values()}
    result.update({key:(ch,'补位') for key,ch in channels.items() if key not in covered})
    selection=c.get('pitch_targets')
    if selection is not None:
        unknown=set(selection)-set(result)
        if unknown:raise ValueError(f'Unknown or ineligible pitch targets: {sorted(unknown)}')
        if not selection:raise ValueError('Pitch target selection must not be empty')
        return {key:value for key,value in result.items() if key in selection}
    return result


def channel_for_target(target,c):
    if target in active(c):return active(c)[target]
    terminal=next((r for r in c['l3']['items'] if r['poi_id']==target),None)
    return active(c).get(terminal['channel_id']) if terminal else None

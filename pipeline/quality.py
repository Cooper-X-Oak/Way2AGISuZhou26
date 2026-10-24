"""Five visible layers. Low confidence queues review; it is not a truth verdict."""
from pipeline.contracts import validate, violation
from pipeline.common import scan

LAYERS = ('structure', 'references', 'business', 'judgment_quality', 'human_gate')


def label(context):
    return context['judge'].label.split(':', 1)[0]


def meta(context, evidence, confidence=0.65, opened=None):
    return dict(evidence=evidence, judged_by=label(context), confidence=confidence,
                open=opened or ['来源：params.yaml；离线规则/模型判断尚待实地核实。'])


def envelope(context, decision, items, **extra):
    return dict(decision=decision, items=items, **meta(context, ['profile:'+context['profile']['id']]), **extra)


def registry(context, stage):
    p = context['profile']
    ids = {'profile:'+p['id']}
    ids |= {f'{kind}:{r["id"]}' for kind, key in [('scene','scenes'), ('selling_point','selling_points'), ('profile_channel','channels')] for r in p[key]}
    ids |= {'poi:'+r['id'] for r in context['pois']}
    ids |= {'poi:'+r['id'] for key in ('partners','competitors') for r in context.get('market',{}).get(key,[])}
    def keys(value, prefix='params'):
        result = {prefix}
        if isinstance(value, dict):
            for k,v in value.items(): result |= keys(v, prefix+'.'+k)
        return result
    ids |= keys(context['params'])
    for upstream, key, field in [('L1','l1','channel_id'), ('L2','l2','zone_id'), ('L2C','l2c','zone_id'), ('L3','l3','poi_id'), ('L3P','l3p','partner_id'), ('L4','l4','target')]:
        if upstream == stage: break
        ids |= {upstream+':'+r[field] for r in context.get(key, {}).get('items', [])}
    return ids


def evaluate(stage, artifact, context, schema, references, business):
    structural = validate(schema, artifact)
    results = {k: [] for k in LAYERS}
    results['structure'] = structural
    review = []
    if not structural:
        allowed = registry(context, stage)
        rows = [artifact, *artifact.get('items', [])]
        for i,row in enumerate(rows):
            if 'evidence' in row:
                if not set(row['evidence']) <= allowed:
                    results['references'].append(violation('evidence', f'rows[{i}].evidence', '引用不在上游/档案/POI/params中'))
                expected = 'rules' if stage == 'PACK' and i == 0 else label(context)
                if row['judged_by'] != expected:
                    results['references'].append(violation('provenance', f'rows[{i}].judged_by', '判断来源不符'))
                if any('来源' not in s for s in row['open']):
                    results['references'].append(violation('open_source', f'rows[{i}].open', '待验证项必须注明来源'))
                if row['confidence'] < context['params']['confidence_threshold']:
                    review.append({'stage':stage, 'item':row.get('channel_id',row.get('zone_id',row.get('poi_id',row.get('target','decision')))),
                                   'confidence':row['confidence'], 'reason':'低于 params.confidence_threshold，待人工判断，不自动判错'})
        results['references'] += references(artifact, context)
        if not results['references']:
            results['business'] += business(artifact, context)
    gate = context.get('approvals', {}).get(stage)
    required = stage in ('L2','L3')
    if gate not in (None, 'human', 'auto (demo)'):
        results['human_gate'].append(violation('approval', stage, '无效的确认来源'))
    elif required and gate is None:
        results['human_gate'].append(violation('approval_pending', stage, '等待人工确认'))
    elif gate == 'auto (demo)' and not context.get('auto_approve'):
        results['human_gate'].append(violation('approval', stage, '自动确认必须显式开启 --auto-approve'))
    layers = []
    for name in LAYERS:
        errors = results[name]
        status = 'failed' if errors else 'passed'
        if structural and name in ('references','business','judgment_quality'): status = 'blocked'
        elif name == 'business' and results['references']: status = 'blocked'
        elif name == 'judgment_quality' and review: status = 'needs_review'
        layers.append({'layer':name, 'status':status, 'violations':[v.model_dump() for v in errors],
                       **({'required':required, 'approved_by':gate} if name == 'human_gate' else {})})
    errors = [v for values in results.values() for v in values]
    return {'stage':stage, 'passed':not errors, 'layers':layers, 'review_queue':review,
            'violations':[v.model_dump() for v in errors]}


def errors(report):
    from pipeline.contracts import Violation
    return [Violation(**v) for v in report['violations']]


def equal_ids(rows, key, expected):
    ids = [r[key] for r in rows]
    return [] if len(ids)==len(set(ids)) and set(ids)==set(expected) else [violation('item_set', 'items', '条目遗漏、重复或多出')]


def check_compliance(artifact, context):
    # Scan claims and assumptions, never recursively scan source rule vocabulary.
    return scan(artifact, context['profile'])

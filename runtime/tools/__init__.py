"""Read/compute tools. No stage.run or model decisions are invoked here.

Handles are local to one stage. JSON assembly copies tool results verbatim and
applies model-authored fields; it never invents priorities, types or prose.
"""
import inspect
from copy import deepcopy
from collections import Counter
from typing import Any, Annotated
from pydantic import ConfigDict, Field, create_model
from pipeline import l0, l2, l2c, l3, l3p, l4, pack
from pipeline.common import scan
from pipeline.geo import distance, grid_id


def tool(function):
    function.is_tool = True
    return function


def compact(value, limit=12):
    if isinstance(value, list):
        if len(value) <= limit:
            return [compact(v, limit) for v in value]
        return {'total': len(value), 'first': [compact(v, limit) for v in value[:limit]], 'truncated': len(value) > limit}
    if isinstance(value, dict):
        if len(value) > max(limit, 20):
            return {'total_keys': len(value), 'first': {k: compact(v, limit) for k, v in list(value.items())[:limit]}, 'truncated': True}
        return {k: compact(v, limit) for k, v in value.items()}
    return value


class Toolbox:
    def __init__(self, context, stage, skills, allowed):
        self.c, self.stage, self.skills = context, stage, skills
        self.values = {}
        self.seen = context.setdefault("seen_references", [])
        self.last_decision = None
        self.source_map = {}
        self.submission_errors = []
        self.used = set()
        self.loaded_skills = set()
        self.allowed = set(allowed)
        self.schemas = {}
        for name in self.names():
            fn = getattr(self, name)
            fields = {k: (v.annotation, ... if v.default is inspect.Parameter.empty else v.default)
                      for k, v in inspect.signature(fn).parameters.items()}
            self.schemas[name] = create_model(name + '_arguments', __config__=ConfigDict(extra='forbid', strict=True), **fields)

    @classmethod
    def names(cls):
        return {name for name, fn in vars(cls).items() if getattr(fn, 'is_tool', False)}

    def definitions(self):
        result = []
        for name in sorted(self.allowed):
            if name=='decision_data' and name in self.used:continue
            if name=='load_skill' and self.loaded_skills==set(self.skills):continue
            schema = self.schemas[name].model_json_schema()
            schema['properties']['intent'] = {'type': 'string'}
            schema.setdefault('required', []).append('intent')
            if name=='load_skill':schema['properties']['name']['enum']=sorted(set(self.skills)-self.loaded_skills)
            def strip(v):
                if isinstance(v,dict):return {k:strip(x) for k,x in v.items() if k not in ('title',)}
                if isinstance(v,list):return [strip(x) for x in v]
                return v
            if name in ('submit_artifact','save_decisions'):
                from runtime.decisions import SCHEMAS
                decision=SCHEMAS[self.stage].model_json_schema()
                defs=decision.get('$defs',{})
                def inline(v):
                    if isinstance(v,dict):
                        if '$ref' in v:return inline(defs[v['$ref'].split('/')[-1]])
                        return {k:inline(x) for k,x in v.items() if k!='$defs'}
                    if isinstance(v,list):return [inline(x) for x in v]
                    return v
                decision=inline(decision)
                if name=='submit_artifact':
                    schema['properties']['payload']=decision
                    if 'save_decisions' in self.allowed:
                        schema['properties']['payload']={'anyOf':[decision,{'type':'object','properties':{'use_saved':{'const':True}},'required':['use_saved'],'additionalProperties':False}]}
                else:
                    schema['properties']['items']=decision['properties']['items']
            schema=strip(schema)
            result.append({'type': 'function', 'function': {'name': name, 'description': getattr(self, name).__doc__, 'parameters': schema}})
        return result

    def call(self, name, arguments):
        if name not in self.allowed:
            raise ValueError('tool_not_allowed')
        args = self.schemas[name].model_validate({k: v for k, v in arguments.items() if k != 'intent'}).model_dump()
        result = getattr(self, name)(**args)
        self.used.add(name)
        if name=='load_skill':self.loaded_skills.add(args['name'])
        if name == 'submit_artifact':
            return result
        key = f'r{len(self.values) + 1}'
        self.values[key] = deepcopy(result)
        from runtime.limits import size, pages
        if size({'ref':key,'data':result}) <= 1500:
            displayed={'ref':key,'data':result}
        else:
            chunks=pages(result)
            displayed={'ref':key,'total_pages':len(chunks),'records':chunks[0],
                       'next_cursor':key+':1' if len(chunks)>1 else None}
        if size(displayed)>1500: raise ValueError('tool_result_limit')
        self.observe(displayed)
        return displayed

    def observe(self, displayed):
        # Only IDs actually present in the rendered page, never hidden storage.
        from pipeline.quality import registry
        import json
        text=json.dumps(displayed,ensure_ascii=False)
        candidates=registry(self.c,self.stage)
        candidates |= {'cell:'+z for z,_ in l2.ordered(self.c)} if 'l1' in self.c else set()
        candidates |= {'rule:'+r['id'] for r in self.c['profile'].get('compliance_rules',[])}
        if self.stage=='L4':
            from runtime.assembly import groups
            candidates |= {'group:'+k for k in groups(self.c)}
        for ref in candidates:
            raw=ref.split(':',1)[-1]
            if json.dumps(ref,ensure_ascii=False) in text or json.dumps(raw,ensure_ascii=False) in text:
                if ref not in self.seen:self.seen.append(ref)

    @tool
    def save_decisions(self, items: list[dict]) -> dict:
        """Save model-authored decision rows across pages. IDs replace earlier draft rows. Final submit payload={use_saved:true}."""
        key_field={'L1':'channel_id','L2':'zone_id','L2C':'zone_id','L3':'poi_id','L3P':'zone_id','L4':'group_id'}.get(self.stage)
        if key_field is None: raise ValueError('draft_not_supported')
        draft=self.c.setdefault('decision_drafts',{}).setdefault(self.stage,{})
        for row in items:
            draft[row[key_field]]=deepcopy(row)
        return {'saved_count':len(draft),'saved_ids':list(draft)}

    @tool
    def decision_data(self) -> Any:
        """Read the small decision worklist, computed constraints, IDs and sources. Read more pages only when necessary for the workshop decision."""
        from runtime.assembly import decision_data
        return decision_data(self.stage,self.c)

    def resolve(self, value, depth=0):
        if depth > 30:
            raise ValueError('reference_depth')
        if isinstance(value, list):
            return [self.resolve(v, depth + 1) for v in value]
        if not isinstance(value, dict):
            return value
        if '$ref' in value:
            if set(value) != {'$ref'}:
                raise ValueError('ref_has_extra_keys')
            parts = value['$ref'].split('/')
            result = self.values[parts[0]]
            for part in parts[1:]:
                result = result[int(part)] if isinstance(result, list) else result[part]
            return deepcopy(result)
        if '$merge' in value:
            result = {}
            for part in value['$merge']:
                result.update(self.resolve(part, depth + 1))
            return result
        if '$concat' in value:
            return [row for part in value['$concat'] for row in self.resolve(part, depth + 1)]
        if '$each' in value:
            rows = self.resolve(value['$each'], depth + 1)
            fields = self.resolve(value.get('set', {}), depth + 1)
            overrides = value.get('overrides', {})
            key = value.get('key')
            return [{**{k: v for k, v in row.items() if k not in value.get('omit', [])}, **deepcopy(fields),
                     **self.resolve(overrides.get(str(row.get(key)), {}), depth + 1)} for row in rows]
        return {k: self.resolve(v, depth + 1) for k, v in value.items()}

    @tool
    def read_result(self, ref: str = "", offset: Annotated[int, Field(ge=0)] = 0,
                    limit: Annotated[int, Field(ge=1, le=100)] = 40, cursor: str | None = None) -> dict:
        """Read a saved result or list page, including exact IDs; ref uses r1/path/0 syntax."""
        if cursor is not None:
            from runtime.limits import pages
            key, page=cursor.rsplit(':',1);page=int(page)
            chunks=pages(self.values[key])
            if not 0<=page<len(chunks):raise ValueError('invalid_page')
            return {'records':chunks[page], 'total_pages':len(chunks), 'next_cursor':key+':'+str(page+1) if page+1<len(chunks) else None}
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError('invalid_page')
        value = self.resolve({'$ref': ref})
        if isinstance(value, list):
            return {'total': len(value), 'offset': offset, 'rows': value[offset:offset + limit]}
        if isinstance(value, dict) and (len(value) > limit or offset):
            return {'total': len(value), 'offset': offset, 'entries': dict(list(value.items())[offset:offset + limit])}
        return value

    @tool
    def get_brief(self) -> dict:
        """Read request, supplied facts, provenance and code-calculated daily capacity; no inferred missing facts."""
        supplied, assumptions = l0.facts(self.c)
        facts = {key: supplied.get(key) for key in l0.REQUIRED}
        if facts['planned_visits_day'] is None and all(type(facts[k]) is int for k in ('delivery_people', 'visits_per_person_day')):
            facts['planned_visits_day'] = facts['delivery_people'] * facts['visits_per_person_day']
        facts.update(request=self.c['request'], assumptions=assumptions,
                     site_note=self.c['site'].get('site_note', self.c['site']['location_note']),
                     location_note=self.c['site']['location_note'])
        return self.c.get('brief', facts)

    @tool
    def get_artifact(self, stage: str) -> dict:
        """Read an accepted upstream artifact; unavailable/downstream IDs fail explicitly."""
        from pipeline.engine import NAMES
        if stage not in NAMES or list(NAMES).index(stage) >= list(NAMES).index(self.stage):
            raise ValueError('artifact_not_upstream')
        return self.c['brief' if stage == 'L0' else stage.lower()]

    @tool
    def get_profile(self, section: str = 'all') -> Any:
        """Read profile section, params, source_documents, or material_targets grouped by channel/strategy."""
        if section in ('params', 'source_documents'):
            return self.c[section]
        if section == 'material_targets':
            groups = {}
            for target, (channel, strategy) in l4.targets(self.c).items():
                key = channel['channel_id'] + ':' + strategy
                group = groups.setdefault(key, dict(channel=channel, strategy=strategy,
                                         required_angle=l4.ANGLES[strategy], targets=[]))
                group['targets'].append({'target': target})
            return groups
        return self.c['profile'] if section == 'all' else self.c['profile'][section]

    @tool
    def poi_query(self, filters: dict = {}, limit: Annotated[int, Field(ge=1, le=100)] = 20) -> dict:
        """Query cached POIs by ids, cells, category or name substring; no new classification or personal contacts."""
        if not set(filters) <= {'ids', 'cells', 'category', 'name'} or not 1 <= limit <= 100:
            raise ValueError('invalid_poi_filters')
        rows = []
        for p in self.c['pois']:
            if 'ids' in filters and p['id'] not in filters['ids']: continue
            if 'cells' in filters and grid_id(p, self.c['brief']) not in filters['cells']: continue
            if 'category' in filters and p['category'] != filters['category']: continue
            if 'name' in filters and filters['name'] not in p['name']: continue
            rows.append(self._poi(p))
        return {'total': len(rows), 'rows': rows[:limit]}

    @staticmethod
    def _poi(p):
        return {**{k: p.get(k) for k in ('id', 'name', 'category', 'lat', 'lon', 'source')},
                'type': p.get('tags', {}).get('type'), 'typecode': p.get('tags', {}).get('typecode')}

    @tool
    def poi_detail(self, poi_id: str) -> dict:
        """Read name and AMap type/typecode for one real merchant; never returns telephone/contact fields."""
        rows = self.c['pois'] + self.c.get('market', {}).get('partners', []) + self.c.get('market', {}).get('competitors', [])
        return self._poi(next(p for p in rows if p['id'] == poi_id))

    @tool
    def grid_stats(self, cells: list[str] | None = None, channels: list[str] | None = None) -> dict:
        """Compute fixed grid counts/rank/selection from upstream priorities; type and tier are left to the agent."""
        selected = set(l2.selection(self.c))
        rows = [dict(zone_id=z, counts={k: v for k, v in n.items() if channels is None or k in channels},
                     rank=i+1, selected=z in selected) for i, (z, n) in enumerate(l2.ordered(self.c))
                if cells is None or z in cells]
        return {'items': rows, 'source': self.c['params']['sources'], 'excluded_zones': self.c.get('excluded_zones', [])}

    @tool
    def competitor_scan(self, cells: list[str] | None = None) -> dict:
        """Count cached availability proxies and compute configured rank intensity; strategy is agent-authored."""
        values = l2c.counts(self.c)
        rows = [dict(zone_id=z, counts=n, intensity=l2c.intensity(sum(n.values()), self.c, values),
                     reason=l2c.reason(n)) for z, n in values.items() if cells is None or z in cells]
        # Numeric rank inputs allow the agent to apply the existing acceptance policy without arithmetic.
        p1 = {r['channel_id'] for r in self.c['l1']['items'] if r['priority'] == 'P1'}
        totals = {z['zone_id']: sum(v for k, v in z['counts'].items() if k in p1) for z in self.c['l2']['items']}
        selected = [totals[z['zone_id']] for z in self.c['l2']['items'] if z['selected']]
        for r in rows:
            total = totals[r['zone_id']]
            r['p1_count'] = total
            r['p1_rank_fraction'] = sum(v > total for v in selected) / len(selected) if selected else 1
        return {'items': rows, 'params': self.c['params']['competition'], 'open': self.c.get('market', {}).get('open', [])}

    @tool
    def partner_candidates(self, cells: list[str] | None = None, radius_m: int = 5000) -> dict:
        """Read cached candidate names and types within radius; returns no business classification."""
        if radius_m <= 0: raise ValueError('radius_positive')
        rows = [self._poi(p) for p in l3p.candidates(self.c).values()
                if distance(self.c['brief']['lat'], self.c['brief']['lon'], p['lat'], p['lon']) <= radius_m
                and (cells is None or grid_id(p, self.c['brief']) in cells)]
        return {'items': rows, 'source': self.c['params']['partners']}

    @tool
    def partner_coverage(self, partner_ids: list[str] | None = None, terminal_ids: list[str] | None = None,
                         radius_m: int | None = None, eligible_partner_ids: list[str] | None = None) -> dict:
        """Compute geometry; pass explicitly agent-selected eligible_partner_ids to compute greedy allocation, otherwise allocation is absent."""
        radius_m = radius_m if radius_m is not None else self.c['params']['partners']['coverage_radius_m']
        if radius_m <= 0: raise ValueError('radius_positive')
        partners, pois = l3p.candidates(self.c), {p['id']: p for p in self.c['pois']}
        partner_ids = list(partners) if partner_ids is None else partner_ids
        terminal_ids = [t['poi_id'] for t in self.c['l3']['items'] if t['grade'] in ('A', 'B')] if terminal_ids is None else terminal_ids
        if not set(terminal_ids) <= {t['poi_id'] for t in self.c['l3']['items']}:
            raise ValueError('unknown_terminal')
        selection = None
        if eligible_partner_ids is not None:
            if not set(eligible_partner_ids) <= partners.keys(): raise ValueError('unknown_partner')
            c = {**self.c, 'params': deepcopy(self.c['params']),
                 'l3': {**self.c['l3'], 'items': [t for t in self.c['l3']['items'] if t['poi_id'] in terminal_ids]}}
            c['params']['partners']['coverage_radius_m'] = radius_m
            selection = l3p.recommended(c, eligible_ids=set(eligible_partner_ids))
        rows = []
        for pid in partner_ids:
            p = partners[pid]
            ids = sorted(tid for tid in terminal_ids if distance(p['lat'], p['lon'], pois[tid]['lat'], pois[tid]['lon']) <= radius_m)
            row = dict(partner_id=pid, covered_terminal_ids=ids, coverage_count=len(ids))
            if selection is not None:
                zones = sorted(z for z, ids_ in selection.items() if pid in ids_)
                row.update(recommended_zone_ids=zones, recommendation='优先' if zones else '暂缓')
            rows.append(row)
        return {'items': rows, 'source': self.c['params']['partners']['source']}

    @tool
    def score_terminals(self, selection: list[str] | None = None) -> dict:
        """Compute all candidate scores/grades from params before optional selection; include mandatory evidence-only reason text."""
        values = l3.ranked(self.c)
        if selection is not None and not set(selection) <= values.keys(): raise ValueError('unknown_terminal')
        rows = [dict(poi_id=p['id'], channel_id=ch['channel_id'], zone_id=z['zone_id'],
                     **values[p['id']], reason=l3.reason(p, ch, z, self.c))
                for p, ch, z in l3.candidates(self.c) if selection is None or p['id'] in selection]
        return {'items': rows, 'grades': dict(Counter(r['grade'] for r in rows)), 'source': self.c['params']['scoring']}

    @tool
    def route_plan(self, terminal_ids: list[str] | None = None, people: int | None = None, weeks: int | None = None) -> dict:
        """Compute visit days/backlog using accepted capacity; overrides only calculate a preview, not a new brief."""
        c = {**self.c, 'brief': deepcopy(self.c['brief'])}
        if people is not None:
            if people <= 0: raise ValueError('people_positive')
            c['brief']['delivery_people'] = people
        if weeks is not None:
            if weeks <= 0: raise ValueError('weeks_positive')
            c['brief']['weeks'] = weeks
        if terminal_ids is not None:
            if not set(terminal_ids) <= {r['poi_id'] for r in c['l3']['items']}: raise ValueError('unknown_terminal')
            c['l3'] = {**c['l3'], 'items': [r for r in c['l3']['items'] if r['poi_id'] in terminal_ids]}
        days = pack.schedule(c)
        return {'days': days, 'backlog': pack.backlog(c, days), 'planning_note': pack.NOTE}

    @tool
    def pack_components(self) -> dict:
        """Copy accepted artifacts and compute source anchors/supply links for PACK; no decision prose is generated."""
        c = self.c
        return dict(items=c['l3']['items'], channels=c['l1'], zones=c['l2'], materials=c['l4'],
                    competition=c['l2c'], partners=c['l3p'], trace=pack.trace(c), coverage=c['coverage'],
                    review_queue=[r for report in c['checks'].values() for r in report['review_queue']],
                    supply_assignments=l3p.assignments(c), recruitment_gaps=l3p.gaps(c))

    @tool
    def compliance_scan(self, text: Any) -> dict:
        """Scan a string or JSON (including result refs); return exact existing compliance hits."""
        hits = scan(self.resolve(text), self.c['profile'])
        return {'passed': not hits, 'hits': [v.model_dump() for v in hits]}

    @tool
    def load_skill(self, name: str) -> dict:
        """Load one allowed capability's full instructions on demand."""
        if name not in self.skills:
            raise ValueError('skill_not_allowed')
        return {'name': name, 'text': self.skills[name]['path'].read_text(encoding='utf-8')}

    @tool
    def submit_artifact(self, payload: dict) -> dict:
        """Submit only the workshop decision_schema. Harness assembles and runs all five check layers."""
        from runtime.assembly import assemble
        from runtime.decisions import SCHEMAS
        from pydantic import ValidationError
        self.last_decision=deepcopy(payload)
        self.submission_errors=[]
        try:
            if payload=={'use_saved':True}:
                payload={'items':list(self.c.get('decision_drafts',{}).get(self.stage,{}).values())}
                self.last_decision=deepcopy(payload)
            d=SCHEMAS[self.stage].model_validate(payload).model_dump()
            # Typed reference fields and explicit citations must have appeared in this session.
            def check(v,path=''):
                if isinstance(v,list):
                    for i,x in enumerate(v):check(x,f'{path}[{i}]')
                elif isinstance(v,dict):
                    for k,x in v.items():
                        prefixes={'poi_id':'poi:','partner_ids':'poi:','zone_id':'cell:',
                                  'channel_id':'profile_channel:','selling_point_ids':'selling_point:','group_id':'group:','profile_id':'profile:'}
                        refs=x if k in ('refs','partner_ids','selling_point_ids') else [x]
                        if k=='refs' or k in prefixes:
                            for ref in refs:
                                if ref is None:continue
                                full=ref if k=='refs' else prefixes[k]+ref
                                if full not in self.seen:
                                    self.submission_errors.append(dict(code='unseen_reference',path=path+'.'+k,message=full))
                        check(x,path+'.'+k)
            check(d)
            if self.submission_errors:return {}
            artifact,self.source_map,provenance=assemble(self.stage,d,self.c)
            self.c.setdefault('assembly_provenance',{})[self.stage]=provenance
            return artifact
        except ValidationError as exc:
            self.submission_errors=[dict(code='decision_schema',path='.'.join(map(str,e['loc'])),message=e['msg']) for e in exc.errors(include_input=False)]
        except (ValueError,KeyError) as exc:
            self.submission_errors=[dict(code=str(exc) if isinstance(exc,ValueError) else 'decision_reference',path='items',message='Check decision IDs, required worklist and unique entries.')]
        return {}

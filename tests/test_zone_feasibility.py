"""Regression: globally graded candidates must support every selected zone."""
from copy import deepcopy

from pipeline import l2, l3
from pipeline.profiles import parameters


def context(monkeypatch):
    c={'params':parameters(),'brief':{'lat':31.0,'lon':120.0,'radius_m':3000}}
    monkeypatch.setattr(l2,'ordered',lambda c:[(str(i),{'office':10}) for i in range(4)])
    def candidates(c):
        return [(dict(id=f'{z["zone_id"]}:{i}',name='synthetic office',category='office',lat=31.0,lon=120.0),
                 {'priority':'P1','channel_id':'office'},z)
                for z in c['l2']['items'] if z['selected'] for i in range(10)]
    monkeypatch.setattr(l3,'candidates',candidates)
    return c


def test_selects_feasible_prefix_without_changing_thresholds(monkeypatch):
    c=context(monkeypatch); original=deepcopy(c['params'])
    assert l2.selection(c)==['0']  # rank bonus makes every later zone C
    assert c['params']==original
    c['excluded_zones']=['0']
    assert l2.selection(c)==['1']


def test_impossible_threshold_keeps_failure_path(monkeypatch):
    c=context(monkeypatch)
    c['params']['min_terminals_per_zone']=11
    assert l2.selection(c)==['0','1','2','3']
    assert c['params']['min_terminals_per_zone']==11

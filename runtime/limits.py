"""Hard conservative upper bounds, independent of a vendor tokenizer.

UTF-8 bytes upper-bound byte-token vocabularies. Report this as a bound, never
as measured usage. Small tools and history compaction keep even this bound low.
"""
import json


def size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def pages(value, path=''):
    """Lossless leaf/row chunks. Long strings retain ordered slice offsets."""
    chunks=[]
    def walk(v, p):
        if size({'path':p,'value':v}) <= 1100:
            chunks.append({'path':p,'value':v})
        elif isinstance(v,dict):
            for k,x in v.items(): walk(x,p+'/'+str(k))
        elif isinstance(v,list):
            for i,x in enumerate(v): walk(x,p+'/'+str(i))
        elif isinstance(v,str):
            for offset in range(0,len(v),200):
                chunks.append({'path':p,'string_offset':offset,'value':v[offset:offset+200]})
        else:
            raise ValueError('oversize_tool_value')
    walk(value,path)
    result=[]; batch=[]
    for row in chunks:
        if batch and size(batch+[row])>1200:
            result.append(batch);batch=[]
        batch.append(row)
    if batch:result.append(batch)
    return result

"""Frozen key lookup and source-defined version selection; no model or price matching."""
import re
import unicodedata


def normalize(description: str) -> str:
    text=unicodedata.normalize('NFKC',description).lower()
    text=re.sub(r'\s*/(?:ng|sa|rm|cw|ph)-\d+\s*$','',text)
    return ' '.join(re.findall(r'[a-z0-9]+',text))


def mapping_index(mappings):return {r['key']:r for r in mappings['records']}


def resolve(description,index,all_ids):
    row=index.get(normalize(description))
    if row and row['state']=='accepted':return row['service_id'],set(row['candidates']),row['grade']
    possible=set(row['candidates']) if row and row['candidates'] else set(all_ids)
    return None,possible,'unresolved'


def rate_version(service,service_date):
    versions=[v for v in service['versions'] if v['start']<=service_date<=v['end']]
    if not versions:return None
    priority=max(v['priority'] for v in versions)
    best=[v for v in versions if v['priority']==priority]
    if len(best)!=1:raise ValueError('Conflicting applicable versions')
    return best[0]

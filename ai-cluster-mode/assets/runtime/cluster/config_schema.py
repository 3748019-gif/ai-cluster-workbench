"""Reject misconfigured deterministic checks before any external work."""
def check_validators(specs,source_ids=None):
    if not isinstance(specs,list) or not specs:raise ValueError('CONFIG_ERROR: validators must be a nonempty list')
    schemas={
      'text':{'required','forbidden','max_characters','heading_count','dates'},
      'json':{'required_fields','equals'},
      'table':{'source_artifact','key','columns','rows','match_columns','sums','same_keys','tolerance'},
      'roster':{'source_artifact','approved','excluded','deadline','max_notice_chars','notice_required'},
      'research':{'require_claims','require_sources'},
    }
    list_fields={'required','forbidden','dates','required_fields','columns','match_columns','notice_required'}
    for s in specs:
        if not isinstance(s,dict) or s.get('kind') not in schemas:raise ValueError('CONFIG_ERROR: unknown validator kind')
        k=s['kind'];unknown=set(s)-schemas[k]-{'kind'}
        if unknown:raise ValueError('CONFIG_ERROR: unknown '+k+' keys: '+','.join(sorted(unknown)))
        if k=='json' and not s.get('required_fields'):raise ValueError('CONFIG_ERROR: JSON requires required_fields, not required')
        if k=='text' and not (any(s.get(x) for x in ('required','forbidden','dates')) or any(x in s for x in ('max_characters','heading_count'))):raise ValueError('CONFIG_ERROR: empty text check')
        if k=='table' and not (s.get('columns') or s.get('sums') or s.get('key') or 'rows' in s):raise ValueError('CONFIG_ERROR: empty table check')
        if k in ('roster',) and not s.get('source_artifact'):raise ValueError('CONFIG_ERROR: missing source_artifact')
        for key,v in s.items():
            if key in list_fields and (not isinstance(v,list) or not all(isinstance(x,str) and x for x in v)):
                raise ValueError('CONFIG_ERROR: '+key+' requires strings')
            if key in ('rows','max_characters','heading_count','max_notice_chars') and (type(v) is not int or v<0):
                raise ValueError('CONFIG_ERROR: invalid '+key)
            if key in ('equals','sums') and not isinstance(v,dict):raise ValueError('CONFIG_ERROR: '+key+' needs object')
            if key in ('same_keys','require_claims','require_sources') and type(v) is not bool:raise ValueError('CONFIG_ERROR: '+key+' needs bool')
        if source_ids is not None and s.get('source_artifact') and s['source_artifact'] not in source_ids:
            raise ValueError('CONFIG_ERROR: unknown source_artifact')
    return specs

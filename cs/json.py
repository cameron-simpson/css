from cStringIO import StringIO
import cs.hier

_JSON_HIER_OPTS = { 'dictSep': ':', 'bareWords': False, 'nullToken': 'null' }

def json(obj,i=None):
  return cs.hier.h2a(obj,i,_JSON_HIER_OPTS)

def json2f(fp,obj):
  cs.hier.h2f(fp,obj,_JSON_HIER_OPTS)

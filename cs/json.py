from cStringIO import StringIO
import cs.hier

def json(obj,i=None,seen=None):
  return cs.hier.h2a(obj,i=i,seen=seen,dictSep=':',bareWords=False)

def json2f(fp,obj,seen=None):
  cs.hier.h2f(fp,obj,seen=seen,dictSep=':',bareWords=False)

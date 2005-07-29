import re
import string
from cStringIO import StringIO

sh_unsafe_re=re.compile(r'[^\-a-z0-9.]');

def quote(args):
  quoted=[]
  for arg in args:
    if len(arg) == 0:
      qarg="''"
    else:
      if sh_unsafe_re.match(arg): qarg=quotestr(arg)
      else: qarg=arg

    quoted.append(qarg)

  return quoted

def quotestr(s):
  qs=StringIO()
  qs.write("'")

  for c in s:
    if c == "'":
      qs.write("'\''")
    else:
      qs.write(c)

  qs.write("'")
  return qs.getvalue()

def vpopen(argv,mode='r',bufsize=-1):
  return os.popen(string.join(quote(argv)),mode,bufsize)

import re
from cStringIO import StringIO

shunsafe_re=re.compile(r'[^\-a-z0-9.]');

def quote(*args):
  quoted=()
  for arg in *args:
    if !arg.length:
      qarg="''"
    else:
      if unsafe_re.match(arg): qarg=quotestr(arg)
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
  return qs

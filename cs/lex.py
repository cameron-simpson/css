import string
import re

int_re=re.compile(r'^\d+$')

def isint(s):
  m=int_re.match(s)
  return m is not None and m.group() == s

def skipwhite(s,start=0):
  """ location of next nonwhite in string """
  while start < len(s) and s[start] in string.whitespace:
    start+=1
  return start

def lastlinelen(s):
  """ length of text after last newline in string
      initially used by cs.hier to compute effective text width
  """
  return len(s)-string.rfind(s,'\n')-1

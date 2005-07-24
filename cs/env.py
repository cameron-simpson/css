import os
import string

def dflt(envvar,dfltval,doenvsub=0):
  env=os.environ
  if envvar in env:
    return env[envvar]
  if doenvsub:
    return envsub(dfltval)
  return dfltval

def envsub(s):
  next=s.find('$')
  if next < 0:
    return s

  expanded=''
  while next >= 0:
    expanded=expanded+s[:next]
    s=s[next+1:]
    endvar=0
    while ( endvar < len(s)
	and ( s[endvar] == '_'
	   or s[endvar] in string.ascii_letters
	   or (endvar > 0 and s[endvar] in string.digits)
	    )):
      endvar=endvar+1

    if endvar == 0:
      expanded=expanded+'$'
    else:
      expanded=expanded+dflt(s[:endvar],'')

    s=s[endvar:]
    next=s.find('$')

  if len(s) > 0:
    expanded=expanded+s

  return expanded

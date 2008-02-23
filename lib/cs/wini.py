import sys
import re

# regexp to recognise a clause opening line
clausehdr_re=re.compile(r'^\[\s*([^\s\]]+)\s*\]')

# regexp to recognise an assignment
assign_re   =re.compile(r'^\s*([^\s=]+)\s*=\s*(.*)')

# regexp to recognise non-negative integers
int_re      =re.compile(r'^(0|[1-9][0-9]*)$')

# read a win.ini file, return a dictionary of dictionaries
def load(fp,parseInts=False):
  contents={}   # empty clause dictionary
  clause=None   # no current clause

  for line in fp:

    # trim newline
    llen=len(line)
    if llen > 0 and line[llen-1] == '\n':
      line=line[:llen-1]

    # skip blank lines and comments
    line=string.strip(line)
    if len(line) == 0: continue # blank line
    if line[0] == '#': continue # comment

    # look for [foo]
    match=clausehdr_re.match(line)
    if match is not None:
      clausename=match.group(1)
      if clausename not in contents: contents[clausename]={}
      clause=contents[clausename]
      continue

    # not inside a clause? complain
    if clause is None:
      print >>sys.stderr, `fp`+": unexpected data outside of [clause]: "+line
      continue

    # look for var=value
    match=assign_re.match(line)
    if match is not None:
      value=match.group(2)
      if parseInts:
        valmatch=int_re.match(value)
        if valmatch is not None: value=int(value)
      clause[match.group(1)]=value
      continue

    print >>sys.stderr, `fp`+": non-assignment inside clause \""+clausename+"\": "+line

  return contents

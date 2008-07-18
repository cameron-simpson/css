import string
import sys
import re

# regexp to recognise a clause opening line
clausehdr_re=re.compile(r'^\[\s*([^\s\]]+)\s*\]')

# regexp to recognise an assignment
assign_re   =re.compile(r'^\s*([^\s=]+)\s*=\s*(.*)')

# regexp to recognise integers
int_re      =re.compile(r'^(0|-?[1-9][0-9]*)$')

# read a win.ini file, return a dictionary of dictionaries
def load(fp,parseInts=False):
  contents={}   # empty clause dictionary
  clause=None   # no current clause

  lineno=0
  for line in fp:
    lineno+=1

    # trim newline
    assert line[-1] == '\n', \
           "%s, line %d: missing newline" % (fp, lineno)
    line=line[:-1]

    # skip blank lines and comments
    line=string.strip(line)
    if len(line) == 0: continue # blank line
    if line[0] == '#': continue # comment

    # look for [foo]
    match=clausehdr_re.match(line)
    if match is not None:
      clausename=match.group(1)
      clause=contents.setdefault(clausename,{})
      continue

    assert clause is not None, \
        "%s, line %d: unexpected data outside of [clause]: %s" \
          % (fp, lineno, line)

    # look for var=value
    match=assign_re.match(line)
    assert match is not None, \
           "%s, line %d: non-assignment inside clause [%s]: %s" \
             % (fp, lineno, clausename, line)

    value=match.group(2)
    if parseInts:
      valmatch=int_re.match(value)
      if valmatch is not None:
        value=int(value)
    clause[match.group(1)]=value

  return contents

#!/usr/bin/python
#
# Handler for rulesets in the format of cats2procmailrc(1cs).
#       - Cameron Simpson <cs@zip.com.au> 22may2011
#

from email.utils import getaddresses
import email.parser
import os
import re
import sys
import mailbox
if sys.hexversion < 0x02060000: from sets import Set as set
from cs.logutils import Pfx, setup_logging, info, warn, error
from cs.mailutils import Maildir, read_message

def main(argv, stdin=None):
  if stdin is None:
    stdin = sys.stdin
  argv = list(argv)
  cmd = argv.pop(0)
  setup_logging(cmd)
  usage = 'Usage: %s rulefile < message' % (cmd,)
  badopts = False

  if len(argv) == 0:
    warn("missing rulefile")
    badopts = True
  else:
    rulefile = argv.pop(0)
    if not os.path.isfile(rulefile):
      warn("rulefile: not a file: %s", rulefile)
      badopts = True
    if len(argv) > 0:
      warn("extra arguments after rulefile: %s" % (' '.join(argv),))
      badopts = True

  if badopts:
    print >>sys.stderr, usage
    return 2

  rules = Rules()
  with open(rulefile) as rfp:
    rules.load(rfp)

  M = email.parser.Parser().parse(stdin)
  filed = list(rules.file_message(M))
  return 0 if filed else 1

F_HALT = 0x01   # halt rule processing if this rule matches
F_ALERT = 0x02  # issue an alert if this rule matches

re_QSTR = re.compile(r'"([^"\\]|\\.)*"')
re_UNQSTR = re.compile(r'[^,\s]+')
re_HEADERLIST = re.compile(r'([a-z][\-a-z0-9]*(,[a-z][\-a-z0-9]*)*):', re.I)
re_ASSIGN = re.compile(r'[a-z]\w+=', re.I)

def get_qstr(s):
  ''' Extract a quoted string from the start of `s`.
      Return:
        qs, etc
      where `qs` is the quoted string after replacing slosh-char
      with char and `etc` is the text after the quoted string.
  '''
  m = re_QSTR.match(s)
  if not m:
    raise ValueError, "no quoted string here: "+s
  qs, etc = m.group()[1:-1], s[m.end():]
  pos = 0
  spos = qs.find('\\', pos)
  while spos >= 0:
    qs = qs[:spos] + qs[spos+1:]
    pos = spos + 1
  return qs, etc

def message_addresses(M, hdrs):
  ''' Yield (realname, address) pairs from all the named headers.
  '''
  for hdr in hdrs:
    for realname, address in getaddresses(M.get_all(hdr, ())):
      yield realname, address

class _Condition(object):
  pass

class Condition_Regexp(_Condition):

  def __init__(self, headernames, atstart, regexp):
    self.headernames = headernames
    self.atstart = atstart
    self.regexp = re.compile(regexp)

  def __str__(self):
    return "<%s:atstart=%s:%s>" \
           % ( self.headernames,
               self.atstart,
               self.regexp
             )
  
  def match(self, M):
    for hdr in self.headernames:
      for value in M.get_all(hdr, ()):
        if self.atstart:
          if self.regexp.match(value):
            return True
        else:
          if self.regexp.search(value):
            return True
    return False

class Condition_AddressMatch(_Condition):

  def __init__(self, headernames, addrkeys):
    self.headernames = headernames
    self.addrkeys = [ k for k in addrkeys if len(k) > 0 ]

  def __str__(self):
    return "<%s:%s>" \
           % ( self.headernames,
               self.addrkeys
             )

  def match(self, M):
    for realname, address in message_addresses(M, self.headernames):
      for key in self.addrkeys:
        if address.lower() == key.lower():
          return True
    return False

class Rule(object):

  def __init__(self):
    self.conditions = []
    self.actions = []
    self.flags = 0

  def __str__(self):
    return "<%s:flags=%s:...>" \
           % ([str(C) for C in self.conditions], self.flags)

  def match(self, M):
    for C in self.conditions:
      if not C.match(M):
        return False
    return True

def parserules(fp):
  ''' Read rules from `fp`, yield Rules.
  '''
  lineno = 0
  R = None
  for line in fp:
    lineno += 1
    with Pfx("%s:%d" % (fp.name, lineno)):
      if not line.endswith('\n'):
        raise ValueError("short line at EOF")

      # skip comments
      if line.startswith('#'):
        continue

      # remove newline and trailing whitespace
      line = line.rstrip()

      # skip blank lines
      if not line:
        continue

      if line[0].isspace():
        # continuation - advance to condition
        line = line.lstrip()
      else:
        # new rule
        # yield old rule if in progress
        if R:
          yield R
        R = None

        m = re_ASSIGN.match(line)
        if m:
          yield Rule_Assign(line)
          continue

        # new rule
        R = Rule()

        if line.startswith('+'):
          R.flags &= ~F_HALT
          line = line[1:]
        elif line.startswith('='):
          R.flags |= F_HALT
          line = line[1:]
        if line.startswith('!'):
          R.flags |= F_ALERT
          line = line[1:]

        # gather targets
        while len(line) and not line[0].isspace():
          if line.startswith('"'):
            target, line = get_qstr(line)
          else:
            m = re_UNQSTR.match(line)
            if m:
              target = m.group()
              line = line[m.end():]
            else:
              error("parse failure at: "+line)
              raise ValueError, "syntax error"
          if target.startswith('|'):
            R.actions.append( ('PIPE', target[1:]) )
          elif '@' in target:
            R.actions.append( ('MAIL', target) )
          else:
            R.actions.append( ('SAVE', target) )
          if line.startswith(','):
            line = line[1:]

        # gather tag
        line = line.lstrip()
        if len(line) == 0:
          raise ValueError, "missing tag"
        if line.startswith('"'):
          tag, line = get_qstr(line)
          # advance to condition
          line = line.lstrip()
        else:
          tag, line = line.split(None, 1)
        R.tag = tag

      # condition
      if len(line) == 0:
        raise ValueError, "missing condition"

      # . always matches - don't bother storing it
      if line == '.':
        continue

      # leading hdr1,hdr2,...:
      m = re_HEADERLIST.match(line)
      if m:
        headernames = [ H.lower() for H in m.group(1).split(',') if H ]
        line = line[m.end():]
      else:
        headernames = ('to', 'cc', 'bcc')

      if line.startswith('/'):
        regexp = line[1:]
        if regexp.startswith('^'):
          atstart = True
          regexp = regexp[1:]
        else:
          atstart = False
        C = Condition_Regexp(headernames, atstart, regexp)
      else:
        addrkeys = [ w.strip() for w in line.split(',') ]
        C = Condition_AddressMatch(headernames, addrkeys)
      R.conditions.append(C)

  if R is not None:
    yield R

class Rules(list):
  ''' Simple subclass of list storing rules, with ethod to load
      rules and file a message using the rules.
  '''

  def __init__(self):
    list.__init__(self)
    self.vars = {}

  def load(self, fp):
    self.extend(list(parserules(fp)))

  def file_message(M, rules):
    ''' File message `M` according to the `rules`.
        Yield (R, filed) for each rule that matches; `filed` is the
        filing locations from each fired action.
    '''
    applied = []
    for R in self:
      if R.match(M):
        filed = []
        for A in R.actions:
          filed.extend(A.act_on(M))
        yield R, filed
        if R.flags & F_HALT:
          break

if __name__ == '__main__':
  sys.exit(main(sys.argv))

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
from cs.env import envsub
from cs.fileutils import abspath_from_file
from cs.lex import get_white, get_nonwhite
from cs.logutils import Pfx, setup_logging, debug, info, warning, error
from cs.mailutils import Maildir, read_message
from cs.app.maildb import MailDB

def main(argv, stdin=None):
  if stdin is None:
    stdin = sys.stdin
  argv = list(argv)
  cmd = argv.pop(0)
  setup_logging(cmd)
  usage = 'Usage: %s rulefile maildb < message' % (cmd,)
  mdburl = None
  badopts = False

  if len(argv) == 0:
    warning("missing rulefile")
    badopts = True
  else:
    rulefile = argv.pop(0)
    if not os.path.isfile(rulefile):
      warning("rulefile: not a file: %s", rulefile)
      badopts = True
  if len(argv) > 0:
    mdburl = argv.pop(0)
  if len(argv) > 0:
    warning("extra arguments: %s" % (' '.join(argv),))
    badopts = True

  if badopts:
    print >>sys.stderr, usage
    return 2

  rules = Rules()
  rules.load(rulefile)

  if mdburl is None:
    mdburl = os.environ['MAILDB']
  MDB = MailDB(mdburl, readonly=True)

  M = email.parser.Parser().parse(stdin)
  state = State(MDB, os.environ)
  state.groups = MDB.groups
  state.vars = {}
  filed = []
  for R, saved_to, ok_actions, failed_actions in list(rules.file_message(M, state)):
    print repr(R), saved_to, repr(ok_actions), repr(failed_actions)
    filed.extend(saved_to)
  return 0 if filed else 1

class State(object):
  
  def __init__(self, mdb, environ=None):
    if environ is None:
      environ = os.environ
    self.maildb = mdb
    self.environ = dict(environ)

F_HALT = 0x01   # halt rule processing if this rule matches
F_ALERT = 0x02  # issue an alert if this rule matches

re_QSTR = re.compile(r'"([^"\\]|\\.)*"')
re_UNQSTR = re.compile(r'[^,\s]+')
re_HEADERLIST = re.compile(r'([a-z][\-a-z0-9]*(,[a-z][\-a-z0-9]*)*):', re.I)
re_ASSIGN = re.compile(r'([a-z]\w+)=', re.I)

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
    self.regexptxt = regexp

  def __str__(self):
    return "<C_RE:%s:atstart=%s:%s>" \
           % ( ','.join(self.headernames),
               self.atstart,
               self.regexptxt
             )

  def match(self, M, state):
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
    return "<C_ADDR:%s:%s>" \
           % ( ','.join(self.headernames),
               '|'.join(self.addrkeys)
             )

  def match(self, M, state):
    for realname, address in message_addresses(M, self.headernames):
      for key in self.addrkeys:
        if key.startswith('{{') and key.endswith('}}'):
          key = key[2:-2].lower()
          if key not in state.groups:
            warning("%s: unknown group {{%s}}, I know: %s", self, key, state.groups.keys())
            continue
          if address in state.groups[key]:
            return True
        elif address.lower() == key.lower():
          return True
    return False

class Rule(object):

  def __init__(self):
    self.conditions = []
    self.actions = []
    self.flags = 0

  def __str__(self):
    return "<RULE:%s:flags=%s:...>" \
           % (', '.join([str(C) for C in self.conditions]), self.flags)

  def match(self, M, state):
    for C in self.conditions:
      if not C.match(M, state):
        return False
    return True

def parserules(fp):
  ''' Read rules from `fp`, yield Rules.
  '''
  if type(fp) is str:
    with open(fp) as rfp:
      for R in parserules(rfp):
        yield R
    return

  filename = fp.name
  lineno = 0
  R = None
  for line in fp:
    lineno += 1
    with Pfx("%s:%d" % (filename, lineno)):
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

        if line.startswith('<<'):
          # include another categories file
          _, offset = get_white(line, offset=2)
          subfilename, offset = get_nonwhite(line, offset=offset)
          if not subfilename:
            raise ValueError, "missing filename"
          subfilename = envsub(subfilename)
          subfilename = abspath_from_file(subfilename, filename)
          for R in parserules(subfilename):
            yield R
          continue

        # new rule
        R = Rule()

        m = re_ASSIGN.match(line)
        if m:
          R.actions.append( ('ASSIGN', (m.group(1), line[m.end():])) )
          yield R
          R = None
          continue

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
          try:
            tag, line = line.split(None, 1)
          except ValueError:
            raise ValueError, "missing tag"
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
  ''' Simple subclass of list storing rules, with methods to load
      rules and file a message using the rules.
  '''

  def __init__(self):
    list.__init__(self)
    self.vars = {}

  def load(self, fp):
    ''' Import an open rule file.
    '''
    self.extend(list(parserules(fp)))

  def file_message(self, M, state):
    ''' File message `M` according to the rules.
        Yield (R, ok_actions, failed_actions) for each rule `R` that matches;
        If the $DEFAULT rule fires the `R` will be None.
        `ok_actions` is a list of (action, arg) from `R.actions`.
        `failed` is a list of (action, arg, exception).
    '''
    savepath = None
    all_saved_to = []
    for R in self:
      debug("try rule: %s", R)
      if R.match(M, state):
        saved_to = []
        ok_actions = []
        failed_actions = []
        for action, arg in R.actions:
          try:
            info("action = %r, arg = %r", action, arg)
            if action == 'SAVE':
              savepath = None
              mdirpath = os.path.join(state.environ['MAILDIR'], arg)
              mdir = Maildir(mdirpath)
              info("SAVE to %s", mdir.dir)
              key = mdir.add(savepath if savepath is not None else M)
              savepath = mdir.keypath(key)
              saved_to.append(mdirpath)
            elif action == 'ASSIGN':
              envvar, s = arg
              state.environ[envvar] = envsub(s, state.environ)
              info("ASSIGN %s=%s", envvar, state.environ[envvar])
            else:
              raise RuntimeError("unimplemented action \"%s\"" % action)
          except NameError:
            raise
          except Exception, e:
            failed_actions.append( (action, arg, e) )
          else:
            ok_actions.append( (action, arg) )
        yield R, saved_to, ok_actions, failed_actions
        all_saved_to.extend(saved_to)
        if R.flags & F_HALT:
          break
    if not all_saved_to:
      dflt = state.environ.get('DEFAULT')
      if dflt is None:
        warn("message matched no rules, and no $DEFAULT")
      else:
        mdirpath = dflt
        mdir = Maildir(mdirpath)
        info("SAVE to default %s", mdir.dir)
        mdir.add(savepath if savepath is not None else M)
        all_saved_to.append(mdirpath)
        yield None, [mdirpath], ('SAVE', dflt), ()
    info("SAVED_TO = %r", saved_to)

if __name__ == '__main__':
  sys.exit(main(sys.argv))

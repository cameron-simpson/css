#!/usr/bin/python
#
# Handler for rulesets in the format of cats2procmailrc(1cs).
#       - Cameron Simpson <cs@zip.com.au> 22may2011
#

from collections import namedtuple
from email.utils import getaddresses
import email.parser
import os
import os.path
import re
import sys
import mailbox
if sys.hexversion < 0x02060000: from sets import Set as set
from thread import allocate_lock
from cs.env import envsub
from cs.fileutils import abspath_from_file
from cs.lex import get_white, get_nonwhite
from cs.logutils import Pfx, setup_logging, debug, info, warning, error, D, LogTime
from cs.mailutils import Maildir, read_message
from cs.misc import O, slist
from cs.threads import locked_property
from cs.app.maildb import MailDB

def main(argv, stdin=None):
  if stdin is None:
    stdin = sys.stdin
  argv = list(argv)
  cmd = argv.pop(0)
  setup_logging(cmd)
  usage = 'Usage: %s filter maildir' % (cmd,)
  mdburl = None
  badopts = False

  if not argv:
    warning("missing op")
    badopts = True
  else:
    op = argv.pop(0)
    with Pfx(op):
      if op == 'filter':
        if not argv:
          warning("missing maildir")
          badopts = True
        else:
          mdirpath = argv.pop(0)
          if argv:
            warning("extra arguments after maildir: %s", " ".join(argv))
            badopts = True
      else:
        warning("unrecognised op: %s", op)
        badopts = True

  if badopts:
    print >>sys.stderr, usage
    return 2

  with Pfx(op):
    if op == 'filter':
      if mdburl is None:
        mdburl = os.environ['MAILDB']
      D("get maildb %s", mdburl)
      MDB = MailDB(mdburl, readonly=True)
      D("got maildb, get WatchedMaildir(%s,..)", mdirpath)
      MW = WatchedMaildir(mdirpath, MDB)
      D("got maildir, call filter()")
      with LogTime("MW.filter()", threshold=0.0):
        for key, reports in MW.filter():
          D("key = %s, did: %s", key, reports)
      D("filtered")
      return 0

      M = email.parser.Parser().parse(stdin)
      state = State(MDB, os.environ)
      state.groups = MDB.groups
      state.vars = {}
      filed = []
      for report in rules.filter(M, state):
        if report.matched:
          for saved_to in report.saved_to:
            print "%s %s => %s" % (M['from'], M['subject'], saved_to)
        filed.extend(report.saved_to)
      return 0 if filed else 1

    raise RunTimeError("unimplemented op")

class State(O):
 
  def __init__(self, mdb, environ=None):
    if environ is None:
      environ = os.environ
    self.maildb = mdb
    self.environ = dict(environ)
    self.flags = O()
    self.current_message = None

  def addresses(self, M, *headers):
    ''' Return the core addresses from the supplies Message and headers.
        Caches results for rapid rule evaluation.
    '''
    if M is not self.current_message:
      self.current_message = M
      self.header_addresses = {}
    if len(headers) != 1:
      addrs = set()
      for header in headers:
        addrs.update(self.addresses(M, header))
      return addrs
    header = headers[0]
    hamap = self.header_addresses
    if header not in hamap:
      hamap[header] = set( [ A for A, N in message_addresses(M, (header,)) ] )
    return hamap[header]

re_QSTR = re.compile(r'"([^"\\]|\\.)*"')
re_UNQSTR = re.compile(r'[^,\s]+')
re_HEADERLIST = re.compile(r'([a-z][\-a-z0-9]*(,[a-z][\-a-z0-9]*)*):', re.I)
re_ASSIGN = re.compile(r'([a-z]\w+)=', re.I)
re_INGROUP = re.compile(r'\(\s*[a-z]\w+(\s*|\s*[a-z]\w+)*\s*\)', re.I)

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
          R.flags.halt = False
          line = line[1:]
        elif line.startswith('='):
          R.flags.halt = True
          line = line[1:]
        if line.startswith('!'):
          R.flags.alert = True
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
            raise ValueError("missing tag")
        R.tag = tag

      # condition
      if len(line) == 0:
        raise ValueError("missing condition")

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
        # (group[,group...])
        m = re_INGROUP.match(line)
        if m:
          group_names = set( w.strip() for w in line.split(',') )
          line = line[m.end():].rstrip()
          if line:
            raise ValueError("extra text after groups: %s" % (line,))
          C = Condition_InGroups(headernames, group_names)
        else:
          # just a comma separated list of addresses
          # TODO: should be RFC2822 list instead?
          addrkeys = [ w.strip() for w in line.split(',') ]
          C = Condition_AddressMatch(headernames, addrkeys)
      R.conditions.append(C)

  if R is not None:
    yield R

def message_addresses(M, hdrs):
  ''' Yield (realname, address) pairs from all the named headers.
  '''
  for hdr in hdrs:
    for realname, address in getaddresses(M.get_all(hdr, ())):
      yield realname, address

def resolve_maildir(mdirpath, environ=None):
  ''' Return a new Maildir based on mdirpath.
  '''
  if not os.path.isabs(mdirpath):
    if mdirpath.startswith('./') or mdirpath.startswith('../'):
      mdirpath = os.path.abspath(mdirpath)
    else:
      if environ is None:
        environ = os.environ
      mdirpath = os.path.join(environ['MAILDIR'], mdirpath)
  return Maildir(mdirpath)

class _Condition(O):
  pass

class Condition_Regexp(_Condition):

  def __init__(self, headernames, atstart, regexp):
    self.headernames = headernames
    self.atstart = atstart
    self.regexp = re.compile(regexp)
    self.regexptxt = regexp

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

  def match(self, M, state):
    for address in state.addresses(M, *self.headernames):
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

class Condition_InGroups(_Condition):

  def __init__(self, headername, group_names):
    self.headername = headernames
    self.group_names = group_names

  def match(self, M, state):
    MDB = state.maildb
    for address in state.addresses(M, *self.headernames):
      for group_name in self.group_names:
        if address in MDB.group(group_name):
          return True
    return False

FilterReport = namedtuple('FilterReport',
                          'rule matched saved_to ok_actions failed_actions')

class Rule(O):

  def __init__(self):
    self.conditions = slist()
    self.actions = slist()
    self.flags = O()
    self.flags.alert = False
    self.flags.halt = False

  def match(self, M, state):
    for C in self.conditions:
      if not C.match(M, state):
        return False
    return True

  def filter(self, M, state, msgpath=None):
    saved_to = []
    ok_actions = []
    failed_actions = []
    matched = self.match(M, state)
    if matched:
      for action, arg in self.actions:
        try:
          info("action = %r, arg = %r", action, arg)
          if action == 'SAVE':
            mdirpath = os.path.join(state.environ['MAILDIR'], arg)
            mdir = resolve_maildir(mdirpath)
            info("SAVE to %s", mdir.dir)
            key = mdir.add(msgpath if msgpath is not None else M)
            msgpath = mdir.keypath(key)
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
    return FilterReport(self, matched, saved_to, ok_actions, failed_actions)

class Rules(list):
  ''' Simple subclass of list storing rules, with methods to load
      rules and filter a message using the rules.
  '''

  def __init__(self):
    list.__init__(self)
    self.vars = {}

  def load(self, fp):
    ''' Import an open rule file.
    '''
    self.extend(list(parserules(fp)))

  def filter(self, M, state):
    ''' Filter message `M` according to the rules.
        Yield FilterReports for each rule consulted.
        If no rules matches and $DEFAULT is set, yield a FilterReport for
        filing to $DEFAULT, with .rule set to None.
    '''
    done = False
    savepath = None
    matches = 0
    for R in self:
      report = R.filter(M, state, savepath)
      yield report
      if report.matched:
        matches += 1
        if report.saved_to:
          if savepath is None:
            savepath = report.saved_to[0]
        if R.flags.halt:
          done = True
          break
      else:
        if report.saved_to:
          raise RunTimeError("matched is False, but saved_to = %s" % (saved_to,))
    if not done:
      if matches:
        dflt = state.environ.get('DEFAULT')
        if dflt is None:
          warn("message matched no rules, and no $DEFAULT")
        else:
          matched = False
          saved_to = []
          ok_actions = []
          failed_actions = []
          mdirpath = dflt
          action, arg = ('SAVE', mdirpath)
          try:
            mdir = resolve_maildir(mdirpath)
            info("SAVE to default %s", mdir.dir)
            key = mdir.add(savepath if savepath is not None else M)
            msgpath = mdir.keypath(key)
            saved_to.append(msgpath)
            matched = True
          except NameError:
            raise
          except Exception, e:
            failed_actions.append( (action, arg, e) )
          else:
            ok_actions.append( (action, arg) )
          yield FilterReport(None, matched, saved_to, ok_actions, failed_actions)

class WatchedMaildir(O):
  ''' A class to monitor a Maildir and filter messages.
  '''

  def __init__(self, mdir, maildb, rules_file=None):
    self.mdir = resolve_maildir(mdir)
    if rules_file is None:
      rules_file = os.path.join(self.mdir.dir, '.rules')
    self.rules_file = rules_file
    self._rules = None
    self.maildb = maildb
    self.lurking = set()
    self._lock = allocate_lock()
    self.flush()

  def flush(self):
    ''' Forget state.
        The set of lurkers is emptied.
    '''
    self.lurking = set()

  @locked_property
  def rules(self):
    with LogTime("load %s" % (self.rules_file,), threshold=0.0):
      rules = Rules()
      rules.load(self.rules_file)
    D("%d rules", len(rules))
    return rules

  def filter(self):
    ''' Scan Maildir contents.
        Yield (key, FilterReports) for all messages filed.
	Update the set of lurkers with any keys not removed to prevent
	filtering on subsequent calls.
    '''
    with Pfx("%s: filter" % (self.mdir.dir,)):
      mdir = self.mdir
      for key in mdir.keys():
        if key in self.lurking:
          debug("skip processed key: %s", key)
          continue
        with LogTime("key = %s" % (key,), threshold=0.0):
          M = mdir[key]
          state = State(self.maildb)
          filed = []
          reports = []
          for report in self.rules.filter(M, state):
            if report.matched:
              reports.append(report)
              for saved_to in report.saved_to:
                print "%s %s => %s" % (M['from'], M['subject'], saved_to)
            filed.extend(report.saved_to)
          if filed and False:
            info("remove key %s", key)
            mdir.remove(key)
          else:
            debug("lurk key %s", key)
            self.lurking.add(key)
          yield key, reports

if __name__ == '__main__':
  sys.exit(main(sys.argv))

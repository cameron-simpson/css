#!/usr/bin/python
#
# Handler for rulesets in the format of cats2procmailrc(1cs).
#       - Cameron Simpson <cs@zip.com.au> 22may2011
#

from __future__ import print_function
from collections import namedtuple
from email.utils import getaddresses
import email.parser
from getopt import getopt, GetoptError
from logging import DEBUG
import mailbox
import os
import os.path
import re
import sys
from time import sleep
if sys.hexversion < 0x02060000: from sets import Set as set
from thread import allocate_lock
from cs.env import envsub
from cs.fileutils import abspath_from_file, watched_file_property
from cs.lex import get_white, get_nonwhite, get_qstr
from cs.logutils import Pfx, setup_logging, debug, info, warning, error, D, LogTime
from cs.mailutils import Maildir, read_message
from cs.misc import O, slist
from cs.threads import locked_property
from cs.app.maildb import MailDB

def main(argv, stdin=None):
  if stdin is None:
    stdin = sys.stdin
  argv = list(argv)
  cmd = os.path.basename(argv.pop(0))
  setup_logging(cmd)
  usage = 'Usage: %s filter [-d delay] [-n] maildirs...' % (cmd,)
  mdburl = None
  badopts = False

  if not argv:
    warning("missing op")
    badopts = True
  else:
    op = argv.pop(0)
    with Pfx(op):
      if op == 'filter':
        delay = None
        no_remove = False
        try:
          opts, argv = getopt(argv, 'd:n')
        except GetoptError, e:
          warning("%s", e)
          badopts = True
        for opt, val in opts:
          with Pfx(opt):
            if opt == '-d':
              try:
                delay = int(val)
              except ValueError, e:
                warning("%s: %s", e, val)
                badopts = True
              else:
                if delay <= 0:
                  warning("delay must be positive, got: %d", delay)
                  badopts = True
            elif opt == '-n':
              no_remove = True
            else:
              warning("unimplemented option")
              badopts = True
        if not argv:
          warning("missing maildirs")
          badopts = True
        else:
          mdirpaths = argv
      else:
        warning("unrecognised op: %s", op)
        badopts = True

  if badopts:
    print(usage, file=sys.stderr)
    return 2

  with Pfx(op):
    if op == 'filter':
      if mdburl is None:
        mdburl = os.environ['MAILDB']
      maildirs = []
      mailinfo = MailInfo(mdburl)
      maildirs = [ WatchedMaildir(mdirpath) for mdirpath in mdirpaths ]
      while True:
        for MW in maildirs:
          with LogTime("MW.filter()", threshold=0.0):
            for key, reports in MW.filter(mailinfo, no_remove=no_remove):
              ##D("key = %s, did: %s", key, reports)
              pass
        if delay is None:
          break
        debug("sleep %d", delay)
        sleep(delay)
      return 0

    raise RunTimeError("unimplemented op")

class State(O):
  ''' State information for rule evaluation.
      .mailinfo MailDB wrapper with access methods.
      .environ  Storage for variable settings.
  '''
 
  def __init__(self, mailinfo, environ=None):
    ''' `mailinfo`: MailDB wrapper with access methods.
        `environ`:  Mapping if initial variable names.
                    Default from os.environ.
    '''
    if environ is None:
      environ = os.environ
    self.mailinfo = mailinfo
    self.environ = dict(environ)
    self.current_message = None
    self._log = None

  def log(self, *a):
    log = self._log
    if log is None:
      log = sys.stdout
    print(*a, file=log)

  def logto(self, logfilepath):
    if self._log:
      self._log.close()
    self._log = open(logfilepath, "a")

  def addresses(self, M, *headers):
    ''' Return the core addresses from the supplies Message and headers.
        Caches results for rapid rule evaluation.
    '''
    if M is not self.current_message:
      # new message - discard cache
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

re_UNQSTR = re.compile(r'[^,\s]+')
re_HEADERLIST = re.compile(r'([a-z][\-a-z0-9]*(,[a-z][\-a-z0-9]*)*):', re.I)
re_ASSIGN = re.compile(r'([a-z]\w+)=', re.I)
re_INGROUP = re.compile(r'\(\s*[a-z]\w+(\s*|\s*[a-z]\w+)*\s*\)', re.I)

def parserules(fp):
  ''' Read rules from `fp`, yield Rules.
  '''
  if type(fp) is str:
    with open(fp) as rfp:
      for R in parserules(rfp):
        yield R
    return

  filename = fp.name
  info("PARSE RULES: %s", filename)
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

      _, offset = get_white(line, 0)
      if not _:
        # new rule
        # yield old rule if in progress
        if R:
          yield R
        R = None

        if line[offset] == '<':
          # include another categories file
          _, offset = get_white(line, offset+1)
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

        m = re_ASSIGN.match(line, offset)
        if m:
          R.actions.append( ('ASSIGN', (m.group(1), line[m.end():])) )
          yield R
          R = None
          continue

        if line[offset] == '+':
          R.flags.halt = False
          offset += 1
        elif line[offset] == '=':
          R.flags.halt = True
          offset += 1
        if line[offset] == '!':
          R.flags.alert = True
          offset += 1

        # gather targets
        while offset < len(line) and not line[offset].isspace():
          if line[offset] == '"':
            target, offset = get_qstr(line, offset)
          else:
            m = re_UNQSTR.match(line, offset)
            if m:
              target = m.group()
              offset = m.end()
            else:
              error("parse failure at %d: %s", offset, line)
              raise ValueError, "syntax error"
          R.actions.append( ('TARGET', target) )
          if offset < len(line) and line[offset] == ',':
            offset += 1

        # gather tag
        _, offset = get_white(line, offset)
        if not _ or offset == len(line):
          R.tag = ''
          warning("no tag or condition")
          continue
        if line[offset] == '"':
          tag, offset = get_qstr(line, offset)
        else:
          tag, offset = get_nonwhite(line, offset)

      # condition
      if not _ or offset == len(line):
        warning("no condition")
        continue

      # . always matches - don't bother storing it as a test
      if line[offset:] == '.':
        continue

      # leading hdr1,hdr2,...:
      m = re_HEADERLIST.match(line, offset)
      if m:
        headernames = [ H.lower() for H in m.group(1).split(',') if H ]
        offset = m.end()
        if offset == len(line):
          raise ValueError("missing match after header names")
      else:
        headernames = ('to', 'cc', 'bcc')

      if line[offset] == '/':
        regexp = line[offset+1:]
        if regexp.startswith('^'):
          atstart = True
          regexp = regexp[1:]
        else:
          atstart = False
        C = Condition_Regexp(headernames, atstart, regexp)
      else:
        # (group[,group...])
        m = re_INGROUP.match(line, offset)
        if m:
          group_names = set( w.strip() for w in line.split(',') )
          line = line[m.end():].rstrip()
          if line:
            raise ValueError("extra text after groups: %s" % (line,))
          C = Condition_InGroups(headernames, group_names)
        else:
          # just a comma separated list of addresses
          # TODO: should be RFC2822 list instead?
          addrkeys = [ w.strip() for w in line[offset:].split(',') ]
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
          if key not in state.mailinfo.groups:
            warning("%s: unknown group {{%s}}, I know: %s", self, key, state.mailinfo.groups.keys())
            continue
          if address in state.mailinfo.groups[key]:
            return True
        elif address.lower() == key.lower():
          return True
    return False

class Condition_InGroups(_Condition):

  def __init__(self, headername, group_names):
    self.headername = headernames
    self.group_names = group_names

  def match(self, M, state):
    minfo = state.mailinfo
    for address in state.addresses(M, *self.headernames):
      for group_name in self.group_names:
        if address in minfo.group(group_name):
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

  def filter(self, M, state):
    try:
      msgpath = M.pathname
    except AttributeError:
      msgpath = None
    saved_to = []
    ok_actions = []
    failed_actions = []
    matched = self.match(M, state)
    if matched:
      for action, arg in self.actions:
        try:
          debug("action = %r, arg = %r", action, arg)
          if action == 'TARGET':
            saved_to.append(self._target(target, M, state))
            target = envsub(arg, state.environ)
            if target.startswith('|'):
              assert False, "pipes not implements"
            elif '@' in target:
              assert False, "email forwarding not implemented"
            else:
              mdir = resolve_maildir(arg, state.environ)
              debug("SAVE to %s", mdir.dir)
              saved_msgpath = mdir.keypath(save_to_maildir(mdir, M))
              if msgpath is None:
                msgpath = saved_msgpath
              saved_to.append(saved_msgpath)
          elif action == 'ASSIGN':
            envvar, s = arg
            state.environ[envvar] = envsub(s, state.environ)
            debug("ASSIGN %s=%s", envvar, state.environ[envvar])
            if envvar == 'LOGFILE':
              state.logto(state.environ[envvar])
          else:
            raise RuntimeError("unimplemented action \"%s\"" % action)
        except (AttributeError, NameError):
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

  def __init__(self, rules_file=None):
    list.__init__(self)
    self.vars = {}
    if rules_file is not None:
      self.load(rules_file)

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
    matches = 0
    for R in self:
      report = R.filter(M, state)
      yield report
      if report.matched:
        matches += 1
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
          warning("message matched no rules, and no $DEFAULT")
        else:
          matched = False
          saved_to = []
          ok_actions = []
          failed_actions = []
          mdirpath = dflt
          action, arg = ('TARGET', mdirpath)
          try:
            mdir = resolve_maildir(arg, state.environ)
            debug("SAVE to default %s", mdir.dir)
            saved_msgpath = mdir.keypath(save_to_maildir(mdir, M))
            if msgpath is None:
              msgpath = saved_msgpath
            saved_to.append(saved_msgpath)
            matched = True
          except (AttributeError, NameError):
            raise
          except Exception, e:
            failed_actions.append( (action, arg, e) )
          else:
            ok_actions.append( (action, arg) )
          yield FilterReport(None, matched, saved_to, ok_actions, failed_actions)

class MailInfo(O):
  ''' Mail external information.
      Includes a maildb and access methods.
  '''

  def __init__(self, maildb_path):
    self._maildb_path = maildb_path

  @watched_file_property
  def maildb(self, path):
    warning("load maildb(%s)", path)
    return MailDB(path, readonly=True)

  @property
  def group(self):
    return self.maildb.group

def save_to_maildir(mdir, M):
  ''' Save the Message `M` to the Maildir `mdir`.
  '''
  debug("save_to_maildir(%s,M)", mdir)
  if type(mdir) is str:
    return save_to_maildir(Maildir(mdir), M)
  try:
    msgpath = M.pathname
  except AttributeError:
    msgpath = None
  savepath = mdir.keypath(mdir.add(msgpath if msgpath is not None else M))
  D("%s %s", M['from'], savepath)
  return savepath

class WatchedMaildir(O):
  ''' A class to monitor a Maildir and filter messages.
  '''

  def __init__(self, mdir, rules_path=None):
    self.mdir = resolve_maildir(mdir)
    if rules_path is None:
      rules_path = os.path.join(self.mdir.dir, '.rules')
    self._rules_path = rules_path
    self._rules_lock = allocate_lock()
    self.lurking = set()
    self.flush()
    warning("%d rules", len(self.rules))

  def flush(self):
    ''' Forget state.
        The set of lurkers is emptied.
    '''
    self.lurking = set()

  @watched_file_property
  def rules(self, rules_path):
    return Rules(rules_path)

  def filter(self, mailinfo, no_remove=False):
    ''' Scan Maildir contents.
        Yield (key, FilterReports) for all messages filed.
	Update the set of lurkers with any keys not removed to prevent
	filtering on subsequent calls.
    '''
    with Pfx("%s: filter" % (self.mdir.dir,)):
      self.mdir.flush()
      nmsgs = 0
      skipped = 0
      with LogTime("all keys") as TK:
        mdir = self.mdir
        for key in mdir.keys():
          if key in self.lurking:
            debug("skip processed key: %s", key)
            skipped += 1
            continue
          nmsgs += 1
          with LogTime("key = %s" % (key,), threshold=0.0, level=DEBUG):
            M = mdir[key]
            msgpath = mdir.keypath(key)
            state = State(mailinfo)
            filed = []
            reports = []
            for report in self.rules.filter(M, state):
              if report.matched:
                reports.append(report)
                for saved_to in report.saved_to:
                  state.log(M['from'], M['subject'], saved_to)
              filed.extend(report.saved_to)
            if filed and not no_remove:
              debug("remove key %s", key)
              mdir.remove(key)
              self.lurking.discard(key)
            else:
              debug("lurk key %s", key)
              self.lurking.add(key)
            yield key, reports
      D("filtered %d messages (%d skipped) in %5.3fs", nmsgs, skipped, TK.elapsed)

if __name__ == '__main__':
  sys.exit(main(sys.argv))

#!/usr/bin/python
#
# Handler for rulesets in the format of cats2procmailrc(1cs).
#       - Cameron Simpson <cs@zip.com.au> 22may2011
#

from __future__ import print_function
from collections import namedtuple
from email import message_from_string
import email.parser
from getopt import getopt, GetoptError
import io
from logging import DEBUG
import mailbox
import os
import os.path
import re
import sys
from time import sleep
if sys.hexversion < 0x02060000: from sets import Set as set
import subprocess
from tempfile import TemporaryFile
from thread import allocate_lock
import time
from cs.env import envsub
from cs.fileutils import abspath_from_file, watched_file_property
from cs.lex import get_white, get_nonwhite, get_qstr, unrfc2047
from cs.logutils import Pfx, setup_logging, debug, info, warning, error, D, LogTime
from cs.mailutils import Maildir, message_addresses
from cs.misc import O, slist
from cs.threads import locked_property
from cs.app.maildb import MailDB

def main(argv, stdin=None):
  if stdin is None:
    stdin = sys.stdin
  argv = list(argv)
  cmd = os.path.basename(argv.pop(0))
  setup_logging(cmd)
  usage = 'Usage: %s monitor [-d delay] [-n] maildirs...' % (cmd,)
  badopts = False

  if not argv:
    warning("missing op")
    badopts = True
  else:
    op = argv.pop(0)
    with Pfx(op):
      if op == 'monitor':
        justone = False
        delay = None
        no_remove = False
        no_save = False
        try:
          opts, argv = getopt(argv, '1d:nN')
        except GetoptError, e:
          warning("%s", e)
          badopts = True
        for opt, val in opts:
          with Pfx(opt):
            if opt == '-1':
              justone = True
            elif opt == '-d':
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
    if op == 'monitor':
      maildir_cache = {}
      filter_modes = FilterModes(justone=justone,
                                 delay=delay,
                                 no_remove=no_remove,
                                 no_save=no_save,
                                 maildb_path=os.environ['MAILDB'],
                                 maildir_cache={})
      maildirs = [ WatchedMaildir(mdirpath, filter_modes=filter_modes)
                   for mdirpath in mdirpaths
                 ]
      while True:
        for MW in maildirs:
          debug("process %s", MW.mdir.dir)
          with LogTime("%s.filter()" % (MW,), threshold=1.0):
            for key, reports in MW.filter():
              pass
        if delay is None:
          break
        debug("sleep %ds", delay)
        sleep(delay)
      return 0

    raise RunTimeError("unimplemented op")

def maildir_from_name(mdirname, maildir_root, maildir_cache):
    ''' Return the Maildir derived from mdirpath.
    '''
    mdirpath = resolve_maildir_path(mdirname, maildir_root)
    if mdirpath not in maildir_cache:
      maildir_cache[mdirpath] = Maildir(mdirpath)
    return maildir_cache[mdirpath]

def resolve_maildir_path(mdirpath, maildir_root):
  ''' Return the full path to the requested maildir.
  '''
  if not os.path.isabs(mdirpath):
    if mdirpath.startswith('./') or mdirpath.startswith('../'):
      mdirpath = os.path.abspath(mdirpath)
    else:
      mdirpath = os.path.join(maildir_root, mdirpath)
  return mdirpath

class FilterModes(O):

  def __init__(self, **kw):
    self._maildb_path = kw.pop('maildb_path')
    self._maildb_lock = allocate_lock()
    O.__init__(self, **kw)

  @watched_file_property
  def maildb(self, path):
    warning("load maildb(%s)", path)
    return MailDB(path, readonly=True)

  def maildir(self, mdirname, environ=None):
    return maildir_from_name(mdirname, environ['MAILDIR'], self.maildir_cache)

class RuleState(O):
  ''' State information for rule evaluation.
      .message  Current message.
      .maildb   MailDB.
      .environ  Storage for variable settings.
  '''
 
  maildirs = {}

  def __init__(self, M, outer_state, environ=None):
    ''' `M`:           The Message object to be filed.
        `outer_state`: External state object, with maildb etc.
        `environ`:     Mapping of initial variable names.
                       Default from os.environ.
    '''
    self.message = M
    self.outer_state = outer_state
    if environ is None:
      environ = os.environ
    self.environ = dict(environ)
    self.reuse_maildir = False
    self.used_maildirs = set()
    self._log = None

  @property
  def maildb(self):
    return self.outer_state.maildb

  def maildir(self, mdirpath):
    return self.outer_state.maildir(mdirpath, self.environ)

  @property
  def message(self):
    return self._message

  @message.setter
  def message(self, new_message):
    self._message = new_message
    self.message_path = None
    self.header_addresses = {}

  def log(self, *a):
    ''' Log a message.
    '''
    log = self._log
    if log is None:
      log = sys.stdout
    try:
      print(*[ unicode(s) for s in a], file=log)
    except UnicodeDecodeError, e:
      print("RuleState.log: %s: a=%r" % (e, a), file=sys.stderr)

  def logto(self, logfilepath):
    ''' Direct log messages to the supplied `logfilepath`.
    '''
    if self._log:
      self._log.close()
    try:
      self._log = io.open(logfilepath, "a", encoding='utf-8')
    except OSError, e:
      self.log("open(%s): %s" % (logfilepath, e))

  @property
  def groups(self):
    ''' The group mapping from the MailDB.
    '''
    return self.maildb.address_groups

  def ingroup(self, coreaddr, group_name):
    ''' Test if a core address is a member of the named group.
    '''
    group = self.groups.get(group_name)
    if group is None:
      warning("unknown group: %s", group_name)
      self.groups[group_name] = set()
      return False
    return coreaddr.lower() in group

  def addresses(self, *headers):
    ''' Return the core addresses from the current Message and supplied
        `headers`. Caches results for rapid rule evaluation.
    '''
    M = self.message
    if M is not self.message:
      # new message - discard cache
      self.message = M
      self.header_addresses = {}
    if len(headers) != 1:
      addrs = set()
      for header in headers:
        addrs.update(self.addresses(header))
      return addrs
    header = headers[0]
    hamap = self.header_addresses
    if header not in hamap:
      hamap[header] = set( [ A for N, A in message_addresses(M, (header,)) ] )
    return hamap[header]

  def save_to_maildir(self, mdir, label):
    mdirpath = mdir.dir
    if self.reuse_maildir or mdirpath not in self.used_maildirs:
      self.used_maildirs.add(mdirpath)
    else:
      return None
    M = self.message
    if label and M.get('x-label', '') != label:
      # modifying message - make copy
      path = None
      M = message_from_string(M.as_string())
      M['X-Label'] = label
    else:
      path = self.message_path
    if path is None:
      savekey = mdir.save_message(M)
    else:
      savekey = mdir.save_filepath(path)
    savepath = mdir.keypath(savekey)
    if not path and not label:
      self.message_path = savepath
    self.log("    OK %s => %s" % (M['message-id'], savepath))
    return savepath

  def pipe_message(self, argv, mfp=None):
    ''' Pipe a message to the command specific by `argv`.
        `mfp` is a file containing the message text.
        If `mfp` is None, use the text of the current message.
    '''
    if mfp is None:
      message_path = self.message_path
      if message_path:
        with open(message_path) as mfp:
          return self.pipe_message(argv, mfp)
      else:
        with TemporaryFile('w+') as mfp:
          mfp.write(str(self.message))
          mfp.flush()
          mfp.seek(0)
          return self.pipe_message(argv, mfp)
    retcode = subprocess.call(argv, env=self.environ, stdin=mfp)
    self.log("    %s %s => | %s" % (("OK" if retcode == 0 else "FAIL"), self.message['message-id'], argv))
    return retcode == 0

  def sendmail(self, address, mfp=None):
    ''' Dispatch a message to `address`.
        `mfp` is a file containing the message text.
        If `mfp` is None, use the text of the current message.
    '''
    return self.pipe_message([self.environ.get('SENDMAIL', 'sendmail'), '-oi', address], mfp=mfp)

re_UNQWORD = re.compile(r'[^,\s]+')
re_HEADERLIST = re.compile(r'([a-z][\-a-z0-9]*(,[a-z][\-a-z0-9]*)*):', re.I)
re_ASSIGN = re.compile(r'([a-z]\w+)=', re.I)
re_INGROUP = re.compile(r'\(\s*[a-z]\w+(\s*\|\s*[a-z]\w+)*\s*\)', re.I)

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
        R = Rule(filename=filename, lineno=lineno)

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
            m = re_UNQWORD.match(line, offset)
            if m:
              target = m.group()
              offset = m.end()
            else:
              error("parse failure at %d: %s", offset, line)
              raise ValueError, "syntax error"
          R.actions.append( ('TARGET', target) )
          if offset < len(line) and line[offset] == ',':
            offset += 1

        # gather label
        _, offset = get_white(line, offset)
        if not _ or offset == len(line):
          R.label = ''
          warning("no label or condition")
          continue
        if line[offset] == '"':
          label, offset = get_qstr(line, offset)
        else:
          label, offset = get_nonwhite(line, offset)
        if label == '.':
          label = ''
        R.label = label
        _, offset = get_white(line, offset)

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
        # (group[|group...])
        m = re_INGROUP.match(line, offset)
        if m:
          group_names = set( w.strip().lower() for w in m.group()[1:-1].split('|') )
          offset = m.end()
          if offset < len(line):
            raise ValueError("extra text after groups: %s" % (line,))
          C = Condition_InGroups(headernames, group_names)
        else:
          if '(ME)' in line:
            error("MISMATCH AT: %s", line[offset:])
            error("    LINE IS: %s", line)
            sys.exit(1)
          if line[offset] == '(':
            error("FAILED GROUP MATCH AT: %s", line[offset:])
            sys.exit(1)
          # just a comma separated list of addresses
          # TODO: should be RFC2822 list instead?
          addrkeys = [ w.strip() for w in line[offset:].split(',') ]
          C = Condition_AddressMatch(headernames, addrkeys)
      R.conditions.append(C)

  if R is not None:
    yield R

class _Condition(O):
  pass

class Condition_Regexp(_Condition):

  def __init__(self, headernames, atstart, regexp):
    self.headernames = headernames
    self.atstart = atstart
    self.regexp = re.compile(regexp)
    self.regexptxt = regexp

  def match(self, state):
    M = state.message
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

  def match(self, state):
    for address in state.addresses(*self.headernames):
      for key in self.addrkeys:
        if key.startswith('{{') and key.endswith('}}'):
          warning("OBSOLETE address key: %s", key)
          group_name = key[2:-2].lower()
          if state.ingroup(address, group_name):
            return True
        elif address.lower() == key.lower():
          return True
    return False

class Condition_InGroups(_Condition):

  def __init__(self, headernames, group_names):
    self.headernames = headernames
    self.group_names = group_names

  def match(self, state):
    for address in state.addresses(*self.headernames):
      for group_name in self.group_names:
        if state.ingroup(address, group_name):
          debug("match %s to (%s)", address, group_name)
          return True
    return False

FilterReport = namedtuple('FilterReport',
                          'rule matched saved_to ok_actions failed_actions')

class Rule(O):

  def __init__(self, filename, lineno):
    self.filename = filename
    self.lineno = lineno
    self.conditions = slist()
    self.actions = slist()
    self.flags = O(alert=False, halt=False)
    self.label = ''
    self.default_rule = None

  def match(self, state):
    for C in self.conditions:
      if not C.match(state):
        return False
    return True

  def filter(self, state):
    M = state.message
    with Pfx("%s:%d" % (self.filename, self.lineno)):
      saved_to = []
      ok_actions = []
      failed_actions = []
      matched = self.match(state)
      if matched:
        for action, arg in self.actions:
          try:
            if action == 'TARGET':
              target = envsub(arg, state.environ)
              if target.startswith('|'):
                shcmd = target[1:]
                if state.pipe_message(['/bin/sh', '-c', shcmd]):
                  saved_to.append(target)
                else:
                  raise RunTimeError("failed to pipe to %s" % (target,))
              elif '@' in target:
                if state.sendmail(target):
                  saved_to.append(target)
                else:
                  raise RunTimeError("failed to sendmail to %s" % (target,))
              else:
                mdir = state.maildir(target)
                savepath = state.save_to_maildir(mdir, self.label)
                # we get None if the message has already been saved here
                if savepath:
                  saved_to.append(savepath)
            elif action == 'ASSIGN':
              envvar, s = arg
              value = state.environ[envvar] = envsub(s, state.environ)
              debug("ASSIGN %s=%s", envvar, value)
              if envvar == 'LOGFILE':
                state.logto(value)
              elif envvar == 'DEFAULT':
                R = state.default_rule = Rule(self.filename, self.lineno)
                R.actions.append( ('TARGET', '$DEFAULT') )
            else:
              raise RuntimeError("unimplemented action \"%s\"" % action)
          except (AttributeError, NameError):
            raise
          except Exception, e:
            warning("EXCEPTION %r", e)
            failed_actions.append( (action, arg, e) )
            raise
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

  def filter(self, state):
    ''' Filter message `M` according to the rules.
        Yield FilterReports for each rule consulted.
        If no rules matches and $DEFAULT is set, yield a FilterReport for
        filing to $DEFAULT, with .rule set to None.
    '''
    done = False
    saved_to = []
    for R in self:
      report = R.filter(state)
      yield report
      if report.matched:
        saved_to.extend(report.saved_to)
        if R.flags.halt:
          done = True
          break
      else:
        if report.saved_to:
          raise RunTimeError("matched is False, but saved_to = %s" % (saved_to,))
    if not done:
      if not saved_to:
        R = state.default_rule
        if not R:
          warning("message not saved and no $DEFAULT")
        else:
          report = R.filter(state)
          if not report.matched:
            raise RunTimeError("default rule faled to match! %r", R)
          saved_to.extend(report.saved_to)
          if not saved_to:
            warning("message not saved by any rules")
          yield report
      else:
        debug("%d filings, skipping DEFAULT", len(saved_to))

class WatchedMaildir(O):
  ''' A class to monitor a Maildir and filter messages.
  '''

  def __init__(self, mdir, filter_modes, rules_path=None):
    self.mdir = Maildir(resolve_maildir_path(mdir, os.environ['MAILDIR']))
    self.filter_modes = filter_modes
    if rules_path is None:
      rules_path = os.path.join(self.mdir.dir, '.rules')
    self._rules_path = rules_path
    self._rules_lock = allocate_lock()
    self.lurking = set()
    self.flush()
    warning("%d rules", len(self.rules))

  def __str__(self):
    return "<WatchedMaildir %s modes=%s, %d rules, %d lurking>" \
           % (self.mdir, self.filter_modes, len(self.rules), len(self.lurking))

  def flush(self):
    ''' Forget state.
        The set of lurkers is emptied.
    '''
    self.lurking = set()

  @watched_file_property
  def rules(self, rules_path):
    return Rules(rules_path)

  def filter(self):
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
          with LogTime("key = %s" % (key,), threshold=1.0, level=DEBUG):
            M = mdir[key]
            state = RuleState(M, self.filter_modes)
            state.message_path = mdir.keypath(key)
            state.logto(envsub("$HOME/var/log/mailfiler"))
            state.log( (u"%s %s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                       unrfc2047(M.get('from', '_no_from')),
                                       unrfc2047(M.get('subject', '_no_subject'))))
                       .replace('\n', ' ') )
            state.log("  "+mdir.keypath(key))
            saved_to = []
            reports = []
            for report in self.rules.filter(state):
              if report.matched:
                reports.append(report)
                saved_to.extend(report.saved_to)
            if saved_to and not self.filter_modes.no_remove:
              debug("remove key %s", key)
              mdir.remove(key)
              self.lurking.discard(key)
            else:
              warning("message not saved, lurking key %s", key)
              self.lurking.add(key)
            yield key, reports
          if self.filter_modes.justone:
            break
      if nmsgs or TK.elapsed >= 0.2:
        info("filtered %d messages (%d skipped) in %5.3fs", nmsgs, skipped, TK.elapsed)

if __name__ == '__main__':
  sys.exit(main(sys.argv))

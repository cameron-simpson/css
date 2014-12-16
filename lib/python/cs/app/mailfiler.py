#!/usr/bin/python
#
# Handler for rulesets similar to the format of cats2procmailrc(1cs).
#       - Cameron Simpson <cs@zip.com.au> 22may2011
#

from __future__ import print_function
from collections import namedtuple
from email import message_from_string, message_from_file
import email.parser
from email.utils import getaddresses
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
from threading import Lock, RLock
import time
from cs.configutils import ConfigWatcher
import cs.env
from cs.env import envsub
from cs.fileutils import abspath_from_file, file_property, files_property, \
                         longpath, Pathname
from cs.lex import get_white, get_nonwhite, get_qstr, unrfc2047
from cs.logutils import Pfx, setup_logging, with_log, \
                        debug, info, warning, error, exception, \
                        D, X, LogTime
from cs.mailutils import Maildir, message_addresses, shortpath, ismaildir, make_maildir
from cs.obj import O, slist
from cs.threads import locked, locked_property
from cs.app.maildb import MailDB
from cs.py3 import unicode as u, StringTypes, ustr

DEFAULT_MAIN_LOG = 'mailfiler/main.log'
DEFAULT_RULES_PATTERN = '$HOME/.mailfiler/{maildir.basename}'
DEFAULT_MAILFILER_RC = '$HOME/.mailfilerrc'
DEFAULT_MAILDB_PATH = '$HOME/.maildb.csv'
DEFAULT_MSGIDDB_PATH = '$HOME/var/msgiddb.csv'
DEFAULT_MAILDIR_PATH = '$MAILDIR'

def main(argv, stdin=None):
  if stdin is None:
    stdin = sys.stdin
  argv = list(argv)
  cmd = os.path.basename(argv.pop(0))
  setup_logging(cmd)
  usage = ( '''Usage:
    %s monitor [-1] [-d delay] [-n] [-N] [-R rules_pattern] maildirs...
      -1  File at most 1 message per Maildir.
      -d delay
          Delay between runs in seconds.
          Default is to make only one run over the Maildirs.
      -n  No remove. Keep filed messages in the origin Maildir.
      -R rules_pattern
          Specify the rules file pattern used to specify rules files from Maildir names.
          Default: %s
    %s save target <message'''
            % (cmd, DEFAULT_RULES_PATTERN, cmd)
          )
  badopts = False

  config_path = None
  maildb_path = None
  msgiddb_path = None
  maildir = None
  rules_pattern = None

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
        try:
          opts, argv = getopt(argv, '1d:nR:')
        except GetoptError as e:
          warning("%s", e)
          badopts = True
        else:
          for opt, val in opts:
            with Pfx(opt):
              if opt == '-1':
                justone = True
              elif opt == '-d':
                try:
                  delay = int(val)
                except ValueError as e:
                  warning("%s: %s", e, val)
                  badopts = True
                else:
                  if delay <= 0:
                    warning("delay must be positive, got: %d", delay)
                    badopts = True
              elif opt == '-n':
                no_remove = True
              elif opt == '-R':
                rules_pattern = val
              else:
                warning("unimplemented option")
                badopts = True
        mdirpaths = argv
      elif op == 'save':
        if not argv:
          warning("missing target")
          badopts = True
        else:
          target = argv.pop(0)
          if argv:
            warning("extra arguments after target: %r", argv)
            badopts = True
        message_fp = sys.stdin
        if message_fp.isatty():
          warning("stdin: will not read from a tty")
          badopts = True
      else:
        warning("unrecognised op")
        badopts = True

  if badopts:
    print(usage, file=sys.stderr)
    return 2

  MF = MailFiler(config_path)

  with Pfx(op):
    if op == 'monitor':
      if not mdirpaths:
        mdirpaths = None
      return MF.monitor(mdirpaths, delay=delay, justone=justone, no_remove=no_remove)
    if op == 'save':
      return MF.save(target, sys.stdin)
    raise RuntimeError("unimplemented op")

  return 0

def current_value(envvar, cfg, cfg_key, default, environ):
  ''' Compute a configurable path value on the fly.
  '''
  value = environ.get(envvar)
  if value is None:
    value = cfg.get(cfg_key)
    if value is None:
      value = envsub(default)
    else:
      value = longpath(value)
  return value

class MailFiler(O):

  def __init__(self, config_path, environ=None):
    ''' Initialise the MailFiler.
        `config_path`: location of config file, default from DEFAULT_MAILFILER_RC.
        `environ`: initial environment, default from os.environ.
    '''
    if config_path is None:
      config_path = envsub(DEFAULT_MAILFILER_RC)
    if environ is None:
      environ = dict(os.environ)
    self.config_path = config_path
    self.environ = environ
    self._lock = RLock()
    self._cfg = ConfigWatcher(config_path)
    self._maildb_path = None
    self._maildb_lock = self._lock
    self._maildb = None
    self._msgiddb_path = None
    self._msgiddb_lock = self._lock
    self._msgiddb = None
    self._maildir_path = None
    self._maildir_watchers = {}
    self._rules_pattern = None

  @property
  def cfg(self):
    return self._cfg['DEFAULT']

  def subcfg(self, section_name):
    return self._cfg[section_name]

  @property
  def cfg_monitor(self):
    return self.subcfg('monitor')

  @locked_property
  def maildb_path(self):
    ''' Compute maildb path on the fly.
    '''
    return current_value('MAILDB', self.cfg, 'maildb', DEFAULT_MAILDB_PATH, self.environ)
  @maildb_path.setter
  @locked
  def maildb_path(self, path):
    self._maildb_path = path
    self._maildb = None

  ##@file_property
  ##def maildb(self, path):
  @locked_property
  def maildb(self):
    path = self.maildb_path
    info("MailFiler: reload maildb %s", shortpath(path))
    return MailDB(path, readonly=False)

  @property
  @locked
  def msgiddb_path(self):
    ''' Compute maildb path on the fly.
    '''
    path = self._msgiddb_path
    if path is None:
      path = current_value('MESSAGEIDDB', self.cfg, 'msgiddb', DEFAULT_MSGIDDB_PATH, self.environ)
    return path
  @msgiddb_path.setter
  @locked
  def msgiddb_path(self, path):
    self._msgiddb_path = path
    self._msgiddb = None

  @locked_property
  def msgiddb(self):
    return NodeDBFromURL(self.msgiddb_path)

  @property
  @locked
  def maildir_path(self):
    path = self._maildir_path
    if path is None:
      path = current_value('MAILDIR', self.cfg, 'maildir', DEFAULT_MAILDIR_PATH, self.environ)
    return path
  @maildir_path.setter
  @locked
  def maildir_path(self, path):
    self._maildir_path = path

  @locked_property
  def rules_pattern(self):
    pattern \
      = self._rules_pattern \
      = current_value('MAILFILER_RULES_PATTERN', self.cfg, 'rules_pattern', DEFAULT_RULES_PATTERN, self.environ)
    X(".rules_pattern=%r", pattern)
    return pattern
  @rules_pattern.setter
  def rules_pattern(self, pattern):
    self._rules_pattern = pattern

  def maildir_from_folderspec(self, folderspec):
    return Pathname(longpath(folderspec, None,  ( (self.maildir_path + '/', '+'), )))

  def maildir_watcher(self, folderspec):
    ''' Return the singleton MaildirWatcher indicated by the `folderspec`.
    '''
    folderpath = self.maildir_from_folderspec(folderspec)
    watchers = self._maildir_watchers
    with self._lock:
      if folderpath not in watchers:
        watchers[folderpath] = WatchedMaildir(folderpath,
                                              self,
                                              rules_path=envsub(
                                                self.rules_pattern.format(
                                                  maildir=folderpath))
                                             )
    return watchers[folderpath]

  def monitor(self, folders, delay=None, justone=False, no_remove=False):
    ''' Monitor the specified `folders`, a list of folder spcifications.
	If `delay` is not None, poll the folders repeatedly with a
	delay of `delay` seconds between each pass.
    '''
    X("monitor: self.cfg=%s", self.cfg)
    X("maildb_path=%r", self.maildb_path)
    X("msgiddb_path=%r", self.msgiddb_path)
    X("rules_pattern=%r", self.rules_pattern)
    op_cfg = self.subcfg('monitor')
    try:
      while True:
        these_folders = folders
        if not these_folders:
          these_folders = op_cfg.get('folders', '').split()
        for folder in these_folders:
          wmdir = self.maildir_watcher(folder)
          with Pfx("%s", wmdir.shortname):
            self.sweep(wmdir, justone=justone, no_remove=no_remove)
        if delay is None:
          break
        debug("sleep %ds", delay)
        sleep(delay)
    except KeyboardInterrupt:
      watchers = self._maildir_watchers
      with self._lock:
        for wmdir in watchers.values():
          wmdir.close()
      return 1
    return 0

  @property
  def logdir(self):
    ''' The pathname of the directory in which log files are written.
    '''
    varlog = cs.env.varlog(self.environ)
    return os.path.join(varlog, 'mailfiler')

  def folder_logfile(self, folder_path):
    ''' Return path to log file associated with the named folder.
        TODO: ase on relative path from folder root, not just basename.
    '''
    return os.path.join(self.logdir, 'filer-%s.log' % (os.path.basename(folder_path)))

  def sweep(self, wmdir, justone=False, no_remove=False, logfile=None):
    ''' Scan a WatchedMaildir for messages to filter.
        Update the set of lurkers with any keys not removed to prevent
        filtering on subsequent calls.
        If `justone`, return after filing the first message.
    '''
    if logfile is None:
      logfile = self.folder_logfile(wmdir.path)
    with with_log(logfile, no_prefix=True):
      debug("sweep %s", wmdir.shortname)
      with Pfx("sweep %s", wmdir.shortname):
        nmsgs = 0
        skipped = 0
        with LogTime("all keys") as all_keys_time:
          for key in wmdir.keys(flush=True):
            with Pfx(key):
              if key in wmdir.lurking:
                debug("skip lurking key")
                skipped += 1
                continue
              nmsgs += 1

              with LogTime("key = %s", key, threshold=1.0, level=DEBUG):
                ok = self.file_wmdir_key(wmdir, key)
                if not ok:
                  warning("NOT OK, lurking key %s", key)
                  wmdir.lurk(key)
                  continue

                if no_remove:
                  info("no_remove: message not removed, lurking key %s", key)
                  wmdir.lurk(key)
                else:
                  debug("remove message key %s", key)
                  wmdir.remove(key)
                  wmdir.lurking.discard(key)
                if justone:
                  break

        if nmsgs or all_keys_time.elapsed >= 0.2:
          info("filtered %d messages (%d skipped) in %5.3fs",
               nmsgs, skipped, all_keys_time.elapsed)

  def save(self, target, msgfp):
    ''' Implementation for command line "save" function: save file to target.
    '''
    filer = MessageFiler(self)
    filer.message = message_from_file(msgfp)
    filer.message_path = None
    filer.save_target(target)
    return 0

  def file_wmdir_key(self, wmdir, key):
    ''' Accept a WatchedMaildir `wmdir` and a message `key`, return success.
        This does not remove a successfully filed message or update the lurking list.
    '''
    with LogTime("file key %s", key, threshold=1.0, level=DEBUG):
      M = wmdir[key]
      filer = MessageFiler(self)
      return filer.file(M, wmdir.rules, wmdir.keypath(key))

def maildir_from_name(mdirname, maildir_root, maildir_cache):
    ''' Return the Maildir derived from mdirpath.
    '''
    mdirpath = resolve_mail_path(mdirname, maildir_root)
    if mdirpath not in maildir_cache:
      maildir_cache[mdirpath] = Maildir(mdirpath, create=True)
    return maildir_cache[mdirpath]

def resolve_mail_path(mdirpath, maildir_root):
  ''' Return the full path to the requested mail folder.
  '''
  if not os.path.isabs(mdirpath):
    if mdirpath.startswith('./') or mdirpath.startswith('../'):
      mdirpath = os.path.abspath(mdirpath)
    else:
      mdirpath = os.path.join(maildir_root, mdirpath)
  return mdirpath

class MessageFiler(O):
  ''' A message filing object, filtering state information used during rule evaluation.
      .maildb   Current MailDB.
      .environ  Storage for variable settings.
      .addresses(header)
                Caching list of addresses from specified header.
  '''
 
  maildirs = {}

  def __init__(self, context, environ=None):
    ''' `context`: External state object, with maildb property, etc..
        `environ`: Mapping which supplies initial variable names.
                   Default from os.environ.
    '''
    if environ is None:
      environ = dict(context.environ)
    self.header_addresses = {}
    self.default_target = None
    self.context = context
    self.environ = dict(environ)
    self.targets = set()
    self.targets_also = set()
    self.labels = set()
    self.flags = O(alert=0,
                   flagged=False, passed=False, replied=False,
                   seen=False, trashed=False, draft=False)
    self.saved_to = []

  def file(self, M, rules, message_path=None):
    ''' File the specified message `M` according to the supplied `rules`.
        If specified and not None, the `message_path` parameter
        specifies the filename of the message, supporting hard linking
        the message into a Maildir.
    '''
    with with_log(os.path.join(cs.env.varlog(self.environ), envsub(DEFAULT_MAIN_LOG))):
      self.message = M
      self.message_path = message_path
      info( (u("%s %s") % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                 unrfc2047(M.get('subject', '_no_subject'))))
                 .replace('\n', ' ') )
      info("  " + self.format_message(M, "{short_from}->{short_recipients}"))
      info("  " + M.get('message-id', '<?>'))
      if self.message_path:
        info("  " + shortpath(self.message_path))

      try:
        rules.match(self)
      except Exception as e:
        exception("matching rules: %s", e)
        return False

      ok = True
      if not self.targets:
        if self.default_target:
          self.targets.add(self.default_target)
        else:
          error("no matching targets and no DEFAULT")
          ok = False

      self.targets.update(self.targets_also)
      if not self.targets:
        return False

      if self.labels:
        xlabels = set()
        for labelhdr in M.get_all('X-Label', ()):
          for label in labelhdr.split(','):
            label = label.strip()
            if label:
              xlabels.add(label)
        new_labels = self.labels - xlabels
        if new_labels:
          # add labels to message
          self.labels.update(new_labels)
          self.message_path = None
          M = message_from_string(M.as_string())
          M['X-Label'] = ", ".join( sorted(list(self.labels)) )
          self.message = M

      for target in sorted(list(self.targets)):
        with Pfx(target):
          try:
            self.save_target(target)
          except Exception as e:
            exception("saving to %r: %s", target, e)
            ok = False

      if self.flags.alert > 0:
        self.alert(self.flags.alert)

      return ok

  def apply_rule(self, R):
    ''' Apply this the rule `R` to this MessageFiler.
        The rule label, if any, is appended to the .labels attribute.
        Each action is applied to the state.
        Assignments update the .environ attribute.
        Targets accrue in the .targets attribute.
    '''
    M = self.message
    with Pfx(R.context):
      self.flags.alert = max(self.flags.alert, R.flags.alert)
      if R.label:
        self.labels.add(R.label)
      for action, arg in R.actions:
        try:
          if action == 'TARGET':
            if len(arg) == 1 and arg.isupper():
              flag_letter = arg
              if flag_letter == 'D':   self.flags.draft = True
              elif flag_letter == 'F': self.flags.flagged = True
              elif flag_letter == 'P': self.flags.passed = True
              elif flag_letter == 'R': self.flags.replied = True
              elif flag_letter == 'S': self.flags.seen = True
              elif flag_letter == 'T': self.flags.trashed = True
              else:
                warning("ignoring unsupported flag \"%s\"" % (flag_letter,))
            else:
              target = arg
              if R.flags.undelivered:
                self.targets_also.add(target)
              else:
                self.targets.add(target)
          elif action == 'ASSIGN':
            envvar, s = arg
            value = self.environ[envvar] = envsub(s, self.environ)
            debug("ASSIGN %s=%s", envvar, value)
            if envvar == 'LOGFILE':
              warning("LOGFILE= unimplemented at present")
              ## TODO: self.logto(value)
            elif envvar == 'DEFAULT':
              self.default_target = value
          else:
            raise RuntimeError("unimplemented action \"%s\"" % action)
        except (AttributeError, NameError):
          raise
        except Exception as e:
          warning("EXCEPTION %r", e)
          ##failed_actions.append( (action, arg, e) )
          raise

  @property
  def maildb(self):
    return self.context.maildb

  @property
  def msgiddb(self):
    return self.context.msgiddb

  def maildir(self, mdirpath):
    return self.context.maildir(mdirpath, self.environ)

  def resolve(self, foldername):
    return resolve_mail_path(foldername, self.MAILDIR)

  @property
  def groups(self):
    ''' The group mapping from the MailDB.
    '''
    return self.maildb.address_groups

  def group(self, group_name):
    ''' Return the set of addresses in the named group.
    '''
    G = self.groups.get(group_name)
    if G is None:
      warning("unknown group: %s", group_name)
      G = self.groups[group_name] = set()
    return G

  def addresses(self, *headers):
    ''' Return the core addresses from the current Message and supplied
        `headers`. Caches results for rapid rule evaluation.
    '''
    M = self.message
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

  def env(self, envvar, default):
    ''' Shorthand for environment lookup.
    '''
    return self.environ.get(envvar, default)

  @property
  def MAILDIR(self):
    return self.env('MAILDIR', os.path.join(self.env('HOME', None), 'mail'))

  def save_target(self, target):
    with Pfx("%s", target):
      if target.startswith('|'):
        # pipe message to shell command
        # NB: let the shell do the environment substitution, not us
        shcmd = target[1:]
        return self.save_to_pipe(['/bin/sh', '-xc', shcmd])

      if target.startswith('+'):
        # add header field values to groups
        m = re_ADDHEADER.match(target)
        if not m:
          error("match failure of re_ADDHEADER against %r", target)
          return None
        hdr = m.group(1)
        group_names = m.group(2).split(',')
        return self.save_header(hdr, group_names)

      target = envsub(target, self.process_environ())
      if '@' in target:
        # send message to email address
        return self.sendmail(target)
      else:
        # save message to Maildir or mbox
        mailpath = self.resolve(target)
        if not os.path.exists(mailpath):
          make_maildir(mailpath)
        # record the target folder
        self.saved_to.append(mailpath)
        if ismaildir(mailpath):
          mdir = Maildir(mailpath)
          maildir_flags = ''
          if self.flags.draft:   maildir_flags += 'D'
          if self.flags.flagged: maildir_flags += 'F'
          if self.flags.passed:  maildir_flags += 'P'
          if self.flags.replied: maildir_flags += 'R'
          if self.flags.seen:    maildir_flags += 'S'
          if self.flags.trashed: maildir_flags += 'T'
          return self.save_to_maildir(mdir,
                                      flags=maildir_flags)
        status = ''
        x_status = ''
        if self.flags.draft:   x_status += 'D'
        if self.flags.flagged: x_status += 'F'
        if self.flags.replied: status += 'R'
        if self.flags.passed:  x_status += 'P'
        if self.flags.seen:    x_status += 'S'
        if self.flags.trashed: x_status += 'T'
        return self.save_to_mbox(mailpath, status, x_status)

  def save_header(self, hdr, group_names):
    ''' Update maildb or msgiddb from message header.
        If a message-id type header, get the msgiddb node for each
        id and add the `group_names` to its GROUP field.
        Otherwise, extract all the addresses from the specified
        header and add to the maildb groups named by `group_names`.
    '''
    with Pfx("save_header(%s, %r)", hdr, group_names):
      if hdr in ('message-id', 'references', 'in-reply-to'):
        msgids = self.message[hdr].split()
        for msgid in msgids:
          debug("%s.GROUPs.update(%r)", msgid, group_names)
          msgid_node = self.msgiddb.make( ('MESSAGE_ID', msgid) )
          msgid_node.GROUPs.update(group_names)
      else:
        debug("%s.GROUPs.update(%r)", msgid, group_names)
        raise RuntimeError("need to pull addresses from hdr and add to address groups")

  def save_to_maildir(self, mdir, flags=''):
    ''' Save the current message to a Maildir unless we have already saved to
        this maildir.
    '''
    mdirpath = mdir.dir
    M = self.message
    path = self.message_path
    if path is None:
      savekey = mdir.save_message(M, flags=flags)
    else:
      savekey = mdir.save_filepath(path, flags=flags)
    savepath = mdir.keypath(savekey)
    if not path:
      self.message_path = savepath
    info("    OK %s" % (shortpath(savepath)))
    return savepath

  def save_to_mbox(self, mboxpath, status, x_status):
    M = self.message
    if len(status) > 0:
      M['Status'] = status
    if len(x_status) > 0:
      M['X-Status'] = x_status
    text = M.as_string(True)
    with open(mboxpath, "a") as mboxfp:
      mboxfp.write(text)
    info("    OK >> %s" % (shortpath(mboxpath)))

  def process_environ(self):
    ''' Compute the environment for a subprocess.
    '''
    lc_ = lambda hdr_name: hdr_name.lower().replace('-', '_')
    env = dict(self.environ)
    M = self.message
    # add header_foo for every Foo: header
    for hdr_name, hdr_value in M.items():
      env['header_' + lc_(hdr_name)] = hdr_value
    # add shortlist_foo for every Foo: address header
    MDB = self.maildb
    for hdr_name in 'from', 'to', 'cc', 'bcc', 'reply-to', 'errors_to':
      hdr_value = M.get(hdr_name)
      if hdr_value:
        env['shortlist_' + lc_(hdr_name)] = ','.join(MDB.header_shortlist(M, (hdr_name,)))
    # ... and the recipients, combined
    env['shortlist_to_cc_bcc'] = ','.join(MDB.header_shortlist(M, ('to', 'cc', 'bcc')))
    return env

  def save_to_pipe(self, argv, mfp=None):
    ''' Pipe a message to the command specific by `argv`.
        `mfp` is a file containing the message text.
        If `mfp` is None, use the text of the current message.
    '''
    if mfp is None:
      message_path = self.message_path
      if message_path:
        with open(message_path) as mfp:
          return self.save_to_pipe(argv, mfp=mfp)
      else:
        with TemporaryFile('w+') as mfp:
          mfp.write(str(self.message))
          mfp.flush()
          mfp.seek(0)
          return self.save_to_pipe(argv, mfp=mfp)
    retcode = subprocess.call(argv, env=self.process_environ(), stdin=mfp)
    info("    %s => | %s" % (("OK" if retcode == 0 else "FAIL"), argv))
    return retcode == 0

  def sendmail(self, address, mfp=None):
    ''' Dispatch a message to `address`.
        `mfp` is a file containing the message text.
        If `mfp` is None, use the text of the current message.
    '''
    return self.save_to_pipe([self.env('SENDMAIL', 'sendmail'), '-oi', address], mfp=mfp)

  @property
  def alert_format(self):
    ''' The format string for alert messages from $ALERT_FORMAT.
    '''
    return self.env('ALERT_FORMAT', 'MAILFILER: {short_from}->{short_recipients}: {subject}')

  def alert_message(self, M):
    ''' Return the alert message filled out with parameters from the Message `M`.
    '''
    try:
      msg = self.format_message(M, self.alert_format)
    except KeyError as e:
      error("alert_message: format=%r, message keys=%s: %s",
            fmt, ','.join(sorted(list(M.keys()))), e)
      msg = "MAILFILER: alert! format=%s" % (fmt,)
    return msg

  def format_message(self, M, fmt):
    ''' Compute the alert message for the message `M` using the supplied format string `fmt`.
    '''
    hmap = dict( [ (k.lower().replace('-', '_'), M[k]) for k in M.keys() ] )
    subj = unrfc2047(M.get('subject', '')).strip()
    if subj:
      hmap['subject'] = subj
    for hdr in ('from', 'to', 'cc', 'bcc', 'reply-to'):
      hmap['short_'+hdr.replace('-', '_')] = ",".join(self.maildb.header_shortlist(M, (hdr,)))
    hmap['short_recipients'] = ",".join(self.maildb.header_shortlist(M, ('to', 'cc', 'bcc')))
    for h, hval in list(hmap.items()):
      hmap[h] = ustr(hval)
    return u(fmt).format(**hmap)

  def alert(self, alert_level, alert_message=None):
    ''' Issue an alert with the specified `alert_message`.
        If missing or None, use self.alert_message(self.message).
	If `alert_level` is more than 1, prepend "-l alert_level"
	to the alert command line arguments.
    '''
    if alert_message is None:
      alert_message = self.alert_message(self.message)
    subargv = [ self.env('ALERT', 'alert') ]
    if alert_level > 1:
      subargv.extend( ['-l', str(alert_level)] )
    # tell alert how to open this message
    # TODO: parameterise so that we can open it with other tools
    if self.saved_to:
      try:
        msg_id = self.message['message-id']
      except KeyError:
        warning("no Message-ID !")
      else:
        if msg_id is None:
          warning("message-id is None!")
        else:
          msg_ids = [ msg_id for msg_id in msg_id.split() if len(msg_id) > 0 ]
          if msg_ids:
            msg_id = msg_ids[0]
            subargv.extend( ['-e',
                              'term',
                               '-e',
                                'mutt-open-message',
                                 '-f', self.saved_to[0], msg_id,
                             '--'] )
    subargv.append(alert_message)
    xit = subprocess.call(subargv)
    if xit != 0:
      warning("non-zero exit from alert: %d", xit)
    return xit

re_UNQWORD = re.compile(r'[^,\s]+')

# header[,header,...]:
re_HEADERLIST = re.compile(r'([a-z][\-a-z0-9]*(,[a-z][\-a-z0-9]*)*):', re.I)

# identifier=
re_ASSIGN = re.compile(r'([a-z]\w+)=', re.I)

# group membership test: (A|B|C|...)
# where A may be a WORD or @domain
# indicating an address group name or an address ending in @domain
re_WORD_s = '[a-z]\w+'
re_atDOM_s = '@[-\w]+(\.[-\w]+)+'
re_WORD_or_DOM_s = '(%s|%s)' % (re_WORD_s, re_atDOM_s)
re_INGROUP_s = r'\(\s*%s(\s*\|\s*%s)*\s*\)' % (re_WORD_or_DOM_s,
                                               re_WORD_or_DOM_s)
## print("re_INGROUP = %r" % (re_INGROUP_s), file=sys.stderr)
re_INGROUP = re.compile( re_INGROUP_s, re.I)

re_HEADERNAME_s = r'[a-z][\-a-z0-9]*'

# header[,header,...].func(
re_HEADERFUNCTION_s = r'(%s(,%s)*)\.(%s)\(' % (re_HEADERNAME_s, re_HEADERNAME_s, re_WORD_s)
re_HEADERFUNCTION = re.compile(re_HEADERFUNCTION_s, re.I)

# target syntax: add header values to named groups
# +header(group|...)
re_ADDHEADER_s = r'\+(%s)(%s)' % (re_HEADERNAME_s, re_INGROUP_s)
re_ADDHEADER = re.compile(re_ADDHEADER_s, re.I)

def parserules(fp):
  ''' Read rules from `fp`, yield Rules.
  '''
  if isinstance(fp, StringTypes):
    with open(fp) as rfp:
      for R in parserules(rfp):
        yield R
    return

  filename = getattr(fp, 'name', None)
  if filename is None:
    file_label = str(type(fp))
  else:
    file_label = shortpath(filename)
  info("PARSE RULES: %s", file_label)
  lineno = 0
  R = None
  for line in fp:
    lineno += 1
    with Pfx("%s:%d", file_label, lineno):
      if filename:
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
            raise ValueError("missing filename")
          subfilename = envsub(subfilename)
          if filename:
            subfilename = abspath_from_file(subfilename, filename)
          else:
            subfilename = os.path.abspath(subfilename)
          for R in parserules(subfilename):
            yield R
          continue

        # new rule
        R = Rule(filename=(filename if filename else file_label), lineno=lineno)

        m = re_ASSIGN.match(line, offset)
        if m:
          R.actions.append( ('ASSIGN', (m.group(1), line[m.end():])) )
          yield R
          R = None
          continue

        # leading optional '+' (continue, undelivered) or '=' (final)
        if line[offset] == '+':
          R.flags.halt = False
          R.flags.undelivered = True
          offset += 1
        elif line[offset] == '=':
          R.flags.halt = True
          offset += 1

        # leading '!' alert: multiple '!' raise the alert level
        while line[offset] == '!':
          R.flags.alert += 1
          offset += 1

        targets, offset = get_targets(line, offset)
        for target in targets:
          R.actions.append( ('TARGET', target) )

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
      condition_flags = O(invert=False)

      if not _ or offset == len(line):
        warning('no condition')
        continue

      if line[offset] == '!':
        condition_flags.invert = True
        _, offset = get_white(line, offset+1)
        if offset == len(line):
          warning('no condition after "!"')
          continue

      # . always matches - don't bother storing it as a test
      if line[offset:] == '.':
        continue

      # leading hdr1,hdr2.func(
      m = re_HEADERFUNCTION.match(line, offset)
      if m:
        header_names = tuple( H.lower() for H in m.group(1).split(',') if H )
        testfuncname = m.group(3)
        offset = m.end()
        _, offset = get_white(line, offset)
        if offset == len(line):
          raise ValueError("missing argument to header function")
        if line[offset] == '"':
          test_string, offset = get_qstr(line, offset)
          _, offset = get_white(line, offset)
          if offset == len(line) or line[offset] != ')':
            raise ValueError("missing closing parenthesis after header function argument")
          offset += 1
          _, offset = get_white(line, offset)
          if offset < len(line):
            raise ValueError("extra text after header function: %r" % (line[offset:],))
        else:
          raise ValueError("unexpected argument to header function, expected double quoted string")
        C = Condition_HeaderFunction(condition_flags, header_names, testfuncname, test_string)
      else:
        # leading hdr1,hdr2,...:
        m = re_HEADERLIST.match(line, offset)
        if m:
          header_names = tuple( H.lower() for H in m.group(1).split(',') if H )
          offset = m.end()
          if offset == len(line):
            raise ValueError("missing match after header names")
        else:
          header_names = ('to', 'cc', 'bcc')
        # headers:/regexp
        if line[offset] == '/':
          regexp = line[offset+1:]
          if regexp.startswith('^'):
            atstart = True
            regexp = regexp[1:]
          else:
            atstart = False
          C = Condition_Regexp(condition_flags, header_names, atstart, regexp)
        else:
          # headers:(group[|group...])
          m = re_INGROUP.match(line, offset)
          if m:
            group_names = set( w.strip().lower() for w in m.group()[1:-1].split('|') )
            offset = m.end()
            if offset < len(line):
              raise ValueError("extra text after groups: %s" % (line,))
            C = Condition_InGroups(condition_flags, header_names, group_names)
          else:
            if line[offset] == '(':
              raise ValueError("incomplete group match at: %s" % (line[offset:]))
            # just a comma separated list of addresses
            # TODO: should be RFC2822 list instead?
            addrkeys = [ coreaddr for realname, coreaddr in getaddresses( (line[offset:],) ) ]
            C = Condition_AddressMatch(condition_flags, header_names, addrkeys)

      R.conditions.append(C)

  if R is not None:
    yield R

def get_targets(s, offset):
  ''' Parse list of targets from the string `s` starting at `offset`.
      Return the list of targets strings and the new offset.
  '''
  targets = []
  while offset < len(s) and not s[offset].isspace():
    # "quoted-string"
    if s[offset] == '"':
      target, offset = get_qstr(s, offset)
    # +header(groups)
    elif s[offset] == '+':
      m = re_ADDHEADER.match(s, offset)
      if m:
        target = m.group()
        offset = m.end()
      else:
        error("parse failure, expected +header(groups) at %d: %s", offset, s)
        raise ValueError("syntax error")
    else:
      # unquoted word
      m = re_UNQWORD.match(s, offset)
      if m:
        target = m.group()
        offset = m.end()
      else:
        error("parse failure at %d: %s", offset, s)
        raise ValueError("syntax error")
    targets.append(target)
    if offset < len(s) and s[offset] == ',':
      offset += 1
  return targets, offset

class _Condition(O):
  
  def __init__(self, flags, header_names):
    self.flags = flags
    self.header_names = header_names

  def match(self, filer):
    status = False
    M = filer.message
    for header_name in self.header_names:
      for header_value in M.get_all(header_name, ()):
        if self.test_value(filer, header_name, header_value):
          status = True
          break
    if self.flags.invert:
      status = not status
    return status

class Condition_Regexp(_Condition):

  def __init__(self, flags, header_names, atstart, regexp):
    _Condition.__init__(self, flags, header_names)
    self.atstart = atstart
    self.regexp = re.compile(regexp)
    self.regexptxt = regexp

  def test_value(self, filer, header_name, header_value):
    if self.atstart:
      return self.regexp.match(header_value)
    return self.regexp.search(header_value)

class Condition_AddressMatch(_Condition):

  def __init__(self, flags, header_names, addrkeys):
    _Condition.__init__(self, flags, header_names)
    self.addrkeys = tuple( k for k in addrkeys if len(k) > 0 )

  def test_value(self, filer, header_name, header_value):
    for address in filer.addresses(header_name):
      address_lc = address.lower()
      for key in self.addrkeys:
        if address_lc == key.lower():
          return True
    return False

class Condition_InGroups(_Condition):

  def __init__(self, flags, header_names, group_names):
    _Condition.__init__(self, flags, header_names)
    self.group_names = group_names

  def test_value(self, filer, header_name, header_value):
    if header_name.lower() in ('message-id', 'references', 'in-reply-to'):
      msgiddb = self.filer.msgiddb
      msgids = [ v for v in header_value.split() if v ]
      for msgid in msgids:
        msgid_node = msgiddb.get( ('MESSAGE_ID', msgid) )
        for group_name in self.group_names:
          if group_name.startswith('@'):
            if msgid.endswith(groupname+'>'):
              debug("match %s to %s", msgid, group_name)
              return True
          elif msgid_node:
              if group_name in msgid_node.GROUPs:
                debug("match %s to (%s)", msgid, group_name)
                return True
    else:
      for address in filer.addresses(header_name):
        for group_name in self.group_names:
          if group_name.startswith('@'):
            # address ending in @foo
            if address.endswith(group_name):
              debug("match %s to %s", address, group_name)
              return True
          elif address.lower() in filer.group(group_name):
            # address in group "foo"
            debug("match %s to (%s)", address, group_name)
            return True
    return False

class Condition_HeaderFunction(_Condition):

  def __init__(self, flags, header_names, funcname, test_string):
    _Condition.__init__(self, flags, header_names)
    self.funcname = funcname
    self.test_string = test_string
    test_method = 'test_func_' + funcname
    try:
      self.test_func = getattr(self, test_method)
    except AttributeError:
      raise ValueError("invalid header function .%s()" % (funcname,))

  def test_value(self, filer, header_name, header_value):
    return self.test_func(filer, header_name, header_value)

  def test_func_contains(self, filer, header_name, header_value):
    return self.test_string in header_value

_FilterReport = namedtuple('FilterReport',
                          'rule matched saved_to ok_actions failed_actions')
def FilterReport(rule, matched, saved_to, ok_actions, failed_actions):
  if not matched:
    if saved_to:
      raise RuntimeError("matched(%r) and not saved_to(%r)" % (matched, saved_to))
  return _FilterReport(rule, matched, saved_to, ok_actions, failed_actions)

class Rule(O):

  def __init__(self, filename, lineno):
    self.filename = filename
    self.lineno = lineno
    self.conditions = slist()
    self.actions = slist()
    self.flags = O(alert=0, halt=False, undelivered=False)
    self.label = ''

  @property
  def context(self):
    return "%s:%d" % (shortpath(self.filename), self.lineno)

  def match(self, filer):
    ''' Test the message in filer against this rule.
    '''
    # all conditions must match
    for C in self.conditions:
      if not C.match(filer):
        return False
    return True

class Rules(list):
  ''' Simple subclass of list storing rules, with methods to load
      rules and filter a message using the rules.
  '''

  def __init__(self, rules_file=None):
    list.__init__(self)
    self.vars = {}
    self.rule_files = set()
    if rules_file is not None:
      with Pfx(rules_file):
        self.load(rules_file)

  def load(self, fp):
    ''' Import an open rule file.
    '''
    new_rules = list(parserules(fp))
    self.rule_files.update( R.filename for R in new_rules )
    self.extend(new_rules)

  def match(self, filer):
    ''' Match the current message (filer.message) against the rules.
        Update filer for matching rules.
    '''
    for R in self:
      with Pfx(R.context):
        if R.match(filer):
          filer.apply_rule(R)
          if R.flags.halt:
            done = True
            break

class WatchedMaildir(O):
  ''' A class to monitor a Maildir and filter messages.
  '''

  def __init__(self, mdir, context, rules_path=None):
    self.mdir = Maildir(resolve_mail_path(mdir, os.environ['MAILDIR']))
    self.context = context
    if rules_path is None:
      # default to looking for .mailfiler inside the Maildir
      rules_path = os.path.join(self.mdir.dir, '.mailfiler')
    self._rules_paths = [ rules_path ]
    self._rules_lock = Lock()
    self.lurking = set()
    self.flush()
    warning("%d rules", len(self.rules))

  def __str__(self):
    return "<WatchedMaildir modes=%s, %d rules, %d lurking>" \
           % (self.shortname,
              len(self.rules),
              len(self.lurking))

  def close(self):
    self.flush()
    self.mdir.close()

  @property
  def shortname(self):
    return self.mdir.shortname

  @property
  def path(self):
    return self.mdir.dir

  def keys(self, flush=False):
    return self.mdir.keys(flush=flush)

  def __getitem__(self, key):
    return self.mdir[key]

  def keypath(self, key):
    return self.mdir.keypath(key)

  def remove(self, key):
    return self.mdir.remove(key)

  def flush(self):
    ''' Forget state.
        The set of lurkers is emptied.
    '''
    self.lurking = set()

  def lurk(self, key):
    info("lurk %s", key)
    self.lurking.add(key)

  def unlurk(self, key):
    info("unlurk %s", key)
    self.lurking.remove(key)

  @files_property
  def rules(self, rules_paths):
    # base file is at index 0
    path0 = rules_paths[0]
    R = Rules(path0)
    # produce rules file list with base file at index 0
    paths = [ path0 ] + [ path for path in R.rule_files if path != path0 ]
    return paths, R

if __name__ == '__main__':
  sys.exit(main(sys.argv))

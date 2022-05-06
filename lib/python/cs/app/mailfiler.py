#!/usr/bin/python
#
# Handler for rulesets similar to the format of cats2procmailrc(1cs).
#       - Cameron Simpson <cs@cskk.id.au> 22may2011
#

''' Mailfiler is my email message filing system.

    It monitors multiple Maildir folders for new messages
    and files them according to various easy to write rules.
    Its use is described fully in the mailfiler(1cs) manual entry.

    The rules files are broadly quite simple and described fully
    in the mailfiler(5cs) manual entry.
    The rules are normally single line rules of the form:

        target,... label condition

    If the rule should always fire
    then the condition may be omitted.

    The targets may be
    mail folders (file the message in the named folder),
    assignment statements (set an environment variable),
    email addresses (send the message to the specified address)
    or some other special purpose actions.

    The conditions are usually tests of the header email addresses
    including whether an address is a member of some group/alias
    but may also test various other things about the message headers.
'''

from __future__ import print_function
from collections import namedtuple
from copy import deepcopy
from datetime import datetime, timezone
from email import message_from_file
from email.header import decode_header, make_header
from email.utils import getaddresses
from getopt import getopt, GetoptError
import logging
import os
import os.path
import re
import sys
import subprocess
from tempfile import TemporaryFile
from threading import Lock, RLock
import time
from time import sleep
from types import SimpleNamespace as NS

from cs.app.maildb import MailDB
from cs.cmdutils import BaseCommand
from cs.configutils import ConfigWatcher
from cs.deco import cachedmethod, fmtdoc
import cs.env
from cs.env import envsub
from cs.excutils import LogExceptions
from cs.filestate import FileState
from cs.fileutils import abspath_from_file, longpath, Pathname
import cs.lex
from cs.lex import (
    get_white, get_nonwhite, skipwhite, get_other_chars, get_qstr,
    match_tokens, get_delimited
)
from cs.logutils import (
    with_log, debug, status, info, track, warning, error, exception, LogTime
)
from cs.mailutils import (
    RFC5322_DATE_TIME, Maildir, message_addresses, modify_header, shortpath,
    ismaildir, make_maildir
)
from cs.obj import singleton
from cs.pfx import Pfx, pfx_method
from cs.py.func import prop
from cs.py.modules import import_module_name
from cs.rfc2047 import unrfc2047
from cs.seq import first
from cs.threads import locked

__version__ = '20200719-post'

DISTINFO = {
    'description':
    "email message filing system which monitors multiple inbound Maildir folders",
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Topic :: Communications :: Email :: Filters",
    ],
    'install_requires': [
        'cs.app.maildb',
        'cs.cmdutils>=20210404',
        'cs.configutils',
        'cs.deco',
        'cs.env',
        'cs.excutils',
        'cs.filestate',
        'cs.fileutils',
        'cs.lex',
        'cs.logutils',
        'cs.mailutils',
        'cs.obj',
        'cs.pfx',
        'cs.py.func',
        'cs.py.modules',
        'cs.rfc2047',
        'cs.seq',
        'cs.threads',
    ],
    'entry_points': {
        'console_scripts': [
            'mailfiler = cs.app.mailfiler:main',
        ],
    },
}

DEFAULT_MAIN_LOG = 'mailfiler/main.log'
DEFAULT_RULES_PATTERN = '$HOME/.mailfiler/{maildir.basename}'
DEFAULT_MAILFILER_RC = '$HOME/.mailfilerrc'
DEFAULT_MAILDB_PATH = '$HOME/.maildb.csv'
DEFAULT_MSGIDDB_PATH = '$HOME/var/msgiddb.csv'
DEFAULT_MAILDIR_PATH = '$MAILDIR'

SELF_FOLDER = '.'

def main(argv=None):
  ''' Mailfiler main programme.
  '''
  return MailFilerCommand(argv).run()

class MailFilerCommand(BaseCommand):
  ''' MailFiler commandline implementation.
  '''

  GETOPT_SPEC = 'R:'

  USAGE_KEYWORDS = {'DEFAULT_RULES_PATTERN': DEFAULT_RULES_PATTERN}

  USAGE_FORMAT = r'''Usage: {cmd} [-R rules_pattern] subopt [subopt-args...]
      -R rules_pattern
          Specify the rules file pattern used to specify rules files from
          Maildir names.
          Default: {DEFAULT_RULES_PATTERN}'''

  def apply_defaults(self):
    ''' Set up default options.
    '''
    options = self.options
    options.stdin = sys.stdin
    options.config_path = None
    options.maildb_path = None
    options.msgiddb_path = None
    options.maildir = None
    options.rules_pattern = DEFAULT_RULES_PATTERN

  def apply_opt(self, opt, val):
    ''' Apply a command line option.
    '''
    if opt == '-R':
      self.options.rules_pattern = val
    else:
      raise RuntimeError("unhandled option: %s %r" % (opt, val))

  def cmd_monitor(self, argv):
    ''' Usage: {cmd} [-1] [-d delay] [-n] [maildirs...]
          Monitor Maildirs for new messages and file them.
          -1  File at most 1 message per Maildir.
          -d delay
              Delay between runs in seconds.
              Default is to make only one run over the Maildirs.
          -n  No remove. Keep filed messages in the origin Maildir.
    '''
    options = self.options
    options.justone = False
    options.delay = None
    options.no_remove = False
    badopts = False
    self.popopts(
        argv,
        _1='justone',
        d_=('delay', int, lambda delay: delay > 0),
        n='justone'
    )
    if badopts:
      raise GetoptError("invalid arguments")
    mdirpaths = argv
    if not mdirpaths:
      mdirpaths = None
    return self.mailfiler().monitor(
        mdirpaths,
        delay=options.delay,
        justone=options.justone,
        no_remove=options.no_remove,
        upd=self.loginfo.upd
    )

  def cmd_save(self, argv):
    ''' Usage: {cmd} target[,target...] <message
          Save a message from standard input to the specified targets.

        Save message to the `targets`,
        a single command line argument of the form
        of a mailfiler targets field.
    '''
    options = self.options
    badopts = False
    if not argv:
      warning("missing targets")
      badopts = True
    else:
      targets = argv.pop(0)
      if argv:
        warning("extra arguments after targets: %r", argv)
        badopts = True
    message_fp = options.stdin
    if message_fp.isatty():
      warning("stdin %s: will not read from a tty", message_fp)
      badopts = True
    if badopts:
      raise GetoptError("invalid arguments")
    return self.mailfiler().save(targets, message_fp)

  def cmd_report(self, argv):
    ''' Usage: {cmd} <message
          Report various things about a message from standard input.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    return self.mailfiler().report(self.options.stdin)

  def mailfiler(self):
    ''' Prepare a `MailFiler` from `self.options`.
    '''
    return MailFiler(
        **{
            k: v
            for k, v in self.options.__dict__.items()
            if k in ('config_path', 'environ',
                     'rules_pattern') and v is not None
        }
    )

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

def scrub_header(value):
  ''' "Scrub" a header value.
      Presently this means to undo RFC2047 encoding where possible.
  '''
  new_value = unrfc2047(value)
  if new_value != value:
    new_value = make_header(decode_header(value))
  return new_value

class MailFiler(NS):
  ''' A mail filer.
  '''

  def __init__(self, config_path=None, environ=None, rules_pattern=None):
    ''' Initialise the MailFiler.

        Parameters:
        * `config_path`: location of config file, default from `DEFAULT_MAILFILER_RC`.
        * `environ`: initial environment, default from `os.environ`.
        * `rules_pattern`: rules pattenr, default from `envsub(DEFAULT_RULES_PATTERN)`
    '''
    self.config_path = config_path or envsub(DEFAULT_MAILFILER_RC)
    self._cfg = ConfigWatcher(self.config_path)
    self.environ = environ or dict(os.environ)
    self.rules_pattern = rules_pattern or current_value(
        'MAILFILER_RULES_PATTERN', self.cfg, 'rules_pattern',
        DEFAULT_RULES_PATTERN, self.environ
    )
    self._lock = RLock()
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
    ''' The [DEFAULT] configuration section.
    '''
    return self._cfg['DEFAULT']

  def subcfg(self, section_name):
    ''' Return a section of the configuration.
    '''
    return self._cfg[section_name]

  @property
  def cfg_monitor(self):
    ''' The [monitor] configuration section.
    '''
    return self.subcfg('monitor')

  @property
  @locked
  @cachedmethod
  def maildb_path(self):
    ''' Compute maildb path on the fly.
    '''
    return current_value(
        'MAILDB', self.cfg, 'maildb', DEFAULT_MAILDB_PATH, self.environ
    )

  @maildb_path.setter
  @locked
  def maildb_path(self, path):
    ''' The path to the email address database.
    '''
    self._maildb_path = path
    self._maildb = None

  @property
  @locked
  @cachedmethod
  def maildb(self):
    ''' The email address database.
    '''
    path = self.maildb_path
    track("MailFiler: reload maildb %s", shortpath(path))
    return MailDB(path, readonly=False)

  @property
  @locked
  def msgiddb_path(self):
    ''' Compute maildb path on the fly.
    '''
    path = self._msgiddb_path
    if path is None:
      path = current_value(
          'MESSAGEIDDB', self.cfg, 'msgiddb', DEFAULT_MSGIDDB_PATH,
          self.environ
      )
    return path

  @msgiddb_path.setter
  @locked
  def msgiddb_path(self, path):
    ''' The path to the Message-ID database.
    '''
    self._msgiddb_path = path
    self._msgiddb = None

  @property
  @locked
  @cachedmethod
  def msgiddb(self):
    ''' The Message-ID database.
    '''
    return NodeDBFromURL(self.msgiddb_path)

  @property
  @locked
  def maildir_path(self):
    ''' The base Maildir path.
    '''
    path = self._maildir_path
    if path is None:
      path = current_value(
          'MAILDIR', self.cfg, 'maildir', DEFAULT_MAILDIR_PATH, self.environ
      )
    return path

  @maildir_path.setter
  @locked
  def maildir_path(self, path):
    self._maildir_path = path

  def maildir_from_folderspec(self, folderspec):
    ''' Return the Maildir from `folderspec`.
    '''
    return Pathname(
        longpath(folderspec, None, ((self.maildir_path + '/', '+'),))
    )

  def maildir_watcher(self, folderspec):
    ''' Return the singleton WatchedMaildir indicated by the `folderspec`.
    '''
    folderpath = self.maildir_from_folderspec(folderspec)
    rules_path = envsub(self.rules_pattern.format(maildir=folderpath))
    watcher_key = os.path.realpath(folderpath), os.path.realpath(rules_path)
    watchers = self._maildir_watchers
    with self._lock:
      _, wmdir = singleton(
          watchers, watcher_key, WatchedMaildir, (folderpath, self),
          {'rules_path': rules_path}
      )
    return wmdir

  def monitor(
      self, folders, *, delay=None, justone=False, no_remove=False, upd=None
  ):
    ''' Monitor the specified `folders`, a list of folder spcifications.
        If `delay` is not None, poll the folders repeatedly with a
        delay of `delay` seconds between each pass.
    '''
    debug("cfg=%s", self.cfg)
    debug("maildb_path=%r", self.maildb_path)
    debug("msgiddb_path=%r", self.msgiddb_path)
    debug("rules_pattern=%r", self.rules_pattern)
    op_cfg = self.subcfg('monitor')
    idle = 0
    try:
      while True:
        these_folders = folders
        if not these_folders:
          these_folders = op_cfg.get('folders', '').split()
        nmsgs = 0
        for folder in these_folders:
          wmdir = self.maildir_watcher(folder)
          with Pfx(wmdir.shortname):
            try:
              nmsgs += self.sweep(
                  wmdir, justone=justone, no_remove=no_remove, upd=upd
              )
            except KeyboardInterrupt:
              raise
            except Exception as e:
              exception("exception during sweep: %s", e)
        if nmsgs > 0:
          idle = 0
        if delay is None:
          break
        if upd is not None:
          if idle > 0:
            status("sleep %ds; idle %ds", delay, idle)
          else:
            status("sleep %ds", delay)
        sleep(delay)
        idle += delay
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
    varlog = cs.env.LOGDIR(self.environ)
    return os.path.join(varlog, 'mailfiler')

  def folder_logfile(self, folder_path):
    ''' Return path to log file associated with the named folder.
        TODO: base on relative path from folder root, not just basename.
    '''
    return os.path.join(
        self.logdir, '%s.log' % (os.path.basename(folder_path))
    )

  def sweep(
      self, wmdir, *, justone=False, no_remove=False, logfile=None, upd=None
  ):
    ''' Scan a WatchedMaildir for messages to filter.
        Return the number of messages processed.

        Update the set of lurkers with any keys not removed to prevent
        filtering on subsequent calls.
        If `justone`, return after filing the first message.
    '''
    if logfile is None:
      logfile = self.folder_logfile(wmdir.path)
    with with_log(logfile, no_prefix=True):
      debug("sweep %s", wmdir.shortname)
      nmsgs = 0
      skipped = 0
      with LogTime("all keys") as all_keys_time:
        for key in list(wmdir.keys(flush=True)):
          if upd:
            status(key)
          if key in wmdir.lurking:
            track("skip lurking key %r", key)
            skipped += 1
            continue
          if key in wmdir.filed:
            debug("skip already filed key %r", key)
            skipped += 1
            continue
          nmsgs += 1
          with LogTime("key = %s", key, threshold=1.0, level=logging.DEBUG):
            ok = self.file_wmdir_key(wmdir, key)
            if not ok:
              warning("NOT OK, lurking key %s", key)
              wmdir.lurk(key)
              continue
            if key in wmdir.filed:
              track("message remains in this folder")
              continue
            if no_remove:
              track("no_remove: message not removed, lurking key %s", key)
              wmdir.lurk(key)
            else:
              debug("remove message key %s", key)
              wmdir.remove(key)
              wmdir.lurking.discard(key)
            if justone:
              break
      if nmsgs or all_keys_time.elapsed >= 0.2:
        track(
            "filtered %d messages (%d skipped) in %5.3fs", nmsgs, skipped,
            all_keys_time.elapsed
        )
    return nmsgs

  def save(self, targets, msgfp):
    ''' Implementation for command line "save" function: save file to target.
    '''
    Ts, offset = get_targets(targets, 0)
    if offset != len(targets):
      raise ValueError("invalid target specifications: %r" % (targets,))
    filer = MessageFiler(self)
    filer.message = message_from_file(msgfp)
    filer.message_path = None
    for T in Ts:
      T.apply(filer)
    filer.save_message()
    return 0

  @staticmethod
  def report(msgfp):
    ''' Implementation for command line "report" function: report on message.
    '''
    message = message_from_file(msgfp)
    for s in message.get_all('subject', ()):
      print('Subject:', repr(s))
      uqs = unrfc2047(s)
      if s != uqs:
        print('  ==>', repr(uqs))
    for hdr in 'from', 'to', 'cc', 'bcc', 'reply-to':
      for s in message.get_all(hdr, ()):
        print(hdr.title() + ':', repr(s))
        uqs = unrfc2047(s)
        if s != uqs:
          print('  ==>', repr(uqs))
    return 0

  def file_wmdir_key(self, wmdir, key):
    ''' Accept a WatchedMaildir `wmdir` and a message `key`, return success.

        This does not remove a successfully filed message or update
        the lurking list.
    '''
    with LogTime("file key %s", key, threshold=1.0, level=logging.DEBUG):
      try:
        M = wmdir[key]
      except KeyError as e:
        warning("unknown key: %s", e)
        return False
      filer = MessageFiler(self)
      ok = filer.file(M, wmdir.rules, wmdir.keypath(key))
      if ok:
        if filer.save_to_self:
          wmdir.filed.add(key)
      return ok

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

def save_to_folderpath(folderpath, M, message_path, flags):
  ''' Save the Message `M` to the resolved `folderpath`.

      Parameters:
      * `folderpath`: the path to the target mail folder.
      * `M`: the message to save.
      * `message_path`: pathname of existing message file, allowing
        hardlinking to new maildir if not `None`.
      * `flags`: save flags as from MessageFiler.flags
  '''
  if not os.path.exists(folderpath):
    make_maildir(folderpath)
  if ismaildir(folderpath):
    # save to Maildir
    mdir = Maildir(folderpath)
    maildir_flags = ''
    if flags.draft:
      maildir_flags += 'D'
    if flags.flagged:
      maildir_flags += 'F'
    if flags.passed:
      maildir_flags += 'P'
    if flags.replied:
      maildir_flags += 'R'
    if flags.seen:
      maildir_flags += 'S'
    if flags.trashed:
      maildir_flags += 'T'
    if message_path is None:
      savekey = mdir.save_message(M, flags=maildir_flags)
    else:
      savekey = mdir.save_filepath(message_path, flags=maildir_flags)
    savepath = mdir.keypath(savekey)
    track("    OK %s" % (shortpath(savepath)))
    if message_path is None:
      # update saved message for hard linking
      message_path = savepath
  else:
    # save to mbox
    status = ''
    x_status = ''
    if flags.draft:
      x_status += 'D'
    if flags.flagged:
      x_status += 'F'
    if flags.replied:
      status += 'R'
    if flags.passed:
      x_status += 'P'
    if flags.seen:
      x_status += 'S'
    if flags.trashed:
      x_status += 'T'
    if status:
      M['Status'] = status
    if x_status:
      M['X-Status'] = x_status
    with LogExceptions():
      text = M.as_string(True).replace('\nFrom ', '\n>From ')
    with open(folderpath, "a") as mboxfp:
      mboxfp.write(text)
    track("    OK >> %s" % (shortpath(folderpath)))
  return message_path

class MessageFiler(NS):
  ''' A message filing object, filtering state information used during rule evaluation.

      Attributes:
      * `.maildb`: Current MailDB.
      * `.environ`: Storage for variable settings.
      * `.addresses(header)`: Caching list of addresses from specified header.
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
    self.context = context
    self.environ = dict(environ)
    self.matched_rules = []
    self.labels = set()
    self.flags = NS(
        alert=0,
        flagged=False,
        passed=False,
        replied=False,
        seen=False,
        trashed=False,
        draft=False
    )
    self.alert_rule = None
    self.save_to_folders = set()
    self.save_to_addresses = set()
    self.save_to_cmds = []
    self.save_to_self = False

  def file(self, M, rules, message_path=None):
    ''' File the specified message `M` according to the supplied `rules`.
        If specified and not `None`, the `message_path` parameter
        specifies the filename of the message, supporting hard linking
        the message into a Maildir.
    '''
    with with_log(os.path.join(cs.env.LOGDIR(self.environ),
                               envsub(DEFAULT_MAIN_LOG))):
      self.message = M
      self.message_path = message_path
      track(
          (
              "%s %s: %s" % (
                  time.strftime("%Y-%m-%d %H:%M:%S"),
                  self.format_message(M, "{short_from}->{short_recipients}"),
                  unrfc2047(M.get('subject', '_no_subject'))
              )
          ).replace('\n', ' ')
      )
      msg_id = M.get('message-id', '<?>').replace('\n', ' ')
      if message_path:
        track("  " + shortpath(self.message_path) + " " + msg_id)
      else:
        track("  " + msg_id)
      # match the rules, gathering labels and save destinations
      try:
        rules.match(self)
      except Exception as e:
        exception("matching rules: %s", e)
        return False

      # apply additional targets from $ALERT_TARGETS, if any
      if self.flags.alert:
        alert_targets = self.environ.get('ALERT_TARGETS', '')
        if alert_targets:
          try:
            Ts, offset = get_targets(alert_targets, 0)
            offset = skipwhite(alert_targets, offset)
            if offset < len(alert_targets):
              raise ValueError(
                  'unparsed $ALERT_TARGETS text: %r' % alert_targets[offset:]
              )
          except Exception as e:
            error('parsing $ALERT_TARGETS: %s', e)
          else:
            for T in Ts:
              T.apply(self)

      # use default destination if no save destinations chosen
      if (not self.save_to_folders and not self.save_to_addresses
          and not self.save_to_cmds):
        default_targets = self.env('DEFAULT', '')
        if not default_targets:
          error("no matching targets and no $DEFAULT")
          return False
        info(
            "    no destinations, falling back to $DEFAULT=%r", default_targets
        )
        try:
          Ts, offset = get_targets(default_targets, 0)
          offset = skipwhite(default_targets, offset)
          if offset < len(default_targets):
            raise ValueError(
                'unparsed $DEFAULT text: %r' % default_targets[offset:]
            )
        except Exception as e:
          error('parsing $DEFAULT: %s', e)
        else:
          for T in Ts:
            T.apply(self)

      # apply labels
      if self.labels:
        xlabels = set()
        for labelhdr in M.get_all('X1-Label', ()):
          for label in labelhdr.split(','):
            label = label.strip()
            if label:
              xlabels.add(label)
        new_labels = self.labels - xlabels
        if new_labels:
          # add labels to message, forget pathname of original file
          self.labels.update(new_labels)
          self.modify('X-Label', ', '.join(sorted(list(self.labels))))

      rcvd = []
      if self.message_path:
        rcvd.append("from " + shortpath(self.message_path))
      rcvd.append("by " + type(self).__module__)
      ## leaks privacy ## rcvd_for_list = []
      ## leaks privacy ## for folder in sorted(self.save_to_folders):
      ## leaks privacy ##   rcvd_for_list.append(shortpath(folder))
      ## leaks privacy ## for address in sorted(self.save_to_addresses):
      ## leaks privacy ##   rcvd_for_list.append(address)
      ## leaks privacy ## rcvd.append("for " + ','.join(rcvd_for_list) if rcvd_for_list else '')
      dtutc = datetime.now(timezone.utc)
      dt = dtutc.astimezone()
      rcvd_datetime = dt.strftime(RFC5322_DATE_TIME)

      M.add_header('Received', '\n        '.join(rcvd) + '; ' + rcvd_datetime)
      self.message_path = None

      # leaks privacy
      ##for R in self.matched_rules:
      ##  M.add_header('X-Matched-Mailfiler-Rule', str(R))
      for R in self.matched_rules:
        if any(map(lambda T: not isinstance(T, Target_Assign), R.targets)):
          track("    MATCH %s", R)

      return self.save_message()

  def save_message(self):
    ''' Perform the message save step based on the current filer state.
        This is separated out to support the command line "save target" operation.
    '''
    ok = True
    # issue arrival alert
    if self.flags.alert > 0:
      self.alert(self.flags.alert)
    # save message to folders
    for folder in sorted(self.save_to_folders):
      try:
        folderpath = self.resolve(folder)
        save_to_folderpath(
            folderpath, self.message, self.message_path, self.flags
        )
      except Exception as e:
        exception("saving to folder %r: %s", folder, e)
        ok = False
    # forward message
    if self.save_to_addresses:
      sender = self.env('EMAIL', None)
      if sender is None:
        error(
            "no $EMAIL, required to set Sender-related fields in forwarded messages"
        )
        ok = False
      else:
        # change who@where to <who@where> for Return-Path etc
        if not sender.startswith('<') or not sender.endswith('>'):
          sender = '<' + sender + '>'
        # create special message copy with adjusted sender fields
        fwd_message = deepcopy(self.message)
        for hdr_name in 'Sender', 'Errors-To', 'Return-Path':
          modify_header(fwd_message, hdr_name, sender)
        # remove delivery loop detection headers
        modify_header(fwd_message, 'Delivered-To', ())
        with TemporaryFile('w+') as fwd_mfp:
          fwd_mfp.write(str(fwd_message))
          fwd_mfp.flush()
          for address in sorted(self.save_to_addresses):
            fwd_mfp.seek(0)
            try:
              self.sendmail(address, mfp=fwd_mfp, sender=sender)
            except Exception as e:
              exception("forwarding to address %r: %s", address, e)
              ok = False
    # pipeline message
    for shcmd, shenv in self.save_to_cmds:
      try:
        self.save_to_pipe(['/bin/sh', '-c', shcmd], shenv)
      except Exception as e:
        exception("piping to %r: %s", shcmd, e)
        ok = False
    return ok

  def modify(self, hdr, new_value, always=False):
    ''' Modify the value of the named header `hdr`
        to the new value `new_value` using cs.mailutils.modify_header.
        `new_value` may be a string or an iterable of strings.
        If headers were changed, forget self.message_path.
    '''
    if modify_header(self.message, hdr, new_value, always=always):
      self.message_path = None
      self.header_addresses = {}

  def apply_rule(self, R):
    ''' Apply this the rule `R` to this MessageFiler.
        The rule label, if any, is appended to the .labels attribute.
        Each target is applied to the state.
    '''
    with Pfx(R.context):
      self.matched_rules.append(R)
      self.flags.alert = max(self.flags.alert, R.flags.alert)
      if R.label:
        self.labels.add(R.label)
      for T in R.targets:
        try:
          T.apply(self)
        except (AttributeError, NameError):
          raise
        except Exception as e:
          exception("EXCEPTION %r", e)
          ##failed_actions.append( (action, arg, e) )
          raise

  @property
  def maildb(self):
    ''' The Mail Address database.
    '''
    return self.context.maildb

  @property
  def msgiddb(self):
    ''' The Mesaage-ID database.
    '''
    return self.context.msgiddb

  def maildir(self, mdirpath):
    ''' Return the Maildir for `mdirpath`.
    '''
    return self.context.maildir(mdirpath, self.environ)

  def resolve(self, foldername):
    ''' Resolve a mail `foldername` against the filer's `MAILDIR` attribute.
    '''
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
      hamap[header] = set([A for N, A in message_addresses(M, (header,))])
    return hamap[header]

  def env(self, envvar, default):
    ''' Shorthand for environment lookup.
    '''
    return self.environ.get(envvar, default)

  @property
  def MAILDIR(self):
    ''' The base folder for Maildirs.
    '''
    return self.env('MAILDIR', os.path.join(self.env('HOME', None), 'mail'))

  def learn_header_addresses(self, header_names, *group_names):
    ''' Update maildb groups with addresses from message headers.
        Extract all the addresses from the specified
        headers and add to the maildb groups named by `group_names`.
    '''
    with Pfx("save_header_addresses(%r, %r)", header_names, group_names):
      self.maildb.importAddresses_from_message(
          self.message, group_names, header_names=header_names
      )

  def learn_message_ids(self, header_names, *group_names):
    ''' Update msgiddb groups with message-ids from message headers.
    '''
    with Pfx("save_message_ids(%r, %r)", header_names, group_names):
      M = self.message
      msgids = set()
      for header_name in header_names:
        for hdr_body in M.get - all(header_name, ()):
          msgids.update(hdr_body.split())
      for msgid in sorted(msgids):
        debug("%s.GROUPs.update(%r)", msgid, group_names)
        msgid_node = self.msgiddb.make(('MESSAGE_ID', msgid))
        msgid_node.GROUPs.update(group_names)

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
        env['shortlist_' +
            lc_(hdr_name)] = ','.join(MDB.header_shortlist(M, (hdr_name,)))
    # ... and the recipients, combined
    env['shortlist_to_cc_bcc'] = ','.join(
        MDB.header_shortlist(M, ('to', 'cc', 'bcc'))
    )
    return env

  def save_to_pipe(self, argv, environ=None, mfp=None):
    ''' Pipe a message to the command specific by `argv`.
        `mfp` is a file containing the message text.
        If `mfp` is None, use the text of the current message.
    '''
    if environ is None:
      environ = self.process_environ()
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
    retcode = subprocess.call(argv, env=environ, stdin=mfp)
    track("    %s => | %s" % (("OK" if retcode == 0 else "FAIL"), argv))
    return retcode == 0

  def sendmail(self, address, mfp=None, sender=None):
    ''' Dispatch a message to `address`.
        `mfp` is a file containing the message text.
        If `mfp` is None, use the text of the current message.
        If `sender` is supplied, pass to sendmail with -f option.
    '''
    sendmail_argv = [self.env('SENDMAIL', 'sendmail'), '-oi']
    if sender is not None:
      sendmail_argv.extend(('-f', sender))
    sendmail_argv.append(address)
    return self.save_to_pipe(sendmail_argv, mfp=mfp)

  @property
  def alert_format(self):
    ''' The format string for alert messages from $ALERT_FORMAT.
    '''
    return self.env(
        'ALERT_FORMAT',
        'MAILFILER: {short_from}->{short_recipients}: {subject}'
    )

  def alert_message(self, M):
    ''' Return the alert message filled out with parameters from the Message `M`.
    '''
    fmt = self.alert_format
    try:
      msg = self.format_message(M, fmt)
    except KeyError as e:
      error(
          "alert_message: format=%r, message keys=%s: %s", fmt,
          ','.join(sorted(list(M.keys()))), e
      )
      msg = "MAILFILER: alert! format=%s" % (fmt,)
    return msg

  def format_message(self, M, fmt):
    ''' Compute the alert message for the message `M` using the supplied format string `fmt`.
    '''
    hmap = {k.lower().replace('-', '_'): M[k] for k in M.keys()}
    subj = unrfc2047(M.get('subject', '')).strip()
    if subj:
      hmap['subject'] = subj
    for hdr in ('from', 'to', 'cc', 'bcc', 'reply-to'):
      shortnames = self.maildb.header_shortlist(M, (hdr,))
      hmap['short_' + hdr.replace('-', '_')] = ",".join(shortnames)
    hmap['short_recipients'] = ",".join(
        self.maildb.header_shortlist(M, ('to', 'cc', 'bcc'))
    )
    msg = fmt.format(**hmap)
    return msg

  def alert(self, alert_level, alert_message=None):
    ''' Issue an alert with the specified `alert_message`.
        If missing or None, use self.alert_message(self.message).
        If `alert_level` is more than 1, prepend "-l alert_level"
        to the alert command line arguments.
    '''
    if alert_message is None:
      alert_message = self.alert_message(self.message)
    subargv = [self.env('ALERT', 'alert')]
    if alert_level > 1:
      subargv.extend(['-l', str(alert_level)])
    # tell alert how to open this message
    # TODO: parameterise so that we can open it with other tools
    if self.save_to_folders:
      try:
        msg_id = self.message['message-id']
      except KeyError:
        warning("no Message-ID !")
      else:
        if msg_id is None:
          warning("message-id is None!")
        else:
          msg_ids = [
              msg_id_word for msg_id_word in msg_id.split() if msg_id_word
          ]
          if msg_ids:
            msg_id = msg_ids[0]
            subargv.extend(
                [
                    '-e', 'term', '-e', 'mutt-open-message', '-f',
                    first(self.save_to_folders), msg_id, '--'
                ]
            )
    subargv.append(alert_message)
    xit = subprocess.call(subargv)
    if xit != 0:
      warning("non-zero exit from alert: %d", xit)
    return xit

# quoted string
re_QSTR_s = r'"([^"]|\\.)*"'
# non-whitespace not containing a comma or a quote mark
re_UNQWORD_s = r'[^,"\s]+'
# non-negative integer
re_NUMBER_s = r'0|[1-9][0-9]*'
# non-alphanumeric/non-white
re_NONALNUMWSP_s = r'[^a-z0-9_\s]'
# header-name
re_HEADERNAME_s = r'[a-z][\-a-z0-9]*'
# header[,header,...]:
re_HEADERNAME_LIST_s = r'(%s(,%s)*)' % (re_HEADERNAME_s, re_HEADERNAME_s)
re_HEADERNAME_LIST_PREFIX_s = re_HEADERNAME_LIST_s + ':'
# header:s/
re_HEADER_SUBST_s = r'(%s):s([^a-z0-9_])'
# identifier
re_IDENTIFIER_s = r'[a-z]\w+'
# dotted identifier (dots optional)
re_DOTTED_IDENTIFIER_s = r'%s(\.%s)*' % (re_IDENTIFIER_s, re_IDENTIFIER_s)
# identifier=
re_ASSIGN_s = r'(%s)=' % (re_IDENTIFIER_s,)

# group membership test: (A|B|C|...)
# where A may be a WORD or @domain
# indicating an address group name or an address ending in @domain

# identifier
re_WORD_s = r'[a-z]\w+'

# GROUPNAME
re_GROUPNAME_s = r'[A-Z][A-Z0-9_]+'

# @domain
re_atDOM_s = r'@[-\w]+(\.[-\w]+)+'

# local-part@domain
re_simpleADDRatDOM_s = r'[a-z0-9][\-\.a-z0-9]*' + re_atDOM_s

# GROUPNAME or @domain
re_GROUPNAMEorDOMorADDR_s = (
    '(%s|%s|%s)' % (re_GROUPNAME_s, re_atDOM_s, re_simpleADDRatDOM_s)
)

# (GROUP[|GROUP...])
re_INGROUP_s = (
    r'\(\s*%s(\s*\|\s*%s)*\s*\)' % (re_GROUPNAME_s, re_GROUPNAME_s)
)

# (GROUPorDOMorADDR[|GROUPorDOMorADDR...])
re_INGROUPorDOMorADDR_s = \
    r'\(\s*%s(\s*\|\s*%s)*\s*\)' \
    % (re_GROUPNAMEorDOMorADDR_s, re_GROUPNAMEorDOMorADDR_s)

# simple argument shorthand (GROUPNAME|@domain|number|"qstr")
re_ARG_s = r'(%s|%s|%s|%s)' % (
    re_GROUPNAME_s, re_atDOM_s, re_NUMBER_s, re_QSTR_s
)
# simple commas separated list of ARGs
re_ARGLIST_s = r'(%s(,%s)*)?' % (re_ARG_s, re_ARG_s)

# header[,header,...].func(
re_HEADERFUNCTION_s = r'(%s(,%s)*)\.(%s)\(' % (
    re_HEADERNAME_s, re_HEADERNAME_s, re_WORD_s
)
re_HEADERFUNCTION = re.compile(re_HEADERFUNCTION_s, re.I)

#############################
# final regexps directly used
re_NONALNUMWSP = re.compile(re_NONALNUMWSP_s, re.I)
re_ASSIGN = re.compile(re_ASSIGN_s, re.I)
re_HEADERNAME_LIST = re.compile(re_HEADERNAME_LIST_s, re.I)
re_HEADERNAME_LIST_PREFIX = re.compile(re_HEADERNAME_LIST_PREFIX_s, re.I)
re_HEADER_SUBST = re.compile(re_HEADER_SUBST_s, re.I)
re_UNQWORD = re.compile(re_UNQWORD_s)
re_HEADERNAME = re.compile(re_HEADERNAME_s, re.I)
re_DOTTED_IDENTIFIER = re.compile(re_DOTTED_IDENTIFIER_s, re.I)
re_ARG = re.compile(re_ARG_s)
re_ARGLIST = re.compile(re_ARGLIST_s)
re_INGROUPorDOMorADDR = re.compile(re_INGROUPorDOMorADDR_s, re.I)

def parserules(fp):
  ''' Read rules from `fp`, yield Rules.
  '''
  if isinstance(fp, str):
    with open(fp) as rfp:
      yield from parserules(rfp)
    return

  filename = getattr(fp, 'name', None)
  if filename is None:
    file_label = str(type(fp))
  else:
    file_label = shortpath(filename)
  lineno = 0
  R = None
  for line in fp:
    lineno += 1
    P = Pfx("%s:%d", file_label, lineno)
    if filename:
      if not line.endswith('\n'):
        with P:
          raise ValueError("short line at EOF")

    # skip comments
    if line.startswith('#'):
      continue

    # remove newline and trailing whitespace
    line = line.rstrip()

    # skip blank lines
    if not line:
      continue

    # check for leading whitespace (continuation line)
    _, offset = get_white(line, 0)
    if not _:
      # text at start of line ==> new rule
      # yield old rule if in progress
      if R:
        yield R
        R = None
      # < includes only match at the start of a line
      if line[offset] == '<':
        # include another categories file
        _, offset = get_white(line, offset + 1)
        subfilename, offset = get_nonwhite(line, offset=offset)
        if not subfilename:
          with P:
            raise ValueError("missing filename")
        subfilename = envsub(subfilename)
        if filename:
          subfilename = abspath_from_file(subfilename, filename)
        else:
          subfilename = os.path.abspath(subfilename)
        for R in parserules(subfilename):
          yield R
        continue
      # new rule: gather targets and label
      R = Rule(filename=(filename if filename else file_label), lineno=lineno)

      # leading optional '+' (continue) or '=' (final)
      if line[offset] == '+':
        R.flags.halt = False
        offset += 1
      elif line[offset] == '=':
        R.flags.halt = True
        offset += 1

      # leading '!' alert: multiple '!' raise the alert level
      while line[offset] == '!':
        R.flags.alert += 1
        offset += 1

      # targets
      Ts, offset = get_targets(line, offset)
      R.targets.extend(Ts)
      _, offset = get_white(line, offset)
      if offset >= len(line):
        # no label; end line parse
        continue

      # label
      label, offset = get_nonwhite(line, offset)
      if label == '.':
        label = ''
      R.label = label
      _, offset = get_white(line, offset)
      if offset >= len(line):
        # no condition; end line parse
        continue

    # parse condition and add to current rule
    condition_flags = NS(invert=False)

    if line[offset:] == '.':
      # placeholder for no condition
      continue

    if line[offset] == '!':
      condition_flags.invert = True
      _, offset = get_white(line, offset + 1)
      if offset == len(line):
        warning('no condition after "!"')
        continue

    # leading hdr1,hdr2.func(
    m = re_HEADERFUNCTION.match(line, offset)
    if m:
      header_names = tuple(H.lower() for H in m.group(1).split(',') if H)
      testfuncname = m.group(3)
      offset = m.end()
      _, offset = get_white(line, offset)
      if offset == len(line):
        with P:
          raise ValueError("missing argument to header function")
      if line[offset] == '"':
        test_string, offset = get_qstr(line, offset)
        _, offset = get_white(line, offset)
        if offset == len(line) or line[offset] != ')':
          with P:
            raise ValueError(
                "missing closing parenthesis after header function argument"
            )
        offset += 1
        _, offset = get_white(line, offset)
        if offset < len(line):
          with P:
            raise ValueError(
                "extra text after header function: %r" % (line[offset:],)
            )
      else:
        with P:
          raise ValueError(
              "unexpected argument to header function, expected double quoted string"
          )
      C = Condition_HeaderFunction(
          condition_flags, header_names, testfuncname, test_string
      )
    else:
      # leading hdr1,hdr2,...:
      m = re_HEADERNAME_LIST_PREFIX.match(line, offset)
      if m:
        header_names = tuple(H.lower() for H in m.group(1).split(',') if H)
        offset = m.end()
        if offset == len(line):
          with P:
            raise ValueError("missing match after header names")
      else:
        header_names = ('to', 'cc', 'bcc')
      # headers:/regexp
      if line[offset] == '/':
        regexp = line[offset + 1:]
        if regexp.startswith('^'):
          atstart = True
          regexp = regexp[1:]
        else:
          atstart = False
        C = Condition_Regexp(condition_flags, header_names, atstart, regexp)
      else:
        # headers:(group[|group...])
        m = re_INGROUPorDOMorADDR.match(line, offset)
        if m:
          group_names = set(
              w.strip().lower() for w in m.group()[1:-1].split('|')
          )
          offset = m.end()
          if offset < len(line):
            with P:
              raise ValueError("extra text after groups: %s" % (line,))
          C = Condition_InGroups(condition_flags, header_names, group_names)
        else:
          if line[offset] == '(':
            with P:
              raise ValueError(
                  "incomplete group match at: %s" % (line[offset:])
              )
          # just a comma separated list of addresses
          # TODO: should be RFC2822 list instead?
          addrkeys = [
              coreaddr
              for realname, coreaddr in getaddresses((line[offset:],))
          ]
          C = Condition_AddressMatch(condition_flags, header_names, addrkeys)

    R.conditions.append(C)

  if R is not None:
    yield R

def get_targets(s, offset):
  ''' Parse list of targets from the string `s` starting at `offset`.
      Return the list of Targets strings and the new offset.
  '''
  targets = []
  while offset < len(s) and not s[offset].isspace():
    T, offset = get_target(s, offset)
    targets.append(T)
    if offset < len(s):
      # check for whitespace (end of targets) or comma (another target)
      ch = s[offset]
      if ch.isspace():
        continue
      elif ch == ',':
        while True:
          offset += 1
          if offset >= len(s) or s[offset] != ',':
            break
      else:
        raise ValueError(
            'offset %d: expected comma after target, found: %r' %
            (offset, s[offset:])
        )
  return targets, offset

def get_target(s, offset, quoted=False):
  ''' Parse a single target specification from a string; return Target and new offset.

      Parameters:
      * `s`: the string to parse
      * `offset`: the starting offset of the parse
      * `quoted`: if true then the parser is already inside quotes:
        do not expect comma or whitespace to end the target specification.
        Default: `False`
  '''
  offset0 = offset

  # "quoted-target-specification"
  if not quoted and s.startswith('"', offset0):
    s2, offset = get_qstr(s, offset0)
    s2q = s[offset0:offset]
    # reparse inner string
    T, offset2 = get_target(s2, 0, quoted=True)
    # check for complete parse, allow some trailing whitespace
    s3 = s2[offset2:].lstrip()
    if s3:
      warning("ignoring unparsed content from %s: %r" % (s2q, s3))
    return T, offset

  # varname=expr
  m = re_ASSIGN.match(s, offset0)
  if m:
    varname = m.group(1)
    offset = m.end()
    if offset >= len(s):
      varexpr = ''
    elif s[offset] == '"':
      varexpr, offset = get_qstr(s, offset)
    else:
      if quoted:
        varexpr = s[offset:]
        offset = len(s)
      else:
        varexpr, offset = get_other_chars(s, offset, cs.lex.whitespace + ',')
    T = Target_Assign(varname, varexpr)
    return T, offset

  # F -- flag
  flag_letter = s[offset0]
  offset = offset + 1
  if (flag_letter.isupper()
      and (offset == len(s) or (not quoted and
                                (s[offset] == ',' or s[offset].isspace())))):
    try:
      T = Target_SetFlag(flag_letter)
    except ValueError as e:
      warning("ignoring bad flag %r: %s", flag_letter, e)
      T = None
    return T, offset

  # |shcmd
  if s.startswith('|', offset0):
    if quoted:
      shcmd = s[offset0 + 1:]
      offset = len(s)
    else:
      shcmd, offset = get_other_chars(s, offset0, cs.lex.whitespace + ',')
    T = Target_PipeLine(shcmd)
    return T, offset

  # headers:s/this/that/
  tokens, offset = match_tokens(
      s, offset0, (re_HEADERNAME_LIST, ':s', re_NONALNUMWSP)
  )
  if tokens:
    m_headers, marker, m_delim = tokens
    header_names = m_headers.group().split(',')
    delim = m_delim.group()
    regexp, offset = get_delimited(s, offset, delim)
    replacement, offset = get_delimited(s, offset, delim)
    try:
      subst_re = re.compile(regexp)
    except Exception as e:
      warning("ignoring substitute: re.compile: %s: regexp=%s", e, regexp)
      T = None
    else:
      T = Target_Substitution(header_names, subst_re, replacement)
    return T, offset

  # s/this/that/ -- modify subject:
  tokens, offset = match_tokens(s, offset0, ('s', re_NONALNUMWSP))
  if tokens:
    header_names = ('subject',)
    marker, m_delim = tokens
    delim = m_delim.group()
    regexp, offset = get_delimited(s, offset, delim)
    replacement, offset = get_delimited(s, offset, delim)
    subst_re = re.compile(regexp)
    try:
      subst_re = re.compile(regexp)
    except Exception as e:
      warning("ignoring substitute: re.compile: %s: regexp=%s", e, regexp)
      T = None
    else:
      T = Target_Substitution(header_names, subst_re, replacement)
    return T, offset

  # headers:func([args...])
  tokens, offset = match_tokens(
      s, offset0, (
          re_HEADERNAME_LIST,
          ':',
          re_DOTTED_IDENTIFIER,
      )
  )
  if tokens:
    m_headers, colon, m_funcname = tokens
    # check for optional (arg,...)
    if offset < len(s) and s[offset] == '(':
      m_arglist = re_ARGLIST.match(s, offset + 1)
      if not m_arglist:
        raise ValueError("expected argument list at %r" % (s[offset + 1:],))
      offset = m_arglist.end()
      if offset >= len(s) or s[offset] != ')':
        raise ValueError("expected closing parenthesis at %r" % (s[offset:],))
      offset += 1
      arglist = m_arglist.group()
    else:
      arglist = ()
    header_names = m_headers.group().split(',')
    funcname = m_funcname.group()
    args = []
    arglist_offset = 0
    while arglist_offset < len(arglist):
      m = re_ARG.match(arglist, arglist_offset)
      if not m:
        raise ValueError(
            "BUG: arglist %r did not match re_ARG (%s)" %
            (arglist[arglist_offset:], re_ARG)
        )
      arglist_offset = m.end()
      args.append(m.group(0))
      if arglist_offset >= len(arglist):
        break
      if arglist[arglist_offset] != ',':
        raise ValueError(
            "BUG: expected comma at %r" % (arglist[arglist_offset:],)
        )
      arglist_offset += 1
      # allow trailing comma
      if arglist_offset >= len(arglist):
        break
    T = Target_Function(header_names, funcname, args)
    return T, offset

  # unquoted word: email address or mail folder
  m = re_UNQWORD.match(s, offset0)
  if m:
    target = m.group()
    offset = m.end()
    if '$' in target:
      T = Target_EnvSub(target)
    elif '@' in target:
      T = Target_MailAddress(target)
    else:
      T = Target_MailFolder(target)
    return T, offset

  error("parse failure at %d: %s", offset, s)
  raise ValueError("syntax error")

class Target_Assign(NS):
  ''' A filing target to set a filing state environment variable.
  '''

  def __init__(self, varname, varexpr):
    self.varname = varname
    self.varexpr = varexpr

  def __str__(self):
    return '%s=%s' % (self.varname, self.varexpr)

  def apply(self, filer):
    ''' Apply the target by updating the filer environment.
    '''
    varname = self.varname
    value = envsub(self.varexpr, filer.environ)
    filer.environ[varname] = value
    if varname == 'LOGFILE':
      debug("LOGFILE= unimplemented at present")
      ## TODO: self.logto(value)

class Target_EnvSub(NS):
  ''' A filing target to delivery to a string
      which is subject to environment subject to environment variable expansion
      where the environment variables are derived from the filing state.
  '''

  def __init__(self, target_expr):
    self.target_expr = target_expr

  def __str__(self):
    return f'"{self.target_expr}"'

  def apply(self, filer):
    ''' Perform environment substitution on target string and then
        deliver to resulting string.
    '''
    target = envsub(self.target_expr, filer.environ)
    if '@' in target:
      T = Target_MailAddress(target)
    else:
      T = Target_MailFolder(target)
    T.apply(filer)

class Target_SetFlag(NS):
  ''' A filing target to apply a flag to a message.
  '''

  def __init__(self, flag_letter):
    if flag_letter == 'D':
      flag_attr = 'draft'
    elif flag_letter == 'F':
      flag_attr = 'flagged'
    elif flag_letter == 'P':
      flag_attr = 'passed'
    elif flag_letter == 'R':
      flag_attr = 'replied'
    elif flag_letter == 'S':
      flag_attr = 'seen'
    elif flag_letter == 'T':
      flag_attr = 'trashed'
    else:
      raise ValueError("unsupported flag \"%s\"" % (flag_letter,))
    self.flag_attr = flag_attr

  def __str__(self):
    return f'flag={self.flag_attr}'

  def apply(self, filer):
    ''' Apply this target:
        set a flag on the message.
    '''
    setattr(filer.flags, self.flag_attr, True)

class Target_Substitution(NS):
  ''' A filing target to apply a regular expression string substitution
      to message headers.
  '''

  def __init__(self, header_names, subst_re, subst_replacement):
    self.header_names = header_names
    self.subst_re = subst_re
    self.subst_replacement = subst_replacement

  def __str__(self):
    return (
        ','.join(self.header_names) + ':s/' + str(self.subst_re.pattern) +
        '/' + self.subst_replacement
    )

  def apply(self, filer):
    ''' Apply this target:
        apply a regexp substitution to the message headers.
    '''
    for header_name in self.header_names:
      M = filer.message
      # fetch old value and "unfold" (strip CRLF, see RFC2822 part 2.2.3)
      old_value = M.get(header_name, '').replace('\r', '').replace('\n', '')
      m = self.subst_re.search(old_value)
      if m:
        env = {}
        # Start with the headers as a basic set of available values.
        # Lowercase header names and replace '-' with '_'.
        # Strip CRLF per RFC2822 part 2.2.3 as we do for old_value above.
        for hname, hvalue in M.items():
          hname = hname.lower().replace('-', '_')
          env[hname] = hvalue.replace('\r', '').replace('\n', '')
        # Override with named substitution values.
        env.update(m.groupdict())
        # Add numbered substitution values.
        env_specials = {'0': m.group(0)}
        for ndx, grp in enumerate(m.groups(), 1):
          env_specials[str(ndx)] = grp
        repl_value, offset = get_qstr(
            self.subst_replacement,
            0,
            q=None,
            environ=env,
            env_specials=env_specials
        )
        new_value = old_value[:m.start()] + repl_value + old_value[m.end():]
        if offset != len(self.subst_replacement):
          warning(
              "after getqstr, offset[%d] != len(subst_replacement)[%d]: %r",
              offset, len(self.subst_replacement), self.subst_replacement
          )
        filer.modify(header_name.title(), new_value)

class Target_Function(NS):
  ''' A filing target to run a Python function against a message.
  '''

  def __init__(self, header_names, funcname, args):
    self.header_names = header_names
    self.funcname = funcname
    self.args = args

  def __str__(self):
    return (
        ','.join(self.header_names) + ':' + self.funcname + '(' +
        ','.join(self.args) + ')'
    )

  def apply(self, filer):
    ''' Apply this target:
        run the Python function against the message.
    '''
    if '.' in self.funcname:
      module_name, func_name = self.funcname.rsplit('.', 1)
      func = import_module_name(module_name, func_name)
      if func is None:
        raise ValueError(
            "no function %r in module %r" % (func_name, module_name)
        )
    elif self.funcname == 'learn_addresses':
      func = filer.learn_header_addresses
    elif self.funcname == 'learn_message_ids':
      func = filer.learn_message_ids
    elif self.funcname == 'scrub':
      func = scrub_header
    else:
      raise ValueError(
          "no simply named functions defined yet: %r" % (self.funcname,)
      )

    # evaluate the arguments and then call the function
    func_args = []
    for arg in self.args:
      if arg.startswith('"'):
        value, offset = get_qstr(arg, 0, environ=filer.environ)
      else:
        try:
          value = int(arg)
        except ValueError:
          value = arg
      func_args.append(value)
    M = filer.message
    for header_name in self.header_names:
      header_values = M.get_all(header_name, ())
      new_header_values = []
      if header_values:
        for s in header_values:
          try:
            s2 = func(s, *func_args)
          except Exception as e:
            exception(
                "exception calling %s(filer, *%r): %s", self.funcname,
                func_args, e
            )
            raise
          else:
            if s2 is not None:
              new_header_values.append(s2)
        if new_header_values and header_values != new_header_values:
          track(
              "%s: %r ==> %r", header_name.title(), header_values,
              new_header_values
          )
          filer.modify(header_name, new_header_values)

class Target_PipeLine(NS):
  ''' A filing target to pipe the message contents to a shell command.
  '''

  def __init__(self, shcmd):
    self.shcmd = shcmd

  def __str__(self):
    return f'|"{self.shcmd}"'

  def apply(self, filer):
    ''' Apply this target:
        append `self.shcmd` to the list of save commands.
    '''
    filer.save_to_cmds.append((self.shcmd, filer.process_environ()))

class Target_MailAddress(NS):
  ''' A filing target for an email address.
  '''

  def __init__(self, address):
    self.address = address

  def __str__(self):
    return self.address

  def apply(self, filer):
    ''' Apply this target:
        add `self.address` to the set of target forwarding email addresses.
    '''
    filer.save_to_addresses.add(self.address)

class Target_MailFolder(NS):
  ''' A filing target for a mail folder.
  '''

  def __init__(self, mailfolder):
    self.mailfolder = mailfolder

  def __str__(self):
    return shortpath(self.mailfolder)

  @fmtdoc
  def apply(self, filer):
    ''' Apply this target:
        if the folder name is {SELF_FOLDER!r}
        mark the filer as saving to the source folder,
        otherwise add the resolved folder name to the set of target folders.
    '''
    mailfolder = self.mailfolder
    if mailfolder == SELF_FOLDER:
      filer.save_to_self = True
    else:
      mailpath = filer.resolve(self.mailfolder)
      filer.save_to_folders.add(mailpath)

class _Condition(NS):

  def __init__(self, flags, header_names):
    self.flags = flags
    self.header_names = header_names

  def __str__(self):
    return (
        ('!' if self.flags.invert else '') + ','.join(self.header_names) +
        ':' + self.tests_str()
    )

  def match(self, filer):
    ''' Test this condition against all the relevant headers.
    '''
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
  ''' A condition testing headers against a regular expression.
  '''

  def __init__(self, flags, header_names, atstart, regexp):
    _Condition.__init__(self, flags, header_names)
    self.atstart = atstart
    self.regexp = re.compile(regexp)
    self.regexptxt = regexp

  def tests_str(self):
    return self.regexptxt

  def test_value(self, filer, header_name, header_value):
    ''' Test this condition against a header value.
    '''
    if self.atstart:
      return self.regexp.match(header_value)
    return self.regexp.search(header_value)

class Condition_AddressMatch(_Condition):
  ''' A condition testing for the presence of an address.
  '''

  def __init__(self, flags, header_names, addrkeys):
    _Condition.__init__(self, flags, header_names)
    self.addrkeys = tuple(k for k in addrkeys if len(k) > 0)

  def tests_str(self):
    return '|'.join(self.addrkeys)

  def test_value(self, filer, header_name, header_value):
    ''' Test this condition against a header value.
    '''
    for address in filer.addresses(header_name):
      address_lc = address.lower()
      for key in self.addrkeys:
        if address_lc == key.lower():
          return True
    return False

class Condition_InGroups(_Condition):
  ''' A condition testing messages addresses against address groups.
  '''

  def __init__(self, flags, header_names, group_names):
    _Condition.__init__(self, flags, header_names)
    self.group_names = group_names

  def tests_str(self):
    return '(' + '|'.join(self.group_names) + ')'

  def test_value(self, filer, header_name, header_value):
    ''' Test this condition against a header value.
    '''
    # choose to test message-ids or addresses
    if header_name.lower() in ('message-id', 'references', 'in-reply-to'):
      # test is against message-ids
      msgiddb = self.filer.msgiddb
      msgids = [v for v in header_value.split() if v]
      for msgid in msgids:
        # get the foo@bar part of <foo@bar>
        # be very lenient
        if msgid.startswith('<'):
          if msgid.endswith('>'):
            # <...>
            msgid_inner = msgid[1:-1]
          else:
            # <...
            msgid_inner = msgid[1:]
        elif msgid.endswith('>'):
          # ...>
          msgid_inner = msgid[:-1]
        else:
          # ...
          msgid_inner = msgid
        msgid_node = msgiddb.get(('MESSAGE_ID', msgid))
        for group_name in self.group_names:
          # look for <...@domain>
          if group_name.startswith('@'):
            if msgid_inner.lower().endswith(group_name):
              debug("match %s to %s", msgid, group_name)
              return True
          # look for specific <local@domain>
          elif '@' in group_name:
            if msgid_inner.lower() == group_name:
              debug("match %s to %s", msgid, group_name)
              return True
          # look for named group in MESSAGE_ID.GROUPs
          elif msgid_node:
            if group_name in msgid_node.GROUPs:
              debug("match %s to (%s)", msgid, group_name)
              return True
    else:
      # test is against addresses
      for address in filer.addresses(header_name):
        for group_name in self.group_names:
          if group_name.startswith('@'):
            # address ending in @foo
            if address.endswith(group_name):
              debug("match %s to %s", address, group_name)
              return True
          elif '@' in group_name:
            # full address local part
            if address.lower() == group_name:
              debug("match %s to %s", address, group_name)
              return True
          elif address.lower() in filer.group(group_name):
            # address in group "foo"
            debug("match %s to (%s)", address, group_name)
            return True
    return False

class Condition_HeaderFunction(_Condition):
  ''' A condition testing the contents of a header.
  '''

  def __init__(self, flags, header_names, funcname, test_string):
    _Condition.__init__(self, flags, header_names)
    self.funcname = funcname
    self.test_string = test_string
    test_method = 'test_func_' + funcname
    try:
      self.test_func = getattr(self, test_method)
    except AttributeError:
      raise ValueError("invalid header function .%s()" % (funcname,))

  def tests_str(self):
    return '%s(%s)' % (self.funcname, self.test_string)

  def test_value(self, filer, header_name, header_value):
    ''' Test the header value against to test function.
    '''
    return self.test_func(filer, header_name, header_value)

  def test_func_contains(self, filer, header_name, header_value):
    ''' Test if the rule's string is in the header.
    '''
    return self.test_string in header_value

_FilterReport = namedtuple(
    'FilterReport', 'rule matched saved_to ok_actions failed_actions'
)

def FilterReport(rule, matched, saved_to, ok_actions, failed_actions):
  ''' Create a FilterReport object.

      Parameters:
      * `rule`: the `Rule` on which to report
      * `matched`: whether the rule was matched
      * `saved_to`: where messages were filed
      * `ok_actions`: actions which succeeded
      * `failed_actions`: actions which failed
  '''
  if not matched:
    if saved_to:
      raise RuntimeError(
          "matched(%r) and not saved_to(%r)" % (matched, saved_to)
      )
  return _FilterReport(rule, matched, saved_to, ok_actions, failed_actions)

class Rule:
  ''' A filing rule.
  '''

  def __init__(self, filename, lineno):
    self.filename = filename
    self.lineno = lineno
    self.conditions = []
    self.targets = []
    self.flags = NS(alert=0, halt=False)
    self.label = ''

  def __str__(self):
    return "%s:%d: %s %s" % (
        shortpath(self.filename), self.lineno,
        ','.join(map(str, self.targets)), ', '.join(map(str, self.conditions))
    )

  def __repr__(self):
    return (
        "%s(%r:%d,targets=%r,conditions=%r,flags=%s,label=%r)" % (
            type(self).__name__, self.filename, self.lineno, self.targets,
            self.conditions, self.flags, self.label
        )
    )

  @property
  def context(self):
    ''' The rule context (filename:lineno).
    '''
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
    self.rule_files.update(R.filename for R in new_rules)
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
            break

class WatchedMaildir(NS):
  ''' A class to monitor a Maildir and filter messages.
  '''

  def __init__(self, mdir, context, rules_path=None):
    self.mdir = Maildir(resolve_mail_path(mdir, os.environ['MAILDIR']))
    self.context = context
    self.rules_path = rules_path
    if rules_path is None:
      # default to looking for .mailfiler inside the Maildir
      rules_path = os.path.join(self.mdir.path, '.mailfiler')
    self._lock = Lock()
    self._rules = None
    self._rules_paths = [rules_path]
    self._rules_lock = Lock()
    self.lurking = set()
    self.filed = set()
    self.flush()
    track("%s: %d rules", self.shortname, len(self.rules))

  def __str__(self):
    return "<WatchedMaildir %s, %s rules, %d lurking>" \
           % (self.shortname,
              "NO" if self._rules is None else len(self._rules),
              len(self.lurking))

  def close(self):
    ''' Close the WatchedMaildir.
    '''
    self.flush()
    self.mdir.close()

  @property
  def shortname(self):
    ''' The shortname if the maildir path.
    '''
    return self.mdir.shortname

  @property
  def path(self):
    ''' The maildir path.
    '''
    return self.mdir.path

  def keys(self, flush=False):
    ''' Return the keys of the Maildir.
    '''
    return self.mdir.keys(flush=flush)

  def __getitem__(self, key):
    return self.mdir[key]

  def keypath(self, key):
    ''' Return the kypath for `key`.
    '''
    return self.mdir.keypath(key)

  def remove(self, key):
    ''' Remove `key` from the maildir.
    '''
    return self.mdir.remove(key)

  def flush(self):
    ''' Forget state.
        The set of lurkers is emptied.
    '''
    self.lurking = set()

  def lurk(self, key):
    ''' Add `key` to the luking list.
    '''
    track("lurk %s", key)
    self.lurking.add(key)

  def unlurk(self, key):
    ''' Remove `key` from the lurking list.
    '''
    track("unlurk %s", key)
    self.lurking.remove(key)

  def _rules_state(self):
    states = []
    for path in self._rules_paths:
      try:
        S = FileState(path)
      except OSError:
        states.append(None)
      else:
        states.append((path, S.mtime, S.size, S.dev, S.ino))
    return states

  @property
  @locked
  @cachedmethod(sig_func=lambda md: md._rules_state())
  def rules(self):
    ''' The `Rules` object for this `WatchedMaildir`.
    '''
    # base file is at index 0
    path0 = self.rules_path
    R = Rules(path0)
    # produce rules file list with base file at index 0
    self._rules_paths = [path0
                         ] + [path for path in R.rule_files if path != path0]
    return R

if __name__ == '__main__':
  sys.exit(main(sys.argv))

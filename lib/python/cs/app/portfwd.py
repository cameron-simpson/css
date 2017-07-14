#!/usr/bin/python
#
# Manage ssh based port forwards.
# - Cameron Simpson <cs@zip.com.au> May 2017
#

from __future__ import print_function
from collections import defaultdict
import errno
from getopt import getopt, GetoptError
import os
from os.path import basename, exists as pathexists
import re
from signal import signal, SIGHUP, SIGINT, SIGTERM
import subprocess
from subprocess import DEVNULL
import sys
from time import sleep
from cs.app.flag import Flags, uppername, lowername, FlaggedMixin
from cs.app.svcd import SvcD
from cs.cmdutils import pipefrom
from cs.env import envsub
from cs.logutils import setup_logging, info, warning, error
from cs.pfx import Pfx
from cs.py.func import prop
from cs.sh import quotecmd as shq
from cs.x import X

USAGE = '''Usage:
  %s -d [targets...]
        Disable portfwd for targets, setting PORTFWD_TARGET_DISABLE.
        If no targets are specified, set PORTFWD_DISABLE.
  %s -e [targets...]
        Enable portfwd for targets, clearing PORTFWD_TARGET_DISABLE.
        If no targets are specified, clear PORTFWD_DISABLE.
  %s -D [targets...]
        Disable the override flag for targets, clearing PORTFWD_TARGET_OVERRIDE.
        If no targets are specified, clear PORTFWD_OVERRIDE.
  %s -E [targets...]
        Enable the override for targets, setting PORTFWD_TARGET_OVERRIDE.
        If no targets are specified, set PORTFWD_OVERRIDE.
  %s -T [-F ssh_config] target
        Print test shell command for target to standard output.
  %s [-1] [-A] [-F ssh_config] [-n] [-x] [-v] targets...
    -1  Once. Do not restart the tunnel after it exits.
        Exactly one target must be specified.
    -A  Automatic. Maintain a port forward to "foo" for each set
        flag PORTFWD_FOO_AUTO.
    -F  Ssh configuration file with clause for target.
        Default from $PORTFWD_SSH_CONFIG,
        otherwise ~/.ssh/config-pf, otherwise ~/.ssh/config.
    -n  No action. Recite final command.
    -x  Trace execution.
    -v  Verbose. Passed to ssh.

If a target starts with an upper case letter it is taken to be a
group name, and the targets are found by collating the first hostname
in each Host claus with the group name appended.
Example: "Host home ALL"'''

def main(argv, environ=None):
  if environ is None:
    environ = os.environ
  cmd = basename(argv.pop(0))
  usage = USAGE % (cmd, cmd, cmd, cmd, cmd, cmd)
  setup_logging(cmd)

  badopts = False
  once = False
  doit = True
  sshcfg = None
  auto_mode = False
  flags = Flags(environ=environ)
  trace = sys.stderr.isatty()
  verbose = False

  try:
    if not argv:
      raise GetoptError("missing arguments")
    opt1 = argv.pop(0)
    if opt1 == '-d':
      if not argv:
        flags.flag_portfwd_disable = True
      else:
        for target in argv:
          flags['PORTFWD_' + uppername(target) + '_DISABLE'] = True
      return 0
    if opt1 == '-e':
      if not argv:
        flags.flag_portfwd_disable = False
      else:
        for target in argv:
          flags['PORTFWD_' + uppername(target) + '_DISABLE'] = False
      return 0
    if opt1 == '-D':
      if not argv:
        flags.flag_portfwd_override = False
      else:
        for target in argv:
          flags['PORTFWD_' + uppername(target) + '_OVERRIDE'] = False
      return 0
    if opt1 == '-E':
      if not argv:
        flags.flag_override = True
      else:
        for target in argv:
          flags['PORTFWD_' + uppername(target) + '_OVERRIDE'] = True
      return 0
    if opt1 == '-T':
      if argv and argv[0] == '-F':
        argv.pop(0)
        if not argv:
          raise GetoptError("%s: -F: missing ssh_config" % (opt1,))
        sshcfg = argv.pop(0)
      if not argv:
        raise GetoptError("%s: missing target" % (opt1,))
      target = argv.pop(0)
      if argv:
        raise GetoptError("%s: extra arguments after target %r: %s"
                          % (opt1, target, ' '.join(argv)))
      PFs = Portfwds(ssh_config=sshcfg, target_list=(target,),
                     auto_mode=auto_mode, trace=trace, verbose=verbose,
                     flags=flags)
      print(PFs.forward(target).test_shcmd())
      return 0
    argv.insert(0, opt1)
    opts, argv = getopt(argv, '1AF:nxv')
    for opt, arg in opts:
      with Pfx(opt):
        if opt == '-1':
          once = True
        elif opt == '-A':
          auto_mode = True
        elif opt == '-F':
          sshcfg = arg
        elif opt == '-n':
          doit = False
        elif opt == '-x':
          trace = True
        elif opt == '-v':
          verbose = True
        else:
          raise RuntimeError('unhandled option')
    if not argv and not auto_mode:
      raise GetoptError("missing targets; targets or -A (auto) option required")
    target_list = argv
    if once and (auto_mode or len(target_list) != 1):
      raise GetoptError("once (-1) requires no auto mode (-A) and exactly one target")
  except GetoptError as e:
    warning("%s", e)
    badopts = True
  if badopts:
    print(usage, file=sys.stderr)
    return 2

  PFs = Portfwds(ssh_config=sshcfg, target_list=argv,
                 auto_mode=auto_mode, trace=trace, verbose=verbose,
                 flags=flags)
  if not doit:
    for target in sorted(PFs.targets_required()):
      print(PFs.forward(target))
    return 0

  running = True
  def signal_handler(signum, frame):
    PFs.stop()
    PFs.wait()
    sys.exit(1)
  signal(SIGHUP, signal_handler)
  signal(SIGINT, signal_handler)
  signal(SIGTERM, signal_handler)
  while running:
    PFs.start()
    sleep(1)
  PFs.stop()
  PFs.wait()
  return 0

class Portfwd(FlaggedMixin):

  def __init__(self, target, ssh_config=None, conditions=(), test_shcmd=None, trace=False, verbose=False, flags=None):
    self.name = 'portfwd-' + target
    FlaggedMixin.__init__(self, flags=flags)
    self.test_shcmd = test_shcmd
    self.ssh_config = ssh_config
    self.conditions = conditions
    self.trace = trace
    self.verbose = verbose
    self.target = target
    self.svcd_name = 'portfwd-' + target
    self.flag_connected = False
    def on_reap():
      self.flag_connected = False
    self.svcd = SvcD(self.ssh_argv,
                     name=self.svcd_name,
                     trace=trace,
                     sig_func=self.sig_func,
                     test_func=self.test_func,
                     test_flags={
                        'PORTFWD_DISABLE': False,
                        'PORTFWD_SSH_READY': True,
                        'ROUTE_DEFAULT': True,
                     },
                     on_reap=on_reap
                    )

  def __str__(self):
    return "Portfwd %s %s" % (self.target, shq(self.ssh_argv))

  def start(self):
    self.svcd.start()

  def stop(self):
    self.svcd.stop()

  def wait(self):
    xit = self.svcd.wait()
    return xit

  @prop
  def ssh_argv(self):
    argv = ['ssh']
    if self.verbose:
      argv.append('-v')
    if self.ssh_config:
      argv.extend(['-F', self.ssh_config])
    argv.extend([ '-N', '-T',
                  '-o', 'ExitOnForwardFailure=yes',
                  '-o', 'PermitLocalCommand=yes',
                  '-o', 'LocalCommand=' + self.ssh_localcommand,
                  '--',
                  self.target ])
    return argv

  @prop
  def ssh_localcommand(self):
    ''' Shell command for ssh to invoke on connection ready.
    '''
    setflag_argv = [ 'flag', '-w', self.flagname_connected, '1' ]
    alert_title = 'PORTFWD ' + self.target.upper()
    alert_message = 'CONNECTED: ' + self.target
    alert_argv = [ 'alert', '-t', alert_title, alert_message ]
    shcmd = 'exec </dev/null; ' + shq(setflag_argv) + '; ' + shq(alert_argv) + ' &'
    return shcmd

  def sig_func(self):
    ''' Signature function, returning the ssh options for this target.
    '''
    ssh_argv = ['ssh']
    if self.ssh_config:
      ssh_argv.extend(['-F', self.ssh_config])
    ssh_argv.extend(['-G', '-T'])
    ssh_argv.append(self.target)
    ssh_proc = pipefrom(ssh_argv)
    ssh_options = ssh_proc.stdout.read()
    ssh_retcode = ssh_proc.wait()
    if ssh_retcode != 0:
      error("%r: non-zero return code: %s", ssh_argv, ssh_retcode)
      return None
    return ssh_options

  def test_func(self):
    for condition in self.conditions:
      if not condition.probe():
        return False
    if self.test_shcmd:
      return os.system(self.test_shcmd) == 0
    return True

class Portfwds(object):

  def __init__(self, ssh_config=None, environ=None, target_list=None,
               auto_mode=None, trace=False, verbose=False, flags=None):
    if environ is None:
      environ = os.environ
    if target_list is None:
      target_list = []
    if auto_mode is None:
      auto_mode = False
    if flags is None:
      flags = Flags(environ=environ)
    self.target_list = target_list
    self.auto_mode = auto_mode
    self.trace = trace
    self.verbose = verbose
    self.flags = flags
    self.environ = environ
    if ssh_config is None:
      ssh_config = environ.get('PORTFWD_SSH_CONFIG')
    self._ssh_config = ssh_config
    self._forwards = {}
    self.target_conditions = defaultdict(list)
    self.target_groups = defaultdict(set)
    self.targets_running = {}

  @property
  def forwards(self):
    ''' A list of the existing Portfwd instances.
    '''
    return list(self._forwards.values())

  def forward(self, target):
    ''' Obtain the named Portfwd, creating it if necessary.
    '''
    try:
      P = self._forwards[target]
    except KeyError:
      info("instantiate new target %r", target)
      P = Portfwd(target, ssh_config=self.ssh_config,
                  trace=self.trace, flags=self.flags)
      self._forwards[target] = P
    return P

  def start(self):
    required = self.targets_required()
    for target in required:
      P = self.forward(target)
      if target not in self.targets_running:
        info("start target %r", target)
        P.start()
        self.targets_running[target] = P
    running = list(self.targets_running.keys())
    for target in running:
      if target not in required:
        info("stop target %r", target)
        P = self.targets_running[target]
        P.stop()
        del self.targets_running[target]

  def stop(self):
    for P in self.targets_running.values():
      P.stop()

  def wait(self):
    while self.targets_running:
      targets = sorted(self.targets_running.keys())
      for target in targets:
        P = self.targets_running.get(target)
        if P is None:
          warning("not in targets_running")
        else:
          P.wait()
          del self.targets_running[target]

  def targets_required(self):
    targets = set()
    for spec in self.target_list:
      targets.update(self.resolve_target_spec(spec))
    if self.auto_mode:
      for flagname in self.flags:
        if ( flagname.startswith('PORTFWD_')
         and flagname.endswith('_AUTO')
         and self.flags[flagname]
        ):
          targets.add(lowername(flagname[8:-5]))
    return targets

  GROUP_NAME = r'[A-Z][A-Z0-9_]*'
  GROUP_NAME_RE = re.compile(GROUP_NAME)
  SPECIAL_RE = re.compile(r'^\s*#\s*(' + GROUP_NAME + '):\s*')

  @prop
  def ssh_config(self):
    cfg = self._ssh_config
    if cfg is None:
      cfg = envsub('$HOME/.ssh/config-pf')
      if not pathexists(cfg):
        cfg = envsub('$HOME/.ssh/config')
    return cfg

  def resolve_target_spec(self, spec):
    ''' Accept a target spec and expand it if it is a group.
        Return a set of targets.
    '''
    targets = set()
    m = self.GROUP_NAME_RE.match(spec)
    if m and m.group(0) == spec:
      targets.update(*self.target_groups[spec])
    else:
      targets.add(spec)
    return targets

  def _load_ssh_config(self):
    ''' Read configuration information from the ssh config.
        # F: target needs
        # GROUP: target...
    '''
    cfg = self.ssh_config
    self.conditions = []
    with Pfx("_load_ssh_config(%r)", cfg):
      try:
        with open(cfg) as cfgfp:
          for lineno, line in enumerate(cfgfp, 1):
            with Pfx(lineno):
              line = line.strip()
              if not line:
                continue
              # LABEL: words...
              m = self.SPECIAL_RE.match(line)
              if not m:
                continue
              label = m.group(1)
              tail = line[m.end():]
              with Pfx(label):
                if label == 'F':
                  # F: target condition...
                  words = tail.split()
                  if not words:
                    warning("nothing follows")
                    continue
                  target = words.pop(0)
                  with Pfx(target):
                    if not words:
                      warning("no condition")
                      continue
                    op = words.pop(0)
                    with Pfx(op):
                      if words and words[0].startswith('!'):
                        invert = True
                        words[0] = words[0][1:]
                        if not words[0]:
                          words.pop(0)
                      else:
                        invert = False
                      C = Condition(self, target, op, invert, *words)
                      self.target_conditions[target].append(C)
                else:
                  # GROUP: targets...
                  if not words:
                    warning("nothing follows")
                  else:
                    self.target_groups[label].update(*words)
      except OSError as e:
        if e.errno != errno.ENOENT:
          raise

def Condition(portfwd, op, invert, *args):
  ''' Factory to construct a condition from a specification.
  '''
  if op == 'gw':
    if len(args) != 1:
      raise ValueError("exactly one argument expected, given: %r" % (args,))
    flag = 'ROUTE_GW_' + uppername(args[0])
    return FlagCondition(portfwd, invert, flag)
  if op == 'needs':
    if len(args) != 1:
      raise ValueError("exactly one argument expected, given: %r" % (args,))
    flag = 'PORTFWD_' + uppername(args[0]) + '_CONNECTED'
    return FlagCondition(portfwd, invert, flag)
  if op == 'ping':
    if len(args) != 1:
      raise ValueError("exactly one argument expected, given: %r" % (args,))
    return PingCondition(portfwd, invert, args[0])
  if op == 'route':
    if len(args) != 1:
      raise ValueError("exactly one argument expected, given: %r" % (args,))
    flag = 'ROUTE_TO_' + uppername(args[0])
    return FlagCondition(portfwd, invert, flag)
  raise ValueError("unsupported op")

class _PortfwdCondition(object):

  def __init__(self, portfwd, invert):
    self.portfwd = portfwd
    self.invert = invert

  def __bool__(self):
    if self.test():
      return not self.invert
    return self.invert

  __nonzero__ = __bool__

  def shcmd(self):
    cmd = ' '.join(shq(self.test_argv))
    if self.invert:
      cmd = 'if ' + cmd + '; then false; else true; fi'
    return cmd

  def probe(self):
    result = self.test()
    return not result if self.ivert else result

class FlagCondition(_PortfwdCondition):

  def __init__(self, portfwd, invert, flag):
    super().__init__(portfwd, invert)
    self.flag = flag

  @prop
  def test_argv(self):
    return ['flag', self.flag]

  def test(self, trace=False):
    ''' Core test, before inversion.
    '''
    if trace:
      info("test flag %r", self.flag)
    return self.portfwd.flags[self.flag]

class PingCondition(_PortfwdCondition):
  def __init__(self, portfwd, invert, ping_target):
    super().__init__(portfwd, invert)
    self.ping_target = ping_target
    self.ping_argv = [ 'ping', '-c', '1', '-t', '3', self.ping_target ]

  @prop
  def test_argv(self):
    return self.ping_argv

  def test(self, trace=False):
    if trace:
      info("run %r", self.ping_argv)
    retcode = subprocess.call(
                self.ping_argv,
                stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL)
    return retcode == 0

if __name__ == '__main__':
  sys.exit(main(sys.argv))

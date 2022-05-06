#!/usr/bin/python
#
# Manage ssh based port forwards.
# - Cameron Simpson <cs@cskk.id.au> May 2017
#

r'''
Manage persistent ssh tunnels and port forwards.

Portfwd runs a collection of ssh tunnel commands persistently,
each in its own `cs.app.svcd <https://pypi.org/project/cs.app.svcd>`_ instance
with all the visibility and process control that SvcD brings.

It reads tunnel preconditions from special comments within the ssh config file.
It uses the configuration options from the config file
as the SvcD signature function
thus restarting particular ssh tunnels when their specific configuration changes.
It has an "automatic" mode (the -A option)
which monitors the desired list of tunnels
from statuses expressed via `cs.app.flag <https://pypi.org/project/cs.app.flag>`_
which allows live addition or removal of tunnels as needed.
'''

from __future__ import print_function
from collections import defaultdict
import errno
from getopt import getopt, GetoptError
import os
from os.path import basename, exists as pathexists
import re
from signal import signal, SIGHUP, SIGINT, SIGTERM
import subprocess
import sys
from threading import RLock
from time import sleep
from cs.app.flag import Flags, uppername, lowername, FlaggedMixin
from cs.app.svcd import SvcD
from cs.env import envsub
from cs.logutils import setup_logging, info, warning, error
from cs.pfx import Pfx
from cs.psutils import pipefrom
from cs.py.func import prop
from cs.py3 import DEVNULL
from cs.sh import quotecmd as shq

__version__ = '20210316-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.app.flag',
        'cs.app.svcd',
        'cs.env',
        'cs.logutils',
        'cs.pfx',
        'cs.psutils',
        'cs.py.func',
        'cs.py3',
        'cs.sh',
    ],
    'entry_points': {
        'console_scripts': ['portfwd = cs.app.portfwd:main'],
    },
}

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

def main(argv=None, environ=None):
  ''' Command line main programme.
  '''
  if argv is None:
    argv = sys.argv
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
  flags = Flags(environ=environ, lock=RLock())
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
        raise GetoptError(
            "%s: extra arguments after target %r: %s" %
            (opt1, target, ' '.join(argv))
        )
      PFs = Portfwds(
          ssh_config=sshcfg,
          target_list=(target,),
          auto_mode=auto_mode,
          trace=trace,
          verbose=verbose,
          flags=flags
      )
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
      raise GetoptError(
          "missing targets; targets or -A (auto) option required"
      )
    target_list = argv
    if once and (auto_mode or len(target_list) != 1):
      raise GetoptError(
          "once (-1) requires no auto mode (-A) and exactly one target"
      )
  except GetoptError as e:
    warning("%s", e)
    badopts = True
  if badopts:
    print(usage, file=sys.stderr)
    return 2

  PFs = Portfwds(
      ssh_config=sshcfg,
      target_list=argv,
      auto_mode=auto_mode,
      trace=trace,
      verbose=verbose,
      flags=flags
  )
  if not doit:
    for target in sorted(PFs.targets_required()):
      print(PFs.forward(target))
    return 0

  running = True

  def signal_handler(*_):
    ''' Action on signal receipt: stop the portfwds and wait, then exit(1).
    '''
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
  ''' An ssh tunnel built on a SvcD.
  '''

  def __init__(
      self,
      target,
      ssh_config=None,
      conditions=(),
      test_shcmd=None,
      trace=False,
      verbose=False,
      flags=None
  ):
    ''' Initialise the Portfwd.

        Parameters:
        * `target`: the tunnel name, and also the name of the ssh configuration used
        * `ssh_config`: ssh configuration file if not the default
        * `conditions`: an iterable of `Condition`s
          which must hold before the tunnel is set up;
          the tunnel also aborts if these cease to hold
        * `test_shcmd`: a shell command which must succeed
          before the tunnel is set up;
          the tunnel also aborts if this command subsequently fails
        * `trace`: issue tracing messages; default `False`
        * `verbose`: be verbose; default `False`
        * `flags`: optional preexisting `Flags` instance
    '''
    self.name = 'portfwd-' + target
    FlaggedMixin.__init__(self, flags=flags)
    self.test_shcmd = test_shcmd
    self.ssh_config = ssh_config
    self.conditions = conditions
    self.trace = trace
    self.verbose = verbose
    self.target = target
    self.svcd_name = 'portfwd-' + target
    self.group_name = 'PORTFWD ' + target.upper()
    self.flag_connected = False
    self.svcd = SvcD(
        self.ssh_argv(),
        name=self.svcd_name,
        group_name=self.group_name,
        flags=self.flags,
        trace=trace,
        sig_func=self.ssh_options,
        test_func=self.test_func,
        test_flags={
            'PORTFWD_DISABLE': False,
            'PORTFWD_SSH_READY': True,
            'ROUTE_DEFAULT': True,
        },
        on_spawn=self.on_spawn,
        on_reap=self.on_reap
    )

  def __str__(self):
    return "Portfwd %s %s" % (self.target, shq(self.ssh_argv()))

  def start(self):
    ''' Call the service start method.
    '''
    self.svcd.start()

  def stop(self):
    ''' Call the service stop method.
    '''
    self.svcd.stop()

  def wait(self):
    ''' Call the service wait method.
    '''
    self.svcd.wait()

  def ssh_argv(self, bare=False):
    ''' An ssh command line argument list.

        `bare`: just to command and options, no trailing "--".
    '''
    argv = ['ssh']
    if self.verbose:
      argv.append('-v')
    if self.ssh_config:
      argv.extend(['-F', self.ssh_config])
    argv.extend(
        [
            '-N',
            '-T',
            '-o',
            'ExitOnForwardFailure=yes',
            '-o',
            'PermitLocalCommand=yes',
            '-o',
            'LocalCommand=' + self.ssh_localcommand,
        ]
    )
    if not bare:
      argv.extend(['--', self.target])
    return argv

  def ssh_options(self):
    ''' Return a defaultdict(list) of `{option: values}`
        representing the ssh configuration.
    '''
    with Pfx("ssh_options(%r)", self.target):
      argv = self.ssh_argv(bare=True) + ['-G', '--', self.target]
      P = pipefrom(argv)
      options = defaultdict(list)
      parsed = [line.strip().split(None, 1) for line in P.stdout]
      retcode = P.wait()
      if retcode != 0:
        error("%r: non-zero return code: %s", argv, retcode)
      else:
        for parsed_item in parsed:
          option = parsed_item.pop(0)
          if parsed_item:
            value, = parsed_item
            options[option].append(value)
      return options

  @prop
  def ssh_localcommand(self):
    ''' Shell command for ssh to invoke on connection ready.
    '''
    setflag_argv = ['flag', '-w', self.flagname_connected, '1']
    alert_group = self.group_name
    alert_title = self.target.upper()
    alert_message = 'CONNECTED: ' + self.target
    alert_argv = [
        'alert', '-g', alert_group, '-t', alert_title, '--', alert_message
    ]
    shcmd = 'exec </dev/null; ' + shq(setflag_argv
                                      ) + '; ' + shq(alert_argv) + ' &'
    return shcmd

  def on_spawn(self):
    ''' Actions to perform before commencing the ssh tunnel.

        Initially remove local socket paths.
    '''
    options = self.ssh_options()
    for localforward in options['localforward']:
      local, remote = localforward.split(None, 1)
      if '/' in local:
        with Pfx("remove %r", local):
          try:
            os.remove(local)
          except OSError as e:
            if e.errno == errno.ENOENT:
              pass
            else:
              raise
          else:
            info("removed")
    if (options['controlmaster'] == [
        'true',
    ] and options['controlpath'] != [
        'none',
    ]):
      controlpath, = options['controlpath']
      with Pfx("remove %r", controlpath):
        try:
          os.remove(controlpath)
        except OSError as e:
          if e.errno == errno.ENOENT:
            pass
          else:
            raise
        else:
          info("removed")

  def on_reap(self):
    ''' Actions to perform after the ssh tunnel exits.
    '''
    self.flag_connected = False

  def test_func(self):
    ''' Servuice test function: probe all the conditions.
    '''
    with Pfx("%s[%s].test_func", type(self).__name__, self.name):
      for condition in self.conditions:
        with Pfx("precondition %s", condition):
          if not condition.probe():
            if self.verbose:
              info('FAILED')
            return False
      if self.test_shcmd:
        with Pfx("test_shcmd %r", self.test_shcmd):
          shcmd_ok = os.system(self.test_shcmd) == 0
          if not shcmd_ok:
            info('FAILED')
            return False
      return True

class Portfwds(object):
  ''' A collection of `Portfwd` instances and associated control methods.
  '''

  def __init__(
      self,
      ssh_config=None,
      environ=None,
      target_list=None,
      auto_mode=None,
      trace=False,
      verbose=False,
      flags=None
  ):
    ''' Initialise the `Portfwds` instance.

        Parameters:
        * `ssh_config`: the ssh configuration file if not the default
        * `environ`: the environment mapping to use;
          default: `os.environ`
        * `target_list`: an iterable of `Portfwd` target names
        * `auto_mode`: also derive target names
          from the set of true `PORTFWD_`*name*`_AUTO` flags
        * `trace`: trace mode, default `False`
        * `verbose`: verbose mode, default `False`
        * `flags`: the `cs.app.flags.Flags` instance to use,
          default is to construct a new one
    '''
    if environ is None:
      environ = os.environ
    if target_list is None:
      target_list = []
    else:
      target_list = list(target_list)
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
    if self.ssh_config:
      self._load_ssh_config()

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
      P = Portfwd(
          target,
          ssh_config=self.ssh_config,
          trace=self.trace,
          flags=self.flags,
          conditions=self.target_conditions[target]
      )
      self._forwards[target] = P
    return P

  def start(self):
    ''' Start all nonrunning targets, stop all running nonrequired targets.
    '''
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
    ''' Stop all running targets.
    '''
    for P in self.targets_running.values():
      P.stop()

  def wait(self):
    ''' Wait for all running targets to stop.
    '''
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
    ''' The concrete list of targets.

        Computed from the target spec and, if in auto mode, the
        PORTFWD_*_AUTO flags.
    '''
    targets = set()
    for spec in self.target_list:
      targets.update(self.resolve_target_spec(spec))
    if self.auto_mode:
      for flagname in self.flags:
        if (flagname.startswith('PORTFWD_') and flagname.endswith('_AUTO')
            and self.flags[flagname]):
          targets.add(lowername(flagname[8:-5]))
    return targets

  GROUP_NAME = r'[A-Z][A-Z0-9_]*'
  GROUP_NAME_RE = re.compile(GROUP_NAME)
  SPECIAL_RE = re.compile(r'^\s*#\s*(' + GROUP_NAME + r'):\s*')

  @prop
  def ssh_config(self):
    ''' The path to the ssh configuration file.
    '''
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
                words = tail.split()
                if label == 'F':
                  # F: target condition...
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
                      invert = False
                      if words:
                        if words[0] == '!':
                          invert = True
                          words.pop(0)
                        elif words[0].startswith('!'):
                          invert = True
                          words[0] = words[0][1:]
                      C = Condition(self, op, invert, *words)
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
  ''' Base class for port forward conditions.
  '''

  def __init__(self, portfwd, invert):
    self.portfwd = portfwd
    self.invert = invert

  def __str__(self):
    return "%s%s[%s]" % (
        self.__class__.__name__, '!' if self.invert else '', ','.join(
            "%s=%r" % (attr, getattr(self, attr))
            for attr in sorted(self._attrnames)
        )
    )

  def __bool__(self):
    if self.test():
      return not self.invert
    return self.invert

  __nonzero__ = __bool__

  def shcmd(self):
    ''' The test argv as a shell command.
    '''
    cmd = ' '.join(shq(self.test_argv))
    if self.invert:
      cmd = 'if ' + cmd + '; then false; else true; fi'
    return cmd

  def probe(self):
    ''' Probe the condition: run the test function, optionally invert the result.
    '''
    result = self.test()
    return not result if self.invert else result

class FlagCondition(_PortfwdCondition):
  ''' A flag based condition.
  '''

  _attrnames = ['flag']

  def __init__(self, portfwd, invert, flag):
    super().__init__(portfwd, invert)
    self.flag = flag

  @prop
  def test_argv(self):
    ''' Argv for testing a flag.
    '''
    return ['flag', self.flag]

  def test(self, trace=False):
    ''' Core test, before inversion.
    '''
    if trace:
      info("test flag %r", self.flag)
    return self.portfwd.flags[self.flag]

class PingCondition(_PortfwdCondition):
  ''' A ping based condition.
  '''

  _attrnames = ['ping_target']

  def __init__(self, portfwd, invert, ping_target):
    super().__init__(portfwd, invert)
    self.ping_target = ping_target
    self.ping_argv = ['ping', '-c', '1', '-t', '3', self.ping_target]

  @prop
  def test_argv(self):
    ''' Test argv for ping.
    '''
    return self.ping_argv

  def test(self, trace=False):
    ''' Ping the target as a test.
    '''
    if trace:
      info("run %r", self.ping_argv)
    retcode = subprocess.call(
        self.ping_argv, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL
    )
    return retcode == 0

if __name__ == '__main__':
  sys.exit(main(sys.argv))

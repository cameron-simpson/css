#!/usr/bin/env python3
#
# Daemon to run and restart a command that provides a service, such
# as an ssh port forward.
#       - Cameron Simpson <cs@cskk.id.au> 10dec2012
#
# Recode as Python module.  - cameron 14may2017
#

r'''
SvcD class and "svcd" command to run persistent service programmes.

This provides the features one wants from a daemon
for arbitrary commands providing a service:

* process id (pid) files for both svcd and the service command
* filesystem visible status (command running, service enabled)
  via `cs.app.flag`
* command restart if the command exits
* command control (stop, restart, disable) via `cs.app.flag`
* test function to monitor for service viability;
  if the test function fails, do not run the service.
  This typically monitors something like
  network routing (suspend service while laptop offline)
  or a ping (suspend ssh tunnel while target does not answer pings).
* signature function to monitor for service restart;
  if the signature changes, restart the service.
  This typically monitors something like
  file contents (restart service on configuration change)
  or network routing (restart ssh tunnel on network change)
* callbacks for service command start and end,
  for example to display desktop notifications

I use this to run persistent ssh port forwards
and a small collection of other personal services.
I have convenient shell commands to look up service status
and to start/stop/restart services.

See `cs.app.portfwd` which I use to manage my `ssh` tunnels;
it is a single Python programme
running multiple `ssh` commands, each via its own `SvcD` instance.
'''

from __future__ import print_function
from getopt import getopt, GetoptError
import os
from os.path import basename, join as joinpath, splitext
from pwd import getpwnam, getpwuid
from signal import SIGHUP, SIGINT, SIGTERM
from subprocess import Popen, PIPE
import sys
from threading import Lock
from time import sleep, time as now

from cs.app.flag import Flags, FlaggedMixin
from cs.cmdutils import BaseCommand
from cs.env import VARRUN
from cs.gimmicks import DEVNULL
from cs.lex import cutsuffix
from cs.logutils import warning, info, debug, exception
from cs.pfx import Pfx, PfxThread as Thread
from cs.psutils import (
    PidFileManager,
    write_pidfile,
    remove_pidfile,
    signal_handlers,
)
from cs.sh import quotecmd

__version__ = '20221228-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        "Topic :: Utilities",
    ],
    'install_requires': [
        'cs.app.flag',
        'cs.cmdutils',
        'cs.env',
        'cs.gimmicks',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.psutils',
        'cs.sh',
    ],
    'entry_points': {
        'console_scripts': ['svcd = cs.app.svcd:main'],
    },
}

def main(argv=None):
  ''' svcd command line.
  '''
  return SvcDCommand(argv).run()

class SvcDCommand(BaseCommand):
  ''' Implementation of `SvcD` command line mode.
  '''

  def disable(self, argv):
    ''' {cmd} disable names...
          For each name set the flag {{NAME}}_DISABLE, causing the matching
          svcd to shut down its daemon process.
    '''
    if not argv:
      raise GetoptError("missing arguments")
    for name in argv:
      with Pfx(name):
        SvcD(name=name).disable()

  def cmd_enable(self, argv):
    ''' {cmd} enable names
          For each name clear the flag {{NAME}}_DISABLE, allowing the matching
          svcd to start up its daemon process.
    '''
    if not argv:
      raise GetoptError("missing arguments")
    for name in argv:
      with Pfx(name):
        SvcD(name=name).enable()

  def cmd_ls(self, argv):
    ''' Usage: {cmd}
          List known services.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    flags = Flags()
    for flagname in sorted(flags):
      flag_prefix = cutsuffix(flagname, '_RUNNING')
      if flag_prefix is not flagname:
        flag = flags[flagname]
        print(flag_prefix.lower(), "running" if flag else "stopped")

  def cmd_restart(self, argv):
    ''' {cmd} restart names...
          For each name set the flag {{NAME}}_RESTART, causing the matching
          svcd to shut down and then restart its daemon process.
    '''
    if not argv:
      raise GetoptError("missing arguments")
    for name in argv:
      with Pfx(name):
        SvcD(name=name).restart()

  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
  def cmd_run(self, argv):
    ''' Usage: {cmd} [-1] [-l] [-L lockname] [-n name] [-t testcmd] [-x] command [args...]
          Run a daemon command.
          -1    Run command only once.
          -l    Use lock \"svcd-<name>\" to prevent multiple instances of this svcd.
          -F [!]flag,...
                Flags to include in the run test. Flags with a leading
                exclaimation point (!) must test false, others true.
          -L lockname
                Use lock \"lockname\" to prevent multiple instances of this svcd.
          -n name
                Specify a name for this svcd.
                Also create a subprocess pid file at {VARRUN}/name.pid for the command.
                This also causes svcd to consult the flags {{NAME}}_OVERRIDE
                and {{NAME}}_DISABLE and {{NAME}}_RESTART.
          -p svcd-pidfile
                Specify {cmd} pid file instead of default.
          -P subp-pidfile
                Specify {cmd} subprocess pid file instead of default.
          -q    Quiet. Do not issue alerts.
          -s sigcmd
                Run the signature shell command \"sigcmd\" whose output is
                used to check for changed circumstances requiring the service
                to restart.
          -t testcmd
                Run the test shell command \"testcmd\" periodically to
                govern whether the command should be active.
          -T testrate
                Interval between test polls in seconds. Default from SvcD.TEST_RATE
          -u username
                Run command as the specified username.
          -U username
                Run test and related commands as the specified username.
          -x    Trace execution.
    '''
    badopts = False
    once = False
    use_lock = False
    lock_name = None
    name = None
    svc_pidfile = None  # pid file for the service process
    mypidfile = None  # pid file for the svcd
    quiet = False
    sig_shcmd = None
    test_shcmd = None
    test_rate = SvcD.TEST_RATE
    uid = os.geteuid()
    username = getpwuid(uid).pw_name
    run_uid = uid
    run_username = username
    test_uid = uid
    test_username = username
    test_flags = {}
    trace = sys.stderr.isatty()
    opts, argv = getopt(argv, '1lF:L:n:p:P:qs:t:T:u:U:x')
    for opt, value in opts:
      with Pfx(opt):
        if opt == '-1':
          once = True
        elif opt == '-l':
          use_lock = True
        elif opt == '-F':
          for flagname in value.split(','):
            with Pfx(flagname):
              truthiness = True
              if flagname.startswith('!'):
                truthiness = False
                flagname = flagname[1:]
              if not flagname:
                warning("invalid empty flag name")
                badopts = True
              else:
                test_flags[flagname] = truthiness
        elif opt == '-L':
          use_lock = True
          lock_name = value
        elif opt == '-n':
          name = value
        elif opt == '-p':
          svc_pidfile = value
        elif opt == '-P':
          mypidfile = value
        elif opt == '-q':
          quiet = True
        elif opt == '-s':
          sig_shcmd = value
        elif opt == '-t':
          test_shcmd = value
        elif opt == '-T':
          try:
            test_rate = int(value)
          except ValueError as e:
            warning("testrate should be a valid integer: %s", e)
            badopts = True
        elif opt == '-u':
          run_username = value
          run_uid = getpwnam(run_username).pw_uid
        elif opt == '-U':
          test_username = value
          test_uid = getpwnam(test_username).pw_uid
        elif opt == '-x':
          trace = True
        else:
          raise RuntimeError("unhandled option")
    if not argv:
      warning("missing command")
    if name is None:
      name = basename(argv[0])
    if use_lock and name is None:
      warning("-l (lock) requires a name (-n)")
      badopts = True
    if badopts:
      raise GetoptError("bad invocation")

    if sig_shcmd is None:
      sig_func = None
    else:

      def sig_func():
        argv = ['sh', ('-xc' if trace else '-c'), sig_shcmd]
        if test_uid != uid:
          su_shcmd = 'exec ' + quotecmd(argv)
          if trace:
            su_shcmd = 'set -x; ' + su_shcmd
          argv = ['su', test_username, '-c', su_shcmd]
        P = LockedPopen(argv, stdin=DEVNULL, stdout=PIPE)
        sig_text = P.stdout.read()
        returncode = P.wait()
        if returncode != 0:
          warning("returncode %s from %r", returncode, sig_shcmd)
          sig_text = None
        return sig_text

    if test_shcmd is None:
      test_func = None
    else:

      def test_func():
        with Pfx("main.test_func: shcmd=%r", test_shcmd):
          argv = ['sh', '-c', test_shcmd]
          if test_uid != uid:
            argv = ['su', test_username, 'exec ' + quotecmd(argv)]
          shcmd_ok = callproc(argv, stdin=DEVNULL) == 0
          if not quiet:
            info("exit status != 0")
          return shcmd_ok

    if run_uid != uid:
      argv = ['su', run_username, 'exec ' + quotecmd(argv)]
    if use_lock:
      argv = ['lock', '--', 'svcd-' + name] + argv
    S = SvcD(
        *argv,
        name=name,
        pidfile=svc_pidfile,
        sig_func=sig_func,
        test_flags=test_flags,
        test_func=test_func,
        test_rate=test_rate,
        once=once,
        quiet=quiet,
        trace=trace
    )

    def signal_handler(*_):
      S.stop()
      S.wait()
      S.flag_stop = False
      sys.exit(1)

    with signal_handlers({
        SIGHUP: signal_handler,
        SIGINT: signal_handler,
        SIGTERM: signal_handler,
    }):
      if S.pidfile or mypidfile:
        if mypidfile is None:
          pidfile_base, pidfile_ext = splitext(S.pidfile)
          mypidfile = pidfile_base + '-svcd' + pidfile_ext
        with PidFileManager(mypidfile):
          S.start()
          S.wait()
      else:
        S.start()
        S.wait()

  def cmd_stop(self, argv):
    ''' {cmd} stop names...
          For each name set the flag {{NAME}}_STOP, causing the the
          montior thread to kill the daemon process and exit.
    '''
    if not argv:
      raise GetoptError("missing arguments")
    for name in argv:
      with Pfx(name):
        SvcD(name=name).stop()

_Popen_lock = Lock()

def LockedPopen(*a, **kw):
  ''' Serialise the `Popen` calls.

      My long term multithreaded `SvcD` programmes sometimes coredumps.
      My working theory is that `Popen`, maybe only on MacOS, is
      slightly not multithead safe. This function exists to test
      that theory.
  '''
  with _Popen_lock:
    P = Popen(*a, **kw)  # pylint: disable=consider-using-with
  return P

def callproc(*a, **kw):
  ''' Workalike for subprocess.call, using LockedPopen.
  '''
  P = LockedPopen(*a, **kw)
  P.wait()
  return P.returncode

# pylint: disable=too-many-instance-attributes
class SvcD(FlaggedMixin, object):
  ''' A process based service.
  '''

  TEST_RATE = 7  # frequency of polling of test condition
  KILL_TIME = 5  # how long to wait for a terminated process to exit
  RESTART_DELAY = 3  # delay be restart of an exited process

  # pylint: disable=too-many-locals
  def __init__(
      self,
      *argv,
      name=None,
      environ=None,
      flags=None,
      group_name=None,
      pidfile=None,
      sig_func=None,
      test_flags=None,
      test_func=None,
      test_rate=None,
      restart_delay=None,
      once=False,
      quiet=False,
      trace=False,
      on_spawn=None,
      on_reap=None,
  ):
    ''' Initialise the SvcD.

        Parameters:
        * `argv`: command to run as a subprocess.
        * `flags`: a cs.app.flag.Flags -like object, default None;
          if None the default flags will be used.
        * `group_name`: alert group name, default "SVCD " + `name`.
        * `pidfile`: path to pid file, default $VARRUN/{name}.pid.
        * `sig_func`: signature function to compute a string which
          causes a restart if it changes
        * `test_flags`: map of {flagname: truthiness} which should
          be monitored at test time; truthy flags must be true and
          untruthy flags must be false
        * `test_func`: test function with must return true if the comannd can run
        * `test_rate`: frequency of tests, default SvcD.TEST_RATE
        * `restart_delay`: delay before start of an exiting command,
          default SvcD.RESTART_DELAY
        * `once`: if true, run the command only once
        * `quiet`: if true, do not issue alerts
        * `trace`: trace actions, default False
        * `on_spawn`: to be called after a new subprocess is spawned
        * `on_reap`: to be called after a subprocess is reaped
    '''
    if name is None:
      name = 'UNNAMED'
    if environ is None:
      environ = os.environ
    if pidfile is None and name is not None:
      pidfile = joinpath(VARRUN(environ=environ), name + '.pid')
    if flags is None:
      flags = Flags(environ=environ)  ##, debug=trace)
    if group_name is None:
      group_name = "SVCD " + name
    if test_flags is None:
      test_flags = {}
    if test_rate is None:
      test_rate = self.TEST_RATE
    if restart_delay is None:
      restart_delay = self.RESTART_DELAY
    FlaggedMixin.__init__(self, flags=flags)
    self.argv = argv
    self.name = name
    self.group_name = group_name
    self.test_flags = test_flags
    self.test_func = test_func
    self.test_rate = test_rate
    self.restart_delay = restart_delay
    self.once = once
    self.quiet = quiet
    self.trace = trace
    self.on_spawn = on_spawn
    self.on_reap = on_reap
    self.active = False  # flag to end the monitor Thread
    self.subp = None  # current subprocess
    self.monitor = None  # monitoring Thread
    self.pidfile = pidfile
    self.sig_func = sig_func

  def __str__(self):
    name = getattr(self, 'name', None)
    if name is None:
      return self.__class__.__name__
    return self.__class__.__name__ + ':' + self.name

  def __repr__(self):
    argv = getattr(self, 'argv', None)
    if argv is None:
      return str(self)
    return str(self) + repr(argv)

  def dbg(self, msg, *a):
    ''' Log a debug message if tracing.
    '''
    if not self.trace:
      return
    debug("%s: " + msg, self, *a)

  def test(self):
    ''' Test whether the service should run.

        In order:
        * `True` if the override flag is true.
        * `False` if the disable flag is true.
        * `False` if any of the specified test flags are false.
        * `False` if the test function fails.
        * Otherwise `True`.
    '''
    with Pfx("%s[%s].test", type(self).__name__, self.name):
      if self.flag_override:
        self.dbg("flag_override true -> True")
        return True
      if self.flag_disable:
        self.dbg("flag_disable true -> False")
        return False
      for flagname, truish in self.test_flags.items():
        if self.flags[flagname]:
          if not truish:
            self.dbg("flags[%r] -> False", flagname)
            return False
        elif truish:
          self.dbg("!flags[%r] -> False", flagname)
          return False
      if self.test_func is not None:
        result = self.test_func()
        if not result:
          self.dbg("test_func -> %r", result)
        return result
      self.dbg("default -> True")
      return True

  def alert(self, msg, *a):
    ''' Issue an alert message via the "alert" command.
    '''
    if self.quiet:
      return
    if a:
      msg = msg % a
    alert_argv = [
        'alert', '-g', self.group_name,
        'SVCD %s: %s' % (self.name, msg)
    ]
    if self.trace:
      info("alert: %s: %s" % (self.name, msg))
    LockedPopen(alert_argv, stdin=DEVNULL)

  def spawn(self):
    ''' Spawn the subprocess.

        Calls the `on_spwan` function if any.
    '''
    if self.subp is not None:
      raise RuntimeError("already running")
    self.dbg("%s: spawn %r", self.name, self.argv)
    self.subp = LockedPopen(self.argv, stdin=DEVNULL)
    self.flag_running = True
    self.alert('STARTED')
    if self.pidfile is not None:
      write_pidfile(self.pidfile, self.subp.pid)
    if self.on_spawn:
      self.on_spawn()

  def reap(self):
    ''' Collect the subprocess status after termination.

        Calls the `on_reap` function if any.
    '''
    if self.subp is None:
      raise RuntimeError("not running")
    returncode = self.subp.wait()
    self.flag_running = False
    self.dbg("subprocess returncode = %s %r", returncode, self.argv)
    self.alert('EXITED')
    self.subp = None
    if self.pidfile is not None:
      remove_pidfile(self.pidfile)
    if self.on_reap:
      self.on_reap()
    return returncode

  def _kill_subproc(self):
    ''' Kill the subprocess and return its exit code.
        Sends `SIGTERM`, then `SIGKILL` if the process does not die promptly.
    '''
    self.subp.terminate()
    final_time = now() + self.KILL_TIME
    while self.probe() and now() < final_time:
      sleep(1)
    if self.probe():
      self.subp.kill()
    return self.reap()

  # pylint: disable=too-many-statements
  def start(self):
    ''' Start the subprocess and its monitor.
    '''
    with Pfx("SvcD.start(%s)", self):

      # pylint: disable=too-many-statements,too-many-branches,too-many-nested-blocks
      def monitor():
        old_sig = None
        next_test_time = now()
        next_start_time = now()
        while True:
          # check for termination state
          if self.flag_stop:
            self.flag_stop = False
            break
          # check for process exit
          if self.subp is not None and not self.probe():
            self.reap()
            if self.once:
              break
            next_start_time = now() + self.restart_delay
          if self.subp is None:
            # not running - see if it should start
            if now() >= max(next_test_time, next_start_time):
              if self.test():
                # test passes, start service
                self.spawn()
              next_test_time = now() + self.test_rate
          else:
            # running - see if it should stop
            stop = False
            if self.flag_restart:
              self.flag_restart = False
              stop = True
            elif now() >= next_test_time:
              if not self.test():
                stop = True
              next_test_time = now() + self.test_rate
            if not stop and self.sig_func is not None:
              try:
                new_sig = self.sig_func()
              except Exception as e:  # pylint: disable=broad-except
                exception("sig_func: %s", e)
                new_sig = None
              if new_sig is not None:
                if old_sig is None:
                  # initial signature probe
                  old_sig = new_sig
                else:
                  try:
                    changed = new_sig != old_sig
                  except TypeError as e:
                    warning(
                        "type error comparing old_sig %s with new_sig %s: %s",
                        type(old_sig),
                        type(new_sig),
                        e,
                    )
                    old_sig = new_sig
                  else:
                    if changed:
                      old_sig = new_sig
                      stop = True
            if stop:
              self._kill_subproc()
              sleep(self.restart_delay)
          sleep(1)
        if self.subp is not None:
          self._kill_subproc()

      T = Thread(name=str(self) + ':monitor', target=monitor)
      if self.flag_stop:
        warning("clearing flag %s before starting thread", self.flagname_stop)
        self.flag_stop = False
      T.start()
      self.monitor = T

  def stop(self):
    ''' Set the stop flag.
    '''
    self.flag_stop = True

  def wait(self):
    ''' Wait for the subprocess by waiting for the monitor.
    '''
    if self.monitor:
      self.monitor.join()
      self.monitor = None

  def restart(self):
    ''' Set the restart flag, will be cleared by the restart.
    '''
    self.flag_restart = True

  def disable(self):
    ''' Turn on the disable flag.
    '''
    self.flag_disable = True

  def enable(self):
    ''' Turn of the disable flag.
    '''
    self.flag_disable = False

  def probe(self):
    ''' Probe the subprocess: true if running.
    '''
    if self.subp is None:
      return False
    return self.subp.poll() is None

if __name__ == '__main__':
  sys.exit(main(sys.argv))

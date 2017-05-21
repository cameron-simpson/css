#!/usr/bin/env python3
#
# Daemon to run and restart a command that provides a service, such
# as an ssh port forward.
#       - Cameron Simpson <cs@zip.com.au> 10dec2012
#
# Recode as Python module.  - cameron 14may2017
#

from __future__ import print_function
from getopt import getopt, GetoptError
import os
from os.path import basename, join as joinpath
import pwd
from subprocess import Popen, DEVNULL, call as callproc
import sys
from time import sleep, time as now
from cs.app.flag import Flags, uppername, FlaggedMixin
from cs.env import LOGDIR, VARRUN
from cs.logutils import setup_logging, warning, X, Pfx, PfxThread as Thread
from cs.py.func import prop

TEST_RATE = 7
KILL_TIME = 5

USAGE = '''Usage:
  %s disable names...
          FOr each name set the flag {NAME}_DISABLE, causing the matching
          svcd to shut down its daemon process.
  %s enable names
          For each name clear the flag {NAME}_DISABLE, allowing the matching
          svcd to start up its daemon process.
  %s restart names...
          For each name set the flag {NAME}_RESTART, causing the matching
          svcd to shut down and then restart its daemon process.
  %s stop names...
          For each name set the flag {NAME}_STOP, causing the the
          montior thread to kill the daemon process and exit.
  %s [-1] [-l] [-L lockname] [-n name] [-t testcmd] [-x] command [args...]
    -1    Run command only once.
    -l    Use lock \"svcd-<name>\" to prevent multiple instances of this svcd.
    -L lockname
          Use lock \"lockname\" to prevent multiple instances of this svcd.
    -n name
          Specify a name for this svcd.
          Also create a subprocess pid file at $VARRUN/name.pid for the command.
          This also causes svcd to consult the flags {NAME}_OVERRIDE
          and {NAME}_DISABLE and {NAME}_RESTART.
    -p svcd-pidfile
          Specify $cmd pid file instead of default.
    -P subp-pidfile
          Specify $cmd subprocess pid file instead of default.
    -q    Quiet. Do not issue alerts.
    -s sigcmd
          Run the signature shell command \"sigcmd\" whose output is
          used to check for changed circumstances requiring the service
          to restart.
    -t testcmd
          Run the test shell command \"testcmd\" periodically to
          govern whether the command should be active.
    -T testrate
          Interval between test polls in seconds. Default: $testrate
    -u username
          Run command as the specified username.
    -U username
          Run test and related commands as the specified username.
    -x    Trace execution.'''

def main(argv, environ=None):
  cmd = basename(argv.pop(0))
  usage = USAGE % (cmd, cmd, cmd, cmd, cmd)
  setup_logging(cmd)

  flags = Flags()

  badopts = False
  try:
    if not argv:
      raise GetoptError("missing arguments")
    arg0 = argv[0]
    if arg0 == 'disable':
      for name in argv:
        Svcd(name=name).disable()
      return 0
    if arg0 == 'enable':
      for name in argv:
        Svcd(name=name).enable()
      return 0
    if arg0 == 'restart':
      for name in argv:
        Svcd(name=name).restart()
      return 0
    if arg0 == 'stop':
      for name in argv:
        SvcD(name=name).stop()
      return 0
    once = False
    use_lock = False
    lock_name = None
    name = None
    svcd_pidfile = None
    subprocess_pidfile = None
    quiet = False
    sig_shcmd = None
    test_shcmd = None
    test_rate = TEST_RATE
    uid = os.geteuid()
    username = pwd.getpwuid(uid).pw_name
    run_uid = uid
    run_username = username
    test_uid = uid
    test_username = username
    trace = False
    opts, argv = getopt(argv, '1lL:n:p:P:qs:t:T:u:U:x')
    for opt, value in opts:
      with Pfx(opt):
        if opt == '-1':
          once = True
        elif opt == '-l':
          use_lock = True
        elif opt == '-L':
          use_lock = True
          lock_name = value
        elif opt == '-n':
          name = value
        elif opt == '-p':
          svcd_pidfile = value
        elif opt == '-P':
          subprocess_pidfile = value
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
            raise GetoptError("testrate should be a valid integer: %s" % (e,))
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
    if use_lock and name is None:
      raise GetoptError("-l (lock) requires a name (-n)")
    if not argv:
      raise GetoptError("missing command")
  except GetoptError as e:
    warning("%s", e)
    badopts = True
  if badopts:
    print(usage, file=sys.stderr)
    return 2
  if sig_shcmd is None:
    sig_func = None
  else:
    def sig_func():
      argv = ['sh', '-c', sig_shcmd]
      if test_uid != uid:
        argv = ['sux', '-u', test_username, '--'] + argv
      P = Popen(argv, stdin=DEVNULL, stdout=PIPE) == 0
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
      argv = ['sh', '-c', test_shcmd]
      if test_uid != uid:
        argv = ['sux', '-u', test_username, '--'] + argv
      return callproc(argv, stdin=DEVNULL) == 0
  if run_uid != uid:
    argv = ['sux', '-u', run_username, '--'] + argv
  if use_lock:
    argv = ['lock', '--', 'svcd-' + name] + argv
  S = SvcD(argv, name=name, sig_func=sig_func, test_func=test_func, test_rate=test_rate)
  S.start()
  S.wait()

class SvcD(FlaggedMixin, object):

  def __init__(self, argv, name=None,
        environ=None,
        flags=None,
        logdir=None,
        pidfile=None,
        sig_func=None,
        test_flags=None,
        test_func=None,
        test_rate=None,
        varrun=None
    ):
    if environ is None:
      environ = os.environ
    if logdir is None:
      logdir = joinpath(LOGDIR(environ), 'svc')
    if varrun is None:
      varrun = VARRUN(environ)
    if flags is None:
      flags = Flags(environ=environ)
    FlaggedMixin.__init__(self, flags=flags)
    if test_flags is None:
      test_flags = {}
    if test_rate is None:
      test_rate = TEST_RATE
    self.argv = argv
    self.name = name
    self.logdir = logdir
    self.varrun = varrun
    self.test_flags = test_flags
    self.test_func = test_func
    self.test_rate = test_rate
    self.restart_delay = self.test_rate
    self.active = False # flag to end the monitor Thread
    self.subp = None    # current subprocess
    self.monitor = None # monitoring Thread
    if pidfile is None and name is not None:
        pidfile = joinpath(self.varrun, name + '.pid')
    self.pidfile = pidfile
    self.sig_func = sig_func

  def __str__(self):
    if self.name is None:
      return self.__class__.__name__
    return self.__class__.__name__ + ':' + self.name

  def __repr__(self):
    return str(self) + repr(self.argv)

  def test(self):
    if self.flag_override:
      return True
    if self.flag_disabled:
      return False
    for flagname, invert in self.test_flags.items():
      if self.flags[flagname]:
        if invert:
          return False
      elif not invert:
        return False
    if self.test_func is not None:
      return self.test_func()
    return True

  def alert(self, msg, *a):
    if a:
      msg = msg % a
    Popen(['alert', 'SVCD %s: %s' % (self.name, msg)], stdin=DEVNULL)

  def spawn(self):
    if self.subp is not None:
      raise RuntimeError("already running")
    self.subp = Popen(self.argv, stdin=DEVNULL)
    self.alert('STARTED')
    if self.pidfile is not None:
      with open(self.pidfile, 'w') as pidfp:
        print(self.subp.pid, file=pidfp)

  def reap(self):
    if self.subp is None:
      raise RuntimeError("not running")
    returncode = self.subp.wait()
    self.alert('EXITED')
    self.subp = None
    if self.pidfile is not None:
      with open(self.pidfile, 'w') as pidfp:
        pass
      try:
        os.remove(self.pidfile)
      except OSError as e:
        if e.errno != errno.EPERM:
          warning("remove %r: %s", self.pidfile, e)
    return returncode

  def _kill_subproc(self):
    ''' Kill the subprocess and return its exit code.
        Sends SIGTERM, then SIGKILL if the process does not die promptly.
    '''
    self.subp.terminate()
    final_time = now() + KILL_TIME
    while self.probe() and now() < final_time():
      sleep(1)
    if self.probe():
      self.subp.kill()
    return self.reap()

  def start(self):
    def monitor():
      old_sig = ''
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
          next_start_time = now() + self.restart_delay
        if self.subp is None:
          # not running - see if it should start
          if now() >= max(next_test_time, next_start_time) and self.test():
            # test passes, start service
            self.spawn()
        else:
          # running - see if it should stop
          stop = False
          if self.flag_restart:
            self.flag_restart = False
            stop = True
          elif now() >= next_test_time and not self.test():
            stop = True
          if not stop and self.sig_func is not None:
            new_sig = self.sig_func()
            if new_sig is not None and new_sig != old_sig:
              old_sig = new_sig
              stop = True
          if stop:
            self.alert('RESTART')
            self.subp._kill_subproc()
            sleep(restart_delay)
        sleep(1)
      if self.subp is not None:
        self.subp._kill_subproc()
    T = Thread(name=str(self)+':monitor', target=monitor)
    T.start()
    self.monitor = T

  def stop(self):
    self.flag_stop = True

  def wait(self):
    if self.monitor:
      self.monitor.join()
      self.monitor = None

  def restart(self):
    self.flag_restart = True

  def disable(self):
    self.flag_disable = True

  def enable(self):
    self.flag_disabled = False

  def probe(self):
    if self.subp is None:
      return False
    return self.subp.poll() is None

if __name__ == '__main__':
  sys.exit(main(sys.argv))

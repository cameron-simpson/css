#!/usr/bin/python
#
# Convenience functions to work with processes.
#       - Cameron Simpson <cs@zip.com.au> 02sep2011
#

import os
from signal import SIGTERM, SIGKILL
from cs.logutils import Pfx

def stop(pid, signum=SIGTERM, wait=None, do_SIGKILL=False):
  ''' Stop the process specified by `pid`.
      If `pid` is a string, treat as a process id file and read the
      process id from it.
      Send the process the signal `signum`, default signal.SIGTERM.
      If `wait` is unspecified or None, return True (signal delivered).
      If `wait` is 0, wait indefinitely until the process exits as
      tested by os.kill(pid, 0).
      If `wait` is greater than 0, wait up to `wait` seconds for
      the process to die; if it exits, return True, otherwise False;
      if `do_SIGKILL` is true then send the process signal.SIGKILL
      as a final measure before return.
  '''
  if type(pid) is str:
    with Pfx(pid):
      return stop(int(open(pid).read().strip()))
  with Pfx(str(pid)):
    os.kill(pid, signum)
    if wait is None:
      return True
    assert wait >= 0, "wait (%s) should be >= 0" % (wait,)
    now = time.time()
    then = now + wait
    while True:
      time.sleep(0.1)
      if wait == 0 or time.time() < then:
        try:
          os.kill(pid, 0)
        except OSError as e:
          if e.errno != os.ESRCH:
            raise
          # process no longer present
          return True
      else:
        if do_SIGKILL:
          try:
            os.kill(pid, SIGKILL)
          except OSError as e:
            if e.errno != os.ESRCH:
              raise
        return False

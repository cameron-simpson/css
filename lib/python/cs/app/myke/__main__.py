#!/usr/bin/env python3

''' Myke main programme.
'''

from contextlib import contextmanager
from getopt import GetoptError
import sys

from cs.cmdutils import BaseCommand
from cs.logutils import error

from .make import Maker
from .parse import Macro

def main(argv=None):
  return MykeCommand(argv).run()

class MykeCommand(BaseCommand):

  GETOPT_SPEC = 'dD:eEf:ij:kmNnpqrRsS:tuvx'
  USAGE_FORMAT = "Usage: {cmd} [options...] [macro=value...] [targets...]"

  def OPTIONS_CLASS(self):
    ''' Factory function to prepare the `.options` object,
        which is a `Maker`.
    '''
    return Maker(self.cmd)

  def apply_opt(self, opt, val):
    ''' Modify the `Maker` according to a command line option.
        '''
    M = self.options
    if opt == '-d':
      # debug mode
      M.setDebug('make', True)
    elif opt == '-D':
      for flag in [w.strip().lower() for w in val.split(',')]:
        if len(flag) == 0:
          # silently skip empty flag items
          continue
        if flag.startswith('-'):
          val = False
          flag = flag[1:]
        else:
          val = True
        try:
          M.setDebug(flag, val)
        except AttributeError as e:
          raise GetoptError("bad flag %r: %s"%( flag, e))
    elif opt == '-f':
      M._makefiles.append(val)
    elif opt == '-j':
      try:
        val = int(val)
      except ValueError as e:
        raise GetoptError("invalid -j val: %s"%(e,))
      if val < 1:
        raise GetoptError("invalid -j value: %d, must be >= 1"%( val,))
      M.parallel = int(val)
    elif opt == '-k':
      M.fail_fast = False
    elif opt == '-n':
      M.no_action = True
    else:
      raise RuntimeError("unhandled option")

  def apply_preargv(self, argv):
    # gather any macro assignments and apply
    M = self.options
    cmd_ns = M.cmd_ns = {}
    while argv:
      try:
        macro = Macro.from_assignment("command line", argv[0])
      except ValueError:
        break
      cmd_ns[macro.name] = macro
      argv.pop(0)
    M.insert_namespace(cmd_ns)
    return argv

  @contextmanager
  def run_context(self):
    M = self.options
    ok = M.loadMakefiles(M.makefiles)
    ok = ok and M.loadMakefiles(M.appendfiles)
    # prepend the command line namespace at the front again
    if M.cmd_ns:
      M.insert_namespace(M.cmd_ns)
    if not ok:
      raise GetoptError("errors loading Mykefiles")
    with M:
      yield

  def main(self, argv):
    ''' Main body.
    '''
    M = self.options
    if argv:
      targets = argv
    else:
      target = M.default_target
      if target is None:
        targets = ()
      else:
        targets = (M.default_target.name,)
    if not targets:
      error("no default target")
      return 1
    return 0 if M.make(targets) else 1

if __name__ == '__main__':
  sys.exit(main(sys.argv))

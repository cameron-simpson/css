#!/usr/bin/env python3

from getopt import GetoptError
import sys
from cs.logutils import setup_logging, warning, error, info, D
from cs.x import X
from .make import Maker
from .parse import parseMacroAssignment

default_cmd = 'myke'

usage="Usage: %s [options...] [macro=value...] [targets...]"

def main(argv=None):
  if argv is None:
    argv = sys.argv

  cmd, args = argv[0], argv[1:]
  setup_logging(cmd)

  M = Maker(argv[0])
  try:
    args, badopts = M.getopt(args)
  except GetoptError as e:
    warning("bad options: %s", e)
    badopts = True
  if badopts:
    print(usage % (cmd,), file=sys.stderr)
    return 2

  # gather any macro assignments and apply
  cmd_ns = {}
  while args:
    macro = parseMacroAssignment("command line", args[0])
    if macro is None:
      break
    cmd_ns[macro.name] = macro
    args.pop(0)

  # defer __enter__ until after option parsing
  ok = M.loadMakefiles(M.makefiles)
  ok = ok and M.loadMakefiles(M.appendfiles)
  if cmd_ns:
    M._namespaces.insert(0, cmd_ns)
  if not ok:
    error("errors loading Mykefiles")
    xit = 1
  else:
    if args:
      targets = args
    else:
      target = M.default_target
      if target is None:
        targets = ()
      else:
        targets = (M.default_target.name,)
    if not targets:
      error("no default target")
      xit = 1
    else:
      with M:
        xit = 0 if M.make(targets) else 1

  return xit

if __name__ == '__main__':
  sys.stderr.flush()
  sys.exit(main([default_cmd] + sys.argv[1:]))

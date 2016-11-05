#!/usr/bin/python
#
# Convenience functions for working with VirtualBox.
# Many operations are done by invoking VBoxManage.
#   - Cameron Simpson <cs@zip.com.au> 23oct2016
#

from __future__ import print_function
import sys
import os
import os.path
from os.path import basename, splitext
from subprocess import Popen, PIPE
from cs.cmdutils import run
from cs.logutils import setup_logging, warning, Pfx

USAGE = r'''Usage:
  %s mkimg {path.vdi|uuid} [VBoxManage clonemedium options...]
  %s mkvdi img [VBoxManage convertfromraw options...]
  %s pause vmname [VBoxManage controlvm options...]
  %s resume vmname [VBoxManage controlvm options...]
  %s start vmname [VBoxManage startvm options...]
  %s suspend vmname [VBoxManage controlvm options...]'''

VBOXMANAGE = 'VBoxManage'

def main(argv):
  cmd = basename(argv.pop(0))
  usage = USAGE % (cmd, cmd, cmd, cmd, cmd, cmd)
  setup_logging(cmd)
  badopts = False
  if not argv:
    warning("missing op")
    badopts = True
  else:
    op = argv.pop(0)
    with Pfx(op):
      if op == 'mkimg':
        if not argv:
          warning("missing VDI")
          badopts = True
      elif op == 'mkvdi':
        if not argv:
          warning("missing img")
          badopts = True
      elif op in ('pause', 'resume', 'start', 'suspend'):
        if not argv:
          warning("missing vmname")
          badopts = True
      else:
        warning("unrecognised op")
        badopts = True
  if badopts:
    print(usage, file=sys.stderr)
    return 2
  with Pfx(op):
    if op == "mkimg":   return cmd_mkimg(argv)
    if op == "mkvdi":   return cmd_mkvdi(argv)
    if op == "pause":   return cmd_pause(argv)
    if op == "resume":  return cmd_resume(argv)
    if op == "start":   return cmd_start(argv)
    if op == "suspend": return cmd_suspend(argv)
    raise RuntimeError("unimplemented")

def cmd_mkvdi(argv):
  imgpath = argv.pop(0)
  imgpfx, imgext = splitext(imgpath)
  if imgext == '.raw' or imgext == '.img':
    vdipath = imgpfx + '.vdi'
  else:
    vdipath = imgpath + '.vdi'
  try:
    return mkvdi(imgpath, vdipath, argv, trace=True)
  except ValueError as e:
    error("mkvdi fails: %s", e)
    return 1

def mkvdi(srcimg, dstvdi, argv, trace=False):
  ''' Create VDI image `dstvdi` from source raw image `srcimg`. Return VBoxManage convertfromraw exit code.
  '''
  if os.path.exists(dstvdi):
    raise ValueError("destination VDI image already exists: %r" % (dstvdi,))
  return run([VBOXMANAGE, 'convertfromraw', srcimg, dstvdi, '--format', 'VDI'] + argv, trace=trace)

def cmd_mkimg(argv):
  src = argv.pop(0)
  srcpfx, srcext = splitext(src)
  if srcext == '.vdi':
    dstimg = srcpfx + '.img'
  else:
    dstimg = src + '.img'
  try:
    return mkimg(src, dstimg, argv, trace=True)
  except ValueError as e:
    error("mkimg fails: %s", e)
    return 1

def mkimg(src, dstimg, argv, trace=False):
  ''' Create raw image `dstimg` from source `src`. Return VBoxManage clonemedium exit code.
  '''
  if os.path.exists(dstimg):
    raise ValueError("destination RAW image already exists: %r" % (dstimg,))
  return run([VBOXMANAGE, 'clonemedium', 'disk', src, dstimg, '--format', 'RAW'] + argv, trace=trace)

def cmd_pause(argv):
  vmspec = argv.pop(0)
  return run([VBOXMANAGE, 'controlvm', vmspec, 'pause'] + argv, trace=True)

def cmd_resume(argv, trace=False):
  vmspec = argv.pop(0)
  return run([VBOXMANAGE, 'controlvm', vmspec, 'resume'] + argv, trace=True)

def cmd_start(argv):
  vmspec = argv.pop(0)
  return run([VBOXMANAGE, 'startvm', vmspec] + argv, trace=True)

def cmd_suspend(argv):
  vmspec = argv.pop(0)
  return run([VBOXMANAGE, 'controlvm', vmspec, 'savestate'] + argv, trace=True)

def parse_clauses(fp):
  ''' Generator that parses VBoxManage clause output and yields maps from field name to field value.
  '''
  clause = {}
  for lineno, line in enumerate(fp, 1):
    with Pfx(lineno):
      if not line.endswith('\n'):
        raise ValueError('missng end of line')
      line = line.rstrip()
      if not line:
        yield clause
        clause = {}
      else:
        k, v = line.strip().split(':', 1)
        v = v.lstrip()
        if k in clause:
          warning("repeated key %r: keeping old %r, ignoring new %r", k, clause[k], v)
        else:
          clause[k] = v
  if clause:
    yield clause

if __name__ == '__main__':
  sys.exit(main(sys.argv))

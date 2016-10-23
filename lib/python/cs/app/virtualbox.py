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
  %s mkvdi img [VBoxManage convertfromraw options...]
  %s pause vmname [VBoxManage controlvm options...]
  %s resume vmname [VBoxManage controlvm options...]
  %s start vmname [VBoxManage startvm options...]
  %s suspend vmname [VBoxManage controlvm options...]'''

VBOXMANAGE = 'VBoxManage'

def main(argv):
  cmd = basename(argv.pop(0))
  usage = USAGE % (cmd, cmd, cmd, cmd, cmd)
  setup_logging(cmd)
  badopts = False
  if not argv:
    warning("missing op")
    badopts = True
  else:
    op = argv.pop(0)
    with Pfx(op):
      if op == 'mkvdi':
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
  return mkvdi(imgpath, vdipath, trace=True)

def mkvdi(srcimg, dstvdi, trace=False):
  ''' Create VDI image `dstvdi` from source raw image `srcimg`. Return VBoxManage exit code.
  '''
  if os.path.exists(dstvdi):
    raise ValueError("destination VDI image already exists: %r" % (dstvdi,))
  return run([VBOXMANAGE, 'convertfromraw', imgpath, vdipath, '--format', 'VDI'] + argv, trace=trace)

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

if __name__ == '__main__':
  sys.exit(main(sys.argv))

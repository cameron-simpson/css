#!/usr/bin/env python3
#

''' Convenience functions for working with VirtualBox.
    Many operations are done by invoking VBoxManage.
    - Cameron Simpson <cs@cskk.id.au> 23oct2016
'''

from __future__ import print_function
from getopt import GetoptError
import os
import os.path
from os.path import splitext
import sys
from cs.cmdutils import BaseCommand
from cs.psutils import run
from cs.logutils import warning, error
from cs.pfx import Pfx

VBOXMANAGE = 'VBoxManage'

def main(argv=None, cmd=None):
  ''' Main command line.
  '''
  return VBoxCommand().run(argv, cmd=cmd)

class VBoxCommand(BaseCommand):
  ''' "vbox" command line implementation.
  '''

  @staticmethod
  def cmd_ls(argv, _):
    ''' Usage: {cmd} [VBoxManage list options...]
          List various things, by default "vms".
    '''
    return run([VBOXMANAGE, 'list'] + argv)

  @staticmethod
  def cmd_mkimg(argv, _):
    ''' Usage: {cmd} {{path.vdi|uuid}} [VBoxManage clonemedium options...]
          Create a .img file from a disc image file.
    '''
    if not argv:
      raise GetoptError("missing source path.vdi or uuid")
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

  @staticmethod
  def cmd_mkvdi(argv, _):
    ''' Usage: {cmd} img [VBoxManage convertfromraw options...]
          Create a .vdi file from a .img file.
    '''
    if not argv:
      raise GetoptError("missing source img")
    imgpath = argv.pop(0)
    imgpfx, imgext = splitext(imgpath)
    if imgext('.raw', '.img'):
      vdipath = imgpfx + '.vdi'
    else:
      vdipath = imgpath + '.vdi'
    try:
      return mkvdi(imgpath, vdipath, argv, trace=True)
    except ValueError as e:
      error("mkvdi fails: %s", e)
      return 1

  @staticmethod
  def cmd_pause(argv, _):
    ''' Usage: {cmd} vmname [VBoxManage controlvm options...]
          Pause the specified VM using "controlvm .. pause".
    '''
    if not argv:
      raise GetoptError("missing vmname")
    vmspec = argv.pop(0)
    return run([VBOXMANAGE, 'controlvm', vmspec, 'pause'] + argv, logger=True)

  @staticmethod
  def cmd_ps(argv, _):
    ''' Usage: {cmd} [VBoxManage list options...]
          List runnings VMs.
    '''
    return run([VBOXMANAGE, 'list'] + argv + ['runningvms'])

  @staticmethod
  def cmd_resume(argv, _):
    ''' Usage: {cmd} vmname [VBoxManage controlvm options...]
          Resume the specified VM using "controlvm .. resume".
    '''
    if not argv:
      raise GetoptError("missing vmname")
    vmspec = argv.pop(0)
    return run([VBOXMANAGE, 'controlvm', vmspec, 'resume'] + argv, logger=True)

  @staticmethod
  def cmd_start(argv, _):
    ''' Usage: {cmd} vmname [VBoxManage startvm options...]
          Start the specified VM using "startvm".
    '''
    if not argv:
      raise GetoptError("missing vmname")
    vmspec = argv.pop(0)
    return run([VBOXMANAGE, 'startvm', vmspec] + argv, logger=True)

  @staticmethod
  def cmd_suspend(argv, _):
    ''' Usage: {cmd} vmname [VBoxManage controlvm options...]
          Suspend the specified VM using "controlvm .. savestate".
    '''
    if not argv:
      raise GetoptError("missing vmname")
    vmspec = argv.pop(0)
    return run(
        [VBOXMANAGE, 'controlvm', vmspec, 'savestate'] + argv, logger=True
    )

def parse_clauses(fp):
  ''' Generator that parses VBoxManage clause output
      and yields maps from field name to field value.
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
          warning(
              "repeated key %r: keeping old %r, ignoring new %r", k, clause[k],
              v
          )
        else:
          clause[k] = v
  if clause:
    yield clause

def mkvdi(srcimg, dstvdi, argv, trace=False):
  ''' Create VDI image `dstvdi` from source raw image `srcimg` using `convertfromraw`.
      Return `VBoxManage convertfromraw` exit code.
  '''
  if os.path.exists(dstvdi):
    raise ValueError("destination VDI image already exists: %r" % (dstvdi,))
  return run(
      [VBOXMANAGE, 'convertfromraw', srcimg, dstvdi, '--format', 'VDI'] + argv,
      logger=trace
  )

def mkimg(src, dstimg, argv, trace=False):
  ''' Create raw image `dstimg` from source `src` using `clonemedium disk`.
      Return `VBoxManage clonemedium` exit code.
  '''
  if os.path.exists(dstimg):
    raise ValueError("destination RAW image already exists: %r" % (dstimg,))
  return run(
      [VBOXMANAGE, 'clonemedium', 'disk', src, dstimg, '--format', 'RAW'] +
      argv,
      logger=trace
  )

if __name__ == '__main__':
  sys.exit(main(sys.argv))

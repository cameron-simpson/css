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
from os.path import exists as existspath, splitext
import sys

from cs.cmdutils import BaseCommand
from cs.psutils import run
from cs.logutils import warning, error
from cs.pfx import Pfx, pfx_call

from typeguard import typechecked

VBOXMANAGE = 'VBoxManage'

def main(argv=None):
  ''' Main command line.
  '''
  return VBoxCommand(argv).run()

class VBoxCommand(BaseCommand):
  ''' "vbox" command line implementation.
  '''

  @staticmethod
  def vbmg(pre_argv, argv, post_argv=()):
    ''' Run `VBoxManage` with an argument list.

        This essentailly a thin wrapper for
        `run([VBOXMANAGE]+pre_argv+argv+post_argv)`.

        Parameters:
        * `pre_argv`: leading command line arguments to go after `VBOX_MANAGE`;
          if this is a `str` it is promoted to a single element list
        * `argv`: argument or arguments to go after `pre_argv`;
          if this is a `str` it is promoted to a single element list
        * `post_argv`: optional arguments to go after `argv`

        Examples:

            self.vbmg('list', argv)
            self.vbmg(['controlvm', vmspec, 'pause'], argv)
            self.vbmg('list', argv, ['runningvms'])
    '''
    if isinstance(pre_argv, str):
      pre_argv = [pre_argv]
    if isinstance(argv, str):
      argv = [argv]
    return run([VBOXMANAGE] + pre_argv + argv + list(post_argv), logger=True)

  def cmd_ls(self, argv):
    ''' Usage: {cmd} [VBoxManage list options...]
          List various things, by default "vms".
    '''
    return self.vbmg('list', argv)

  @staticmethod
  def cmd_mkimg(argv):
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
  def cmd_mkvdi(argv):
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

  def cmd_pause(self, argv):
    ''' Usage: {cmd} vmname [VBoxManage controlvm options...]
          Pause the specified VM using "controlvm .. pause".
    '''
    if not argv:
      raise GetoptError("missing vmname")
    vmspec = argv.pop(0)
    return self.vbmg(['controlvm', vmspec, 'pause'], argv)

  def cmd_ps(self, argv):
    ''' Usage: {cmd} [VBoxManage list options...]
          List runnings VMs.
    '''
    return self.vbmg('list', argv, ['runningvms'])

  @staticmethod
  def cmd_resize(argv):
    ''' Usage: {cmd} vdipath new_size_mb
          Resize a .vdi file to new_size_mb, a size in megabytes.
    '''
    if not argv:
      raise GetoptError("missing vdi")
    vdipath = argv.pop(0)
    with Pfx("vdipath %r", vdipath):
      if not vdipath.endswith('.vdi'):
        raise GetoptError("does not end with .vdi")
      if not existspath(vdipath):
        raise GetoptError("does not exist")
    if not argv:
      raise GetoptError("missing new_size_mb")
    new_size_mb_s = argv.pop(0)
    with Pfx("new_size_mb %r", new_size_mb_s):
      try:
        new_size_mb = int(new_size_mb_s)
      except ValueError as e:
        raise GetoptError("not an integer: %s" % (e,))
      else:
        if new_size_mb <= 0:
          raise GetoptError("must be >0")
    try:
      return pfx_call(resizevdi, vdipath, new_size_mb, trace=True)
    except ValueError as e:
      error("resize fails: %s", e)
      return 1

  def cmd_resume(self, argv):
    ''' Usage: {cmd} vmname [VBoxManage controlvm options...]
          Resume the specified VM using "controlvm .. resume".
    '''
    if not argv:
      raise GetoptError("missing vmname")
    vmspec = argv.pop(0)
    return self.vbmg(['controlvm', vmspec, 'resume'], argv)

  def cmd_start(self, argv):
    ''' Usage: {cmd} vmname [VBoxManage startvm options...]
          Start the specified VM using "startvm".
    '''
    if not argv:
      raise GetoptError("missing vmname")
    vmspec = argv.pop(0)
    return self.vbmg(['startvm', vmspec], argv)

  def cmd_suspend(self, argv):
    ''' Usage: {cmd} vmname [VBoxManage controlvm options...]
          Suspend the specified VM using "controlvm .. savestate".
    '''
    if not argv:
      raise GetoptError("missing vmname")
    vmspec = argv.pop(0)
    return self.vbmg(['controlvm', vmspec, 'savestate'], argv)

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

@typechecked
def resizevdi(vdipath: str, new_size_mb: int, trace=False) -> int:
  ''' Resize VDI image `vdipath` to `new_size_mb`, a size in megabytes,
      using `modifyhd`.
      Return `VBoxManage modifyhd` exit code.
  '''
  if not os.path.exists(vdipath):
    raise ValueError("VDI image does not exist: %r" % (vdipath,))
  return run(
      [VBOXMANAGE, 'modifyhd', vdipath, '--resize',
       str(new_size_mb)],
      logger=trace,
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

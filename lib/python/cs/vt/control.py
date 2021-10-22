#!/usr/bin/python3
#
# Control operations given a Dirent.
# - Cameron Simpson <cs@cskk.id.au> 31may2018
#

''' Control operations, intended to be available to the command
    line and via FTPlike interfaces and via the setxattr('x-vt-control',
    command_string) action in a FUSE implementation.
'''

from getopt import GetoptError
from cs.logutils import exception
from cs.pfx import Pfx
from . import PATHSEP
from .dir import _Dirent
from .transcribe import parse

class Control:
  ''' Command line interface to perform operations on Dirents.
  '''

  def control(self, E, argv):
    ''' Perform a control action `argv` on the Dirent `E`.
    '''
    with Pfx("%s.control(E=%s,argv=%r)", self, E, argv):
      if not argv:
        raise ValueError('empty argv')
      op = argv.pop()
      with Pfx(op):
        try:
          action = getattr(self, 'cmd_' + op)
        except AttributeError as e:
          exception("unknown operation %r: %s", op, e)
          raise ValueError("unknown operation %r: %s" % (op, e))
        try:
          return action(E, argv)
        except GetoptError as e:
          exception("%s: %s", type(e), e)
          raise ValueError("%s: %s" % (type(e), e))

  @staticmethod
  def cmd_attach(D, argv):
    ''' Attach an arbitrary Dirent to the current directory `D`.
        Usage: attach name dirent_spec
    '''
    if not D.isdir:
      raise GetoptError("not a Dir: %s" % (D,))
    if not argv:
      raise GetoptError("missing name")
    name = argv.pop()
    if not name or PATHSEP in name:
      raise GetoptError(
          "invalid name, may not be empty or contain the separator %r: %r" %
          (PATHSEP, name)
      )
    if not argv:
      raise GetoptError("missing dirent_spec")
    dirent_spec = argv.pop()
    if argv:
      raise GetoptError("extra arguments after dirent_spec: %r" % (argv,))
    try:
      E = parse(dirent_spec)
    except ValueError as e:
      raise GetoptError("parse failure: %r: %s" % (dirent_spec, e)) from e
    if not isinstance(E, _Dirent):
      raise GetoptError("not a Dirent specification: %r" % (dirent_spec,))
    if name in D:
      raise GetoptError("name already exists: %r" % (name,))
    D[name] = E

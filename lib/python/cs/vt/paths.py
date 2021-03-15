#!/usr/bin/python
#
# Operations on pathnames using a vt store.
#       - Cameron Simpson <cs@cskk.id.au> 07may2013
#

''' Assorted path related utility functions and classes.
'''

from abc import ABC, abstractmethod
import errno
import os
from os.path import dirname, join as joinpath, exists as pathexists
from stat import S_ISDIR
from cs.buffer import CornuCopyBuffer
from cs.fileutils import datafrom
from cs.logutils import warning
from cs.pfx import Pfx
from . import PATHSEP
from .transcribe import parse

def path_resolve(path, do_mkdir=False):
  ''' Resolve a path with a leading Dirent transcription.

      Parameters:
      * `path`: a path with a leading Dirent transcription
      * `do_mkdir`: if true, create missing directory components.
        Default: `False`.
  '''
  E, offset = parse(path)
  if offset < len(path):
    subpath = path[offset:]
    if not E.isdir:
      raise ValueError("extra path after no-Dir: %r" % (subpath,))
    if not path.startswith(PATHSEP, offset):
      raise ValueError(
          "extra path does not commence with %r: %r" % (PATHSEP, subpath)
      )
    E = resolve(E, subpath, do_mkdir=do_mkdir)
  return E

def path_split(path):
  ''' Split path into components, discarding the empty string and ".".
      The returned subparts are useful for path traversal.
  '''
  return [
      subpath for subpath in path.split(PATHSEP)
      if subpath != '' and subpath != '.'
  ]

def resolve(rootD, subpath, do_mkdir=False):
  ''' Descend from the Dir `rootD` via the path `subpath`.
      Return the final Dirent, its parent, and a list of unresolved path components.

      `subpath` may be a str or an array of str.
  '''
  if not rootD.isdir:
    raise ValueError("resolve: not a Dir: %s" % (rootD,))
  E = rootD
  parent = E.parent
  if isinstance(subpath, str):
    subpaths = path_split(subpath)
  else:
    subpaths = subpath
  while subpaths and E.isdir:
    name = subpaths[0]
    if name == '' or name == '.':
      # stay on this Dir
      pass
    elif name == '..':
      # go up a level if available
      if E.parent is None:
        break
      E = E.parent
    elif name in E:
      parent = E
      E = E[name]
    elif do_mkdir:
      parent = E
      E = E.mkdir(name)
    else:
      break
    subpaths.pop(0)
  return E, parent, subpaths

class DirLike(ABC):
  ''' Facilities offered by directory like objects.
  '''

  isdir = property(lambda self: True)
  isfile = property(lambda self: False)

  @staticmethod
  def check_subname(name):
    ''' Sanity check `name`, raise ValueError if invalid.
    '''
    if not name:
      raise ValueError("name may not be empty")
    if PATHSEP in name:
      raise ValueError("name may not contain PATHSEP %r: %r" % (PATHSEP, name))

  @abstractmethod
  def __contains__(self, name):
    raise NotImplementedError("no %s.__contains__" % (type(self),))

  @abstractmethod
  def __getitem__(self, name):
    raise NotImplementedError("no %s.__getitem__" % (type(self),))

  def get(self, name, default=None):
    ''' Mapping method.
    '''
    try:
      return self[name]
    except KeyError:
      return default

  @abstractmethod
  def __delitem__(self, name):
    raise NotImplementedError("no %s.__delitem__" % (type(self),))

  @abstractmethod
  def keys(self):
    ''' The names in this DirLike.
    '''
    raise NotImplementedError("no %s.__getitem__" % (type(self),))

  def __iter__(self):
    return iter(self.keys())

  def values(self):
    ''' Iterator yielding values.
    '''
    for k in self.keys():
      try:
        v = self[k]
      except KeyError:
        pass
      else:
        yield v

  def items(self):
    ''' Iterator yielding `(key, vlaue)` tuples.
    '''
    for k in self.keys():
      try:
        v = self[k]
      except KeyError:
        pass
      else:
        yield k, v

  @abstractmethod
  def lstat(self):
    ''' Return a stat object.
        Depending on the backend various fields may be None or meaningless.
        Returns None if `self` does not represent
        an existing object in the backend.
    '''
    raise NotImplementedError("no %s.stat" % (type(self),))

  @abstractmethod
  def mkdir(self, name):
    ''' Construct and return a new empty subdirectory.
    '''
    raise NotImplementedError("no %s.mkdir" % (type(self),))

  @abstractmethod
  def file_frombuffer(self, name, bfr):
    ''' Contruct a new file containing data from `bfr`.
    '''
    raise NotImplementedError("no %s.file_frombuffer" % (type(self),))

  def file_fromchunks(self, name, chunks):
    ''' Create a new file named `name` from the data in `chunks`.
    '''
    return self.file_frombuffer(name, CornuCopyBuffer(chunks))

  def resolve(self, rpath):
    ''' Resolve `rpath` relative to `self`, return resolved node or `None`.

        *WARNING*: this can walk upwards an arbitrary distance.
    '''
    node = self
    for part in rpath.split(PATHSEP):
      if not part:
        continue
      if part == '.':
        continue
      if part == '..':
        node = self.parent
        if node is None:
          break
      node = node.get(part)
      if node is None:
        break
    return node

  def walk(self):
    ''' Walk this tree.
    '''
    pending = [(self, [])]
    while pending:
      node, rparts = pending.pop()
      rpath = PATHSEP.join(rparts)
      dirnames = []
      filenames = []
      for name in sorted(node.keys()):
        subnode = node.get(name)
        if subnode is None:
          continue
        if subnode.isdir:
          dirnames.append(name)
        else:
          filenames.append(name)
      odirnames = set(dirnames)
      yield rpath, dirnames, filenames
      for name in dirnames:
        if name not in odirnames:
          warning(
              "walk(%s): %r: dirname %r not in original set", self, rpath, name
          )
          continue
        subnode = node.get(name)
        if subnode is None:
          warning("walk(%s): %r: dirname %r not in node", self, rapth, name)
          continue
        pending.append((subnode, rparts + [name]))

class FileLike(ABC):
  ''' Facilities offered by file like objects.
  '''

  isdir = property(lambda self: False)
  isfile = property(lambda self: True)

  @abstractmethod
  def lstat(self):
    ''' Return a stat object.
        Depending on the backend various fields may be None or meaningless.
        Returns None if `self` does not represent
        an existing object in the backend.
    '''
    raise NotImplementedError("no %s.stat" % (type(self),))

  @abstractmethod
  def datafrom(self):
    ''' Iterator yielding natural data chunks from the file.
    '''
    raise NotImplementedError("no %s.datafrom" % (type(self),))

  def bufferfrom(self):
    ''' Return a CornuCopyBuffer presenting data from the file.
    '''
    return CornuCopyBuffer(self.datafrom())

class OSDir(DirLike):
  ''' DirLike class for an OS directory.
  '''

  def __init__(self, path):
    if not path:
      path = '.'
    DirLike.__init__(self)
    self.path = path

  def __str__(self):
    return "%s(%r)" % (type(self).__name__, self.path)

  def keys(self):
    ''' Directory entry names.
    '''
    return os.listdir(self.path)

  def __contains__(self, name):
    return name in self.keys()

  def __getitem__(self, name):
    if name not in self.keys():
      raise KeyError(name)
    namepath = joinpath(self.path, name)
    try:
      S = os.stat(namepath)
    except OSError as e:
      if e.errno == errno.ENOENT:
        raise KeyError(name)
      raise
    if S_ISDIR(S.st_mode):
      return type(self)(namepath)
    return OSFile(namepath)

  def __delitem__(self, name):
    self.check_subname(name)
    os.remove(joinpath(self.path, name))

  def lstat(self):
    with Pfx("lstat(%r)", self.path):
      try:
        return os.lstat(self.path)
      except OSError as e:
        if e.errno == errno.ENOENT:
          return None
        raise

  @property
  def parent(self):
    ''' This directory's parent.
    '''
    if PATHSEP in self.path:
      return OSDir(dirname(self.path))
    return None

  def exists(self):
    ''' Test if the directory exists.
    '''
    return pathexists(self.path)

  def create(self):
    ''' Create this directory.
    '''
    os.mkdir(self.path)

  def mkdir(self, name):
    ''' Create a subdirectory.
    '''
    if not name or PATHSEP in name:
      raise ValueError(
          "name may not be empty or contain PATHSEP %r: %r" % (PATHSEP, name)
      )
    subpath = joinpath(self.path, name)
    with Pfx("mkdir(%r)", subpath):
      os.mkdir(subpath)
    return OSDir(subpath)

  def file_frombuffer(self, name, bfr):
    ''' Create a new file from data from a `CornuCopyBuffer`.
    '''
    if not name or PATHSEP in name:
      raise ValueError(
          "name may not be empty or contain PATHSEP %r: %r" % (PATHSEP, name)
      )
    subpath = joinpath(self.path, name)
    with open(subpath, 'wb') as f:
      for data in bfr:
        f.write(data)
    return OSFile(subpath)

class OSFile(FileLike):
  ''' FileLike class for an OS file.
  '''

  def __init__(self, path):
    FileLike.__init__(self)
    self.path = path

  def lstat(self):
    with Pfx("lstat(%r)", self.path):
      try:
        return os.lstat(self.path)
      except OSError as e:
        if e.errno == errno.ENOENT:
          return None
        raise

  def exists(self):
    ''' Test if the file exists.
    '''
    return pathexists(self.path)

  def datafrom(self):
    ''' Yield data from the file.
    '''
    with open(self.path, 'rb') as f:
      yield from datafrom(f, 0)

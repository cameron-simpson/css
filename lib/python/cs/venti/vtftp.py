#!/usr/bin/python -tt
#
# Command line access to a Dir.
# - Cameron Simpson <cs@zip.com.au> 02jan2017
#

from cmd import Cmd
import errno
from getopt import GetoptError
import readline
import shlex
import stat
import sys
from cs.logutils import X, warning, error, exception, Pfx
from cs.fileutils import shortpath
from .archive import last_Dirent
from .paths import resolve

def main(argv):
  cmd, special = argv

def ftp_archive(archive):
  with Pfx(archive):
    when, E = last_Dirent(archive, missing_ok=True)
    F = FTP(E, prompt=shortpath(archive))
    F.cmdloop()
  
def docmd(dofunc):
  def wrapped(self, *a, **kw):
    funcname = dofunc.__name__
    if funcname.startswith('do_'):
      argv0 = funcname[3:]
    else:
      argv0 = funcname
    with Pfx(argv0):
      try:
        return dofunc(self, *a, **kw)
      except GetoptError as e:
        warning("%s", e)
        self.do_help(argv0)
        return None
      except Exception as e:
        exception("%s", e)
        return None
  wrapped.__doc__ = dofunc.__doc__
  return wrapped

class FTP(Cmd):

  def __init__(self, D, sep=None, FS=None, prompt=None):
    Cmd.__init__(self)
    self._prompt = prompt
    if sep is None:
      sep = '/' # NB: _not_ os.sep
    self.root = D
    self.cwd = D
    self.sep = sep
    self.fs = FS
    self._set_prompt()

  def _set_prompt(self):
    prompt = self._prompt
    pwd = '/' + self.op_pwd()
    self.prompt = ( pwd if prompt is None else ":".join( (prompt, pwd) ) ) + '> '

  def precmd(self, line):
    X("precmd: line=%r", line)
    return line

  def postcmd(self, stop, line):
    X("postcmd: stop=%s, line=%r", stop, line)
    self._set_prompt()
    return stop

  def emptyline(self):
    pass

  def do_EOF(self, args):
    ''' Quit on end of input.
    '''
    return True

  @docmd
  def do_quit(self, args):
    ''' Usage: quit
    '''
    return True

  @docmd
  def do_cd(self, args):
    ''' Usage: cd pathname
        Change working directory.
    '''
    argv = shlex.split(args)
    if len(argv) != 1:
      raise GetoptError("exactly one argument expected, received: %r" % (argv,))
    self.op_cd(argv[0])
    print(self.op_pwd())

  def op_cd(self, path):
    ''' Change working directory.
    '''
    if path.startswith(self.sep):
      D = self.root
    else:
      D = self.cwd
    for base in path.split(self.sep):
      if base == '' or base == '.':
        pass
      elif base == '..':
        if D is not self.root:
          D = D.parent
      else:
        D = D.chdir1(base)
    self.cwd = D

  @docmd
  def do_inspect(self, args):
    ''' Usage: inspect name
        Print VT level details about name.
    '''
    argv = shlex.split(args)
    if len(argv) != 1:
      raise GetoptError("invalid arguments: %r" % (argv,))
    name, = argv
    E, P, tail = resolve(self.cwd, name)
    if tail:
      raise OSError(errno.ENOENT)
    print("%s: %s" % (name, E))
    M = E.meta
    print(M.textencode())
    print("size=%d" % (len(E.block),))

  @docmd
  def do_pwd(self, args):
    ''' Usage: pwd
        Print the current working directory path.
    '''
    argv = shlex.split(args)
    if argv:
      raise GetoptError("extra arguments: %r" % (args,))
    print(self.op_pwd())

  def op_pwd(self):
    ''' Return the path to the current working directory.
    '''
    E = self.cwd
    names = []
    seen = set()
    while E is not self.root:
      seen.add(E)
      P = E.parent
      if P is None:
        raise ValueError("no parent: names=%r, E=%s" % (names, E))
      if P in seen:
        raise ValueError("loop detected: names=%r, E=%s" % (names, E))
      name = E.name
      if P[name] is not E:
        name = None
        for Pname, PE in sorted(P.entries.items()):
          if PE is E:
            name = Pname
            break
        if name is None:
          raise ValueError("detached: E not present in P: E=%s, P=%s" % (E, P))
      names.append(name)
      E = P
    return self.sep.join(reversed(names))

  def do_ls(self, args):
    ''' Usage: ls [paths...]
    '''
    argv = shlex.split(args)
    if not argv:
      argv = sorted(self.cwd.entries.keys())
    for name in argv:
      with Pfx(name):
        E, P, tail = resolve(self.cwd, name)
        if tail:
          error("not found: unresolved path elements: %r", tail)
        else:
          M = E.meta
          S = M.stat()
          u, g, perms = M.unix_perms
          typemode = M.unix_typemode
          typechar = ( '-' if typemode == stat.S_IFREG
                  else 'd' if typemode == stat.S_IFDIR
                  else 's' if typemode == stat.S_IFLNK
                  else '?'
                     )
          print("%s%s%s%s %s" % ( typechar, 
                                  rwx((typemode>>6)&7),
                                  rwx((typemode>>3)&7),
                                  rwx((typemode)&7),
                                  name
                                ))

  def op_ls(self):
    ''' Return a dict mapping current directories names to Dirents.
    '''
    return dict(self.cwd.entries)

  def do_mkdir(self, args):
    argv = shlex.split(args)
    if not argv:
      raise GetoptError("missing arguments")
    for arg in argv:
      with Pfx(arg):
        E, P, tail = resolve(self.cwd, arg)
        if not tail:
          error("path exists")
        elif len(tail) > 1:
          error("missing superdirectory")
        elif not E.isdir:
          error("superpath is not a directory")
        else:
          subname = tail[0]
          if subname in E:
            error("%r exists", subname)
          else:
            E.mkdir(subname)
        self.cwd

def rwx(mode):
  return ( 'r' if mode&4 else '-' ) \
       + ( 'w' if mode&2 else '-' ) \
       + ( 'x' if mode&1 else '-' )

if __name__ == '__main__':
  sys.exit(main(sys.argv))

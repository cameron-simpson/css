#!/usr/bin/python
#
# Python 2 specific implementations.
# Provided to separate non-portable syntax across python 2 and 3.
#   - Cameron Simpson <cs@zip.com.au> 12nov2015
# 

DISTINFO = {
    'description': "python 2 specific support for cs.py3 module",
    'keywords': ["python2"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        ],
}

def raise3(exc_type, exc_value, exc_traceback):
  exec("raise exc_type, exc_value, exc_traceback")

def exec_code(code, *a):
  if not a:
    exec(code)
  else:
    gs = a.pop(0)
    if not a:
      exec("exec code in gs")
    else:
      ls = a.pop(0)
      if not a:
        exec("exec code in gs, ls")
      else:
        raise ValueError("exec_code: extra arguments after locals: %r" % (a,))

class bytes(object):
  ''' Trite bytes implementation.
  '''
  def __init__(self, arg):
    if isinstance(arg, str):
      # accept a str as is
      self.__s = arg
    else:
      # not a str: should be interable of ints or length
      try:
        bytevals = [ b for b in arg ]
      except TypeError:
        bytevals = [ 0 for i in range(arg) ]
      bvals = list(bytevals)
      self.__s = ''.join( chr(b) for b in bytevals )
      if len(self.__s) != len(bvals):
        raise TypeError("bvals=%r, __s=%r", bvals, self.__s)
    if not isinstance(self.__s, str):
      raise TypeError("__s is not a str!")
  def __repr__(self):
    return 'b' + repr(self.__s)
  def __iter__(self):
    for _ in self.__s:
      yield ord(_)
  def __getitem__(self, index):
    return ord(self.__s[index])
  def __getslice__(self, i, j):
      return bytes( ord(_) for _ in self.__s[i:j] )
  def __contains__(self, b):
    return chr(b) in self.__s
  def __eq__(self, other):
    if type(other) is type(self):
      return self.__s == other.__s
    if len(other) != len(self):
      return False
    for i, b in enumerate(self):
      if b != other[i]:
        return False
    return True
  def __len__(self):
    return len(self.__s)
  def __add__(self, other):
    return bytes( list(self) + list(bytes(other)) )
  def as_str(self):
    ''' Back convert to a str, only meaningful for Python 2.
    '''
    return self.__s
    ##return ''.join( chr(_) for _ in self )
  def as_buffer(self):
    ''' For python 2, support buffer protocol.
    '''
    return self.__s
  @staticmethod
  def join(bss):
    return bytes(str.join(bss))

class BytesFile(object):
  ''' Wrapper class for a file opened in binary mode which uses bytes in its methods instead of str.
  '''

  def __init__(self, fp):
    self.fp = fp

  def read(self, *a, **kw):
    s = self.fp.read(*a, **kw)
    if not isinstance(s, str):
      raise TypeError("s=%s %r from %s.read", type(s), s, type(self.fp))
    return bytes(s)
    ##return bytes(self.fp.read(*a, **kw))

  def write(self, bs, *a, **kw):
    if isinstance(bs, str):
      s = bs
    else:
      s = bs.as_str()
    return self.fp.write(s, *a, **kw)

  def seek(self, *a, **kw):
    return self.fp.seek(*a, **kw)

  def flush(self):
    return self.fp.flush()

  def close(self):
    return self.fp.close()

from struct import pack as _pack, unpack as _unpack

def pack(fmt, *values):
  return bytes(_pack(fmt, *values))

def unpack(fmt, bs):
  from cs.logutils import X
  X("py3_for2.unpack: fmt=%r, bs=%r", fmt, bs)
  if isinstance(bs, bytes):
    bs = bs._bytes__s
    X("py3_for2.unpack: bs => %r", bs)
  return _unpack(fmt, bs)

#!/usr/bin/python

''' Convenience hashing facilities.
'''

from binascii import hexlify, unhexlify
import hashlib
import mmap
import os
from typing import Optional

from cs.buffer import CornuCopyBuffer
from cs.deco import promote
from cs.lex import r

__version__ = '20250414'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.buffer',
        'cs.deco',
        'cs.lex',
    ],
}

class BaseHashCode(bytes):
  ''' Base class for hashcodes, subclassed by `SHA1`, `SHA256` et al.

      You can obtain the class for a particular hasher by name, example:

          SHA256 = BaseHashCode.hashclass('sha256')
  '''

  __slots__ = ()

  # registry of classes
  by_hashname = {}

  @staticmethod
  def get_hashfunc(hashname: str):
    ''' Fetch the hash function implied by `hashname`.
    '''
    try:
      hashfunc = getattr(hashlib, hashname)
    except AttributeError as hashlib_e:
      if hashname == 'blake3':
        # see if the blake3 module is around
        # pylint:disable=import-outside-toplevel
        try:
          from blake3 import blake3 as hashfunc
        except ImportError as import_e:
          import sys
          raise ValueError(
              f'unknown {hashname=}: {import_e}; {sys.path=}'
          ) from import_e
      else:
        raise ValueError(f'unknown {hashname=}: {hashlib_e}')
    return hashfunc

  @classmethod
  def hashclass(cls, hashname: str, hashfunc=None, **kw):
    ''' Return the class for the hash function named `hashname`.

        Parameters:
        * `hashname`: the name of the hash function
        * `hashfunc`: optional hash function for the class
    '''
    try:
      hashcls = cls.by_hashname[hashname]
    except KeyError:
      if hashfunc is None:
        hashfunc = cls.get_hashfunc(hashname)

      class hashcls(
          cls,
          hashfunc=hashfunc,
          hashname=hashname,
          **kw,
      ):
        ''' Hash class implementation.
        '''
        __slots__ = ()

      hashcls.__name__ = hashname.upper()
      hashcls.__doc__ = f'Hash class for the {hashname!r} algorithm.'
    else:
      if hashfunc is not None:
        if hashfunc is not hashcls.hashfunc:
          raise ValueError(
              f'class {hashcls.__name__} already exists with a different hash function {hashcls.hashfunc} from supplied {hashfunc=}'
          )

    return hashcls

  @classmethod
  def __init_subclass__(
      cls, *, hashname: Optional[str] = None, hashfunc=None, **kw
  ):
    super().__init_subclass__(**kw)
    if hashname is None and hashfunc is None:
      # we're a superclass of another base class
      return
    if hashfunc is None:
      hashfunc = cls.get_hashfunc(hashname)
    by_hashname = cls.by_hashname
    try:
      hashcls = by_hashname[hashname]
    except KeyError:
      # new hash class, register it
      by_hashname[hashname] = cls
    else:
      raise ValueError(
          f'hashname {hashname!r}: class {hashcls} already exists for hashname'
      )
    cls.hashname = hashname
    cls.hashfunc = hashfunc
    cls.hashlen = len(hashfunc(b'').digest())
    if not cls.__doc__:
      cls.__doc__ = f'{hashfunc.__name__} hashcode class, subclass of `bytes`.'

  hashfunc = lambda bs=None: None  # pylint: disable=unnecessary-lambda-assignment

  def __str__(self):
    ''' Return `f'{self.hashname}:{self.hex()}'`.
    '''
    return f'{self.hashname}:{self.hex()}'

  @property
  def hashname(self):
    ''' The hash code type name, derived from the class name.
    '''
    return self.__class__.__name__.lower()

  def hex(self) -> str:
    ''' Return the hashcode bytes transcribes as a hexadecimal ASCII `str`.
    '''
    return hexlify(self).decode('ascii')

  @classmethod
  def from_hashbytes(cls, hashbytes):
    ''' Factory function returning a `BaseHashCode` object from the hash bytes.
    '''
    assert len(hashbytes) == cls.hashlen, (
        "expected %d bytes, received %d: %r" %
        (cls.hashlen, len(hashbytes), hashbytes)
    )
    return cls(hashbytes)

  @classmethod
  def from_hashbytes_hex(cls, hashhex: str):
    ''' Factory function returning a `BaseHashCode` object
        from the hash bytes hex text.
    '''
    bs = unhexlify(hashhex)
    return cls.from_hashbytes(bs)

  @classmethod
  def from_named_hashbytes_hex(cls, hashname, hashhex):
    ''' Factory function to return a `BaseHashCode` object
        from the hash type name and the hash bytes hex text.
    '''
    hashclass = cls.hashclass(hashname)
    if not issubclass(hashclass, cls):
      raise ValueError(
          f'{cls.__name__}.from_named_hashbytes_hex({hashname!r},{hashhex!r}):'
          f' inferred hash class {hashclass.__name__}:{hashclass!r}'
          f' is not a subclass of {cls.__name__}:{cls!r}'
      )
    bs = unhexlify(hashhex)
    return hashclass.from_hashbytes(bs)

  @classmethod
  def from_prefixed_hashbytes_hex(cls, hashtext: str):
    ''' Factory function returning a `BaseHashCode` object
        from the hash bytes hex text prefixed by the hashname.
        This is the reverse of `__str__`.
    '''
    hashname, hashhex = hashtext.split(':')
    return cls.from_named_hashbytes_hex(hashname, hashhex)

  @classmethod
  def from_data(cls, bs):
    ''' Compute hashcode from the data `bs`.
    '''
    return cls(cls.hashfunc(bs).digest())

  @classmethod
  @promote
  def from_buffer(cls, bfr: CornuCopyBuffer):
    ''' Compute hashcode from the contents of the `CornuCopyBuffer` `bfr`.
    '''
    h = cls.hashfunc()
    for bs in bfr:
      h.update(bs)
    return cls(h.digest())

  @classmethod
  def from_fspath(cls, fspath, **kw):
    ''' Compute hashcode from the contents of the file `fspath`.
    '''
    # try to mmap the file and hash the whole thing in one go
    fd = None
    try:
      fd = os.open(fspath, os.O_RDONLY)
    except OSError:
      pass
    else:
      S = os.fstat(fd)
      if S.st_size == 0:
        return cls.from_data(b'')
      try:
        with mmap.mmap(fd, 0, flags=mmap.MAP_PRIVATE,
                       prot=mmap.PROT_READ) as mmapped:
          return cls.from_data(mmapped)
      except OSError:
        pass
    finally:
      if fd is not None:
        os.close(fd)
    # mmap fails, try plain open of file
    return cls.from_buffer(CornuCopyBuffer.from_filename(fspath, **kw))

  @classmethod
  def promote(cls, obj):
    ''' Promote to a `BaseHashCode` instance.
    '''
    if isinstance(obj, cls):
      return obj
    if isinstance(obj, bytes):
      return cls.from_hashbytes(obj)
    if isinstance(obj, str):
      return cls.from_prefixed_hashbytes_hex(obj)
    try:
      hashname, hashhex = obj
    except (TypeError, ValueError):
      pass
    else:
      return cls.from_named_hashbytes_hex(hashname, hashhex)
    raise TypeError(f'{cls.__name__}.promote({r(obj)}): cannot promote')

# convenience predefined hash classes
MD5 = BaseHashCode.hashclass('md5')
SHA1 = BaseHashCode.hashclass('sha1')
SHA224 = BaseHashCode.hashclass('sha224')
SHA256 = BaseHashCode.hashclass('sha256')
SHA384 = BaseHashCode.hashclass('sha384')
SHA512 = BaseHashCode.hashclass('sha512')
# define BLAKE3 if available
try:
  from blake3 import blake3
except ImportError:
  pass
else:
  BLAKE3 = BaseHashCode.hashclass('blake3')

#!/usr/bin/env python3
#

''' Functions and classes around hashcodes.
'''

from binascii import unhexlify
from bisect import bisect_left
from hashlib import sha1, sha256
from os.path import splitext
import sys
from icontract import require
from typeguard import typechecked
from cs.binary import BSUInt, BinarySingleValue
from cs.excutils import exc_fold
from cs.lex import get_identifier, hexify
from cs.resources import MultiOpenMixin
from .pushpull import missing_hashcodes
from .transcribe import Transcriber, transcribe_s, register as register_transcriber

class MissingHashcodeError(KeyError):
  ''' Subclass of KeyError
      raised when accessing a hashcode is not present in the Store.
  '''

  def __init__(self, hashcode):
    KeyError.__init__(self, str(hashcode))
    self.hashcode = hashcode

  def __str__(self):
    return "missing hashcode: %s" % (self.hashcode,)

def io_fail(func):
  ''' Decorator to transmute a `MissingHashcodeError` into a return of `False`.
  '''
  return exc_fold(exc_types=(MissingHashcodeError,))(func)

class HasDotHashclassMixin:
  ''' Mixin providing `.hashenum` and `.hashname` properties.
  '''

  @property
  def hashenum(self):
    ''' The hashclass enum value.
    '''
    return self.hashclass.HASHENUM

  @property
  def hashname(self):
    ''' The name token for this hashclass.
    '''
    return self.hashclass.HASHNAME

class HashCodeField(BinarySingleValue, HasDotHashclassMixin):
  ''' Binary transcription of hashcodes.
  '''

  @property
  def hashcode(self):
    ''' The value named `.hashcode`.
    '''
    return self.value

  @property
  def hashclass(self):
    ''' The hash class comes from the hash code.
    '''
    return self.value.hashclass

  @staticmethod
  def parse_value(bfr):
    ''' Decode a serialised hash from the CornuCopyBuffer `bfr`.
    '''
    hashenum = BSUInt.parse_value(bfr)
    hashcls = HASHCLASS_BY_ENUM[hashenum]
    return hashcls.from_hashbytes(bfr.take(hashcls.HASHLEN))

  @staticmethod
  def transcribe_value(hashcode):
    ''' Serialise a hashcode.
    '''
    yield BSUInt.transcribe_value(hashcode.HASHENUM)
    yield hashcode

decode_buffer = HashCodeField.parse_value
decode = HashCodeField.parse_value_from_bytes

class HashCode(bytes, Transcriber, HasDotHashclassMixin):
  ''' All hashes are bytes subclasses.
  '''

  __slots__ = ()

  by_name = {}
  by_enum = {}

  @classmethod
  @typechecked
  @require(lambda cls, hashname: hashname not in cls.by_name)
  @require(lambda cls, hashenum: hashenum not in cls.by_enum)
  def new_class(
      cls, hashname: str, hashenum: int, *, hashfunc: callable, hashlen: int
  ):
    ''' Factory t create, register and return a new `HashCode` subclass.
    '''

    class hashclass(HashCode):
      ''' `HashCode` subclass.
      '''
      __slotes__ = ()
      HASHNAME = hashname
      HASHFUNC = hashfunc
      HASHLEN = hashlen
      HASHENUM = hashenum
      HASHENUM_BS = bytes(BSUInt(hashenum))
      HASHLEN_ENCODED = len(HASHENUM_BS) + HASHLEN

    hashclass.__name__ = 'Hash_' + hashname.upper()
    hashclass.__doc__ = f"HashCode(bytes) subclass for the {hashname} hash function."
    cls.by_name[hashname] = hashclass
    cls.by_enum[hashenum] = hashclass
    return hashclass

  @classmethod
  def by_index(cls, index):
    ''' Obtain a hash class from its name or enum.
    '''
    if isinstance(index, str):
      return cls.by_name[index]
    if isinstance(index, int):
      return cls.by_enum[index]
    raise TypeError(
        "%s.by_index: expected str or int, got %s:%r" %
        (cls.__name__, type(index).__name__, index)
    )

  transcribe_prefix = 'H'

  def __str__(self):
    return transcribe_s(self)

  def __repr__(self):
    return ':'.join((self.HASHNAME, hexify(self)))

  @property
  def hashclass(self):
    ''' The hash class is our own type.
    '''
    return type(self)

  @property
  def bare_etag(self):
    ''' An HTTP ETag string (HTTP/1.1, RFC2616 3.11) without the quote marks.
    '''
    return ':'.join((self.HASHNAME, hexify(self)))

  @property
  def etag(self):
    ''' An HTTP ETag string (HTTP/1.1, RFC2616 3.11).
    '''
    return '"' + self.bare_etag + '"'

  def __eq__(self, other):
    return self.HASHENUM == other.HASHENUM and bytes.__eq__(self, other)

  def __hash__(self):
    return bytes.__hash__(self)

  @staticmethod
  def from_buffer(bfr):
    ''' Decode a hash from a buffer.
    '''
    return HashCodeField.parse_value(bfr)

  def transcribe_b(self):
    ''' Binary transcription of this hash.
    '''
    return HashCodeField.transcribe_value(self)

  def encode(self):  # pylint: disable=arguments-differ
    ''' Return the serialised form of this hash object: hash enum plus hash bytes.

        If we ever have a variable length hash function,
        hash bytes will have to include that information.
    '''
    return bytes(HashCodeField(self))

  @classmethod
  def from_hashbytes(cls, hashbytes):
    ''' Factory function returning a `HashCode` object from the hash bytes.
    '''
    assert len(hashbytes) == cls.HASHLEN, (
        "expected %d bytes, received %d: %r" %
        (cls.HASHLEN, len(hashbytes), hashbytes)
    )
    return cls(hashbytes)

  @classmethod
  def from_hashbytes_hex(cls, hashtext):
    ''' Factory function returning a `HashCode` object
        from the hash bytes hex text.
    '''
    bs = unhexlify(hashtext)
    return cls.from_hashbytes(bs)

  @staticmethod
  def from_named_hashbytes_hex(hashname, hashtext):
    ''' Factory function to return a `HashCode` object
        from the hash type name and the hash bytes hex text.
    '''
    try:
      hashclass = HASHCLASS_BY_NAME[hashname.lower()]
    except KeyError:
      raise ValueError(
          "unknown hashclass name %r" % (hashname,)
      )  # pylint: raise-missing-from
    return hashclass.from_hashbytes_hex(hashtext)

  @classmethod
  def from_chunk(cls, chunk):
    ''' Factory function returning a HashCode object from a data block.
    '''
    hashbytes = cls.HASHFUNC(chunk).digest()  # pylint: disable=not-callable
    return cls.from_hashbytes(hashbytes)

  @property
  def hashfunc(self):
    ''' Convenient hook to this Hash's class' .from_chunk method.
    '''
    return self.__class__.from_chunk

  @property
  def filename(self):
    ''' A file basename for files related to this hashcode: {hashcodehex}.{hashtypename}
    '''
    return hexify(self) + '.' + self.HASHNAME

  @classmethod
  def from_filename(cls, filename):
    ''' Take a *hashcodehex*`.`*hashname* string
        and return a `HashCode` subclass instance.

        If `cls` has a `.HASHNAME` attribute then that is taken as
        a default if there is no `.`*hashname*.
    '''
    hexpart, ext = splitext(filename)
    if ext:
      hashname = ext[1:]
    else:
      try:
        hashname = cls.HASHNAME
      except AttributeError as e:
        raise ValueError("no .hashname extension") from e
    hashclass = cls.by_index(hashname)
    hashbytes = bytes.fromhex(hexpart)
    return hashclass.from_hashbytes(hashbytes)

  def transcribe_inner(self, T, fp):
    fp.write(self.HASHNAME)
    fp.write(':')
    fp.write(hexify(self))

  @staticmethod
  def parse_inner(T, s, offset, stopchar, prefix):
    ''' Parse hashname:hashhextext from `s` at offset `offset`.
        Return HashCode instance and new offset.
    '''
    hashname, offset = get_identifier(s, offset)
    if not hashname:
      raise ValueError("missing hashname at offset %d" % (offset,))
    hashclass = HASHCLASS_BY_NAME[hashname]
    if offset >= len(s) or s[offset] != ':':
      raise ValueError("missing colon at offset %d" % (offset,))
    offset += 1
    hexlen = hashclass.HASHLEN * 2
    hashtext = s[offset:offset + hexlen]
    if len(hashtext) != hexlen:
      raise ValueError(
          "expected %d hex digits, found only %d" % (hexlen, len(hashtext))
      )
    offset += hexlen
    H = hashclass.from_hashbytes_hex(hashtext)
    return H, offset

register_transcriber(HashCode)

# legacy names, to be removed (TODO)
HASHCLASS_BY_NAME = HashCode.by_name
HASHCLASS_BY_ENUM = HashCode.by_enum

# enums for hash types; TODO: remove and use names throughout
HASH_SHA1_T = 0
HASH_SHA256_T = 1

Hash_SHA1 = HashCode.new_class('sha1', HASH_SHA1_T, hashfunc=sha1, hashlen=20)
Hash_SHA256 = HashCode.new_class(
    'sha256', HASH_SHA256_T, hashfunc=sha256, hashlen=32
)

DEFAULT_HASHCLASS = Hash_SHA1

class HashCodeUtilsMixin:
  ''' Utility methods for classes which use `HashCode`s as keys.

      Subclasses will generally override `.hashcodes_from`,
      which returns an iterator that yields hashcodes until none remains.
      The default implementation presumes the class is iterable and
      that that iteration yields hashcodes; this works for mappings,
      for example. However, because of the need for sorted output
      the default implementation is expensive. A subclass built on
      some kind of database will often have an efficient ordered key
      iteration that can be used instead.

      The other methods include:
      * `.hashcodes`:
        the big brother of hashcodes_from with more options;
        the default implentation uses `.hashcodes_from` and
        is roughly as efficient or inefficient.
        Classes like `StreamStore` provide their own implementation,
        but this is usually not necessary.
      * `.hash_of_hashcodes`: used for comparing Store contents efficiently
      * `.hashcodes_missing`: likewise
  '''

  def hash_of_byteses(self, bss):
    ''' Compute a `HashCode` from an iterable of `bytes`.

        This underlies the mechanism for comparing remote Stores,
        which is based on the `hash_of_hashcodes` method.
    '''
    hashclass = self.hashclass
    hashstate = hashclass.HASHFUNC()
    for bs in bss:
      hashstate.update(bs)
    return hashclass.from_chunk(hashstate.digest())

  @require(
      lambda self, start_hashcode: start_hashcode is None or
      type(start_hashcode) is self.hashclass
  )  # pylint: disable=unidiomatic-typecheck
  def hash_of_hashcodes(
      self, *, start_hashcode=None, after=False, length=None
  ):
    ''' Return a hash of the hashcodes requested and the last
        hashcode (or `None` if no hashcodes matched);
        used for comparing remote Stores.
    '''
    if length is not None and length < 1:
      raise ValueError("length < 1: %r" % (length,))
    if after and start_hashcode is None:
      raise ValueError(
          "after=%s but start_hashcode=%s" % (after, start_hashcode)
      )
    hs = list(
        self.hashcodes(
            start_hashcode=start_hashcode, after=after, length=length
        )
    )
    if hs:
      h_final = hs[-1]
    else:
      h_final = None
    return self.hash_of_byteses(hs), h_final

  def hashcodes_missing(self, other, *, window_size=None):
    ''' Generator yielding hashcodes in `other` which are missing in `self`.
        Note that a StreamStore overrides this with a call to
        missing_hashcodes_by_checksum to reduce bandwidth.
    '''
    return missing_hashcodes(self, other, window_size=window_size)

  @require(
      lambda self, start_hashcode: start_hashcode is None or
      type(start_hashcode) is self.hashclass
  )
  # pylint: disable=too-many-branches,unidiomatic-typecheck
  def hashcodes_from(self, *, start_hashcode=None):
    ''' Default generator yielding hashcodes from this object until none remains.

        See the `hashcodes()` method for a wrapper with more features.

        This implementation starts by fetching and sorting all the
        keys, so for large mappings this implementation is memory
        expensive and also runtime expensive if only a few hashcodes
        are desired.

        Parameters:
        * `start_hashcode`: starting hashcode;
          the returned hashcodes are `>=start_hashcode`;
          if `None` start the sequences from the smallest hashcode
    '''
    ks = sorted(self.keys())
    if not ks:
      return
    if start_hashcode is None:
      ndx = 0
    else:
      ndx = bisect_left(ks, start_hashcode)
      if ndx == len(ks):
        # start_hashcode > max hashcode
        # nothing to return
        return
    # yield keys until we're not wanted
    while True:
      try:
        hashcode = ks[ndx]
      except IndexError:
        break
      yield hashcode
      ndx += 1

  @require(
      lambda self, start_hashcode: start_hashcode is None or
      type(start_hashcode) is self.hashclass
  )  # pylint: disable=unidiomatic-typecheck
  def hashcodes(self, *, start_hashcode=None, after=False, length=None):
    ''' Generator yielding up to `length` hashcodes `>=start_hashcode`.
        This relies on `.hashcodes_from` as the source of hashcodes.

        Parameters:
        * `start_hashcode`: starting hashcode;
          the returned hashcodes are `>=start_hashcode`;
          if None start the sequences from the smallest hashcode
        * `after`: skip the first hashcode if it is equal to `start_hashcode`
        * `length`: the maximum number of hashcodes to yield
    '''
    if length is not None and length < 1:
      raise ValueError("length < 1: %r" % (length,))
    if after and start_hashcode is None:
      raise ValueError(
          "after=%s but start_hashcode=%s" % (after, start_hashcode)
      )
    # try to short circuit if there are no hashcodes
    try:
      nhashcodes = len(self)
    except TypeError:
      pass
    else:
      if nhashcodes == 0:
        return
    first = True
    for hashcode in self.hashcodes_from(start_hashcode=start_hashcode):
      if first:
        first = False
        if after and hashcode == start_hashcode:
          # skip start_hashcode if present
          continue
      yield hashcode
      if length is not None:
        length -= 1
        if length < 1:
          break

  @require(
      lambda self, start_hashcode: start_hashcode is None or
      isinstance(start_hashcode, type(self))
  )
  def hashcodes_bg(self, *, start_hashcode=None, after=False, length=None):
    ''' Background a hashcodes call.
    '''
    return self._defer(
        self.hashcodes,
        start_hashcode=start_hashcode,
        after=after,
        length=length
    )

class HashUtilDict(dict, MultiOpenMixin, HashCodeUtilsMixin):
  ''' Simple dict subclass supporting HashCodeUtilsMixin.
  '''

  def __init__(self, hashclass=None):
    dict.__init__(self)
    if hashclass is None:
      hashclass = DEFAULT_HASHCLASS
    self.hashclass = hashclass

  def __str__(self):
    return '<%s:%d-entries>' % (self.__class__.__name__, len(self))

  def add(self, data):
    ''' Add `data` to the dict.
    '''
    hashcode = self.hashclass.from_chunk(data)
    self[hashcode] = data
    return hashcode

  def startup(self):
    ''' Dummy method to support unit tests with open/close.
    '''

  def shutdown(self):
    ''' Dummy method to support unit tests with open/close.
    '''

if __name__ == '__main__':
  from .hash_tests import selftest
  selftest(sys.argv)

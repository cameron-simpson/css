import sys
if sys.hexversion >= 0x02050000:
  from hashlib import sha1
else:
  from sha import new as sha1
if sys.hexversion < 0x02060000:
  bytes = str
from binascii import unhexlify
from bisect import bisect_left, bisect_right
from cs.lex import hexify, get_identifier
from cs.logutils import D
from cs.resources import MultiOpenMixin
from cs.serialise import get_bs, put_bs
from cs.x import X
from .pushpull import missing_hashcodes
from .transcribe import Transcriber, transcribe_s, register as register_transcriber

# enums for hash types, used in encode/decode
HASH_SHA1_T = 0

def decode(bs, offset=0):
  ''' Decode a serialised hash.
      Return the hash object and new offset.
  '''
  hashenum, offset = get_bs(bs, offset)
  if hashenum == HASH_SHA1_T:
    hashcls = Hash_SHA1
  else:
    raise ValueError("unsupported hashenum %d", hashenum)
  return hashcls._decode(bs, offset)

def hash_of_byteses(bss):
  ''' Compute a Hash_SHA1 from the bytes of the supplied `hashcodes`.
      This underlies the mechanism for comparing remote Stores.
  '''
  H = sha1()
  for bs in bss:
    H.update(bs)
  return Hash_SHA1.from_chunk(H.digest())

class _Hash(bytes, Transcriber):
  ''' All hashes are bytes subclasses.
  '''

  __slots__ = ()

  transcribe_prefix = 'H'

  def __str__(self):
    return transcribe_s(self)

  def __repr__(self):
    return ':'.join( (self.HASHNAME, hexify(self)) )

  def __eq__(self, other):
    return self.HASHENUM == other.HASHENUM and bytes.__eq__(self, other)

  def __hash__(self):
    return bytes.__hash__(self)

  def encode(self):
    ''' Return the serialised form of this hash object: hash enum plus hash bytes.
        If we ever have a variable length hash function, hash bytes will include that information.
    '''
    # no hashenum and raw hash
    return self.HASHENUM_BS + self

  @classmethod
  def _decode(cls, encdata, offset=0):
    ''' Pull off the encoded hash from the start of the encdata.
        Return Hash_* object and new offset.
        NOTE: this happens _after_ the hash type signature prefixed by .encode.
    '''
    hashbytes = encdata[offset:offset+cls.HASHLEN]
    if len(hashbytes) != cls.HASHLEN:
      raise ValueError("short data? got %d bytes, expected %d: %r"
                       % (len(hashbytes), cls.HASHLEN, encdata[offset:offset+cls.HASHLEN]))
    return cls.from_hashbytes(hashbytes), offset+len(hashbytes)

  @classmethod
  def from_hashbytes(cls, hashbytes):
    ''' Factory function returning a Hash_SHA1 object from the hash bytes.
    '''
    if len(hashbytes) != cls.HASHLEN:
      raise ValueError("expected %d bytes, received %d: %r" % (cls.HASHLEN, len(hashbytes), hashbytes))
    return cls(hashbytes)

  @classmethod
  def from_chunk(cls, chunk):
    ''' Factory function returning a _Hash object from a data block.
    '''
    hashbytes = cls.HASHFUNC(chunk).digest()
    return cls.from_hashbytes(hashbytes)

  @property
  def hashfunc(self):
    ''' Convenient hook to this Hash's class' .from_chunk method.
    '''
    return self.__class__.from_chunk

  def transcribe_inner(self, T, fp):
    fp.write(self.HASHNAME)
    fp.write(':')
    fp.write(hexify(self))

  @staticmethod
  def parse_inner(T, s, offset, stopchar):
    ''' Parse hashname:hashhextext from `s` at offset `offset`. Return _Hash instance and new offset.
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
      raise ValueError("expected %d hex digits, found only %d" % (hexlen, len(hashtext)))
    offset += hexlen
    bs = unhexlify(hashtext)
    H = hashclass.from_hashbytes(bs)
    return H, offset

register_transcriber(_Hash)

class Hash_SHA1(_Hash):
  __slots__ = ()
  HASHFUNC = sha1
  HASHNAME = 'sha1'
  HASHLEN = 20
  HASHENUM = HASH_SHA1_T
  HASHENUM_BS = put_bs(HASHENUM)
  HASHLEN_ENCODED = len(HASHENUM_BS) + HASHLEN

HASHCLASS_BY_NAME = {}

def register_hashclass(klass):
  global HASHCLASS_BY_NAME
  hashname = klass.HASHNAME
  if hashname in HASHCLASS_BY_NAME:
    raise ValueError(
            'cannot register hash class %s: hashname %r already registered to %s'
            % (klass, hashname, HASHCLASS_BY_NAME[hashname]))
  HASHCLASS_BY_NAME[hashname] = klass

register_hashclass(Hash_SHA1)

DEFAULT_HASHCLASS = Hash_SHA1

class HashCodeUtilsMixin(object):
  ''' Utility methods for classes which use hashcodes as keys.
      Subclasses will generally override .hashcodes_from, which
        returns an iterator that yields hashcodes until none remains.
        The default implementation presumes the class is iterable and
        that that iteration yields hashcodes; this works for mappings,
        for example. However, because of the need for sorted output
        the default implementation is expensive. A subclass built on
        some kind of database will often have an efficient key iteration
        that can be used instead.
      The other methods include:
        .hashcodes, the big brother of hashcodes_from with more
          options; the default implentation uses .hashcodes_from and
          is roughly as efficient or inefficient. Classes like
          StreamStore provide their own implementation, but this is
          usually not necessary.
        .hash_of_hashcodes, used for comparing Store contents efficiently
        .hashcodes_missing, likewise
  '''

  def hash_of_hashcodes(self, start_hashcode=None, reverse=None, after=False, length=None):
    ''' Return a hash of the hashcodes requested and the last hashcode (or None if no hashcodes matched); used for comparing remote Stores.
    '''
    if length is not None and length < 1:
      raise ValueError("length < 1: %r" % (length,))
    if after and start_hashcode is None:
      raise ValueError("after=%s but start_hashcode=%s" % (after, start_hashcode))
    hs = list(self.hashcodes(start_hashcode=start_hashcode, reverse=reverse, after=after, length=length))
    if hs:
      h_final = hs[-1]
    else:
      h_final = None
    return hash_of_byteses(hs), h_final

  def hashcodes_missing(self, other, window_size=None):
    ''' Generator yielding hashcodes in `other` which are missing in `self`.
        Note that a StreamStore overrides this with a call to
        missing_hashcodes_by_checksum to reduce bandwidth.
    '''
    return missing_hashcodes(self, other, window_size=window_size)

  def hashcodes_from(self, start_hashcode=None, reverse=False):
    ''' Default generator yielding hashcodes from this object until none remains.
        This implementation starts by fetching and sorting all the
        keys, so for large mappings this implementation is memory
        expensive and also runtime expensive if only a few hashcodes
        are desired.
        `start_hashcode`: starting hashcode - hashcodes are >=`start_hashcode`;
                          if None start the sequences from the smallest
                          hashcode or from the largest if `reverse` is true
        `reverse`: yield hashcodes in reverse order (counting down instead of up).
    '''
    hashclass = self.hashclass
    if start_hashcode is not None:
      if not isinstance(start_hashcode, hashclass):
        raise TypeError("hashclass %s does not match start_hashcode %r"
                        % (hashclass, start_hashcode))
    ks = sorted(hashcode for hashcode in iter(self) if isinstance(hashcode, hashclass))
    if not ks:
      return
    if start_hashcode is None:
      if reverse:
        ndx = len(ks) - 1
      else:
        ndx = 0
    else:
      ndx = bisect_left(ks, start_hashcode)
      if ndx == len(ks):
        # start_hashcode > max hashcode
        if reverse:
          # step back into array
          ndx -= 1
        else:
          # nothing to return
          return
      else:
        # start_hashcode <= max hashcode
        # ==> ks[ndx] >= start_hashcode
        if reverse and ks[ndx] > start_hashcode:
          if ndx > 0:
            ndx -= 1
          else:
            return
    # yield keys until we're not wanted
    while True:
      try:
        hashcode = ks[ndx]
      except IndexError:
        break
      yield hashcode
      if reverse:
        if ndx == 0:
          break
        ndx -= 1
      else:
        ndx += 1

  def hashcodes(self, start_hashcode=None, reverse=False, after=False, length=None):
    ''' Generator yielding up to `length` hashcodes >=`start_hashcode`.
        This relies on .hashcodes_from as the source of hashcodes.
        `start_hashcode`: starting hashcode - hashcodes are >=`start_hashcode`;
                          if None start the sequences from the smallest
                          hashcode or from the largest if `reverse` is true
        `reverse`: yield hashcodes in reverse order (counting down instead of up).
        `after`: skip the first hashcode if it is equal to `start_hashcode`
        `length`: the maximum number of hashcodes to yield
    '''
    if length is not None and length < 1:
      raise ValueError("length < 1: %r" % (length,))
    if after and start_hashcode is None:
      raise ValueError("after=%s but start_hashcode=%s" % (after, start_hashcode))
    # try to short circuit if there are no hashcodes
    try:
      nhashcodes = len(self)
    except TypeError:
      pass
    else:
      if nhashcodes == 0:
        return
    first = True
    for hashcode in self.hashcodes_from(start_hashcode=start_hashcode,
                                        reverse=reverse):
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

  def hashcodes_bg(self, start_hashcode=None, reverse=None, after=False, length=None):
    return self._defer(self.hashcodes, start_hashcode=start_hashcode, reverse=reverse, after=after, length=length)

class HashUtilDict(dict, MultiOpenMixin, HashCodeUtilsMixin):
  ''' Simple dict subclass supporting HashCodeUtilsMixin.
  '''

  def __init__(self):
    dict.__init__(self)
    MultiOpenMixin.__init__(self)
    self.hashclass = DEFAULT_HASHCLASS

  def __str__(self):
    return '<%s:%d-entries>' % (self.__class__.__name__, len(self))

  def add(self, data):
    hashcode = Hash_SHA1.from_chunk(data)
    self[hashcode] = data
    return hashcode

  def startup(self):
    ''' Dummy method to support unit tests with open/close.
    '''
    pass

  def shutdown(self):
    ''' Dummy method to support unit tests with open/close.
    '''
    pass

if __name__ == '__main__':
  from .hash_tests import selftest
  selftest(sys.argv)

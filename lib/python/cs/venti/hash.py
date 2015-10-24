import sys
if sys.hexversion >= 0x02050000:
  from hashlib import sha1
else:
  from sha import new as sha1
if sys.hexversion < 0x02060000:
  bytes = str
from cs.lex import hexify
from cs.logutils import D, X
from cs.serialise import get_bs, put_bs

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

def checksum(hashcodes):
  ''' Compute a Hash_SHA1 from the bytes of the supplied `hashcodes`.
      This underlies the mechanism for comparing remote Stores.
  '''
  H = sha1()
  for hashcode in hashcodes:
    H.update(hashcode)
  return Hash_SHA1.from_data(H.digest())

class _Hash(bytes):
  ''' All hashes are bytes subclasses.
  '''

  def __str__(self):
    return hexify(self)

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
  def from_data(cls, data):
    ''' Factory function returning a _Hash object from a data block.
    '''
    hashbytes = cls.HASHFUNC(data).digest()
    return cls.from_hashbytes(hashbytes)

  @property
  def hashfunc(self):
    ''' Convenient hook to this Hash's class' .from_data method.
    '''
    return self.__class__.from_data

class Hash_SHA1(_Hash):
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
  '''

  def hashcodes_checksum(self, hashcode, length, hashclass=None):
    ''' Collate `length` hashcodes in order from `hashcode` onward, return checksum hashcode and final hashcode covered.
        This is to be used for scanning remote Stores for differences.
    '''
    if length < 1:
      raise ValueError("length must be >=1 (%d)" % (length,))
    final_hashcode_list = [None]
    def scan_hashcodes():
      # using reverse=False to request ordered hashcodes
      # should raise an exception if hashcodes cannot return ordered hashcodes
      for hashcode in self.hashcodes(hashcode=hashcode,
                                     hashclass=hashclass,
                                     reverse=False,
                                     length=length):
        yield hashcode
      final_hashcode_list[0] = hashcode
    H = checksum(scan_hashcodes())
    return H, final_hashcode_list[0]

  def hashcodes_missing(self, other):
    ''' Yield hashcodes in `other` which are not present in self.
    '''
    # number of hashcodes to request in one go
    start_hashcode = other.first()
    if start_hashcode not in self:
      yield start_hashcode
    # post: start_hashcode has been checked
    span_size = 1024
    while start_hashcode is not None:
      hashcode = None
      for hashcode in other.hashcodes(hashcode=start_hashcode, length=span_size, after=True):
        if hashcode not in self:
          yield hashcode
      start_hashcode = hashcode

class HashUtilDict(dict, HashCodeUtilsMixin):
  ''' Simple dict subclass supporting HashCodeUtilsMixin.
  '''

  def add(self, data):
    hashcode = Hash_SHA1.from_data(data)
    self[hashcode] = data
    return hashcode

  def sorted_keys(self, hashclass=None):
    if hashclass is None:
      hashclass = DEFAULT_HASHCLASS
    return sorted(h for h in self.keys() if isinstance(h, hashclass))

  def first(self, hashclass=None):
    ks = self.sorted_keys(hashclass=hashclass)
    if ks:
      return ks[0]
    return None

  def hashcodes(self, hashclass=None, hashcode=None, reverse=None, after=False, length=None):
    if length is not None and length < 1:
      raise ValueError("length < 1: %r" % (length,))
    if not len(self):
      return
    if hashclass is None:
      first_hashcode = self.first()
      hashclass = first_hashcode.__class__
    ks = self.sorted_keys()
    if hashcode is None:
      ndx = 0
    else:
      ndx = ks.index(hashcode)
    first = True
    while length is None or length > 0:
      try:
        hashcode = ks[ndx]
      except IndexError:
        break
      if not first or not after:
        yield hashcode
        if length is not None:
          length -= 1
      if reverse:
        ndx -= 1
      else:
        ndx += 1
      first = False

if __name__ == '__main__':
  import cs.venti.hash_tests
  cs.venti.hash_tests.selftest(sys.argv)

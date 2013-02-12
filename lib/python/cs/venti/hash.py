import sys
if sys.hexversion >= 0x02050000:
  from hashlib import sha1
else:
  from sha import new as sha1
if sys.hexversion < 0x02060000:
  bytes = str
from cs.lex import hexify
from cs.logutils import D
from cs.serialise import get_bs, put_bs

# enums for hash types, used in encode/decode
# TODO: use classes directly?
HASH_SHA1_T = 0
HASH_SIZE_SHA1 = 20                               # size of SHA-1 hash
HASH_SIZE_DEFAULT = 20                            # default size of hash
                                                # NEVER CHANGE HASH_SIZE!!!
assert HASH_SIZE_DEFAULT == 20

def decode(bs, offset=0):
  ''' Decode a serialised hash.
      Return the hash object and new offset.
  '''
  hashenum, offset = get_bs(bs, offset)
  if hashenum == HASH_SHA1_T:
    hashcls = Hash_SHA1
  else:
    raise ValueError("unsupported hashenum %d", hashenum)
  hashlen = hashcls.HASHLEN
  hashdata = bs[offset:offset+hashlen]
  if len(hashdata) < hashlen:
    raise ValueError("short hashdata, expected %d bytes, got %d: %r"
                     % (hashlen, len(hashdata), hashdata))
  offset += len(hashdata)
  return hashcls(hashdata), offset

class Hash_SHA1(bytes):
  HASHLEN = 20
  HASHENUM = HASH_SHA1_T
  HASHENUM_BS = put_bs(HASHENUM)
  HASHLEN_ENCODED = len(HASHENUM_BS) + HASHLEN

  def __str__(self):
    return hexify(self)

  def __repr__(self):
    return "Hash_SHA1:" + hexify(self)

  @classmethod
  def fromData(cls, data):
    ''' Factory function returning a Hash_SHA1 object for a data block.
    '''
    hashcode = sha1(data).digest()
    return cls.fromHashcode(hashcode)

  @classmethod
  def fromHashcode(cls, hashcode):
    assert len(hashcode) == cls.HASHLEN
    return cls(hashcode)

  def encode(self):
    ''' Return the serialised form of this hash object.
    '''
    # no hashenum and raw hash
    return self.HASHENUM_BS + self

  @classmethod
  def decode(cls, encdata, offset=0):
    ''' Pull off the encoded hash from the start of the encdata.
        Return Hash_SHA1 object and tail of encdata.
    '''
    hashenum = encdata[offset]
    if hashenum != cls.HASHENUM:
      raise ValueError("unexpected hashenum; expected 0x%02x, found 0x%02x"
                       % (cls.HASHENUM, hashenum))
    offset += 1
    hashdata = encdata[offset:offset+cls.HASHLEN]
    if len(hashdata) != cls.HASHLEN:
      raise ValueError("short data? got %d bytes, expected %d: %r"
                       % (len(hashdata), cls.HASHLEN, encdata[offset:offset+cls.HASHLEN]))
    return cls.fromHashcode(hashdata), offset+len(hashdata)

if __name__ == '__main__':
  import cs.venti.hash_tests
  cs.venti.hash_tests.selftest(sys.argv)

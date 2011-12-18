import sys
if sys.hexversion >= 0x02050000:
  from hashlib import sha1
else:
  from sha import new as sha1
if sys.hexversion < 0x02060000:
  bytes = str
from cs.lex import hexify

# enums for hash types, used in encode/decode
# TODO: use classes directly?
HASH_SHA1_T = 0
HASH_SIZE_SHA1 = 20                               # size of SHA-1 hash
HASH_SIZE_DEFAULT = 20                            # default size of hash
                                                # NEVER CHANGE HASH_SIZE!!!
assert HASH_SIZE_DEFAULT == 20

class Hash_SHA1(bytes):
  hashlen = 20
  hashenum = HASH_SHA1_T

  def __str__(self):
    return hexify(self)

  def __repr__(self):
    return "Hash_SHA1:"+hexify(self)

  @classmethod
  def fromData(cls, data):
    ''' Factory function returning a Hash_SHA1 object for a data block.
    '''
    hashcode = sha1(data).digest()
    return cls.fromHashcode(hashcode)

  @classmethod
  def fromHashcode(cls, hashcode):
    assert len(hashcode) == cls.hashlen
    return cls(hashcode)

  def encode(self):
    ''' Return the serialised form of this hash object.
        This presumes the reader will know the hash type,
        and thus the decode() method to use.
    '''
    # no hashenum, no hashlen, just raw hash
    return self

  @classmethod
  def decode(cls, encdata):
    ''' Pull off the encoded hash from the start of the encdata.
        Return Hash_SHA1 object and tail of encdata.
    '''
    hashcode = encdata[:cls.hashlen]
    assert len(hashcode) == cls.hashlen, "encdata (%d bytes) too short" % (len(encdata),)
    return cls.fromHashcode(hashcode), encdata[cls.hashlen:]

if __name__ == '__main__':
  import cs.venti.hash_tests
  cs.venti.hash_tests.selftest(sys.argv)

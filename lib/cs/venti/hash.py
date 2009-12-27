import sys
if sys.hexversion >= 0x02050000:
  from hashlib import sha1
else:
  from sha import new as sha1
if sys.hexversion < 0x02060000:
  bytes = str
import unittest
from cs.venti import tohex

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

  def __repr__(self):
    return "Hash_SHA1:"+tohex(self)

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

class TestAll(unittest.TestCase):
  def setUp(self):
    import random
    random.seed()
  def testSHA1(self):
    import random
    for i in range(10):
      rs = ''.join( chr(random.randint(0,255)) for x in range(100) )
      H = Hash_SHA1.fromData(rs)
      self.assertEqual( sha1(rs).digest(), H )
      Hencode = H.encode()
      H2, etc = Hash_SHA1.decode(Hencode)
      assert len(etc) == 0
      assert H == H2

if __name__ == '__main__':
  unittest.main()

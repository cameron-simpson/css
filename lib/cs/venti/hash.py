import sys
if sys.hexversion >= 0x02050000:
  from hashlib import sha1
else:
  from sha import new as sha1
if sys.hexversion < 0x02060000:
  bytes = str
import unittest

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
  @classmethod
  def hash(cls, data):
    ''' Compute hash digest from data.
    '''
    return sha1(data).digest()

  def __init__(self, data=None, hashcode=None):
    ''' Initialise a Hash from data or a pre-supplied hash digest.
    '''
    assert (data is None) ^ (hashcode is None)
    assert hashcode is None or len(hashcode) == Hash_SHA1.hashlen
    if hashcode is None:
      hashcode = self.hash(data)
    self.hashcode = hashcode

  def encode(self):
    ''' Return the serialised form of this hash object.
    '''
    # no hashenum, no hashlen
    return self.hashcode

  @classmethod
  def decode(cls, encdata):
    ''' Pull off the encoded hash from the start of the encdata.
        Return Hash_SHA1 object and tail of encdata.
    '''
    hashcode = encdata[:self.hashlen]
    assert len(hashcode) == self.hashlen, "encdata (%d bytes) too short" % (len(encdata),)
    return Hash_SHA1(hashcode=hashcode), encdata[20:]

class TestAll(unittest.TestCase):
  def setUp(self):
    import random
    random.seed()
  def testSHA1(self):
    import random
    for i in range(10):
      rs = ''.join( chr(random.randint(0,255)) for x in range(100) )
      self.assertEqual( sha1(rs).digest(), Hash_SHA1.hash(rs) )

HASH_T = HASH_SHA1_T

if __name__ == '__main__':
  unittest.main()

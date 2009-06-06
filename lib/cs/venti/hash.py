import sys
if sys.hexversion < 0x02050000:
  import sha as _mod_sha
  sha1 = _mod_sha.new
else:
  from hashlib import sha1
import unittest
  
HASH_SHA1_T=0
HASH_SIZE_SHA1=20                               # size of SHA-1 hash
HASH_SIZE_DEFAULT=20                            # default size of hash
                                                # NEVER CHANGE HASH_SIZE!!!
assert HASH_SIZE_DEFAULT == 20
MIN_BLOCKSIZE=80                                # less than this seems silly
MAX_BLOCKSIZE=16383                             # fits in 2 octets BS-encoded
MAX_SUBBLOCKS=int(MAX_BLOCKSIZE/(3+HASH_SIZE_DEFAULT))  # flags(1)+span(2)+hash

def hash_sha1(block):
  ''' Returns the SHA-1 checksum for the supplied block.
  '''
  hash=sha1(block)
  h=hash.digest()
  assert len(h) == HASH_SIZE_SHA1
  return h

hash=hash_sha1
HASH_T=HASH_SHA1_T

class TestAll(unittest.TestCase):
  def setUp(self):
    import random
    random.seed()
  def testSHA1(self):
    import random
    for i in range(10):
      rs = ''.join( chr(random.randint(0,255)) for x in range(100) )
      self.assertEqual( sha1(rs).digest(), hash_sha1(rs) )

if __name__ == '__main__':
  unittest.main()

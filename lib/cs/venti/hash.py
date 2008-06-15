## NOTE: migrate to hashlib sometime when python 2.5 more common
import sha
HASH_SHA1_T=0
HASH_SIZE_SHA1=20                               # size of SHA-1 hash
HASH_SIZE_DEFAULT=20                            # default size of hash
                                                # NEVER CHANGE HASH_SIZE!!!
assert HASH_SIZE_DEFAULT == 20
MIN_BLOCKSIZE=80                                # less than this seems silly
MAX_BLOCKSIZE=16383                             # fits in 2 octets BS-encoded
MAX_SUBBLOCKS=int(MAX_BLOCKSIZE/(3+HASH_SIZE_DEFAULT))  # flags(1)+span(2)+hash

def hash_sha(block):
  ''' Returns the SHA-1 checksum for the supplied block.
  '''
  hash=sha.new(block)
  h=hash.digest()
  assert len(h) == HASH_SIZE_SHA1
  return h

hash=hash_sha
HASH_T=HASH_SHA1_T

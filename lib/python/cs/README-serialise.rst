Some serialisation functions.
=============================

I use these functions to serialise and de-serialise non-negative integers of arbitrary size and run length encoded block data.

The integers are encoded as octets in big-endian order, with the high bit indicating that more octets follow.

* get_bs(bs, offset=0): collect an integer from the bytes `bs` at the specified `offset`

* get_bsdata(bs, offset=0): collect a run length encoded data block from the bytes `bs` at the specified `offset`

* get_bsfp(fp): collect an integer from the binary file `fp`

* put_bs(n): return the bytes encoding of the supplied integer `n`

* put_bsdata(data): return the bytes run length encoding of the supplied data block

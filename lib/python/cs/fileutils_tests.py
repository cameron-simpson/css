#!/usr/bin/python
#
# Unit tests for cs.fileutils.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
import unittest
from tempfile import NamedTemporaryFile
from .fileutils import compare, rewrite

class Test(unittest.TestCase):

  def setUp(self):
    pass

  def tearDown(self):
    pass

  def test_compare(self):
    data = "here are some data\n"
    with NamedTemporaryFile() as T1:
      T1.write(data)
      T1.flush()
      with NamedTemporaryFile() as T2:
        T2.write(data)
        T2.flush()
        self.assertEquals( open(T1.name).read(), data, "bad data in %s" % (T1.name,) )
        self.assertEquals( open(T2.name).read(), data, "bad data in %s" % (T2.name,) )
        self.assert_(compare(T1.name, T2.name), "mismatched data in %s and %s" % (T1.name, T2.name))

  def test_rewrite(self):
    from StringIO import StringIO
    olddata = "old data\n"
    newdata = "new data\n"
    with NamedTemporaryFile() as T1:
      T1.write(olddata)
      T1.flush()
      self.assertEquals( open(T1.name).read(), olddata, "bad old data in %s" % (T1.name,) )
      rewrite(T1.name, StringIO(newdata))
      self.assertEquals( open(T1.name).read(), newdata, "bad new data in %s" % (T1.name,) )

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)

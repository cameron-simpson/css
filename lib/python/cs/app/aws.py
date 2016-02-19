#!/usr/bin/python
#
# Access Amazon AWS services.
# Uses boto underneath, but boto does not feel awfully pythonic.
# In any case, this exercise will give me convenient AWS access and
# an avenue to learn the boto interfaces.
#       - Cameron Simpson <cs@zip.com.au> 17nov2012
#

from __future__ import print_function
import sys
from contextlib import contextmanager
from threading import RLock
import os.path
from getopt import getopt, GetoptError
import boto3
from cs.logutils import setup_logging, D, error, Pfx
from cs.threads import locked_property
from cs.obj import O, O_str

def main(argv, stderr=None):
  if stderr is None:
    stderr = sys.stderr

  argv=list(sys.argv)
  cmd=os.path.basename(argv.pop(0))
  usage="Usage: %s {s3|ec2} [-L location] command [args...]"
  setup_logging(cmd)

  location = None

  badopts = False

  try:
    opts, argv = getopt(argv, 'L:')
  except GetoptError as e:
    error("bad option: %s", e)
    badopts = True
    opts = ()

  for opt, val in opts:
    if opt == '-L':
      location = val
    else:
      error("unimplemented option: %s", opt)
      badopts = True

  if not argv:
    error("missing command")
    badopts = True
  else:
    command = argv.pop(0)
    with Pfx(command):
      try:
        if command == 's3':
          xit = cmd_s3(argv)
        else:
          warning("unrecognised command")
          xit = 2
      except GetoptError as e:
        warning(str(e))
        badopts = True
        xit = 2

  if badopts:
    print(usage % (cmd,), file=stderr)
    xit = 2

  return xit

def cmd_s3(argv):
  s3 = boto3.resource('s3')
  print("s3 =", s3)
  if not argv:
    for bucket in s3.buckets.all():
      print(bucket.name)
    return 0
  else:
    raise GetoptError('extra arguments: %r' % (argv,))

if __name__ == '__main__':
  sys.exit(main(sys.argv))

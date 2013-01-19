#!/usr/bin/python
#
# Access Amazon AWS services.
# Uses boto underneath, but boto does not feel awfully pythonic.
# In any case, this exercise will give me convenient AWS access and
# an avenue to learn the boto interfaces.
#       - Cameron Simpson <cs@zip.com.au> 17nov2012
#

from __future__ import print_function
from contextlib import contextmanager
from threading import RLock
import os.path
from getopt import getopt, GetoptError
from boto.ec2.connection import EC2Connection
from boto.s3.connection import S3Connection, Location
from cs.logutils import setup_logging, D, error, Pfx
from cs.threads import locked_property
from cs.misc import O, O_str

def main(argv, stderr=None):
  if stderr is None:
    stderr = sys.stderr

  argv=list(sys.argv)
  cmd=os.path.basename(argv.pop(0))
  usage="Usage: %s {s3|ec2} [-L location] command [args...]"
  setup_logging(cmd)

  location = None

  badopts = False

  if not argv:
    error("missing s3 or ec2")
    badopts = True
  else:
    mode = argv.pop(0)
    if mode == 's3':
      klass = S3
    elif mode == 'ec2':
      klass = EC2
    else:
      error("unknown mode, I expect \"s3\" or \"ec2\", got \"%s\"", mode)
      badopts = True

    with Pfx(mode):
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
        command_method = "cmd_" + command
        if not hasattr(klass, command_method):
          error("unimplemented command: %s", command)
          badopts = True
        else:
          with Pfx(command):
            if mode == 'ec2':
              aws = klass(region=location)
            elif mode == 's3':
              aws = klass(location=location)
            else:
              raise RuntimeError("unimplemented mode: %s" % (mode,))
            with aws:
              try:
                xit = getattr(aws, command_method)(argv)
              except GetoptError as e:
                error("%s", e)
                badopts = True

  if badopts:
    print(usage % (cmd,), file=stderr)
    xit = 2

  return xit

class _AWS(O):
  ''' Convenience wrapper for EC2 connections.
  '''

  def __init__(self, aws_access_key_id=None, aws_secret_access_key=None):
    ''' Initialise the EC2 with access id and secret.
    '''
    O.__init__(self)
    self.aws = O()
    self.aws.access_key_id = aws_access_key_id
    self.aws.secret_access_key = aws_secret_access_key
    self._lock = RLock()
    self._O_omit.append('conn')

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    self.conn.close()
    return False

  @contextmanager
  def connection(self, **kwargs):
    ''' Return a context manager for a Connection.
    '''
    conn = self.connect(**kwargs)
    yield conn
    conn.close()

  @locked_property
  def conn(self):
    ''' The default connection, on demand.
    '''
    return self.connect()

  def cmd_report(self, argv):
    for line in self.report():
      print(line)
    return 0

class EC2(_AWS):

  def __init__(self, aws_access_key_id=None, aws_secret_access_key=None, region=None):
    ''' Initialise the EC2 with access id and secret.
    '''
    _AWS.__init__(self, aws_access_key_id=aws_access_key_id, aws_secret_access_key=aws_secret_access_key)
    self.aws.region = region
    self._O_omit.extend( ('regions', 'instances') )

  def __getattr__(self, attr):
    ''' Intercept public attributes.
        Support:
          Region name with '-' replaced by '_' -> RegionInfo
    '''
    if not attr.startswith('_'):
      dashed = attr.replace('_', '-')
      if dashed in self.regions:
        return self.region(dashed)
    raise AttributeError(attr)

  def connect(self, **kwargs):
    ''' Obtain a boto.ec2.connection.EC2Connection.
        Missing `aws_access_key_id`, `aws_secret_access_key`, `region`
        arguments come from the corresponding EC2 attributes.
    '''
    for kw in ('aws_access_key_id', 'aws_secret_access_key'):
      if kw not in kwargs:
        kwargs[kw] = getattr(self.aws, kw[4:], None)
    for kw in ('region',):
      if kw not in kwargs:
        kwargs[kw] = getattr(self.aws, kw, None)
    if isinstance(kwargs.get('region', None), (str, unicode)):
      kwargs['region'] = self.region(kwargs['region'])
    return EC2Connection(**kwargs)

  @locked_property
  def regions(self):
    ''' Return a mapping from Region name to Region.
    '''
    with self.connection(region=None) as ec2conn:
      RS = dict( [ (R.name, R) for R in ec2conn.get_all_regions() ] )
    return RS

  def region(self, name):
    ''' Return the Region with the specified `name`.
    '''
    return self.regions[name]

  @property
  def reservations(self):
    ''' Return Reservations in the default Connection.
    '''
    return self.conn.get_all_instances()

  def report(self):
    ''' Report AWS info. Debugging/testing method.
    '''
    yield str(self)
    yield "  regions: " + str(self.regions)
    yield "  reservations: " + str(self.reservations)
    for R in self.reservations:
      region = R.region
      yield "    %s @ %s %s" % (R.id, R.region.name, O_str(R))
      for I in R.instances:
        yield "      %s %s %s" % (I, I.public_dns_name, O_str(I))

class S3(_AWS):

  def __init__(self, aws_access_key_id=None, aws_secret_access_key=None, location=None, **kwargs):
    ''' Initialise the S3 with access id and secret.
    '''
    _AWS.__init__(self, aws_access_key_id=aws_access_key_id, aws_secret_access_key=aws_secret_access_key)
    if 'location' is None:
      self.default_location = Location.DEFAULT
    else:
      self.default_location = location
    self._buckets = {}
    D("S3 = %s", self)

  def connect(self, **kwargs):
    ''' Obtain a boto.s3.connection.S3Connection.
        Missing `aws_access_key_id`, `aws_secret_access_key`, `region`
        arguments come from the corresponding S3 attributes.
    '''
    for kw in ('aws_access_key_id', 'aws_secret_access_key'):
      if kw not in kwargs:
        kwargs[kw] = getattr(self.aws, kw[4:], None)
    return S3Connection(**kwargs)

  def bucket(self, name):
    ''' Return an S3 bucket.
	TODO: contrive that self.conn.lookup() does not block other
	stuff unnecessarily.
    '''
    with self._lock:
      if name in self._buckets:
        B = self._buckets[name]
      else:
        B = self._buckets[name] = self.conn.lookup(name)
    return B

  def create_bucket(self, name, location=None):
    ''' Create a new S3 bucket.
    '''
    if location is None:
      location = self.default_location
    B = self.conn.create_bucket(name, location)
    with self._lock:
      self._buckets[name] = B
    return B

  def report(self):
    ''' Report AWS info. Debugging/testing method.
    '''
    yield str(self)
    for name in sorted(self._buckets.keys()):
      yield "  %s => %s" % (name, self._buckets[name])

  def cmd_new(self, argv):
    badopts = False
    if not argv:
      error("missing bucket name")
      badopts = True
    else:
      bucket_name = argv.pop(0)
    if argv:
      error("extra arguments after bucket_name: %s", " ".join(argv))
      badopts = True
    if badopts:
      raise GetoptError("invalid invocation")
    B = self.create_bucket(bucket_name)
    print("new bucket \"%s\": %s" % (bucket_name, B))

if __name__ == '__main__':
  import sys
  sys.exit(main(sys.argv))

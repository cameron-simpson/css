#!/usr/bin/python
#
# Access Amazon AWS services.
# Uses boto underneath, but boto does not feel awfully pythonic.
# In any case, this exercise will give me convenient AWS access and
# an avenue to learn the boto interfaces.
#       - Cameron Simpson <cs@zip.com.au> 17nov2012
#

from contextlib import contextmanager
from threading import RLock
from boto.ec2.connection import EC2Connection
from boto.s3.connection import S3Connection, Location
from cs.logutils import D
from cs.threads import locked_property
from cs.misc import O, O_str

class _AWS(O):
  ''' Convenience wrapper for EC2 connections.
  '''

  def __init__(self, aws_access_key_id=None, aws_secret_access_key=None, region=None):
    ''' Initialise the EC2 with access id and secret.
    '''
    self.aws = O()
    self.aws.access_key_id = aws_access_key_id
    self.aws.secret_access_key = aws_secret_access_key
    self.aws.region = region
    self._lock = RLock()
    self._O_omit = ('conn', 'regions', 'instances')

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

class EC2(_AWS):

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

  def __init__(self, **kwargs):
    if 'location' in kwargs:
      self.default_location = kwargs.pop('location')
    else:
      self.default_location = Location.DEFAULT
    self._buckets = {}
    _AWS.__init__(self, **kwargs)
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

if __name__ == '__main__':
  ##with EC2(region='ap-southeast-2') as ec2:
  ##  for line in ec2.report():
  ##    print line
  ##  print
  ##  R = ec2.us_east_1
  ##  print O_str(R)

  with S3(location=Location.APSoutheast) as s3:
    for line in s3.report():
      print line

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
from cs.logutils import D
from cs.threads import locked_property
from cs.misc import O

class EC2(O):
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

  @contextmanager
  def connection(self, **kwargs):
    conn = self.connect(**kwargs)
    yield conn
    conn.close()

  def connect(self, **kwargs):
    ''' Get a boto.ec2.connection.EC2Connection.
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
  def conn(self):
    return self.connect()

  @locked_property
  def regions(self):
    ''' Return a mapping from Region name to Region.
    '''
    with self.connection(region=None) as ec2:
      RS = dict( [ (R.name, R) for R in ec2.get_all_regions() ] )
    return RS

  def region(self, name):
    ''' Return the Region with the specified `name`.
    '''
    return self.regions[name]

  @property
  def reservations(self):
    return self.conn.get_all_instances()

  def report(self):
    yield str(self)
    yield "  regions: " + str(self.regions)
    yield "  reservations: " + str(self.reservations)
    for R in self.reservations:
      region = R.region
      yield "    %s @ %s %s" % (R.id, R.region.name, dir(R))
      for I in R.instances:
        yield "      %s %s %s" % (I, I.public_dns_name, dir(I))

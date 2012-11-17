#!/usr/bin/python
#
# Access Amazon AWS services.
# Uses boto underneath, but boto is not awfully pythonic.
# In any case, this exercise will give me convenient AWS access and
# an avenue to learn the boto interfaces.
#       - Cameron Simpson <cs@zip.com.au> 17nov2012
#

from boto.ec2.connection import EC2Connection
from cs.threads import locked_property
from cs.misc import O

class EC2(O):
  ''' Convenience wrapper for EC2 connections.
  '''

  def __init__(self, access_key_id, access_key_secret):
    ''' Initialise the EC2 with access id and secret.
    '''
    self.access_key_id = access_key_id
    self.access_key_secret = access_key_secret

  def _EC2Connection(self):
    return EC2Connection(self.access_key_id, self.access_key_secret)

  @locked_property
  def conn(self):
    return self._EC2Connection()


#!/usr/bin/python
#
# Based on code found at:
#
#   Python and Apple AddressBook
#   http://www.programmish.com/?p=26
#
# The Objective C stuff from MacOSX is in:
#   /System/Library/Frameworks/Python.framework/Versions/2.6/Extras/lib/python/PyObjC
# so I may need to add that to sys.path in for other python installs.
#

import pprint
import sys
import time
from thread import allocate_lock
from AddressBook import ABAddressBook
from .objc import convertObjCtype

def main(argv):
  from cs.app.maildb import MailDB
  import os.path
  AB = AddressBookWrapper()
  MDB = MailDB(os.path.abspath('maildb.csv'), readonly=False)
  ##print "dir(AB.address_book) =",
  ##pprint.pprint(dir(AB.address_book))
  for P in AB.people:
    pprint.pprint(P)
    updateNodeDB(MDB, [P])
    break
  for G in AB.people:
    pprint.pprint(G)
    break
  ##pprint.pprint(dir(AB.address_book.people()[0]))

class AddressBookWrapper(object):
  ''' Wrapper class for Mac OSX AddressBook with more pythonic facilities.
  '''

  def __init__(self, address_book=None):
    if address_book is None:
      address_book = ABAddressBook.sharedAddressBook()
    self.address_book = address_book
    self._people = None
    self._groups = None
    self._lock = allocate_lock()

  @property
  def people(self):
    ''' Return the cached list of decoded people.
    '''
    if self._people is None:
      with self._lock:
        if self._people is None:
          self._people = list(self.iterpeople())
    return self._people

  def iterpeople(self):
    ''' Return an iterator that yields people from the addressbook
        converted to pure python types.
    '''
    for abPerson in self.address_book.people():
      yield dict( [ (k, convertObjCtype(abPerson.valueForProperty_(k)))
                    for k in abPerson.allProperties()
                    if k not in ('com.apple.ABPersonMeProperty','com.apple.ABImageData',)
                  ] )

  @property
  def groups(self):
    ''' Return the cached list of decoded groups.
    '''
    if self._groups is None:
      with self._lock:
        if self._groups is None:
          self._groups = list(self.itergroups())
    return self._groups

  def itergroups(self):
    ''' Return an iterator that yields people from the addressbook
        converted to pure python types.
    '''
    for abGroup in ABAddressBook.sharedAddressBook().groups():
      yield dict( [ (k, convertObjCtype(abGroup.valueForProperty_(k)))
                    for k in abGroup.allProperties()
                    if k not in ()
                  ] )

def epoch(abperson, field='Modification', default=None):
  ''' Return the Creation timestamp of the address book person
      as seconds since the epoch, or `default` if there is no Creation
      field.
  '''
  dt = abperson.get(field)
  if dt is None:
    return None
  return time.mktime(dt.timetuple())

def ctime(abperson, default=None):
  ''' Return the Creation timestamp of the address book person
      as seconds since the epoch, or `default` if there is no Creation
      field.
  '''
  return epoch(abperson, 'Creation', default)

def mtime(abperson, default=None):
  ''' Return the Modification timestamp of the address book person
      as seconds since the epoch, or `default` if there is no Modification
      field.
  '''
  return epoch(abperson, 'Modification', default)

def contactByOSXUID(maildb, uid):
  for contact in maildb.CONTACTs:
    if contact.OSX_AB_UID == uid:
      return contact
  return None

def updateNodeDB(maildb, people):
  ''' Update the specified `maildb` with the addressbook `people`.
  '''
  for person in people:
    uid = person['UID']
    C = contactByOSXUID(maildb, uid)
    if not C:
      C = maildb.newNode('CONTACT', maildb.seq())
      C.OSX_AB_UID = uid
    lastUpdate = C.get('OSX_AB_LAST_UPDATE', 0)
    abMTime = mtime(person, 0)
    if abMTime > lastUpdate:
      print "UPDATE USER %s" % (C,)
      ## C.OSX_AB_LAST_UPDATE = abMTime

if __name__ == '__main__':
  sys.exit(main(sys.argv))

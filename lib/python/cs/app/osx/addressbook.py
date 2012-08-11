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
# - Cameron Simpson <cs@zip.com.au>
#

import os.path
import sys
import pprint
# need to import .objc first to tweak sys.path if necessary
from .objc import convertObjCtype
import time
from threading import Lock
from AddressBook import ABAddressBook
from cs.logutils import setup_logging, Pfx, warning, info, D, debug
from cs.threads import locked_property
from cs.app.maildb import MailDB

AB_FLAGS_ORGANIZATION = 0x01

def main(argv):
  cmd = os.path.basename(argv[0])
  setup_logging(cmd)
  AB = AddressBookWrapper()
  MDB = MailDB(os.path.abspath('maildb.csv'), readonly=False)
  ##print "dir(AB.address_book) =",
  ##pprint.pprint(dir(AB.address_book))
  for P in AB.people:
    pprint.pprint(P)
    updateNodeDB(MDB, [P])
  for G in AB.groups:
    pprint.pprint(G)
    break
  ##pprint.pprint(dir(AB.address_book.people()[0]))
  MDB.close()

class AddressBookWrapper(object):
  ''' Wrapper class for Mac OSX AddressBook with more pythonic facilities.
  '''

  def __init__(self, address_book=None):
    if address_book is None:
      address_book = ABAddressBook.sharedAddressBook()
    self.address_book = address_book
    self._people = None
    self._groups = None
    self._lock = Lock()

  @locked_property
  def people(self):
    ''' Return the cached list of decoded people.
    '''
    return list(self.iterpeople())

  def iterpeople(self):
    ''' Return an iterator that yields people from the addressbook
        converted to pure python types.
    '''
    for abPerson in self.address_book.people():
      yield dict( [ (k, convertObjCtype(abPerson.valueForProperty_(k)))
                    for k in abPerson.allProperties()
                    if k not in ('com.apple.ABPersonMeProperty',
                                 'com.apple.ABImageData',
                                )
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
    if uid in contact.OSX_AB_UIDs:
      return contact
  return None

def updateNodeDB(maildb, people):
  ''' Update the specified `maildb` with the addressbook `people`.
  '''
  for person in people:
    cite = '%s %s/%s' % ( person.get('First',''), person.get('Last',''), person.get('Organization','') )
    with Pfx('updateNodeDB: %s', cite):
      uid = person['UID']
      C = contactByOSXUID(maildb, uid)
      if not C:
        C = maildb.seqNode('CONTACT')
        C.OSX_AB_UID = str(uid)
      lastUpdate = C.get0('OSX_AB_LAST_UPDATE', 0)
      abMTime = mtime(person, 0)
      D("USER %s: abMTime=%s, lastUpdate=%s", cite, abMTime, lastUpdate)
      if abMTime > lastUpdate:
        print("UPDATE USER %s" % (cite,))
        ## C.OSX_AB_LAST_UPDATE = abMTime
        ok = True
        for k, v in person.items():
          if k in ('UID', 'Creation', 'Modification'):
            pass
          elif k == 'ABPersonFlags':
            if v & AB_FLAGS_ORGANIZATION:
              C.FLAGs.add('ORGANIZATION')
              v &= ~AB_FLAGS_ORGANIZATION
            if v != 0:
              warning("unhandled ABPersonFlags: 0x%x", v)
              ok = False
          elif k == 'First':
            C.FIRST_NAME = v
          elif k == 'Last':
            C.LAST_NAME = v
          elif k == 'Organization':
            C.ORGANIZATION = v
          # TODO: split job title into one per org
          elif k == 'JobTitle':
            C.JOB_TITLE = v
          elif k == 'Email':
            C.EMAIL_ADDRESSes.update(v)
          elif k == 'Phone':
            C.TELEPHONEs.update(v)
          elif k == 'Address':
            for addr in v:
              if addr not in C.ADDRESSes:
                N = maildb.seqNode('ADDRESS')
                N.COUNTRY = str(addr.get('Country', ''))
                N.STATE = str(addr.get('State', ''))
                N.STREETs = [ line for line in map(lambda it : it.strip(), addr.get('Street', '').split('\n')) if len(line) ]
                N.POSTAL_CODE = str(addr.get('ZIP', ''))
                C.ADDRESSes.append(N)
          elif k == 'Note':
            v = v.strip()
            if v:
              C.NOTEs.add(v)
          elif k == 'URLs':
            C.URLs.update( url for url in map(lambda U: U.strip(), v) )
          elif k in ('com.apple.carddavvcf',):
            debug("ignore %s", k)
          else:
            warning("unhandled AB key: %s", k)
            pprint.pprint(person)
            ok = False
        if ok:
          info("SKIP OSX_AB_LAST_UPDATE uptdate")
          ##C.OSX_AB_LAST_UPDATE = abMTime
      else:
        info("SKIP USER %s", cite)

if __name__ == '__main__':
  sys.exit(main(sys.argv))

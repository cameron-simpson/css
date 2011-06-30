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
from AddressBook import ABAddressBook
from .objc import convertObjCtype

def main(argv):
  AB = AddressBookWrapper()
  print "dir(AB.address_book) =",
  pprint.pprint(dir(AB.address_book))
  for G in AB.groups:
    pprint.pprint(G)

class AddressBookWrapper(object):
  ''' Wrapper class for Mac OSX AddressBook with more pythonic facilities.
  '''

  def __init__(self, address_book=None):
    if address_book is None:
      address_book = ABAddressBook.sharedAddressBook()
    self.address_book = address_book

  @property
  def people(self):
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
    ''' Return an iterator that yields people from the addressbook
        converted to pure python types.
    '''
    for abGroup in ABAddressBook.sharedAddressBook().groups():
      yield dict( [ (k, convertObjCtype(abGroup.valueForProperty_(k)))
                    for k in abGroup.allProperties()
                    if k not in ()
                  ] )

if __name__ == '__main__':
  sys.exit(main(sys.argv))

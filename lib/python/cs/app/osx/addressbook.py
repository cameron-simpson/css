#!/usr/bin/python
#
# Original code from:
#
#   Python and Apple AddressBook
#   http://www.programmish.com/?p=26
#

import sys
import pprint
# need to import .objc first to tweak sys.path if necessary
from .objc import convertObjCtype
from AddressBook import ABAddressBook

def main(argv):
  for P in addressBookPeople():
    ##pprint.pprint(P)
    pass

def addressBookPeople():
  """
  Read the current user's AddressBook database, converting each person
  in the address book into a Dictionary of values. Some values (addresses,
  phone numbers, email, etc) can have multiple values, in which case a
  list of all of those values is stored. The result of this method is
  a List of Dictionaries, with each person represented by a single record
  in the list.
  """
  # get the shared addressbook and the list of
  # people from the book.
  # convert the ABPerson to a hash
  for abPerson in ABAddressBook.sharedAddressBook().people():
    yield dict( [ (k, convertObjCtype(abPerson.valueForProperty_(k)))
                  for k in abPerson.allProperties()
                  if k not in ('com.apple.ABPersonMeProperty','com.apple.ABImageData',)
                ] )

if __name__ == '__main__':
  sys.exit(main(sys.argv))

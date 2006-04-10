#!/usr/bin/python
#

import sys
import getopt
import os.path
import pwd
import re
import string
import cStringIO
import readline
import email
import email.Errors
import email.Parser
import mailbox
import cs.misc
from cs.misc import cmderr, debug, warn
import cs.gzindex

# Code from module-mailbox.html.
def _emailFactory(fp):
  try:
    return email.message_from_file(fp)
  except email.Errors.MessageParseError:
    # Don't return None since that will
    # stop the mailbox iterator
    return ''

# An interface to a Maildir mail store.
class Maildir(mailbox.Maildir):
  def __init__(self,path):
    self.__path=path

  def __iter__(self):
    for msg in mailbox.Maildir(self.__path,_emailFactory):
      if msg != '':
	yield msg

class MailItem(email.Message):
  def __init__(self,folder,subpath):
    email.Message.__init(self)
    self.__folder=folder
    self.__subpath=subpath
    self.__dirty=False

class MaildirFolder:
  def __init__(self,path):
    self.__path=path
    self.__msgindex={}

    self.__pathindex={}

    # Look up all the existing messages
    # with quick readdir()s of "new" and "cur".
    #
    for subdir in ("cur", "new"):
      subdirpath=os.path.join(path,subdir)
      debug("reading", subdirpath)
      for name in os.listdir(subdirpath):
	if len(name) == 0 or name[0] == '.':
	  continue

	subpath=os.path.join(subdir,name)
	debug(subpath)
	self.__pathindex[subpath]=None

    # Load the index file if present,
    # discarding entries not seen in the readdir() passes.
    #
    self.__indexpath=os.path.join(path,'.hdrindex.gz')
    hparse=email.Parser.HeaderParser()
    debug("loading", self.__indexpath)
    for (key,lines) in cs.gzindex.iter(self.__indexpath):
      if key not in self.__pathindex:
	continue

      debug(" ", key)
      item=MailItem(self,key)
      ndxitem=hparse.parsestr(string.join(lines,''),True)
      self.__pathindex[key]=
      self.__pathindex[key][2]=self.__papi.parse(cStringIO.StringIO(string.join(lines,'')),True)

    for entry in self.__pathindex:
      if entry[2] is None:
	entry[2]=self.__papi.parse(file(os.path.join(path,subpath)),True)

  def __syncIndex(self):
    gzout=cs.gzindex.append(self.__indexpath)
    for entry in self.__pathindex:
      if not entry[1]:
	continue

      subpath=entry[0]
      gzout.write(subpath)
      gzout.write("\n")
      for (hdr,val) in entry[2].items():
	gzout.write(hdr)
	gzout.write(":")
	gzout.write(val)
	gzout.write("\n")

      gzout.write("\n")

  def __iter__(self):
    for subpath in self.__pathindex:
      yield self.__pathindex[subpath][0]

import os
import os.path
import dircache
import string
import re
import mailbox
import cs.env
import cs.misc

numericRe=re.compile('^(0|[1-9][0-9]*)$')

def _dfltRoot():
  return cs.env.dflt('CSBUG_ROOT','$HOME/var/csbugs',1)

class Bugset:
  def __init__(self,pathname=None):
    if not pathname:
      pathname=_dfltRoot()
    self.root=pathname

  def bugnums(self):
    for dent in dircache.listdir(self.root):
      if dent != '0' and numericRe.match(dent):
	yield string.atoi(dent)

  def __getitem__(self,bugnum):
    return Bug(self,bugnum)

  def bugpath(self,bugnum):
    return os.path.join(self.root,`bugnum`)
    

class Bug:
  def __init__(self,bugset,bugnum,create=0):
    self.bugset=bugset
    self.bugnum=bugnum

    if create:
      mkdir(self.path())

  def path(self):
    return self.bugset.bugpath(self.bugnum)

  def __fieldpath(self,field):
    return os.path.join(self.path(),field)

  def __getitem__(self,field):
    if len(field) < 1 or field[0] == '.' or field.find(os.sep) >= 0:
      raise IndexError

    if field[0] in string.ascii_lowercase:
      fpath=self.__fieldpath(field)
      if not os.path.isfile(fpath):
	return None
      return cs.misc.chomp(file(fpath).read())

    raise IndexError

  def __delitem__(self,field):
    if len(field) < 1 or field[0] == '.' or field.find(os.sep) >= 0:
      raise IndexError

    if field[0] in string.ascii_lowercase:
      fpath=self.__fieldpath(field)
      if os.path.isfile(fpath):
	os.remove(fpath)
      return

    raise IndexError

  def __setitem__(self,field,value):
    if len(field) < 1 or field[0] == '.' or field.find(os.sep) >= 0:
      raise IndexError

    if field[0] in string.ascii_lowercase:
      fpath=self.__fieldpath(field)
      fp=file(fpath)
      fp.write(value)
      fp.write('\n')
      fp.close()
      return

    raise IndexError

  # __getitem__ but transmute None to ''
  def value(field,dflt=''):
    v=self[field]
    if v is None:
      v=''
    return v

class BugMail(mailbox.Maildir):
  def __init__(self,bug):
    self.__bug=bug
    mailbox.Maildir.__init__(self,self.path(),__msgfactory)

  def path(self):
    return os.path.join(self.__bug.path(),'MAIL')

  # email message factory from Python Library Reference, mailbox -- Read various mailbox formats
  def __msgfactory(fp):
    try:
      return email.message_from_file(fp)
    except email.Errors.MessageParseError:
      # Don't return None since that will
      # stop the mailbox iterator
      return ''

def test():
  bugs=Bugset()
  print "bugs.root =", bugs.root
  buglist=[bugnum for bugnum in bugs.bugnums()]
  print "bugnums =", `buglist`
  for bugnum in buglist:
    bug=bugs[bugnum]
    print bugnum, bug['hacker'], bug['headline']

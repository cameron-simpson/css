import os
import os.path
import sys
import dircache
import string
import re
import mailbox
import cs.env
import cs.sh
from cs.misc import chomp

numericRe=re.compile('^(0|[1-9][0-9]*)$')
scalarFieldNameRe=re.compile('^[a-z][a-z0-9_]*$')

def _dfltRoot():
  return cs.env.dflt('CSBUG_ROOT','/home/cameron/var/csbugs',1)

class BugSet:
  def __init__(self,pathname=None):
    if pathname is None: pathname=_dfltRoot()
    self.root=pathname
    self.bugcache={}

  def subpath(self,sub):
    return os.path.join(self.root,sub)

  def __getitem__(self,bugnum):
    if bugnum not in self.bugcache:
      self.bugcache[bugnum]=Bug(self,bugnum)
    return self.bugcache[bugnum]

  def bugpath(self,bugnum):
    return self.subpath(str(bugnum))

  def keys(self):
    return [int(e) for e in dircache.listdir(self.root) if numericRe.match(e) and int(e) > 0]

  def newbug(self):
    bugdir=cs.misc.mkdirn(self.root+'/')
    return self[int(os.path.basename(bugdir))]

  # Perform an SQL query on the bug database.
  # Returns a generator containing the results.
  # This requires the B<sqlite> package: http://freshmeat.net/projects/sqlite/
  def sql(self,query):
    sqldb=self.subpath('db.sqlite')
    dblog=self.subpath('db.log.csv')

    if not os.path.isfile(sqldb):
      # no db? create it
      os.system("set -x; sqlite '"+sqldb+"' 'create table bugfields (bugnum int, field varchar(64), value varchar(16384));'")
      # populate db from raw data
      sqlpipe=cs.sh.vpopen(("sqlite",sqldb),"w")
      for bugnum in self.keys():
	bug=self[bugnum]
	for field in bug.keys():
	  sqlpipe.write("insert into bugfields values(")
	  sqlpipe.write(bugnum)
	  sqlpipe.write(",'")
	  sqlpipe.write(field)
	  sqlpipe.write("','")
	  sqlpipe.write(bug[field])
	  sqlpipe.write("';\n")
      sqlpipe.close()
    else:
      # just update the db from the log file
      if os.path.isfile(dblog):
	sqlpipe=cs.sh.vpopen(("sqlite",sqldb),"w")
	dblogfp=file(dblog)
	os.unlink(dblog)
	for csvline in dblogfp:
	  csvf=split(csvline,",",2)
	  bugnum=csvf[0]
	  field=csvf[1]
	  value=csvf[2]
	  sqlpipe.write("delete from bugfields where bugnum = ")
	  sqlpipe.write(str(bugnum))
	  sqlpipe.write(" and field = \"")
	  sqlpipe.write(field)
	  sqlpipe.write("\";\n")
	  sqlpipe.write("insert into bugfields values (")
	  sqlpipe.write(str(bugnum))
	  sqlpipe.write(",\"")
	  sqlpipe.write(field)
	  sqlpipe.write("\",\"")
	  sqlpipe.write(value)
	  sqlpipe.write("\");\n")
	sqlpipe.close()

    sys.stderr.write("QUERY=["+query+"]\n")
    sqlpipe=cs.sh.vpopen(("sqlite","-list",sqldb,query))
    for row in sqlpipe:
      yield string.split(chomp(row),'|')

def isScalarField(fieldname):
  return len(fieldname) > 0 and fieldname[0] in string.ascii_lowercase and scalarFieldNameRe.match(fieldname)

class Bug:
  def __init__(self,bugset,bugnum,create=0):
    self.bugset=bugset
    self.bugnum=bugnum

    if create:
      mkdir(self.path())

  def path(self):
    return self.bugset.bugpath(self.bugnum)

  def keys(self):
    return [e for e in dircache.listdir(self.path) if isScalarField(e)]

  def __fieldpath(self,field):
    return os.path.join(self.path(),field)

  def __getitem__(self,field):
    if isScalarField(field):
      fpath=self.__fieldpath(field)
      if not os.path.isfile(fpath):
	return None
      return chomp(file(fpath).read())

    if field == "MAIL":
      return self.bugmail()

    raise IndexError

  def __delitem__(self,field):
    if isScalarField(field):
      fpath=self.__fieldpath(field)
      if os.path.isfile(fpath):
	os.remove(fpath)
      return

    raise IndexError

  def __setitem__(self,field,value):
    if isScalarField(field):
      fpath=self.__fieldpath(field)
      fp=file(fpath,'w')
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

  def bugmail(self):
    return BugMail(self)

class BugMail(mailbox.Maildir):
  def __init__(self,bug):
    self.__bug=bug
    mailbox.Maildir.__init__(self,self.path())

  def path(self):
    return os.path.join(self.__bug.path(),'MAIL')

def test():
  bugs=Bugset()
  print "bugs.root =", bugs.root
  buglist=[bugnum for bugnum in bugs.bugnums()]
  print "bugnums =", `buglist`
  for bugnum in buglist:
    bug=bugs[bugnum]
    print bugnum, bug['hacker'], bug['headline']

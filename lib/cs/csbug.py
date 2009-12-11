import os
import os.path
import pwd
import sys
import string
import re
import email
import email.parser
import time
import socket
import mailbox
import cs.env
import cs.sh
from cs.misc import chomp, progress, seq


numericRe=re.compile('^(0|[1-9][0-9]*)$')
scalarFieldNameRe=re.compile('^[a-z][a-z0-9_]*$')

def _dfltRoot():
  return cs.env.dflt('CSBUG_ROOT','$HOME/var/csbug',True)

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
    return [int(e) for e in os.listdir(self.root) if numericRe.match(e) and int(e) > 0]

  def newbug(self):
    bugdir=cs.misc.mkdirn(self.root+'/')
    return self[int(os.path.basename(bugdir))]

  def log(self,bugid,field,value):
    dblog=self.subpath('db.log.csv')
    logfp=file(dblog,"a")
    logfp.write(field)
    logfp.write(',')
    logfp.write(value)
    logfp.write('\n')
    logfp.close()

  # Perform an SQL query on the bug database.
  # Returns a generator containing the results.
  # This requires the B<sqlite> package: http://freshmeat.net/projects/sqlite/
  def sql(self,query):
    sqldb=self.subpath('db.sqlite')
    dblog=self.subpath('db.log.csv')

    if not os.path.isfile(sqldb):
      # no db? create it
      progress("create SQLite database...")
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
      progress("sync db from log...")
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

    progress("QUERY =", query)
    sqlpipe=cs.sh.vpopen(("sqlite","-list",sqldb,query))
    for row in sqlpipe:
      yield string.split(chomp(row),'|')

def isScalarField(fieldname):
  return len(fieldname) > 0 and fieldname[0] in string.ascii_lowercase and scalarFieldNameRe.match(fieldname)

class Bug:
  def __init__(self,bugset,bugnum,create=False):
    self.bugset=bugset
    self.bugnum=bugnum
    self.__cache={}
    self.__mail=None

    if create:
      mkdir(self.path())

  def path(self):
    return self.bugset.bugpath(self.bugnum)

  def keys(self):
    return [e for e in os.listdir(self.path) if isScalarField(e)]

  def __fieldpath(self,field):
    return os.path.join(self.path(),field)

  def __getitem__(self,field):
    if isScalarField(field):
      if field not in self.__cache:
        fpath=self.__fieldpath(field)
        if not os.path.isfile(fpath):
          return None
        self.__cache[field]=chomp(file(fpath).read())
      return self.__cache[field]

    if field == "MAIL":
      return self.bugmail()

    raise IndexError

  def __delitem__(self,field):
    if isScalarField(field):
      fpath=self.__fieldpath(field)
      if os.path.isfile(fpath):
        os.remove(fpath)
      if field in self.__cache:
        del self.__cache[field]
      return

    raise IndexError

  def __setitem__(self,field,value):
    if isScalarField(field):
      value=str(value).replace('\n','\\n')
      fpath=self.__fieldpath(field)
      fp=file(fpath,'w')
      fp.write(value)
      fp.write('\n')
      fp.close()
      self.bugset.log(self.bugnum,field,value)
      self.__cache[field]=value
      return

    raise IndexError

  # __getitem__ but transmute None to ''
  def value(field,dflt=''):
    v=self[field]
    if v is None:
      v=''
    return v

  def bugmail(self):
    if self.__mail is None:
      self.__mail=BugMail(self)
    return self.__mail

class BugMail(mailbox.Maildir):
  def __init__(self,bug):
    self.__bug=bug
    self.__fpParser=email.parser.Parser()
    mailbox.Maildir.__init__(self,self.path())

  def path(self):
    return os.path.join(self.__bug.path(),'MAIL')

  def addmsgFromFile(self,fp):
    self.addmsg(self.__fpParser.parse(fp))

  def addmsg(self,msg):
    if 'from' not in msg:
      pw=pwd.getpwuid(os.geteuid())
      gecos=pw[4]
      cpos=gecos.find(',')
      if cpos >= 0: gecos=gecos[:cpos]
      msg['From']="%s <%s>" % (gecos,pw[0])

    if 'date' not in msg:
      # RFC822 time
      # FIXME: locale likely to break this?
      msg['Date']=time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime())

    if 'message-id' not in msg:
      msg['Message-ID']='<%d.%d.%d@%s>' % (time.time(), os.getpid(), seq(), socket.gethostname())

    self.add(msg)

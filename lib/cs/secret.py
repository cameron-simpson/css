import os
import os.path
import cs.hier
from cs.misc import debug, ifdebug, progress, verbose, warn, DictUC_Attrs

def get(secret,path=None):
  return Secret(secret,path=path)

def dfltpath():
  path=os.environ.get('SECRETPATH',None)
  if path is None:
    path=( os.path.join(os.environ["HOME"], ".secret"),
           '/opt/config/secret',
         )
  else:
    path=path.split(':')
  return path

class Secret(DictUC_Attrs):
  def __init__(self,secret,path=None):
    if os.path.isabs(secret):
      S=cs.hier.load(secret)
    else:
      if path is None:
        path=dfltpath()
      S=None
      for base in path:
        pathname=os.path.join(base,secret)
        try:
          S=cs.hier.load(pathname)
        except IOError:
          continue
      if S is None:
        raise IOError
    dict.__init__(self,S)

def list(path=None):
  if path is None:
    path=dfltpath()
  path=[ p for p in path ]      # because list() taken :-(
  path.reverse()
  ss={}
  for base in path:
    try:
      names=os.listdir(base)
    except OSError:
      continue
    for name in names:
      if len(name) > 0 and name[0] != '.':
        ss[name]=base
  ks=ss.keys()
  ks.sort()
  return ks
  
def mysql(secret,db):
  import MySQLdb
  if type(secret) is str or not(hasattr(secret,'__keys__') or hasattr(secret,'keys')):
    # transmute secret name into structure
    secret=get(secret)

  host=secret['HOST']
  if 'LOGIN' not in secret:
    return MySQLdb.connect(host=host,db=db)

  user=secret['LOGIN']
  passwd=secret['PASSWORD']
  return MySQLdb.connect(host=host,db=db,user=user,passwd=passwd)

def sqlAlchemy(secret,scheme,login,password,host,database):
  import sqlalchemy
  return sqlalchemy.create_engine(
           '%s://%s:%s@%s/%s' % (scheme,
                                 secret.LOGIN,
                                 secret.PASSWORD,
                                 secret.HOST,
                                 secret.DATABASE),
           echo=ifdebug())

def mssql(secret,db=None):
  import pymssql
  if type(secret) is str or not(hasattr(secret,'__keys__') or hasattr(secret,'keys')):
    # transmute secret name into structure
    secret=get(secret)

  print "secret =", repr(secret)
  host=secret['HOST']
  port=secret['PORT']
  database=secret['DATABASE']
  user=secret['LOGIN']
  passwd=secret['PASSWORD']

  from os import environ as env
  ohost=env.get('TDSHOST'); env['TDSHOST']=host
  oport=env.get('TDSPORT'); env['TDSPORT']=str(port)
  conn=pymssql.connect(user=user,password=passwd,database=database)
  if ohost is None:
    del env['TDSHOST']
  else:
    env['TDSHOST']=ohost
  if oport is None:
    del env['TDSPORT']
  else:
    env['TDSPORT']=oport

  return conn

def ldap(secret,host=None,binddn=None,bindpw=None):
  # transmute secret name into structure
  if not(hasattr(secret,'__keys__') or hasattr(secret,'keys')):
    import cs.secret
    verbose("lookup secret:", secret)
    secret=cs.secret.get(secret)

  debug("LDAP secret =", repr(secret))
  ldap_host=secret['HOST']
  if host is not None:   ldap_host=host
  ldap_binddn=secret['BINDDN']
  if binddn is not None: ldap_binddn=binddn
  ldap_bindpw=secret['BINDPW']
  if bindpw is not None: ldap_bindpw=bindpw

  import ldap
  debug("ldap_host =", repr(ldap_host))
  debug("ldap_binddn =", repr(ldap_binddn))
  debug("ldap_bindpw =", repr(ldap_bindpw))
  L=ldap.open(ldap_host)
  L.simple_bind_s(ldap_binddn,ldap_bindpw)
  return L

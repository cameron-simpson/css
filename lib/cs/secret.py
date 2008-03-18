import os
import os.path
import cs.hier
from cs.misc import debug, ifdebug, progress, verbose, warn

def get(secret):
  if os.path.isabs(secret):
    return cs.hier.load(secret)

  for base in os.path.join(os.environ["HOME"],".secret"), \
              '/opt/config/secret':
    try:
      pathname=os.path.join(base,secret)
      return cs.hier.load(pathname)
    except IOError:
      pass

  raise IndexError, "no secret named "+secret

def list():
  import os
  ss={}
  for base in '/opt/config/secret', \
              os.path.join(os.environ["HOME"],".secret"):
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

def mssql(secret,db=None):
  import pymssql
  if type(secret) is str or not(hasattr(secret,'__keys__') or hasattr(secret,'keys')):
    # transmute secret name into structure
    secret=get(secret)

  print "secret =", `secret`
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

  debug("LDAP secret =", `secret`)
  ldap_host=secret['HOST']
  if host is not None:   ldap_host=host
  ldap_binddn=secret['BINDDN']
  if binddn is not None: ldap_binddn=binddn
  ldap_bindpw=secret['BINDPW']
  if bindpw is not None: ldap_bindpw=bindpw

  import ldap
  debug("ldap_host =", `ldap_host`)
  debug("ldap_binddn =", `ldap_binddn`)
  debug("ldap_bindpw =", `ldap_bindpw`)
  L=ldap.open(ldap_host)
  L.simple_bind_s(ldap_binddn,ldap_bindpw)
  return L

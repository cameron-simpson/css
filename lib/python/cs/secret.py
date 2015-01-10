import os
import os.path
import cs.hier
import logging
import cs.logutils
from cs.mixin.ucattrs import UCdict

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

class Secret(UCdict):
  def __init__(self,secret,path=None):
    ''' Initialise a Secret object from the named secret.
        Raises IOError if no such file can be accessed.
    '''
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
        raise IOError("can't locate secret file for \"%s\"" % (secret,))
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
           echo=(lcs.logutils.logging_level <= logging.DEBUG))

def mssql(secret, db=None):
  import pymssql
  if type(secret) is str or not(hasattr(secret,'__keys__') or hasattr(secret,'keys')):
    # transmute secret name into structure
    secret=get(secret)

  host=secret['HOST']
  port=secret['PORT']
  database=secret['DATABASE']
  user=secret['LOGIN']
  passwd=secret['PASSWORD']

  from os import environ as env
  ohost=env.get('TDSHOST'); env['TDSHOST']=host
  oport=env.get('TDSPORT'); env['TDSPORT']=str(port)
  conn=pymssql.connect(user=user, password=passwd, database=database)
  if ohost is None:
    del env['TDSHOST']
  else:
    env['TDSHOST']=ohost
  if oport is None:
    del env['TDSPORT']
  else:
    env['TDSPORT']=oport

  return conn

def ldap(secret, host=None, binddn=None, bindpw=None):
  # transmute secret name into structure
  if not(hasattr(secret,'__keys__') or hasattr(secret,'keys')):
    import cs.secret
    secret=cs.secret.get(secret)

  ldap_host=secret['HOST']
  if host is not None:   ldap_host=host
  ldap_binddn=secret['BINDDN']
  if binddn is not None: ldap_binddn=binddn
  ldap_bindpw=secret['BINDPW']
  if bindpw is not None: ldap_bindpw=bindpw

  import ldap
  L=ldap.open(ldap_host)
  L.simple_bind_s(ldap_binddn,ldap_bindpw)
  return L

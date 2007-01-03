import os
import os.path
import cs.hier
from cs.misc import debug, ifdebug, progress, verbose, warn

def get(secret):
  for base in (os.path.join(os.environ["HOME"],".secret"), '/opt/config/secret'):
    ##try:
    pathname=os.path.join(base,secret)
    return cs.hier.load(pathname)
    ##except Exception, e:
      ##pass

  return None

def mysql(secret,db=None):
  import types
  if type(secret) is types.StringType or not(hasattr(secret,'__keys__') or hasattr(secret,'keys')):
    # transmute secret name into structure
    ##debug("secret: get", `secret`)
    secret=get(secret)

  import cs.dbi.mysql
  debug("secret =", `secret`)
  host=secret['HOST']
  if 'LOGIN' not in secret:
    return cs.dbi.mysql.Conn(host=host, db=db)

  user=secret['LOGIN']
  passwd=secret['PASSWORD']
  return cs.dbi.mysql.Conn(host=host, db=db, user=user, passwd=passwd)

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

import os
import os.path
import cs.hier
from cs.misc import debug, progress, verbose, warn

def get(secret,base=None):
  if base is None:
    base=os.path.join(os.environ["HOME"],".secret")
  return cs.hier.load(os.path.join(base,secret))

def mysql(secret,db=None):
  import types
  if type(secret) is types.StringType or not(hasattr(secret,'__keys__') or hasattr(secret,'keys')):
    # transmute secret name into structure
    secret=get(secret)

  import cs.dbi.mysql
  return cs.dbi.mysql.Conn(host=secret['HOST'],
			   db=db,
			   user=secret['LOGIN'],
			   passwd=secret['PASSWORD'])

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

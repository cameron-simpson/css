import os
import os.path
import cs.hier
from cs.misc import debug, progress, warn

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

def ldap(secret):
  # transmute secret name into structure
  if not(hasattr(secret,'__keys__') or hasattr(secret,'keys')):
    import cs.secret
    progress("lookup secret:", secret)
    secret=cs.secret.get(secret)

  warn("LDAP secret =", `secret`)
  import ldap
  L=ldap.open(secret['HOST'])
  L.simple_bind_s(secret['BINDDN'],secret['BINDPW'])
  return L

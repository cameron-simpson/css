import os
import os.path
import cs.hier

def get(secret,base=os.path.join(os.environ["HOME"],".secret")):
  return cs.hier.load(os.path.join(base,secret))

def mysql(secret,db=None):
  if not(hasattr(secret,'__keys__') or hasattr(o,'keys')):
    # transmute secret name into structure
    secret=get(secret)

  import cs.dbi.mysql
  return cs.dbi.mysql.Conn(host=secret['HOST'],
			   db=db,
			   user=secret['LOGIN'],
			   password=secret['PASSWORD'])

def ldap(secret,basedn,user=None,password=None):
  if not(hasattr(secret,'__keys__') or hasattr(o,'keys')):
    # transmute secret name into structure
    secret=get(secret)

  import ldap
  return ldap.open(secret['HOST'])

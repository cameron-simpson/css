from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
import cs.hier
import cs.json
import cs.www
from cs.misc import cmderr, debug
import base64
import re

class JSONRPC(HTTPServer):
  def __init__(self,bindaddr,base=None):
    ''' Start a server bounce to the supplied bindaddr, a tuple of (ip-addr, port).
        base is the DocumentRoot for requests and defaults to "/".
    '''
    if base is None: base='/'
    self.rpcBaseURL=base

    HTTPServer.__init__(self,bindaddr,JSONRPCHandler)

basic_authorization_re=re.compile(r'^\s*basic\s+(\S+)',re.I)

def testAuthPWNam(user, password):
  import posix
  try:
    pw=posix.getpwnam(user)
  except KeyError:
    return False

  from crypt import crypt
  pwcrypt = pw[1]
  salt = pwcrypt[:2]
  cmderr("testauth: user=%s, crypt=%s, salt=%s, passwd=%s" % (user, pwcrypt, salt, password))
  return crypt.crypt(password, salt) == pwcrypt

class RequestHandler(BaseHTTPRequestHandler):
  def __init__(self,rq,cliaddr,srv):
    BaseHTTPRequestHandler.__init__(self,rq,cliaddr,srv)

  def getAuth(self, testAuth=None):
    if testAuth is None:
      testAUth=testAuthPWNam

    authhdr=self.get('AUTHORIZATION')
    if authhdr is None:
      return None

    global basic_authorization_re
    m=basic_authorization_re.match(authhdr)
    if not m:
      return None

    try:
      cred=base64.b64decode(m.group(1))
    except TypeError:
      return None
    userid, password = cred.split(":",1)

    if not testAuth(userid, password):
      return None

    return userid

# || { echo "Status: 401"
#      echo "WWW-Authenticate: Basic realm=\"$realm\""
#      echo "Content-Type: text/plain"
#      echo
#      echo "Authorisation needed for realm \"$realm\"."

class JSONRPCHandler(RequestHandler):
  def __init__(self,rq,cliaddr,srv):
    RequestHandler.__init__(self,rq,cliaddr,srv)

  def reject(self,code,complaint):
    self.wfile.write("%03d %s\r\nContent-Type: text/plain\r\n\r\n%03d %s\r\n"
                     % (code,complaint,code,complaint))

  def do_GET(self):
    cmderr("path =", `self.path`)
    path=self.path
    root=self.server.rpcBaseURL
    if not path.startswith(root):
      self.reject(500, "path not inside root prefix: %s" % root)
      return
    path=path[len(root):]

    slndx=path.find('/')
    if slndx < 1 or not path[:slndx].isdigit():
      self.reject(500,"missing sequence number")
      return

    seq=int(path[:slndx])
    self.headers['Content-Type']="application/x-javascript\r\n"
    jsontxt=path[slndx+1:]
    cmderr("jsontxt0 =", `jsontxt`)
    jsontxt=cs.www.unhexify(jsontxt)
    cmderr("jsontxt1 =", `jsontxt`)
    (args,unparsed)=cs.hier.tok(jsontxt)
    cmderr("args =", `args`, "unparsed =", `unparsed`)
    (cb,result)=self.server.rpc(args)
    jscode="%s(%d,%s);\r\n" % (cb,seq,cs.json.json(result));
    debug("JSCODE:\n"+jscode);
    self.wfile.write(jscode);

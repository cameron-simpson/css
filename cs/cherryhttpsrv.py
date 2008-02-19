#!/usr/bin/python -tt

import cherrypy
import cs.www
import cs.json
from cs.misc import ifdebug, cmderr

def testAuth(rq=None):
  ''' Examine the cherrypy.request object supplied for HTTP Basic authentication,
      match against UNIX getpwnam() lookup, return login if the password is correct.
      Return None otherwise.
  '''
  if rq is None:
    rq=cherrypy.request
  if 'authorization' in rq.headers:
    authwords = rq.headers['authorization'].split()
    if len(authwords) == 2 and authwords[0].lower() == 'basic':
      from base64 import b64decode
      dec = b64decode(authwords[1]).split(':',1)
      if len(dec) == 2:
        login, password = dec
        from pwd import getpwnam
        try:
          pw=getpwnam(login)
        except:
          return None
        if pw:
          from crypt import crypt
          if pw[1] == crypt(password, pw[1]):
            return login

  return None

def setNeedAuth(realm,rsp=None):
  ''' Set the supplied cherrypy.response object to require authentication.
  '''
  if rsp is None:
    rsp=cherrypy.response
  rsp.status=401
  rsp.headers.update({ "WWW-Authenticate": "Basic realm=\"%s\"" % realm})

class RPC:

  def _RPCargs(self,jsontxt):
    jsontxt=cs.www.unhexify(jsontxt)
    (args,unparsed)=cs.json.tok(jsontxt)
    if ifdebug(): cmderr("args =", `args`, "unparsed =", `unparsed`)
    return args

  def _RPCreturn(self,cb,seq,result):
    return "%s(%d,%s);\r\n" % (cb,seq,cs.json.json(result,4))

  @cherrypy.expose
  def default(self, *words):
    if len(words) < 4:
      return "Incomplete RPC URL tail: %s" % (words,)
    elif len(words) > 4:
      return "Overfull RPC URL tail: %s" % (words,)

    fn, seq, cb, args = "_rpc_"+words[0], int(words[1]), words[2], self._RPCargs(words[3])
    result = getattr(self,fn)(args)

    jscode=self._RPCreturn(cb,seq,result)
    cherrypy.response.headers.update({ 'Content-Type': "application/x-javascript"})
    return jscode+"\r\n                                                                                      "

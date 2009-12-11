#!/usr/bin/python -tt

import cherrypy
import urllib
import sys
if sys.hexversion < 0x02060000:
  import simplejson as json
else:
  import json

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
    return json.loads(urllib.unquote(jsontxt))

  def _RPCreturn(self,callback,seq,result):
    return "%s(%d,%s);\r\n" % (callback,seq,json.dumps(result))

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

def _tableRows(htmlCells):
  return "\n".join( "\n  ".join( ["<TR>",] + ["<TD>"+cell for cell in R] )
                    for R in htmlCells
                  )

class DBBrowse:
  def __init__(self,conn):
    self.__conn=conn

  @cherrypy.expose
  def default(self, *words):
    words=list(words)
    if len(words) == 0:
      dbs=[ R[0] for R in self.__conn.execute('show databases') ]
      dbs.sort()
      return "<TABLE>\n" \
           + _tableRows( ( "<a href=\"%s/\">%s/</a>" % (dbname, dbname), "[database]" )
                         for dbname in dbs
                       ) \
           + "</TABLE>"

    dbname=words.pop(0)
    self.__conn.execute('use %s' % dbname)
    if len(words) == 0:
      tbs=[ R[0] for R in self.__conn.execute('show tables') ]
      tbs.sort()
      return '<H1>Database %s</H1>\n' % dbname \
           + "<TABLE>\n" \
           + _tableRows( ( "<a href=\"%s/\">%s.%s/</a>" % (tbname,dbname,tbname), "[table]")
                         for tbname in tbs
                       ) \
           + "</TABLE>"

    tbname=words.pop(0)
    return '<H1>Table %s.%s</H1>' % (dbname,tbname) \
         + "<TABLE>\n" \
         + _tableRows( [ ("Column", "Type", "Default" ), ]
                     + [ ( R[0], R[1], "NULL" if R[4] is None else str(R[4]) ) for R in self.__conn.execute('describe %s' % tbname) ]
                     ) \
         + "</TABLE>"

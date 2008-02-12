#!/usr/bin/python -tt

import cherrypy
import cs.www
import cs.json
from cs.misc import cmderr

class RPC:

  def _RPCargs(self,jsontxt):
    jsontxt=cs.www.unhexify(jsontxt)
    (args,unparsed)=cs.json.tok(jsontxt)
    cmderr("args =", `args`, "unparsed =", `unparsed`)
    return args

  def _RPCreturn(self,cb,seq,result):
    return "%s(%d,%s);\r\n" % (cb,seq,cs.json.json(result,4))

  @cherrypy.expose
  def default(self, *words):
    cmderr("RPC: default(words=%s)" % (words,))
    if len(words) < 4:
      return "Incomplete RPC URL tail: %s" % (words,)
    elif len(words) > 4:
      return "Overfull RPC URL tail: %s" % (words,)

    fn, seq, cb, args = "_rpc_"+words[0], int(words[1]), words[2], self._RPCargs(words[3])
    result = getattr(self,fn)(args)

    jscode=self._RPCreturn(cb,seq,result)
    cmderr("JSCODE=[%s]" % jscode)

    cherrypy.response.headerMap['Content-Type']="application/x-javascript\r\n"
    return jscode+"\r\n                                                                                      "

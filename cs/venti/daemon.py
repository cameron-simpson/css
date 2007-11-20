#!/usr/bin/python -tt
#
# Facilities for stores with a daemon mode.
#       - Cameron Simpson <cs@zip.com.au> 18nov2007
# 

class DaemonicStore:
  ''' A class supplying a .daemon() method.
      It expects its subclasses supply .daemon_op(op,backCh,*args)
      to handle requests, accepting ops:
        OP_SYNC         Sync the store.
                        Result is None.
        OP_STORE_BLOCK  args[0] is a data block to store.
                        Result is the block hash.
        OP_CONTAINS_HASH Test if the specified hash is present in the store.
                        Result is the corresponding Boolean.
      The result will be returned by backCh.write(result).
  '''
  OP_SYNC=0
  OP_STORE_BLOCK=1
  OP_CONTAINS_HASH=2
  def daemon(self):
    ''' Return a threading.Thread subclass to manage the store
        for asynchronous use by multiple callers via its .channel
        attribute, a cs.threads.Channel.
    '''
    import cs.threads
    import threading
    self.channel=None
    class D(threading.Thread):
      def __init__(self,S):
        threading.Thread.__init__(self)
        self.__store=S
        self.channel=cs.threads.Channel()
      def run(self):
        global opMap
        for rq, backCh in self.channel:
          cmderr(
          op=rq[0]
          args=rq[1:]
          self.daemon_op(op, backCh, *args)
    return D(self)

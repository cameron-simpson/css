import os
import cs.dbi
import cs.secret
import MySQLdb

def conn(db,systemid=os.environ['SYSTEMID']):
  secret=cs.secret.get(db+'-mysql@'+systemid)
  return Conn(host=secret['HOST'],db=db,
	      user=secret['LOGIN'],passwd=secret['PASSWORD'])

""" a connection to a MySQL server
"""
class Conn:
  def __init__(self,host="localhost",db=None,user="",passwd=""):
    print "connect to "+host+"/"+db
    self.__conn=MySQLdb.connect(host=host,db=db,user=user,passwd=passwd)
    ##print "paramstyle=", MySQLdb.paramstyle

  def cursor(self):
    return self.__conn.cursor()

  def view(self,*args):
    return cs.dbi.View(self.__conn,*args)

  def table(self,table,idfields=["ID"]):
    return cs.dbi.UpdateableTableView(self,table,idfields)

  def close():
    self.conn.close()

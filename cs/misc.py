def index(seq,val):
  for i in xrange(len(seq)-1):
    if val == seq[i]: return i
  return -1

import os
import os.path
import cs.hier

def get(secret,base=os.path.join(os.environ["HOME"],".secret")):
  return cs.hier.load(os.path.join(base,secret))

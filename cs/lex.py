import string

def skipwhite(s,start=0):
  while start < len(s) and s[start] in string.whitespace:
    start+=1
  return start


import string

""" location of next nonwhite in string """
def skipwhite(s,start=0):
  while start < len(s) and s[start] in string.whitespace:
    start+=1
  return start

""" length of text after last newline in string """
def lastlinelen(s):
  return len(s)-string.rfind(s,'\n')-1

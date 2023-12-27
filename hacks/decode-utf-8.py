#!/usr/bin/python3
import sys
import unicodedata
s=sys.stdin.read()
for c in s:
  try:
    uname = unicodedata.name(c)
  except ValueError as e:
    uname = "UNNAMED: %s" % (e,)
  print("0x%04x %s" % (ord(c), uname))

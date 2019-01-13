#!/usr/bin/python
#
# The generic Store factory and parser for Store specifications.
#   - Cameron Simpson <cs@cskk.id.au> 20dec2016
#

''' Composition of Stores from text specifications.
'''

from os.path import isdir
from subprocess import Popen, PIPE
from cs.lex import skipwhite, get_identifier, get_qstr
from cs.lex import get_identifier, get_qstr, get_qstr_or_identifier
from cs.pfx import Pfx
from .convert import get_integer
from .stream import StreamStore

def parse_store_specs(s, offset=0):
  ''' Parse the string `s` for a list of Store specifications.
  '''
  with Pfx("parse_store_specs(%r)", s):
    store_specs = []
    while offset < len(s):
      with Pfx("offset %d", offset):
        store_text, store_type, params, offset = get_store_spec(s, offset)
        store_specs.append( (store_text, store_type, params) )
      if offset < len(s):
        with Pfx("offset %d", offset):
          sep = s[offset]
          offset += 1
          if sep == ',':
            continue
          raise ValueError(
              "expected comma ',', found unexpected separator: %r"
              % (sep,))
    return store_specs

def get_store_spec(s, offset=0):
  ''' Get a single Store specification from a string.
      Return `(matched, type, params, offset)`
      being the matched text, store type, parameters and the new offset.

      Recognised specifications:
      * `"text"`: Quoted store spec, needed to enclose some of the following
        syntaxes if they do not consume the whole string.
      * `[clause_name]`: The name of a clause to be obtained from a Config.
      * `/path/to/something`, `./path/to/something`:
        A filesystem path to a local resource.
        Supported paths:
        - `.../foo.sock`: A UNIX socket based StreamStore.
        - `.../dir`: A DataDirStore directory.
        - `.../foo.vtd `: (STILL TODO): A DataFileStore.
      * `|command`: A subprocess implementing the streaming protocol.
      * `store_type(param=value,...)`:
        A general Store specification.
      * `store_type:params...`:
        An inline Store specification.
        Supported inline types: `tcp:[host]:port`

      TODO:
      * `ssh://host/[store-designator-as-above]`:
      * `unix:/path/to/socket`:
        Connect to a daemon implementing the streaming protocol.
      * `http[s]://host/prefix`:
        A Store presenting content under prefix:
        + `/h/hashcode.hashtype`: Block data by hashcode
        + `/i/hashcode.hashtype`: Indirect block by hashcode.
      * `s3://bucketname/prefix/hashcode.hashtype`:
        An AWS S3 bucket with raw blocks.
  '''
  offset0 = offset
  if offset >= len(s):
    raise ValueError("empty string")
  if s.startswith('"', offset):
    # "store_spec"
    qs, offset = get_qstr(s, offset, q='"')
    _, store_type, params, offset2 = get_store_spec(qs, 0)
    if offset2 < len(qs):
      raise ValueError("unparsed text inside quotes: %r" % (qs[offset2:],))
  elif s.startswith('[', offset):
    # [clause_name]
    store_type = 'config'
    offset = skipwhite(s, offset + 1)
    clause_name, offset = get_qstr_or_identifier(s, offset)
    offset = skipwhite(s, offset)
    if offset >= len(s) or s[offset] != ']':
      raise ValueError("offset %d: missing closing ']'" % (offset,))
    offset += 1
    params = {'clause_name': clause_name}
  elif s.startswith('/', offset) or s.startswith('./', offset):
    path = s[offset:]
    offset = len(s)
    if path.endswith('.sock'):
      store_type = 'socket'
      params = {'socket_path': path}
    elif isdir(path):
      store_type = 'datadir'
      params = {'path': path}
    else:
      raise ValueError(
          "%r: not a directory or a socket"
          % (path,))
  elif s.startswith('|', offset):
    # |shell command
    store_type = 'shell'
    params = {'shcmd': s[offset + 1:].strip()}
    offset = len(s)
  else:
    store_type, offset = get_identifier(s, offset)
    if not store_type:
      raise ValueError(
          "expected identifier at offset %d, found: %r"
          % (offset, s[offset:]))
    with Pfx(store_type):
      if s.startswith('(', offset):
        params, offset = get_params(s, offset + 1, ')')
      elif s.startswith(':', offset):
        offset += 1
        params = {}
        if store_type == 'tcp':
          colon2 = s.find(':', offset)
          if colon2 < offset:
            raise ValueError("missing second colon after offset %d" % (offset,))
          hostpart = s[offset:colon2]
          offset = colon2 + 1
          if not isinstance(hostpart, str):
            raise ValueError(
                "expected hostpart to be a string, got: %r" % (hostpart,))
          if not hostpart:
            hostpart = 'localhost'
          params['host'] = hostpart
          portpart, offset = get_token(s, offset)
          params['port'] = portpart
        else:
          raise ValueError("unrecognised Store type for inline form")
      else:
        raise ValueError("no parameters")
  return s[offset0:offset], store_type, params, offset

def get_params(s, offset, endchar):
  ''' Parse "param=value,...)". Return params dict and new offset.
  '''
  params = {}
  first = True
  while True:
    ch = s[offset:offset + 1]
    if not ch:
      raise ValueError("hit end of string, expected param or %r" % (endchar,))
    if ch == endchar:
      offset += 1
      return params, offset
    if not first and ch == ',':
      offset += 1
    param, offset = get_qstr_or_identifier(s, offset)
    if not param:
      raise ValueError("rejecting empty parameter name at: %r" % (s[offset:],))
    offset = skipwhite(s, offset)
    ch = s[offset:offset + 1]
    if not ch:
      raise ValueError("hit end of string after param %r, expected '='" % (param,))
    if ch != '=':
      raise ValueError("expected '=', found %r" % (ch,))
    offset = skipwhite(s, offset + 1)
    ch = s[offset:offset + 1]
    if not ch:
      raise ValueError("hit end of string after param %r=, expected token" % (param,))
    if ch == endchar or ch == ',':
      raise ValueError("expected token, found %r" % (ch,))
    value, offset = get_token(s, offset)
    params[param] = value
    first = False

def get_token(s, offset):
  ''' Parse an integer value, an identifier or a quoted string.
  '''
  if offset == len(s):
    raise ValueError("unexpected end of string, expected token")
  if s[offset].isdigit():
    token, offset = get_integer(s, offset)
  else:
    token, offset = get_qstr_or_identifier(s, offset)
  return token, offset

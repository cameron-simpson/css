#!/usr/bin/env python3

''' Graphviz utility functions, initially just gvprint(dot_s,...).
'''

from os.path import exists as existspath
from subprocess import Popen, PIPE
import sys

from cs.x import X
from cs.lex import r
import shlex

def gvprint(dot_s, output=None, fmt=None, layout=None, **dot_kw):
  ''' Print the graph specified by `dot_s`, a graph in graphViz DOT syntax,
      to `output` (default `sys.stdout`)
      in format `fmt` using the engine specified by `layout` (default `'dot'`).
    '''
  if isinstance(output, str):
    if existspath(output):
      raise ValueError("output %r: already exists" % (output,))
    with open(output, 'wb') as f:
      return print(dot_s, output=f, fmt=fmt, layout=layout, **dot_kw)
  if output is None:
    output = sys.stdout
  if layout is None:
    layout = 'dot'
  if fmt is None:
    if output.isatty():
      fmt = 'sixel'
    else:
      fmt = 'png'
  graph_modes = dict(layout=layout, splines='true')
  node_modes = {}
  edge_modes = {}
  dot_fmt = 'png' if fmt == 'sixel' else fmt
  dot_argv = ['dot', f'-T{dot_fmt}']
  for gmode, gvalue in sorted(graph_modes.items()):
    dot_argv.append(f'-G{gmode}={gvalue}')
  for nmode, nvalue in sorted(node_modes.items()):
    dot_argv.append(f'-N{nmode}={nvalue}')
  for emode, evalue in sorted(edge_modes.items()):
    dot_argv.append(f'-E{emode}={evalue}')
  # make sure any preceeding output gets out first
  output.flush()
  # subprocesses to wait for in order
  subprocs = []
  if fmt == 'sixel':
    # pipeline to pipe "dot" through "img2sixel"
    img2sixel_popen = Popen(['img2sixel'], stdin=PIPE, stdout=output)
    dot_output = img2sixel_popen.stdin
    subprocs.append(img2sixel_popen)
  else:
    img2sixel_popen = None
    dot_output = output
  X("RUN %s", shlex.join(dot_argv))
  X("DOT_OUTPUT = %s", dot_output)
  dot_popen = Popen(dot_argv, stdin=PIPE, stdout=dot_output)
  subprocs.insert(0, dot_popen)
  if img2sixel_popen is not None:
    # release out handle to img2sixel
    img2sixel_popen.stdin.close()
  X("dot_s = %s", r(dot_s))
  dot_bs = dot_s.encode('ascii')
  X("dot_bs = %s", r(dot_bs))
  X("dot_popen.stdin = %s", r(dot_popen.stdin))
  dot_popen.stdin.write(dot_bs)
  dot_popen.stdin.close()
  ##print(dot_bs, file=dot_popen.stdin)
  for subp in subprocs:
    X("WAIT FOR %s", subp)
    subp.wait()
  return None

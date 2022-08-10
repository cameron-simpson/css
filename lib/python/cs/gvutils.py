#!/usr/bin/env python3

''' Graphviz utility functions.

    See also the [https://www.graphviz.org/documentation/](graphviz documentation)
    and particularly the [https://graphviz.org/doc/info/lang.html](DOT language specification)
    and the [https://www.graphviz.org/doc/info/command.html](`dot` command line tool).
'''

from os.path import exists as existspath
from subprocess import Popen, PIPE
import sys
from typing import Mapping

from cs.lex import is_identifier

__version__ = '20220805.1-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.lex',
    ],
}

def quote(s):
  ''' Quote a string for use in DOT syntax.
      This implementation passes identifiers and sequences of decimal numerals
      through unchanged and double quotes other strings.
  '''
  if s.isalnum() or is_identifier(s):
    return s
  return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'

# pylint: disable=too-many-branches,too-many-statements,too-many-locals
def gvprint(dot_s, file=None, fmt=None, layout=None, **dot_kw):
  ''' Print the graph specified by `dot_s`, a graph in graphViz DOT syntax,
      to `file` (default `sys.stdout`)
      in format `fmt` using the engine specified by `layout` (default `'dot'`).

      If `fmt` is unspecified it defaults to `'png'` unless `file`
      is a terminal in which case it defaults to `'sixel'`.

      This uses the graphviz utility `dot` to draw graphs.
      If printing in SIXEL format the `img2sixel` utility is required,
      see [https://saitoha.github.io/libsixel/](libsixel).
    '''
  if isinstance(file, str):
    if existspath(file):
      raise ValueError("file %r: already exists" % (file,))
    with open(file, 'wb') as f:
      return gvprint(dot_s, file=f, fmt=fmt, layout=layout, **dot_kw)
  if file is None:
    file = sys.stdout
  if layout is None:
    layout = 'dot'
  if fmt is None:
    if file.isatty():
      fmt = 'sixel'
    else:
      fmt = 'png'
  graph_modes = dict(layout=layout, splines='true')
  node_modes = {}
  edge_modes = {}
  for dot_mode, value in dot_kw.items():
    try:
      modetype, mode = dot_mode.split('_', 1)
    except ValueError:
      if dot_mode in ('fg',):
        node_modes.update(color=value)
        edge_modes.update(color=value)
      elif dot_mode in ('fontcolor',):
        node_modes.update(fontcolor=value)
      else:
        graph_modes[dot_mode] = value
    else:
      if modetype == 'graph':
        graph_modes[mode] = value
      elif modetype == 'node':
        node_modes[mode] = value
      elif modetype == 'edge':
        edge_modes[mode] = value
      else:
        raise ValueError(
            "%s=%r: unknown mode type %r,"
            " expected one of graph, node, edge" % (dot_mode, value, modetype)
        )
  dot_fmt = 'png' if fmt == 'sixel' else fmt
  dot_argv = ['dot', f'-T{dot_fmt}']
  for gmode, gvalue in sorted(graph_modes.items()):
    dot_argv.append(f'-G{gmode}={gvalue}')
  for nmode, nvalue in sorted(node_modes.items()):
    dot_argv.append(f'-N{nmode}={nvalue}')
  for emode, evalue in sorted(edge_modes.items()):
    dot_argv.append(f'-E{emode}={evalue}')
  # make sure any preceeding output gets out first
  file.flush()
  # subprocesses to wait for in order
  subprocs = []
  if fmt == 'sixel':
    # pipeline to pipe "dot" through "img2sixel"
    # pylint: disable=consider-using-with
    img2sixel_popen = Popen(['img2sixel'], stdin=PIPE, stdout=file)
    dot_output = img2sixel_popen.stdin
    subprocs.append(img2sixel_popen)
  else:
    img2sixel_popen = None
    dot_output = file
  # pylint: disable=consider-using-with
  dot_popen = Popen(dot_argv, stdin=PIPE, stdout=dot_output)
  subprocs.insert(0, dot_popen)
  if img2sixel_popen is not None:
    # release out handle to img2sixel
    img2sixel_popen.stdin.close()
  dot_bs = dot_s.encode('ascii')
  dot_popen.stdin.write(dot_bs)
  dot_popen.stdin.close()
  for subp in subprocs:
    subp.wait()
  return None

class DOTNodeMixin:
  ''' A mixin providing methods for things which can be drawn as
      nodes in a DOT graph description.
  '''

  def dot_node(self, label=None, **node_attrs) -> str:
    ''' A DOT syntax node definition for `self`.
    '''
    if label is None:
      label = self.dot_node_label()
    attrs = dict(self.dot_node_attrs())
    attrs.update(node_attrs)
    attrs_s = ','.join(
        f'{quote(attr)}={quote(value)}' for attr, value in attrs.items()
    )
    return f'{quote(label)}[{attrs_s}]'

  # pylint: disable=no-self-use
  def dot_node_attrs(self) -> Mapping[str, str]:
    ''' The default DOT node attributes.
    '''
    return dict(style='solid')

  def dot_node_label(self) -> str:
    ''' The default node label.
        This implementation returns `str(serlf)`
        and a common implementation might return `self.name` or similar.
    '''
    return str(self)

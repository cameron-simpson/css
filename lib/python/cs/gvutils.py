#!/usr/bin/env python3

''' Graphviz utility functions.

    See also the [https://www.graphviz.org/documentation/](graphviz documentation)
    and particularly the [https://graphviz.org/doc/info/lang.html](DOT language specification)
    and the [https://www.graphviz.org/doc/info/command.html](`dot` command line tool).
'''

from base64 import b64encode
from os.path import exists as existspath
from subprocess import Popen, PIPE
import sys
from threading import Thread
from typing import Mapping
from urllib.parse import quote as urlquote

from cs.lex import cutprefix, cutsuffix

__version__ = '20221118'

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
  if s.isalnum() or s.replace('_', '').isalnum():
    return s
  return (
      '"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') +
      '"'
  )

# special value to capture the output of gvprint as binary data
GVCAPTURE = object()

# special value to capture the output of gvprint as a data: URL
GVDATAURL = object()

# pylint: disable=too-many-branches,too-many-statements,too-many-locals
def gvprint(
    dot_s, file=None, fmt=None, layout=None, dataurl_encoding=None, **dot_kw
):
  ''' Print the graph specified by `dot_s`, a graph in graphViz DOT syntax,
      to `file` (default `sys.stdout`)
      in format `fmt` using the engine specified by `layout` (default `'dot'`).

      If `fmt` is unspecified it defaults to `'png'` unless `file`
      is a terminal in which case it defaults to `'sixel'`.

      In addition to being a file or file descriptor,
      `file` may also take the following special values:
      * `GVCAPTURE`: causes `gvprint` to return the image data as `bytes`
      * `GVDATAURL`: causes `gvprint` to return the image data as a `data:` URL

      For `GVDATAURL`, the parameter `dataurl_encoding` may be used
      to override the default encoding, which is `'utf8'` for `fmt`
      values `'dot'` and `'svg'`, otherwise `'base64'`.

      This uses the graphviz utility `dot` to draw graphs.
      If printing in SIXEL format the `img2sixel` utility is required,
      see [https://saitoha.github.io/libsixel/](libsixel).

      Example:

          data_url = gvprint('digraph FOO {A->B}', file=GVDATAURL, fmt='svg')
    '''
  if file is None:
    file = sys.stdout
  if isinstance(file, str):
    if existspath(file):
      raise ValueError("file %r: already exists" % (file,))
    with open(file, 'wb') as f:
      return gvprint(dot_s, file=f, fmt=fmt, layout=layout, **dot_kw)
  if file is GVDATAURL:
    if dataurl_encoding is None:
      dataurl_encoding = 'utf8' if fmt in (
          'dot',
          'svg',
      ) else 'base64'
    gvdata = gvprint(dot_s, file=GVCAPTURE, fmt=fmt, layout=layout, **dot_kw)
    data_content_type = f'image/{"svg+xml" if fmt == "svg" else fmt}'
    if dataurl_encoding == 'utf8':
      gv_data_s = gvdata.decode('utf8')
      data_part = urlquote(gv_data_s.replace('\n', ''), safe=':/<>{}')
    elif dataurl_encoding == 'base64':
      data_part = b64encode(gvdata).decode('ascii')
    else:
      raise ValueError(
          "invalid data URL encoding %r; I accept 'utf8' or 'base64'" %
          (dataurl_encoding,)
      )
    return f'data:{data_content_type};{dataurl_encoding},{data_part}'
  if file is GVCAPTURE:
    capture_mode = True
    file = PIPE
  else:
    capture_mode = False
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
  if file is not PIPE:
    file.flush()
  # subprocesses to wait for in order
  subprocs = []
  output_popen = None
  if fmt == 'sixel':
    # pipeline to pipe "dot" through "img2sixel"
    # pylint: disable=consider-using-with
    img2sixel_popen = Popen(['img2sixel'], stdin=PIPE, stdout=file)
    dot_output = img2sixel_popen.stdin
    subprocs.append(img2sixel_popen)
    output_popen = img2sixel_popen
  else:
    img2sixel_popen = None
    dot_output = file
  # pylint: disable=consider-using-with
  dot_popen = Popen(dot_argv, stdin=PIPE, stdout=dot_output)
  if output_popen is None:
    output_popen = dot_popen
  subprocs.insert(0, dot_popen)
  if img2sixel_popen is not None:
    # release our handle to img2sixel
    img2sixel_popen.stdin.close()
  if capture_mode:
    captures = []
    T = Thread(
        target=lambda: captures.append(output_popen.stdout.read()),
        daemon=True,
    )
    T.start()
  dot_bs = dot_s.encode('ascii')
  dot_popen.stdin.write(dot_bs)
  dot_popen.stdin.close()
  for subp in subprocs:
    subp.wait()
  if capture_mode:
    # get the captured bytes
    T.join()
    bs, = captures
    return bs
  return None

## Nothing renders this :-(
##
##gvprint.__doc__ += (
##    '\n    produces a `data:` URL rendering as:\n    <img src="' + gvprint(
##        'digraph FOO {A->B}',
##        file=GVDATAURL,
##        fmt='svg',
##        dataurl_encoding='base64',
##    ) + '">'
##)

def gvdata(dot_s, **kw):
  ''' Convenience wrapper for `gvprint` which returns the binary image data.
  '''
  return gvprint(dot_s, file=GVCAPTURE, **kw)

def gvdataurl(dot_s, **kw):
  ''' Convenience wrapper for `gvprint` which returns the binary image data
      as a `data:` URL.
  '''
  return gvprint(dot_s, file=GVDATAURL, **kw)

class DOTNodeMixin:
  ''' A mixin providing methods for things which can be drawn as
      nodes in a DOT graph description.
  '''

  DOT_NODE_FONTCOLOR_PALETTE = {}
  DOT_NODE_FILLCOLOR_PALETTE = {}

  def __getattr__(self, attr: str):
    ''' Recognise various `dot_node_*` attributes.

        `dot_node_*color` is an attribute derives from `self.DOT_NODE_COLOR_*PALETTE`.
    '''
    dot_node_suffix = cutprefix(attr, 'dot_node_')
    if dot_node_suffix is not attr:
      # dot_node_*
      colourname = cutsuffix(dot_node_suffix, 'color')
      if colourname is not dot_node_suffix:
        # dot_node_*color
        palette_name = f'DOT_NODE_{colourname.upper()}COLOR_PALETTE'
        try:
          palette = getattr(self, palette_name)
        except AttributeError:
          # no colour palette
          pass
        else:
          try:
            colour = palette[self.dot_node_palette_key]
          except KeyError:
            colour = palette.get(None)
          return colour
    try:
      sga = super().__getattr__
    except AttributeError as e:
      raise AttributeError(
          "no %s.%s attribute" % (self.__class__.__name__, attr)
      ) from e
    return sga(attr)

  @property
  def dot_node_id(self):
    ''' An id for this DOT node.
    '''
    return str(id(self))

  @property
  def dot_node_palette_key(self):
    ''' Default palette index is `self.fsm_state`,
        overriding `DOTNodeMixin.dot_node_palette_key`.
    '''
    return self.dot_node_id

  def dot_node(self, label=None, **node_attrs) -> str:
    ''' A DOT syntax node definition for `self`.
    '''
    if label is None:
      label = self.dot_node_label()
    attrs = dict(self.dot_node_attrs())
    attrs.update(label=label)
    attrs.update(node_attrs)
    if not attrs:
      return quote(label)
    attrs_s = ','.join(
        f'{quote(attr)}={quote(value)}' for attr, value in attrs.items()
    )
    return f'{quote(self.dot_node_id)}[{attrs_s}]'

  # pylint: disable=no-self-use
  def dot_node_attrs(self) -> Mapping[str, str]:
    ''' The default DOT node attributes.
    '''
    attrs = dict(style='solid')
    fontcolor = self.dot_node_fontcolor
    if fontcolor is not None:
      attrs.update(fontcolor=fontcolor)
    fillcolor = self.dot_node_fillcolor
    if fillcolor is not None:
      attrs.update(style='filled')
      attrs.update(fillcolor=fillcolor)
    return attrs

  def dot_node_label(self) -> str:
    ''' The default node label.
        This implementation returns `str(serlf)`
        and a common implementation might return `self.name` or similar.
    '''
    return str(self)

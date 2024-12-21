#!/usr/bin/env python3

def load_pilferrcs(pathname):
  ''' Load `PilferRC` instances from the supplied `pathname`,
      recursing if this is a directory.
      Return a list of the `PilferRC` instances obtained.
  '''
  rcs = []
  with Pfx(pathname):
    if os.path.isfile(pathname):
      # filename: load pilferrc file
      rcs.append(PilferRC(pathname))
    elif os.path.isdir(pathname):
      # directory: load pathname/rc and then pathname/*.rc
      # recurses if any of these are also directories
      rcpath = os.path.join(pathname, "rc")
      if os.path.exists(rcpath):
        rcs.extend(load_pilferrcs(rcpath))
      subrcs = sorted(
          name for name in os.listdir(pathname)
          if not name.startswith('.') and name.endswith('.rc')
      )
      for subrc in subrcs:
        rcpath = os.path.join(pathname, subrc)
        rcs.extend(load_pilferrcs(rcpath))
    else:
      warning("neither a file nor a directory, ignoring")
  return rcs

class PilferRC:

  def __init__(self, filename):
    ''' Initialise the `PilferRC` instance.
        Load values from `filename` if not `None`.
    '''
    self.filename = filename
    self._lock = Lock()
    self.defaults = {}
    self.pipe_specs = {}
    self.action_map = {}
    self.seen_backing_paths = {}
    if filename is not None:
      self.loadrc(filename)

  def __str__(self):
    return type(self).__name__ + '(' + repr(self.filename) + ')'

  @locked
  def add_pipespec(self, spec, pipe_name=None):
    ''' Add a `PipeSpec` to this `Pilfer`'s collection,
        optionally with a different `pipe_name`.
    '''
    if pipe_name is None:
      pipe_name = spec.name
    specs = self.pipe_specs
    if pipe_name in specs:
      raise KeyError("pipe %r already defined" % (pipe_name,))
    specs[pipe_name] = spec

  def loadrc(self, filename):
    ''' Read a pilferrc file and load pipeline definitions.
    '''
    trace("load %s", filename)
    with Pfx(filename):
      cfg = ConfigParser()
      with open(filename) as f:
        cfg.read_file(f)
      self.defaults.update(cfg.defaults().items())
      if cfg.has_section('actions'):
        for action_name in cfg.options('actions'):
          with Pfx('[actions].%s', action_name):
            self.action_map[action_name] = shlex.split(
                cfg.get('actions', action_name)
            )
      if cfg.has_section('pipes'):
        for pipe_name in cfg.options('pipes'):
          with Pfx('[pipes].%s', pipe_name):
            pipe_spec = cfg.get('pipes', pipe_name)
            debug("loadrc: pipe = %s", pipe_spec)
            self.add_pipespec(PipeSpec(pipe_name, shlex.split(pipe_spec)))
      # load [seen] name=>backing_path mapping
      # NB: not yet envsub()ed
      if cfg.has_section('seen'):
        for setname in cfg.options('seen'):
          backing_path = cfg.get('seen', setname).strip()
          self.seen_backing_paths[setname] = backing_path

  def __getitem__(self, pipename):
    ''' Fetch PipeSpec by name.
    '''
    return self.pipe_specs[pipename]

  def __setitem__(self, pipename, pipespec):
    specs = self.pipespecs
    if pipename in specs:
      raise KeyError("repeated definition of pipe named %r", pipename)
    specs[pipename] = pipespec

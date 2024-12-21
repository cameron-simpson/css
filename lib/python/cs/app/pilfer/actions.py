#!/usr/bin/env python3

''' Implementations of actions.
'''

def Action(action_text, do_trace):
  ''' Wrapper for parse_action: parse an action text and promote (sig, function) into an BaseAction.
  '''
  parsed = parse_action(action_text, do_trace)
  try:
    sig, function = parsed
  except TypeError:
    action = parsed
  else:
    action = ActionFunction(action_text, sig, function)
  return action

class BaseAction:
  ''' The base class for all actions.

      Each instance has the following attributes:
      * `srctext`: the text defining the action
      * `sig`: the action's function signature
  '''

  @typechecked
  def __init__(self, srctext: str, sig: StageType):
    self.srctext = srctext
    self.sig = sig

  def __str__(self):
    s = "%s(%s:%r" % (self.__class__.__name__, self.sig, self.srctext)
    if self.args:
      s += ",args=%r" % (self.args,)
    if self.kwargs:
      s += ",kwargs=%r" % (self.kwargs,)
    s += ")"
    return s

  def __call__(self, P):
    ''' Calling an BaseAction with an item creates a functor and passes the item to it.
    '''
    return self.functor()(P, *self.args, **self.kwargs)

class ActionFunction(BaseAction):

  def __init__(self, action0, sig, func):
    super().__init__(action0, sig)
    # stash a retriable version of the function
    func0 = func
    self.func = retriable(func)
    self.func.__name__ = "%s(%r,func=%s)" % (
        type(self).__name__, action0, funcname(func0)
    )

  def functor(self, L):
    return self.func

class ActionPipeTo(BaseAction):

  def __init__(self, action0, pipespec):
    super().__init__(action0, StageType.PIPELINE)
    self.pipespec = pipespec

  class _OnDemandPipeline(MultiOpenMixin):

    def __init__(self, pipespec, L):
      self.pipespec = pipespec
      self.later = L
      self._Q = None

    @property
    def outQ(self):
      X("GET _OnDemandPipeline.outQ")
      return self._Q.outQ

    def put(self, P):
      with self._lock:
        Q = self._Q
        if Q is None:
          X(
              "ActionPipeTo._OnDemandPipeline: create pipeline from %s",
              self.pipespec
          )
          Q = self._Q = P.pipe_from_spec(self.pipespec)
      self._pipeline.put(P)

  def functor(self, L):
    ''' Return an _OnDemandPipeline to process piped items.
    '''
    X("ActionPipeTo: create _OnDemandPipeline(%s)", self.pipespec)
    return self._OnDemandPipeline(self.pipespec, L)

class ActionShellFilter(BaseAction):

  def __init__(self, action0, shcmd, args, kwargs):
    super().__init__(action0, StageType.PIPELINE, args, kwargs)
    self.shcmd = shcmd

  # TODO: substitute parameters into shcmd
  def functor(self):
    ''' Return an iterable queue interface to a shell pipeline.
    '''
    return self.ShellProcFilter(self.shcmd)

class ShellProcFilter(MultiOpenMixin):
  ''' An iterable queue-like interface to a filter subprocess.
  '''

  def __init__(self, shcmd, outQ):
    ''' Set up a subprocess running `shcmd`.

        Parameters:
        * `no_flush`: do not flush input lines for the subprocess,
          block buffer instead
        * `discard`: discard .put items, close subprocess stdin
          immediately after startup
    '''
    self.shcmd = shcmd
    self.shproc = None
    self.outQ = outQ
    outQ.open()

  def _startproc(self, shcmd):
    self.shproc = Popen(shcmd, shell=True, stdin=PIPE, stdout=PIPE)

    def copy_out(fp, outQ):
      ''' Copy lines from the shell output, put new `Pilfer`s onto the `outQ`.
      '''
      for line in fp:
        if not line.endswith('\n'):
          raise ValueError('premature EOF (missing newline): %r' % (line,))
        outQ.put(P.copy_with_vars(_=line[:-1]))
      outQ.close()

    self.copier = Thread(
        name="%s.copy_out" % (self,),
        target=copy_out,
        args=(shproc.stdout, self.outQ)
    ).start()

  def put(self, P):
    with self._lock:
      if self.shproc is None:
        self._startproc()
    self.shproc.stdin.write(P._)
    self.shproc.stdin.write('\n')
    if not self.no_flush:
      self.shproc.stdin.flush()

  def shutdown(self):
    if self.shproc is None:
      outQ.close()
    else:
      self.shproc.wait()
      xit = self.shproc.returncode
      if xit != 0:
        error("exit %d from: %r", xit, self.shcmd)
    self.shproc.stdin.close()

class ActionShellCommand(BaseAction):

  def __init__(self, action0, shcmd, args, kwargs):
    super().__init__(action0, StageType.PIPELINE, args, kwargs)
    self.shcmd = shcmd

  # TODO: substitute parameters into shcmd
  def functor(self):
    ''' Return an iterable queue interface to a shell pipeline.
    '''
    return self.ShellProcCommand(self.shcmd, self.outQ)

class ShellProcCommand(MultiOpenMixin):
  ''' An iterable queue-like interface to a shell command subprocess.
  '''

  def __init__(self, shcmd, outQ):
    ''' Set up a subprocess running `shcmd`.
        `discard`: discard .put items, close subprocess stdin immediately after startup.
    '''
    self.shcmd = shcmd
    self.shproc = None
    self.outQ = outQ
    outQ.open()

  def _startproc(self, shcmd):
    self.shproc = Popen(shcmd, shell=True, stdin=PIPE, stdout=PIPE)
    self.shproc.stdin.close()

    def copy_out(fp, outQ):
      ''' Copy lines from the shell output, put new Pilfers onto the outQ.
      '''
      for line in fp:
        if not line.endswith('\n'):
          raise ValueError('premature EOF (missing newline): %r' % (line,))
        outQ.put(P.copy_with_vars(_=line[:-1]))
      outQ.close()

    self.copier = Thread(
        name="%s.copy_out" % (self,),
        target=copy_out,
        args=(self.shproc.stdout, self.outQ)
    ).start()

  def put(self, P):
    with self._lock:
      if self.shproc is None:
        self._startproc()
    self.shproc.stdin.write(P._)
    self.shproc.stdin.write('\n')
    if not self.no_flush:
      self.shproc.stdin.flush()

  def shutdown(self):
    if self.shproc is None:
      self.outQ.close()
    else:
      self.shproc.wait()
      xit = self.shproc.returncode
      if xit != 0:
        error("exit %d from: %r", xit, self.shcmd)

def action_shcmd(shcmd):
  ''' Return (function, func_sig) for a shell command.
  '''
  shcmd = shcmd.strip()

  @typechecked
  def function(P) -> Iterable[str]:
    U = P._
    uv = P.user_vars
    try:
      v = P.format_string(shcmd, U)
    except KeyError as e:
      warning("shcmd.format(%r): KeyError: %s", uv, e)
    else:
      with Pfx(v):
        with open('/dev/null') as fp0:
          fd0 = fp0.fileno()
          try:
            # TODO: use cs.psutils.run
            subp = Popen(
                ['/bin/sh', '-c', 'sh -uex; ' + v],
                stdin=fd0,
                stdout=PIPE,
                close_fds=True
            )
          except Exception as e:
            exception("Popen: %r", e)
            return
        for line in subp.stdout:
          if line.endswith('\n'):
            yield line[:-1]
          else:
            yield line
        subp.wait()
        xit = subp.returncode
        if xit != 0:
          warning("exit code = %d", xit)

  return function, StageType.ONE_TO_MANY

def action_pipecmd(shcmd):
  ''' Return (function, func_sig) for pipeline through a shell command.
  '''
  shcmd = shcmd.strip()

  @typechecked
  def function(items) -> Iterable[str]:
    if not isinstance(items, list):
      items = list(items)
    if not items:
      return
    P = items[0]
    uv = P.user_vars
    try:
      v = P.format_string(shcmd, P._)
    except KeyError as e:
      warning("pipecmd.format(%r): KeyError: %s", uv, e)
    else:
      with Pfx(v):
        # spawn the shell command
        try:
          subp = Popen(
              ['/bin/sh', '-c', 'sh -uex; ' + v],
              stdin=PIPE,
              stdout=PIPE,
              close_fds=True
          )
        except Exception as e:
          exception("Popen: %r", e)
          return
        # spawn a daemon thread to feed items to the pipe
        def feedin():
          for P in items:
            print(P._, file=subp.stdin)
          subp.stdin.close()

        T = Thread(target=feedin, name='feedin to %r' % (v,))
        T.daemon = True
        T.start()
        # read lines from the pipe, trim trailing newlines and yield
        for line in subp.stdout:
          if line.endswith('\n'):
            yield line[:-1]
          else:
            yield line
        subp.wait()
        xit = subp.returncode
        if xit != 0:
          warning("exit code = %d", xit)

  return function, StageType.MANY_TO_MANY

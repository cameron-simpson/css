#!/usr/bin/env python3

''' POP3 stuff, particularly a streaming downloader.
'''

from collections import namedtuple
from email.parser import BytesParser
from mailbox import Maildir
from netrc import netrc
from os import geteuid
from os.path import isdir as isdirpath
from pwd import getpwuid
from socket import create_connection
import ssl
import sys
from threading import Lock
from cs.cmdutils import BaseCommand
from cs.lex import cutprefix, cutsuffix
from cs.logutils import warning
from cs.pfx import pfx
from cs.queues import IterableQueue
from cs.resources import MultiOpenMixin
from cs.result import Result, ResultSet
from cs.threads import bg as bg_thread

class POP3(MultiOpenMixin):
  ''' Simple POP3 class with support for streaming use.
  '''

  def __init__(self, conn_spec):
    if isinstance(conn_spec, str):
      conn_spec = ConnectionSpec.from_spec(conn_spec)
    self.conn_spec = conn_spec
    self._result_queue = None
    self._client_worker = None
    self._sock = None
    self.recvf = None
    self.sendf = None
    self._lock = Lock()

  @pfx
  def startup(self):
    ''' Connect to the server and log in.
    '''
    self._sock = self.conn_spec.connect()
    self.recvf = self._sock.makefile('r', encoding='iso8859-1')
    self.sendf = self._sock.makefile('w', encoding='ascii')
    self._lock = Lock()
    self._result_queue = IterableQueue()
    self._client_worker = bg_thread(
        self._client_response_worker, args=(self._result_queue,)
    )
    self.client_begin()
    self.client_auth(self.conn_spec.user, self.conn_spec.password)
    return self

  @pfx
  def shutdown(self):
    ''' Quit and disconnect.
    '''
    quitR = self.client_quit_bg()
    self.flush()
    quitR.join()
    if self._result_queue:
      self._result_queue.close()
      self._result_queue = None
    if self._client_worker:
      self._client_worker.join()
      self._client_worker = None
    self.sendf.close()
    self.sendf = None
    bs = self.recvf.read()
    if bs:
      warning("received %d bytes from the server at shutdown", len(bs))
    self.recvf.close()
    self.recvf = None
    self._sock.close()
    self._sock = None

  def readline(self):
    ''' Read a CRLF terminated line from `self.recvf`.
        Return the text preceeding the CRLF.
        Return `None` at EOF.
    '''
    line0 = self.recvf.readline()
    if not line0:
      return None
    line = cutsuffix(line0, '\n')
    assert line is not line0, "missing LF: %r" % (line0,)
    line = cutsuffix(line, '\r')
    return line

  def readlines(self):
    ''' Generator yielding lines from `self.recf`.
    '''
    while True:
      line = self.readline()
      if line is None:
        break
      yield line

  def get_response(self):
    ''' Read a server response.
        Return `(ok,status,etc)`
        where `ok` is true if `status` is `'+OK'`, false otherwise;
        `status` is the status word
        and `etc` is the following text.
    '''
    line = self.readline()
    try:
      status, etc = line.split(None, 1)
    except ValueError:
      status = line
      etc = ''
    return status == '+OK', status, etc

  def get_ok(self):
    ''' Read server response, require it to be `'OK+'`.
        Returns the `etc` part.
    '''
    ok, status, etc = self.get_response()
    if not ok:
      raise ValueError("no ok from server: %r %r" % (status, etc))
    return etc

  def get_multiline(self):
    ''' Generator yielding unstuffed lines from a multiline response.
    '''
    for line in self.readlines():
      if line == '.':
        break
      if line.startswith('.'):
        line = line[1:]
      yield line

  def flush(self):
    ''' FLush the send stream.
    '''
    self.sendf.flush()

  def sendline(self, line, do_flush=False):
    ''' Send a line (excluding its terminating CRLF).
        If `do_flush` is true (default `False`)
        also flush the sending stream.
    '''
    assert '\r' not in line and '\n' not in line
    self.sendf.write(line)
    self.sendf.write('\r\n')
    if do_flush:
      self.flush()

  def _client_response_worker(self, result_queue):
    ''' Worker to process queued request responses.
        Each completed response assigns `(etc,lines)` to the `Result`
        where `etc` is the addition text from the server ok response
        and `lines` is a list of the multiline part of the response
        or `None` if the response is not multiline.
    '''
    for R, is_multiline in result_queue:
      try:
        etc = self.get_ok()
        if is_multiline:
          lines = list(self.get_multiline())
        else:
          lines = None
      except Exception as e:  # pylint: disable=broad-except
        warning("%s: %s", R, e)
        R.exc_info = sys.exc_info
      else:
        R.result = etc, lines

  def client_begin(self):
    ''' Read the opening server response.
    '''
    etc = self.get_ok()
    print(etc)

  def client_auth(self, user, password):
    ''' Perform a client authentication.
    '''
    self.sendline(f'USER {user}', do_flush=True)
    print('USER', user, self.get_ok())
    self.sendline(f'PASS {password}', do_flush=True)
    print('PASS', '****', self.get_ok())

  def client_uidl(self):
    ''' Return a mapping of message number to message UID string.
    '''
    self.sendline('UIDL', do_flush=True)
    self.get_ok()
    for line in self.get_multiline():
      n, msg_uid = line.split(None, 1)
      n = int(n)
      yield n, msg_uid

  def client_bg(self, rq_line, is_multiline=False):
    ''' Dispatch a request `rq_line` in the background.
        Return a `Result` to collect the request result.

        *Note*: DOES NOT flush the send stream.
        Call `self.flush()` when a batch of requests has been submitted,
        before trying to collect the `Result`s.

        The `Result` will receive `(etc,lines)` on success
        where:
        * `etc` is the trailing portion of an ok response line
        * `lines` is a list of unstuffed text lines from the response
          if `is_multiline` is true, `None` otherwise
    '''
    with self._lock:
      self.sendline(rq_line)
      R = Result(rq_line)
      self._result_queue.put((R, is_multiline))
    R.extra.update(rq_line=rq_line)
    return R

  def client_dele_bg(self, msg_n):
    ''' Queue a delete request for message `msg_n`,
        return ` Result` for collection.
    '''
    R = self.client_bg(f'DELE {msg_n}')
    R.extra.update(msg_n=msg_n)
    return R

  def client_quit_bg(self):
    ''' Queue a QUIT request.
        return ` Result` for collection.
    '''
    R = self.client_bg('QUIT')
    return R

  def client_retr_bg(self, msg_n):
    ''' Queue a retrieve request for message `msg_n`,
        return ` Result` for collection.
    '''
    R = self.client_bg(f'RETR {msg_n}', is_multiline=True)
    R.extra.update(msg_n=msg_n)
    return R

class NetrcEntry(namedtuple('NetrcEntry', 'machine login account password')):
  ''' A `namedtuple` representation of a `netrc` entry.
  '''

  NO_ENTRY = None, None, None

  @classmethod
  def get(cls, machine, netrc_hosts=None):
    ''' Look up an entry by the `machine` field value.
    '''
    if netrc_hosts is None:
      netrc_hosts = netrc().hosts
    entry = netrc_hosts.get(machine, cls.NO_ENTRY)
    return cls(machine, *entry)

  @classmethod
  def by_account(cls, account_name, netrc_hosts=None):
    ''' Look up an entry by the `account` field value.
    '''
    if netrc_hosts is None:
      netrc_hosts = netrc().hosts
    for machine, entry_tuple in netrc_hosts.items():
      if entry_tuple[1] == account_name:
        return cls(machine, *entry_tuple)
    return cls(None, *cls.NO_ENTRY)

class ConnectionSpec(namedtuple('ConnectionSpec',
                                'user host sni_host port ssl')):
  ''' A specification for a POP3 connection.
  '''

  # pylint: disable=too-many-branches
  @classmethod
  def from_spec(cls, spec):
    ''' Construct an instance from a connection spec string
        of the form [`tcp:`|`ssl:`][*user*`@`]*[tcp_host!]server_hostname*[`:`*port*].

        The optional prefixes `tcp:` and `ssl:` indicate that the connection
        should be cleartext or SSL/TLS respectively.
        The default is SSL/TLS.
    '''
    spec2 = cutprefix(spec, 'tcp:')
    if spec2 is not spec:
      spec = spec2
      use_ssl = False
    else:
      spec = cutprefix(spec, 'ssl:')
      use_ssl = True
    # see if what's left after the mode matches a netrc account name
    account_entry = NetrcEntry.by_account(spec)
    if account_entry.machine is None:
      account_entry = None
    else:
      # a match, use the machine name as the spec
      spec = account_entry.machine
    try:
      user, hostpart = spec.split('@', 1)
    except ValueError:
      # no user specified, use a default
      hostpart = spec
      current_user = getpwuid(geteuid()).pw_name
      if account_entry:
        if account_entry.login:
          user = account_entry.login
        else:
          # see if the account name has a user part
          try:
            user, _ = account_entry.account.split('@', 1)
          except ValueError:
            user = current_user
      else:
        user = current_user
    try:
      host, port = hostpart.split(':')
    except ValueError:
      host = hostpart
      port = 995 if use_ssl else 110
    else:
      port = int(port)
    try:
      tcp_host, sni_host = host.split('!', 1)
    except ValueError:
      # get the SNI name from the account name
      if account_entry:
        tcp_host = host
        try:
          _, sni_host = account_entry.account.split('@', 1)
        except ValueError:
          sni_host = account_entry.account
      else:
        tcp_host, sni_host = host, host
    conn_spec = cls(
        user=user, host=tcp_host, sni_host=sni_host, port=port, ssl=use_ssl
    )
    ##print("conn_spec =", conn_spec)
    return conn_spec

  @property
  def netrc_entry(self):
    ''' The default `NetrcEntry` for this `ConnectionSpec`.
    '''
    machine = f'{self.user}@{self.host}:{self.port}'
    return NetrcEntry.get(machine)

  @property
  def password(self):
    ''' The password for this connection, obtained from the `.netrc` file
        via the key *user*`@`*host*`:`*port*.
    '''
    entry = self.netrc_entry
    return entry.password

  def connect(self):
    ''' Connect according to this `ConnectionSpec`, return the `socket`.
    '''
    sock = create_connection((self.host, self.port))
    if self.ssl:
      context = ssl.create_default_context()
      sock = context.wrap_socket(sock, server_hostname=self.sni_host)
      print("SSL:", sock.version())
    return sock

class POP3Command(BaseCommand):
  ''' Command line implementation for POP3 operations.

      Credentials are obtained via the `.netrc` file presently.
  '''

  # pylint: disable=too-many-locals
  @staticmethod
  def cmd_dl(argv):
    ''' Collect messages from a POP3 server and deliver to a Maildir.

        Usage: {cmd} [{{ssl,tcp}}:][user@]host[:port] maildir
    '''
    pop_target = argv.pop(0)
    maildir_path = argv.pop(0)
    assert len(argv) == 0
    if not isdirpath(maildir_path):
      raise ValueError("maildir %s: not a directory" % (maildir_path,))
    M = Maildir(maildir_path)
    with POP3(pop_target) as pop3:
      msg_uid_map = dict(pop3.client_uidl())
      print(
          f'{len(msg_uid_map)} message',
          ('' if len(msg_uid_map) == 1 else 's'),
          ('.' if len(msg_uid_map) == 0 else ':'),
          sep=''
      )
      with ResultSet() as retrRs:
        with ResultSet() as deleRs:
          for msg_n in msg_uid_map.keys():
            retrRs.add(pop3.client_retr_bg(msg_n))
          pop3.flush()
          parser = BytesParser()
          for retrR in retrRs:
            # release reference
            retrRs.remove(retrR)
            _, lines = retrR.result
            msg_n = retrR.extra.msg_n
            msg_bs = b''.join(
                map(lambda line: line.encode('iso8859-1') + b'\r\n', lines)
            )
            msg = parser.parsebytes(msg_bs)
            Mkey = M.add(msg)
            deleRs.add(pop3.client_dele_bg(msg_n))
            print(
                f'  msg {msg_n}: {len(msg_bs)} octets, saved as {Mkey}, deleted'
            )
          pop3.flush()
          if deleRs:
            print("wait for DELEs...")
            deleRs.wait()

if __name__ == '__main__':
  sys.exit(POP3Command.run_argv(sys.argv))

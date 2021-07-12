#!/usr/bin/env python3

''' POP3 stuff, particularly a streaming downloader and a simple command line which runs it.

    I spend some time on a geostationary satellite connection,
    where round trip ping times are over 600ms when things are good.

    My mail setup involves fetching messages from my inbox
    for local storage in my laptop, usually using POP3.
    The common standalone tools for this are `fetchmail` and `getmail`.
    However, both are very subject to the link latency,
    in that they request a message, collect it, issue a delete, then repeat.
    On a satellite link that incurs a cost of over a second per message,
    making catch up after a period offline a many minutes long exercise in tedium.

    This module does something I've been meaning to do for literally years:
    a bulk fetch. It issues `RETR`ieves for every message up front as fast as possible.
    A separate thread collects the messages as they are delivered
    and issues `DELE`tes for the saved messages as soon as each is saved.

    This results in a fetch process which is orders of magnitude faster.
    Even on a low latency link the throughput is much faster;
    on the satellite it is gobsmackingly faster.
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
from threading import RLock
from cs.cmdutils import BaseCommand
from cs.lex import cutprefix, cutsuffix
from cs.logutils import debug, warning, exception
from cs.pfx import pfx
from cs.queues import IterableQueue
from cs.resources import MultiOpenMixin
from cs.result import Result, ResultSet
from cs.threads import bg as bg_thread

__version__ = '20210407.2-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Environment :: Console",
        "Topic :: Communications :: Email :: Post-Office :: POP3",
        "Topic :: Internet",
        "Topic :: Utilities",
    ],
    'install_requires': [
        'cs.cmdutils>=20210407.1',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.queues',
        'cs.resources',
        'cs.result>=20210407',
        'cs.threads',
    ],
    'entry_points': {
      'console_scripts': ['pop3 = cs.pop3:POP3Command.run_argv'],
    },
}

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
    self._lock = RLock()

  @pfx
  def startup(self):
    ''' Connect to the server and log in.
    '''
    self._sock = self.conn_spec.connect()
    self.recvf = self._sock.makefile('r', encoding='iso8859-1')
    self.sendf = self._sock.makefile('w', encoding='ascii')
    self.client_begin()
    self.client_auth(self.conn_spec.user, self.conn_spec.password)
    self._result_queue = IterableQueue()
    self._client_worker = bg_thread(
        self._client_response_worker, args=(self._result_queue,)
    )
    return self

  @pfx
  def shutdown(self):
    ''' Quit and disconnect.
    '''
    logmsg = debug
    logmsg("send client QUIT")
    try:
      quitR = self.client_quit_bg()
      logmsg("flush QUIT")
      self.flush()
      logmsg("join QUIT")
      quitR.join()
    except Exception as e:
      exception("client quit: %s", e)
      logmsg = warning
    if self._result_queue:
      logmsg("close result queue")
      self._result_queue.close()
      self._result_queue = None
    if self._client_worker:
      logmsg("join client worker")
      self._client_worker.join()
      self._client_worker = None
    logmsg("close sendf")
    self.sendf.close()
    self.sendf = None
    logmsg("check for uncollected server responses")
    bs = self.recvf.read()
    if bs:
      warning("received %d bytes from the server at shutdown", len(bs))
    logmsg("close recvf")
    self.recvf.close()
    self.recvf = None
    logmsg("close socket")
    self._sock.close()
    self._sock = None
    logmsg("shutdown complete")

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
        Return `(None,None,None)` on EOF from the receive stream.
    '''
    line = self.readline()
    if line is None:
      return None, None, None
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
    ''' Flush the send stream.
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
        # save a list so that we can erase it in a handler to release memory
        R.result = [etc, lines]

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

  def client_bg(self, rq_line, is_multiline=False, notify=None):
    ''' Dispatch a request `rq_line` in the background.
        Return a `Result` to collect the request result.

        Parameters:
        * `rq_line`: POP3 request text, without any terminating CRLF
        * `is_multiline`: true if a multiline response is expected,
          default `False`
        * `notify`: a optional handler for `Result.notify`,
          applied if not `None`

        *Note*: DOES NOT flush the send stream.
        Call `self.flush()` when a batch of requests has been submitted,
        before trying to collect the `Result`s.

        The `Result` will receive `[etc,lines]` on success
        where:
        * `etc` is the trailing portion of an ok response line
        * `lines` is a list of unstuffed text lines from the response
          if `is_multiline` is true, `None` otherwise
        The `Result` gets a list instead of a tuple
        so that a handler may clear it in order to release memory.

        Example:

            R = self.client_bg(f'RETR {msg_n}', is_multiline=True, notify=notify)
    '''
    with self._lock:
      self.sendline(rq_line)
      R = Result(rq_line)
      self._result_queue.put((R, is_multiline))
    R.extra.update(rq_line=rq_line)
    if notify is not None:
      R.notify(notify)
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

  def client_retr_bg(self, msg_n, notify=None):
    ''' Queue a retrieve request for message `msg_n`,
        return ` Result` for collection.

        If `notify` is not `None`, apply it to the `Result`.
    '''
    R = self.client_bg(f'RETR {msg_n}', is_multiline=True, notify=notify)
    R.extra.update(msg_n=msg_n)
    return R

  def dl_bg(self, msg_n, maildir, deleRs):
    ''' Download message `msg_n` to Maildir `maildir`.
        Return the `Result` for the `RETR` request.

        After a successful save,
        queue a `DELE` for the message
        and add its `Result` to `deleRs`.
    '''

    def dl_bg_save_result(R):
      _, lines = R.result
      R.result[1] = None  # release lines
      msg_bs = b''.join(
          map(lambda line: line.encode('iso8859-1') + b'\r\n', lines)
      )
      msg = BytesParser().parsebytes(msg_bs)
      with self._lock:
        Mkey = maildir.add(msg)
        deleRs.add(self.client_dele_bg(msg_n))
      print(f'msg {msg_n}: {len(msg_bs)} octets, saved as {Mkey}, deleted.')

    R = self.client_retr_bg(msg_n, notify=dl_bg_save_result)
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

      Connection specifications consist of an optional leading mode prefix
      followed by a netrc(5) account name
      or an explicit connection specification
      from which to derive:
      * `user`: the user name to log in as
      * `tcp_host`: the hostname to which to establish a TCP connection
      * `port`: the TCP port to connect on, default 995 for TLS/SSL or 110 for cleartext
      * `sni_host`: the TLS/SSL SNI server name, which may be different from the `tcp_host`

      The optional mode prefix is one of:
      * `ssl:`: use TLS/SSL - this is the default
      * `tcp:`: use cleartext - this is useful for ssh port forwards
        to some not-publicly-exposed clear text POP service;
        in particular streaming performs better this way,
        I think because the Python SSL layer does not buffer writes

      Example connection specifications:
      * `username@mail.example.com`:
        use TLS/SSL to connect to the POP3S service at `mail.example.com`,
        logging in as `username`
      * `mail.example.com`:
        use TLS/SSL to connect to the POP3S service at `mail.example.com`,
        logging in with the same login as the local effective user
      * `tcp:username@localhost:1110`:
        use cleartext to connect to `localhost:1110`,
        typically an ssh port forward to a remote private cleartext POP service,
        logging in as `username`
      * `username@localhost!mail.example.com:1995`:
        use TLS/SSL to connect to `localhost:1995`,
        usually an ssh port forward to a remote private TLS/SSL POP service,
        logging in as `username` and passing `mail.exampl.com`
        as the TLS/SSL server name indication
        (which allows certificate verification to proceed correctly)

      Note that the specification may also be a `netrc` account name.
      If the specification matches such an account name
      then values are derived from the `netrc` entry.
      The entry's `machine` name becomes the TCP connection specification,
      the entry's `login` provides a default for the username,
      the entry's `account` host part provides the `sni_host`.

      Example `netrc` entry:

          machine username@localhost:1110
            account username@mail.example.com
            password ************

      Such an entry allows you to use the specification `tcp:username@mail.example.com`
      and obtain the remaining detail via the `netrc` entry.
  '''

  # pylint: disable=too-many-locals
  @staticmethod
  def cmd_dl(argv):
    ''' Collect messages from a POP3 server and deliver to a Maildir.

        Usage: {cmd} [{{ssl,tcp}}:]{{netrc_account|[user@]host[!sni_name][:port]}} maildir
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
      with ResultSet() as deleRs:
        with ResultSet() as retrRs:
          for msg_n in msg_uid_map.keys():
            retrRs.add(pop3.dl_bg(msg_n, M, deleRs))
          pop3.flush()
          retrRs.wait()
        # now the deleRs are all queued
        pop3.flush()
        if deleRs:
          print("wait for DELEs...")
          deleRs.wait()

if __name__ == '__main__':
  sys.exit(POP3Command.run_argv(sys.argv))

#!/usr/bin/perl
#
# Open and manipulate TCP stream connections.
#	- Cameron Simpson <cameron@dap.csiro.au> 21sep95
#
# Generally, ports may be named or numeric.
# Likewise, hostnames may be named or x.x.x.x numeric quadrets.
#
# new host,port -> cs::Port
#	Attach a cs::Port to a stream connection to (host,port).
# new [port] -> service
#	Set up a service (on the port if specified).
#
# Accept() -> cs::Port
#	accept an incoming connection.
#	In a scalar context returns the next connection stream.
#	In an array context returns the new stream and the address of
#	the connecting host.
# Serve(service,flags,func,@args) -> void
#	A simple single threaded server loop.
#	Binds to the specified port and services incoming connections.
#	For each connection it calls
#		func(CONN,$peer,@args)
#	where CONN is the newly connected stream, $peer is the peer address
#	and @args are those passed as context to TCP::Serve.
#	Flags is a bitmask of
#		$cs::Net::TCP::TCP::F_FORK	Fork a subprocess to handle the connection.
#		$cs::Net::TCP::TCP::F_ONCE	Return after servicing a single connection.
#		$cs::Net::TCP::TCP::F_BOUND	Port is not a port name but the filehandle
#				of an already bound socket. This can be
#				used in conjunction with F_ONCE to call
#				TCP::Serve from another main loop as
#				connections come in, allowing intermixing
#				with other functions or I/O.
#

=head1 NAME

cs::Net::TCP - handle TCP network connections

=head1 SYNOPSIS

use cs::Net::TCP;

=head1 DESCRIPTION

This module supplied facilities for dealing with TCP stream connections.

=cut

use strict qw(vars);

use Socket;
use cs::Misc;
use cs::Net;
use cs::Port;

package cs::Net::TCP;

@cs::Net::TCP::ISA=qw(cs::Port);

=head1 CONSTANTS

The follow flag constants should be ORed together
to select the mode for the B<Serve> method.

=over 4

=item B<F_FORK>

Fork a subprocess to handle connections.

=cut

$cs::Net::TCP::F_FORK=0x01;	# TCP::serv: fork on connection

=item B<F_ONCE>

Return after handling a single connection.

=cut

$cs::Net::TCP::F_ONCE=0x02;	# TCP::serv: service a single connection

=item B<F_SYNC>

Wait for the forked child.

=cut

$cs::Net::TCP::F_SYNC=0x04;	# TCP::serv: wait for forked child

=item B<F_FORK2>

Fork twice so children are orphans
to avoid zombies when children need not be waited for.
This flag implies B<F_FORK>.

=cut

$cs::Net::TCP::F_FORK2=0x08;	# TCP::serv: fork twice to orphan children

=back

=cut

{ my($name,$aliases);

  if (!( ($name,$aliases,$cs::Net::TCP::TCP)=getprotobyname('tcp') ))
	{ die "$::cmd: can't look up tcp protocol: $!";
	}
}

=head1 GENERAL FUNCTIONS

=over 4

=item conn(I<host>, I<port>, I<localport>)

Connect to the specified I<port>
on the specified I<host>
using the specified I<localport> at this end.
Normally I<localport> is omitted
an an arbitrary free local port is chosen.
Returns the filehandle of the new socket.

=cut

sub conn	# (host,port[,localport]) -> (FILE)
{ my($rhost,$rport,$localport)=@_;
  die "conn(\$\$;\$) with [@_]" if @_ != 2 && @_ != 3;

  $rport=cs::Net::portNum($rport,TCP);
  return undef if ! defined $rport;

  # get IP address of remote host
  my(@ra)=cs::Net::a2addr($rhost);
  return undef if ! @ra;

  my($sockf);
  my($local,$remote);

  my($laddr)=Socket::INADDR_ANY;
  if (defined $localport)
  { $local=Socket::sockaddr_in($localport,$laddr);
  }


  CONNECT:
  for my $raddr (@ra)
  { $sockf=_sockHandle();
    if (! socket($sockf,Socket::AF_INET,Socket::SOCK_STREAM,$cs::Net::TCP::TCP))
    { warn "socket(AF_INET,SOCK_STREAM,TCP): $!";
      next CONNECT;
    }

    if (defined $localport)
    # use particular local port
    { if (! bind($sockf,$local))
      { warn "bind($sockf,$localport,INADDR_ANY): $!";
	close($sockf);
	next CONNECT;
      }
    }

    $remote=Socket::sockaddr_in($rport,$raddr);
    if (! connect($sockf, $remote))
    { warn "connect($sockf,sockaddr_in($rport,"
	  .cs::Net::addr2a($raddr)
	  .")): $!";
      close($sockf);
      next CONNECT;
    }

    return $sockf;
    last CONNECT;
  }

  return undef;
}

=item service(I<port>)

Listen for connection on the specified I<port>.
If omitted, I<port> defaults to B<0>
and an arbitrary free port is chosen to listen on.
Returns the file handle of the new socket.

=cut

sub service	# [port] -> socket
{ my($port)=@_;
  $port=0 if ! defined $port;

  # pull local interface prefix off
  my $laddr;
  my $laddrpart;
  if ($port =~ /:/)
  { $laddrpart=$`; $port=$';
    $laddr=Socket::inet_aton($laddrpart)
      || die "$::cmd: can't resolve \"$laddrpart\"\n";
  }
  else
  { $laddrpart="INADDR_ANY";
    $laddr=Socket::INADDR_ANY;
  }

  $port=cs::Net::portNum($port,TCP);
  return undef if ! defined $port;

  my($local)=scalar(Socket::sockaddr_in($port,$laddr));

  my($sockf)=_sockHandle();
  if (! socket($sockf,Socket::PF_INET,Socket::SOCK_STREAM,$cs::Net::TCP::TCP))
  { warn "$::cmd: socket($sockf,PF_INET,SOCK_STREAM,TCP): $!";
    return undef;
  }

  if (! bind($sockf,$local))
  { warn "bind($sockf,sockaddr_in($port,$laddrpart)): $!";
    close($sockf);
    return undef;
  }

  listen($sockf,10) || warn "listen($sockf,10): $!";

  $sockf;
}

=back

=head1 OBJECT CREATION

=over 4

=item new B<cs::Net::TCP> I<port>

Listen on the named I<port>.
If omitted, I<port> defaults to B<0>, which should choose a free port.

=item new B<cs::Net::TCP> I<host>, I<port>, I<localport>

Connect to the I<port> on the specified host
using the local I<localport> at this end.
Normally I<localport> is omitted,
and an arbitrary free local port number is allocated.

=cut

sub new
{ my($class)=shift;
  my($this);

  if (@_ == 2 || @_ == 3)
  # connect to remote host/port
  { my($conn)=conn(@_);
    return undef if ! defined $conn;

    $this=new cs::Port $conn;
    return undef if ! defined $this;
  }
  elsif (@_ == 0 || @_ == 1)
  # set up a service
  { my($sockf)=service(@_);
    return undef if ! defined $sockf;
    $this={ cs::Net::TCP::SOCKET	=> $sockf,
	  };
  }
  else
  { my(@c)=caller;
    warn "bad arguments to new $class (@_) called from [@c]:\n"
	."   new $class (host,port[,localport]) -> connection\n"
	."or new $class [port] -> service";

    return undef;
  }

  bless $this, $class;
}

=back

=cut

$cs::Net::TCP::_SOCK='TCPSOCK0000';
sub _sockHandle
{ "cs::Net::TCP::".$cs::Net::TCP::_SOCK++;
}

=head1 OBJECT METHODS

=over 4

=item Port()

Return the local port number of this socket.

=cut

sub Port
{
  my($this)=@_;
  my($sockaddr);

  if (exists $this->{cs::Net::TCP::SOCKET})
  { $sockaddr=getsockname($this->{cs::Net::TCP::SOCKET});
  }
  else
  { $sockaddr=getsockname($this->{IN}->{FILE});
  }

  if (! defined $sockaddr)
  { warn "$::cmd: getsockname: $!";
    return undef;
  }

  ## warn "sockaddr=".cs::Hier::h2a($sockaddr,0);
  my($port,$addr)=Socket::sockaddr_in($sockaddr);

  $port;
}

sub Accept	# service -> cs::Port
{ my($this)=@_;
  my($sockf)=$this->{cs::Net::TCP::SOCKET};
  my($conn)=_sockHandle();
  my($peer);

  if (! ($peer=accept($conn,$sockf)))
  { warn "$::cmd: accept($sockf): $!";
    return undef;
  }

  new cs::Port $conn;
}

# collect incoming connections and call func(conn,@args),
# forking first if flags&F_FORK
# don't loop if flags&F_ONCE
# wait for child if flags&F_SYNC
sub Serve	# (this,flags,func,@args) -> void
{ my($this,$flags,$func,@args)=@_;

  ## warn "Serve(@_)";

  my($dofork,$dofork2,$onceonly,$sync);
  $dofork	=($flags & $cs::Net::TCP::F_FORK);
  $dofork2	=($flags & $cs::Net::TCP::F_FORK2);
  $onceonly	=($flags & $cs::Net::TCP::F_ONCE);
  $sync		=($flags & $cs::Net::TCP::F_SYNC);

  warn "$::cmd: can't use F_SYNC and F_FORK2" if $sync && $dofork2;

  if (! ref $func && $func !~ /'|::/)
  { my($caller,@etc)=caller;
    $func=$caller."::".$func;
  }

  my($conn,$pid);

  CONN:
  while (defined ($conn=$this->Accept()))
  { if ($dofork || $dofork2)
    { if (defined($pid=fork))
      { if ($pid)
	# parent
	{ undef $conn;
	  waitpid($pid,0) if $sync || $dofork2;
	  last CONN if $onceonly;
	  next CONN;
	}
	elsif ($dofork2)
	# child - make grandchild
	{ if (! defined ($pid=fork))
	  { warn "subfork fails(): $!; a zombie will result";
	  }
	  else
	  { exit 0 if $pid;
	  }

	  # child or grandchild proceeds
	  close($this->{cs::Net::TCP::SOCKET})
	      || warn "$::cmd: child: can't close($this->{cs::Net::TCP::SOCKET}): $!\n";
	}
      }
      else
      { warn "fork(): $!";
	next CONN;
      }
    }

    &$func($conn,@args);
    undef $conn;	# flush, close, etc

    exit 0 if $dofork;

    last CONN if $onceonly;
  }
}

=back

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;

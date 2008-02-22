#!/usr/bin/perl
#
# Old TCP package.	- Cameron Simpson <cs@zip.com.au>
#

use Socket;
use Net;

package tcp;

{ local($name,$aliases);

  if (!( ($name,$aliases,$proto)=getprotobyname('tcp') ))
	{ print STDERR "$0: can't look up tcp protocol: $!\n";
	  require 'netinet/in.ph';
	  $proto=&IPPROTO_TCP;
	}
}

$F_FORK=0x01;	# tcp'serv: fork on connection
$F_ONCE=0x02;	# tcp'serv: service a single connection
$F_BOUND=0x04;	# tcp'serv: bypass binding; $port is the FILE

$SOCK='TCPSOCK0000';
sub rwopen	# (host,port[,localport[,firstfree]]) -> (FILE)
	{ local($rhost,$port,$localport,$firstfree)=@_;
	  local($name,$aliases,$type,$len,$raddr);
	  local($dummy);

	  # pick a free port if not specified
	  $localport=0 unless defined($localport);

	  ($port,$dummy)=Net::service($port,'tcp')
		unless $port =~ /^\d+$/;
	  ($name,$aliases,$type,$len,$raddr)=gethostbyname($rhost);

	  local($local,$remote);
	  $remote=Net::mkaddr_in($port,$raddr);

	  local($sockf)=$SOCK++;

	  ((warn "socket: $!"), return undef)
		unless socket($sockf,Socket->AF_INET,Socket->SOCK_STREAM,$proto);

	  if ($firstfree)
	  	{ do {	$local=Net::mkaddr_in($localport,$Net::hostaddr);
		  	$dummy=bind($sockf,$local);
			if (!$dummy)
				{ $localport++;
				}
		     }
		  while (!$dummy);
		}
	  else
	  { $local=Net::mkaddr_in($localport,$Net::hostaddr);
	    $dummy=bind($sockf,$local);
	  }

	  ((warn "bind($localport): $!"), close($sockf), return undef)
		if !$dummy;

	  ((warn "connect($localport,$rhost:$port): $!"),
	   close($sockf),
	   return undef)
		if !connect($sockf,$remote);

	  local($s)=select($sockf); $|=1; select($s);

	  "tcp'".$sockf;
	}

sub rwopen2 # (host,port[,localport[,firstfree]]) -> (FROM,TO)
	{ local($TO)=&rwopen;

	  return undef unless defined($TO);

	  local($FROM);
	  $FROM="tcp'".$tcp'SOCK++;
	  (close($TO), return undef) unless open($FROM,'<&'.fileno($TO));

	  ($FROM,$TO);
	}

sub bind	# (port) -> FILE
	{ local($port)=@_;
	  local($name,$aliases);
	  local($FILE,$dummy);

	  ($port,$dummy)=Net::service($port,'tcp')
		unless $port =~ /^\d+$/;

	  $FILE=$SOCK++;
	  ((warn "socket: $!"), return undef)
		unless socket($FILE, Socket->PF_INET, Socket->SOCK_STREAM, $proto);

	  $name=Net::mkaddr_in($port, "\0\0\0\0");
	  ((warn "bind: $!"), return undef)
		unless bind($FILE, $name);

	  listen($FILE,10) || warn("listen($FILE,10): $!");

	  "tcp'".$FILE;
	}

sub accept	# (FILE) -> wantarray ? (connFILE,peeraddress) : connFILE
	{ local($FILE)=@_;
	  local($CONN)="tcp'".$tcp'SOCK++;
	  local($peer);

	  ((warn "accept($FILE): $!"), return undef)
		unless $peer=accept($CONN,$FILE);
	  
	  print STDERR "accept returns [$CONN]\n";
	  wantarray ? ($CONN,$peer) : $CONN;
	}

# bind to a port and service TCP connections
# for each connection call func($FREAD,$peer,@args)
# forking first if flags&tcp'F_FORK
# bypass bind($port) if flags&F_BOUND; $port is name of FILE
# don't loop if flags&F_ONCE
# the select()ed output goes back down the connection
sub serv	# (port,flags,func,@args) -> void
	{ local($port,$flags,$func,@args)=@_;
	  local($mySOCK,$CONN,$peer,$pid,$dofork);

	  $dofork	=($flags&$F_FORK);
	  $onceonly	=($flags&$F_ONCE);
	  $bound	=($flags&$F_BOUND);

	  if ($func !~ /'/)
		{ local($caller,@etc)=caller;
		  $func=$caller."'".$func;
		}

	  if ($bound)
		{ $mySOCK=$port;
		}
	  else
	  { ((warn "can't bind: $!"), return)
		unless defined($mySOCK=&bind($port));
	  }

	  CONN:
	    while (($CONN,$peer)=&accept($mySOCK))
		{ if ($dofork)
			{ if (defined($pid=fork))
				{ if ($pid)
					# parent
					{ close($CONN)
						|| print STDERR "$cmd: parent: can't close($CONN): $!\n";
					  last CONN if $onceonly;
					  next CONN;
					}
				  else
				  # child
				  { close($mySOCK)
					|| print STDERR "$cmd: child: can't close($mySOCK): $!\n";
				  }
				}
			}

		  { local($newOUTPUT,$oldOUTPUT);

		    $newOUTPUT="tcp'".$SOCK++;
		    if (!open($newOUTPUT,">&$CONN"))
		      { print STDERR "$cmd: can't dup($CONN): $!\n";
			close($CONN)
				|| print STDERR "$cmd: can't close($CONN): $!\n";
			die if $dofork;
			next CONN;
		      }

		    $oldOUTPUT=select($newOUTPUT);
		    &$func($CONN,$peer,@args);
		    select($oldOUTPUT);
		    close($newOUTPUT)
			|| print STDERR "$cmd: can't close($newOUTPUT): $!\n";
		    close($CONN)
			|| print STDERR "$cmd: can't close($CONN): $!\n";
		  }

		  exit 0 if $dofork;
		  last CONN if $onceonly;
		}
	}

1;

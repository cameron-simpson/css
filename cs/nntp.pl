require 'flush.pl';
require 'cs/tcp.pl';
require 'cs/open.pl';

package nntp;

# This package is written from the point of view that the caller supplies:
#	$nntp'FROM, $nntp'TO	NNTP connection.
#	$nntp'NNTPSERVER	Name of currently connected server,
#				set by call to nntp'connect(servername).
# and uses local() to control the context.
#

# connect to server, return (FROM,TO,canpost) or undef
# sets $nntp'FROM, $nntp'TO.
sub connect	# (nntpserver) -> undef=failure or canpost (0=no-posting, 1=posting-ok)
	{ $NNTPSERVER=shift @_;

	  # print main'STDERR "connecting to $NNTPSERVER ...\n";

	  ($FROM,$TO)=&tcp'rwopen2($NNTPSERVER,'nntp');
	  # print main'STDERR "tcp'rwopen2 returns\n";

	  return undef unless defined($FROM);

	  local($_,$text)=&reply;

	  if (!defined)
		{ print STDERR "$'cmd: unexpected EOF from $NNTPSERVER server\n";
		  &disconnect;
		  return undef;
		}

	  # print main'STDERR "connected to $NNTPSERVER\n";
	  if ($_ eq '200')	{ return 1; }
	  if ($_ eq '201')	{ return 0; }

	  print main'STDERR "$'cmd: unexpected opening response from $NNTPSERVER: $code $text\n";

	  &disconnect;
	  undef;
	}

sub disconnect
	{ &out("quit\n");
	  close($FROM);
	  close($TO);
	}

# collect reply from  server, skipping info replies
sub reply	# void -> (code,text) or undef on EOF
	{ local($_);

	  &'flush($TO);
	  # print main'STDERR "getting reply\n";
	  do {	$_=<$FROM>;
		return undef if !defined;
		print main'STDERR "nntp'reply: ", $_;
		return ($1,$2) if /^([2345][0123489]\d)\s*(.*\S?)/;

		if (/^1[0123489]\d/)	# add $verbose hook later
			{ print main'STDERR "$'cmd: $_";
			  next;
			}

		print main'STDERR $'cmd, ': ignoring unexpected response from ', $NNTPSERVER, ': ', $_;
	     }
	  while (1);
	}

# collect a text response from the server, stripping \r?\n
sub text	# (void) -> @lines
	{ local(@lines);

	  &'flush($TO);

	  @lines=();
	  while (<$FROM>)
		{ s/\r?\n$//;
		  last if $_ eq '.';
		  s/^\.\././;
		  push(@lines,$_);
		}

	  @lines;
	}

# post a complete article contained in a file
sub post	# articlefile -> ok
	{ local($file)=@_;
	  local($_,$text,$FILE);

	  if (!defined($FILE=&'subopen('<',$file)))
		{ print main'STDERR "$'cmd: can't open $file for posting: $!\n";
		  return 0;
		}

	  &out("post\n");
	  ($_,$text)=&reply;

	  if (!defined)
		{ print main'STDERR "$'cmd: unexpected EOF from $NNTPSERVER server\n";
		  close($FILE);
		  return 0;
		}

	  if ($_ eq '440')
		{ print main'STDERR "$'cmd: posting forbidden on $NNTPSERVER\n";
		  close($FILE);
		  return 0;
		}

	  while (<$FILE>)
		{ &out('.') if /^\./;
		  &out($_);
		}

	  &out(".\n");
	  close($FILE);

	  ($_,$text)=&reply;

	  if (!defined)
		{ print main'STDERR "$'cmd: unexpected EOF from $NNTPSERVER\n";
		  return 0;
		}

	  return 1 if $_ eq '240';

	  print main'STDERR "$'cmd: $NNTPSERVER: $_ $text\n";
	  0;
	}

sub req		# request -> undef or ($code,$text)
	{ &out;
	  &reply;
	}

# select news group
sub group	# group -> ($low,$high) or undef
	{ local($_,$text)=&req('group ', $_[0], "\n");

	  print main'STDERR "group($_[0]): _=$_, text=$text\n";
	  if (defined && /^2/ && $text =~ /^\d+\s+(\d+)\s+(\d+)/)
		{ return ($1+0,$2+0);
		}

	  undef;
	}

1;	# for require

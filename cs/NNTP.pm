#!/usr/bin/perl
#
# Access an NNTP service.	- Cameron Simpson <cs@zip.com.au>
#

=head1 NAME

cs::NNTP - access an NNTP service

=head1 SYNOPSIS

use cs::NNTP;

=head1 DESCRIPTION

This module contacts
and converses with an NNTP service.

=cut

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::RFC822;
use cs::MIME;
use cs::Source;
use cs::Net::TCP;
use cs::Port;
use cs::IO;
require 'open3.pl';

package cs::NNTP;

@cs::NNTP::ISA=qw(cs::Net::TCP);

$cs::NNTP::Debug=exists $ENV{DEBUG_NNTP};

=head1 OBJECT CREATION

=over 4

=item new(I<server>,I<needpost>)

Connect to the I<server> specified.
If omitted,
use the value of the B<NNTPSERVER> environment variable.
If the optional parameter I<needpost> is true,
return B<undef> if the server refuses posting permission.

=back

=cut

sub new($;$$)	# server -> connectionrec or undef
{ my($class,$server,$needpost)=@_;
  if (! defined $server || ! length $server)
  { $server=$ENV{NNTPSERVER};
  }
  $needpost=0 if ! defined $needpost;

  my $port = '';

  my $this;

  if ($server =~ /^\|\s*/)
  # pipe to/from subprocess
  { my $subproc = $';
    warn "subproc=[$subproc]";
    my $to = cs::IO::mkHandle;
    my $from = cs::IO::mkHandle;
    my $pid = ::open3($to,$from,'>&STDERR',$subproc);

    if (! defined $pid)
    { warn "$::cmd: can't open3($subproc): $!";
      return undef;
    }

    $this = new cs::Port ($from,$to);
  }
  else
  { if ($server =~ /:/)
    { $port=$';
      $server=$`;
    }
    else
    { $port=NNTP;
    }

    # open new connection
    $this=new cs::Net::TCP ($server, $port);

    if (! defined $this)
    { warn "can't make cs::Net::TCP($server,$port): $!";
      return undef;
    }
  }

  $cs::NNTP::Debug && warn "connect to $server:$port";

  bless $this, $class;

  $this->{SERVER}="$server:$port";
  undef $this->{GROUP};

  local($_);
  my($text);
  ($_,$text)=$this->Reply();

  if (! defined)
  { warn "$::cmd: unexpected EOF from $server:$port server";
    return undef;
  }

  my($canpost);

  if ($_ eq '200')	{ $canpost=1; }
  elsif ($_ eq '201')	{ $canpost=0; }
  else
  { warn "unexpected opening response from $server:$port: $_ $text";
    return undef;
  }

  warn "$::cmd: needpost=$needpost but canpost=$canpost ($server:$port)"
	if $needpost && ! $canpost;

  $this->{CANPOST}=$canpost;

  $this;
}

sub DESTROY
{ my($this)=@_;
  $this->Disconnect();
  $cs::NNTP::Debug && warn "closed connection to $this->{SERVER}";
}

sub Disconnect($)
{ shift->Put("quit\n");
}

sub CanPost()
{ shift->{CANPOST};
}

sub Group($$)
{ my($this,$group)=@_;

  my($code,$text)=$this->Command("group $group\n");
  return undef unless defined $code;

  if ($code =~ /^2/ && $text =~ /^\s*\d+\s+(\d+)\s+(\d+)/)
  # it worked
  { $this->{GROUP}=$group;
    return ($1+0,$2+0);
  }

  warn "$::cmd: group $group: unexpected return \"$code $text\"\n";

  return ();
}

# return list of active newsgroups
sub List($)
{ my($this)=@_;

  my($code,$text)=$this->Command("LIST\n");

  return undef unless defined $code;
  return undef unless $code =~ /^2/;

  $this->Text();
}

sub Head($$)
{ my($this,$id)=@_;

  my($code,$text)=$this->Command("HEAD $id\n");

  return undef unless defined $code;
  return undef unless $code eq '221';

  my $H = new cs::RFC822;
  my(@head)=$this->Text();

  $H->ArrayExtract(@head);

  $H;
}

sub Article($$)
{ my($this,$id)=@_;

  my($code,$text)=$this->Command("ARTICLE $id\n");
  return undef if ! defined $code || $code ne '220';

  my $art = $this->Text();
  new cs::MIME (new cs::Source (SCALAR, \$art));
}

# collect reply from  server, skipping info replies
sub Reply($)
{ my($this)=@_;

  local($_);

  $this->Flush();

  FINDREPLY:
  do {	$_=$this->GetLine();
	return undef if ! defined || ! length;
	return ($1,$2) if /^([2345][0123489]\d)\s*(.*\S?)/;

	if (/^1[0123489]\d/)	# add $verbose hook later
	{ warn "$::cmd: $_";
	  next FINDREPLY;
	}

	warn "$::cmd: ignoring unexpected response from $cs::NNTP::This->{NNTPSERVER}: $_";
     }
  while (1);	# return is inside loop
}

sub Command($$)
{ my($this,$command)=@_;
  $this->Put($command);
  $this->Reply();
}

# collect a text response from the server, stripping \r?\n
sub Text($)
{ my($this)=@_;

  my(@lines);
  local($_);

  $this->Flush();

  @lines=();
  while (defined ($_=$this->GetLine()) && length)
  { last if /^\.\r?\n$/;
    s/^\.\././;
    push(@lines,$_);
  }

  wantarray ? @lines : join('',@lines);
}

# post a complete article contained in a file
sub PostFile($$)
{ my($this,$fname)=@_;

  my $s = new cs::Source PATH, $fname;
  return undef if ! defined $s;

  $this->PostSource($s);
}

sub PostSource($$)
{ my($this,$s)=@_;

  if (! $this->CanPost())
  { warn "$::cmd: posting forbidden\n";
    return 0;
  }

  my($code,$text)=$this->Command("POST\n");

  if (! defined $code)
  { warn "$::cmd: unexpected EOF\n";
    return 0;
  }

  if ($code eq '440')
  { warn "$::cmd: posting forbidden\n";
    return 0;
  }

  my($inhdrs,$hadfrom)=(1,0);

  local($_);

  while (defined ($_=$s->GetLine()) && length)
  { if ($inhdrs)
    { if (/^\r?\n$/)
      { $inhdrs=0;
	if (! $hadfrom)
	{ my($login)=scalar getpwuid($>);
	  $this->Put("From: $login\@research.canon.com.au\n");
	}
      }
      elsif (/^from:/i)
      { $hadfrom=1;
      }
    }

    $this->Put(".") if /^\./;
    $this->Put($_);
  }

  $this->Put(".\n");

  ($code,$text)=$this->Reply();

  if (! defined $code)
  { warn "$::cmd: unexpected EOF\n";
    return 0;
  }

  return 1 if $code eq '240';

  warn "$::cmd: $_ $text\n";

  0;
}

1;

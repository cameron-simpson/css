#!/usr/bin/perl
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::NNTP;
require 'open3.pl';

package cs::metaNNTP;

@cs::NNTP::ISA=qw(cs::Net::TCP);

$cs::NNTP::Debug=exists $ENV{DEBUG_NNTP};

sub new($;$)	# server -> connectionrec or undef
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
  _with($this,\&_disconnect);
  $cs::NNTP::Debug && warn "closed connection to $this->{SERVER}";
}

sub _disconnect
{ _out("quit\n");
}

sub Group	# (s,groupname) -> (low,high)
{ my($s,$group)=@_;
  &_with($s,\&_group,$group);
}
sub _group
{ my($group)=@_;

  _out("group $group\n");
  my($code,$text)=_reply();

  return undef unless defined $code;

  if ($code =~ /^2/ && $text =~ /^\s*\d+\s+(\d+)\s+(\d+)/)
  # it worked
  { $cs::NNTP::This->{GROUP}=$group;
    return ($1+0,$2+0);
  }

  warn "group $group: unexpected return \"$code $text\"\n";

  ();
}

# return list of active newsgroups
sub List
{ my($this)=@_;
  &_with($this,\&_list);
}
sub _list
{ _out("LIST\n");
  my($code,$text)=&_reply;

  return undef unless defined $code;
  return undef unless $code =~ /^2/;

  _text();
}

sub Head	# (s,article-id) -> RFC822::hdr or undef
{ my($s,$id)=@_;

  &_with($s,\&_head,$id);
}
sub _head
{ my($id)=@_;

  _out("head $id\n");
  my($code,$text)=&_reply;

  return undef unless defined $code;
  return undef unless $code =~ /^2/;

  my($h)=new cs::RFC822;
  my(@head)=_text();

  $h->ArrayExtract(@head);

  $h;
}

# fetch and return a multiline response
sub Body
{ my($s,$id)=@_;
  my($b)='';
  my($sink)=new cs::Sink (SCALAR,\$b);
  &_with($s,\&_body,$id,$sink);
}

sub CanPost
{ shift->{CANPOST};
}

# copy a BODY (or any multiline text response)
# directly to a cs::Sink instead of bundling it into a string
sub CopyBody
{ my($this,$id,$sink)=@_;
  &_with($this,\&_body,$id,$sink);
}
sub _body
{ my($id,$sink)=@_;

  _out("body $id\n");
  my($code,$text)=&_reply;

  return undef unless defined $code;
  return undef unless $code =~ /^2/;

  local($_);

  BODY:
    while (defined ($_=$cs::NNTP::This->GetLine()) && length)
	{ last BODY if /^\.\r?\n$/;
	  s/^\.\././;
	  $sink->Put($_);
	}

  1;
}

sub _with	# (service, coderef, @args) => &code(server-rec, args)
{ local($cs::NNTP::This)=shift;
  my($code,@args)=@_;

  &$code(@args);
}

# collect reply from  server, skipping info replies
sub Reply { _with(shift,\&_reply); }
sub _reply
{ local($_);

  $cs::NNTP::This->Flush();
  # warn "getting reply\n";
  FINDREPLY:
  do {	$_=$cs::NNTP::This->GetLine();
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

sub Out	{ my($s)=shift; _with($s,\&_out,@_); }
sub _out{ $cs::NNTP::This->Put(@_);
	  # warn "_out(@_)\n";
	}

# collect a text response from the server, stripping \r?\n
sub Text{ _with(shift,\&_text); }
sub _text # (void) -> @lines
{ my(@lines);
  local($_);

  $cs::NNTP::This->Flush();

  @lines=();
  while (defined ($_=$cs::NNTP::This->GetLine()) && length)
	{ last if /^\.\r?\n$/;
	  s/^\.\././;
	  push(@lines,$_);
	}

  wantarray ? @lines : join('',@lines);
}

# post a complete article contained in a file
sub PostFile{ my($this,$fname)=@_;
	      my($s);

	      $s=new cs::Source PATH, $fname;
	      return undef if ! defined $s;

	      _with($this,\&_post,$s);
	    }

sub Post{ my($this,$s)=@_;

	  _with($this,\&_post,$s);
	}

sub _post
{ my($s)=@_;
  my($text);
  local($_);

  if (! $cs::NNTP::This->{CANPOST})
	{ warn "posting forbidden on $cs::NNTP::This->{NNTPSERVER}\n";
	  return 0;
	}

  _out("post\n");
  ($_,$text)=&_reply;

  if (! defined)
	{ warn "unexpected EOF from $cs::NNTP::This->{NNTPSERVER} server\n";
	  return 0;
	}

  if ($_ eq '440')
	{ warn "posting forbidden on $cs::NNTP::This->{NNTPSERVER}\n";
	  return 0;
	}

  my($inhdrs,$hadfrom)=(1,0);

  while (defined ($_=$s->GetLine()) && length)
	{ if ($inhdrs)
		{ if (/^\r?\n$/)
			{ $inhdrs=0;
			  if (! $hadfrom)
				{ my($login)=scalar getpwuid($>);
				  _out("From: $login\@$ENV{MAILDOMAIN}\n");
				}
			}
		  elsif (/^from:/i)
			{ $hadfrom=1;
			}
		}

	  _out('.') if /^\./;
	  _out($_);
	}

  _out(".\n");

  ($_,$text)=&_reply;

  if (! defined)
	{ warn "unexpected EOF from $cs::NNTP::This->{NNTPSERVER}\n";
	  return 0;
	}

  return 1 if $_ eq '240';

  warn "$cs::NNTP::This->{NNTPSERVER}: $_ $text\n";

  0;
}

1;

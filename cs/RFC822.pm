#!/usr/bin/perl
#
# Code to support RFC822-style message headers.
# Recoded in Perl5.
#	- Cameron Simpson <cs@zip.com.au> 16oct94
#
# Cleaner parseaddrs().	- cameron, 17mar97
#
# new	Ref to empty header structure.
# Hdr(hdrname)
#	Return line of headers in array context, string in scalar, or undef.
# HdrNames -> @headernames
# Hdrs -> @headers
# Add("hdrname: body\n")
# Addrs(@hdrnames) -> addrSet
# Extract(@lines) -> @remaining-lines
#	Add headers from @lines, up to blank line. Return lines _after_
#	blank line.
# FExtract($fname) -> @bodylines
#	Open file, get lines, pass to Extract.
# Del(hdrname,keep)
#	Remove mention of header, renaming to X-Original-hdrname if $keep.
# norm(hdrname)
#	Field name to output form (capitalises words).
#
# &parseaddrs($addresslist) -> @(addr, text)
#	Break comma separated address list into a list of (addr,full text).
#
# &msgid
#	Generate a message-id for an article.
#
# &ForceMsgID
#	Return message-id of article. Add one to the headers if it doesn't
#	have one.
#
# &msgids(text) -> @ids
#	Extract all strings looking like message-ids from the text.
#	Return just the first in a scalar context.
#
# &from_(addr,gmtime)
#	Generate the body of the From_ header given an address and a time.
#
# ForceFrom_
#	Add From_ line if missing, guessing from From: line and current time.
#
# &date2gm(date-body) -> gmtime
#	Parse Date: line and return GMT or undef on error.
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use Time::Local;
use cs::Misc;
use cs::Set;

package cs::RFC822;

sub Debug { my($this)=shift;
	    my($p,$f,$l)=caller(0);
	    $f =~ s:.*/::;
	    warn "$f:$l: @_ this=$this, synched=$this->{SYNCHED}, Hdrs=$this->{HDRS}, To="
		.(defined($this->{HDRS}->{TO})
			? "[$this->{HDRS}->{TO}]"
			: 'UNDEF');
	  }

@cs::RFC822::mailhdrs=(TO,CC,BCC,FROM,SENDER,REPLY_TO,RETURN_RECEIPT_TO,ERRORS_TO);
@cs::RFC822::newshdrs=(NEWSGROUPS,FOLLOWUP_TO);
$cs::RFC822::mailptn=join('|',@cs::RFC822::mailhdrs);
$cs::RFC822::newsptn=join('|',@cs::RFC822::newshdrs);

$cs::RFC822::msgidptn='<[^@<>]+@[^@<>]+>';

@cs::RFC822::listhdrs=(@cs::RFC822::mailhdrs,@cs::RFC822::newshdrs,KEYWORDS);
for (@cs::RFC822::listhdrs)
	{ $cs::RFC822::_IsListHdr{$_}=1;
	}

# timezones with GMT offsets in minutes
%cs::RFC822::tzones=(	UT,	0,	GMT,	0,
		EST,	-300,	EDT,	-240,
		CST,	-360,	CDT,	-300,
		MST,	-420,	MDT,	-360,
		PST,	-480,	PDT,	-420,
		A, -60, B, -120, C, -180, D, -240,
		E, -300, F, -360, G, -420, H, -480,
		I, -540, K, -600, L, -660, M, -720,
		N, 60, O, 120, P, 180, Q, 240,
		R, 300, S, 360, T, 420, U, 480,
		V, 540, W, 600, X, 660, Y, 720
	);

sub tzone2minutes
	{ local($_)=@_; tr/a-z/A-Z/;
	  if (/^([-+]?)([01][0-9])([0-5][0-9])$/)
			{ return ($1 eq '-' ? -1 : 1)*($3+60*$2)*60;
			}
	  return undef if !defined $cs::RFC822::tzones{$_};
	  $cs::RFC822::tzones{$_};
	}

sub new	{ my($class,$src)=@_;
	  my($this)={ HDRLIST => [],
		      HDRS    => {},
		      SYNCHED  => 1,
		    };

	  bless $this, $class;

	  if (! defined $src)
		{}
	  elsif (ref $src)
		{ $this->SourceExtract($src);
		}
	  else	{ $this->FileExtract($src);
		}

	  $this;
	}

# for tie - XXX - how to do firstkey, next, last?
# sub fetch { &Hdr(@_); }
# sub store { my($this)=shift; &Add($this,$_[0].': '.$_[1]); }
# sub delete { &Del(@_); }

sub Hdr
	{ my($this,$key,$first)=@_;
	  $first=0 if ! defined $first;
	  # Debug($this,"Hdr($key)");

	  $key=&hdrkey($key);

	  if (! $first && ! wantarray)
		{ $this->Sync();
		  my($hash)=$this->{HDRS};
	  	  return undef unless exists $hash->{$key};
	  	  return $hash->{$key};
		}

	  my(@bodies,$hdr)=();
	  my($hdrlist)=$this->{HDRLIST};

	  for $hdr (@$hdrlist)
		{ $hdr =~ /^([^:]*):\s*/;
		  push(@bodies,$') if $key eq &hdrkey($1);
		}

#	  print STDERR "returning array [",
#			join('|',@bodies),
#			"] to ", join(':',caller(0)), "\n";

	  @bodies
	    ? $first
	      ? shift(@bodies)
	      : @bodies
	    : wantarray
	      ? ()
	      : undef;
	}

sub Hdrs
	{ my($this)=shift;
	  my($hdrlist)=$this->{HDRLIST};
	  wantarray ? @$hdrlist : $hdrlist;
	}

sub HdrNames
{ my($this)=shift;
  $this->Sync();
  my($hdrhash)=$this->{HDRS};
  my(@names);

  for (keys %$hdrhash)
	{ push(@names,&norm($_));
	}

  @names;
}

sub Sync
{ my($this)=@_;
  # Debug($this,"Sync");

  return if $this->{SYNCHED};

  my(%hash);

  my($hdr,$key,$body);
  my($hdrlist)=$this->{HDRLIST};

  for $hdr (@$hdrlist)
  { $hdr =~ /^([^:]*):\s*/;
    $body=$';
    $key=&hdrkey($1);
    if (exists $hash{$key})
    { if ($cs::RFC822::_IsListHdr{$key})
      { $hash{$key} =~ s/\s*\r?\n\Z/,\n\t/;
      }

      $hash{$key}.=$body;
    }
    else
    { $hash{$key}=$body;
    }
  }

  $this->{HDRS}=\%hash;
  $this->{SYNCHED}=1;

  # Debug($this,"after Sync");
}

sub Add	# (hdr[,how])
{ my($this)=shift;
  local($_)=shift;

  my(@c);
  my($field,$body);

  if (ref)
  { ($field,$body)=@$_;
  }
  elsif (/^[-\w_]+$/)
  { ($field=$_) =~ tr/_/-/;
    $body=shift;
  }
  elsif (! /^([^:\s]+):\s*/)
  { @c=caller;
    warn "tried to add bad header ($_) from [@c]";
    return;
  }
  else
  { ($field,$body)=($1,$');
  }

  my($how)=@_;
  $how=ADD if ! defined $how;

  # clean up
  $_=$body;

  chomp; s/\s+$//;
  s/\n([^ \t])/\n\t$1/g;	# enforce breaks

  $body=$_;

  my($htext)=norm($field).": $body";
  my($hlist)=$this->{HDRLIST};
  my($cfield)=hdrkey($field);

  if ($how eq ADD)
  { push(@$hlist,$htext);
  }
  elsif ($how eq PREPEND)
  { unshift(@$hlist,$htext);
  }
  elsif ($how eq SUPERCEDE)
  {
    ## warn "add header \"$htext\", mode SUPERCEDE";
    HDR_SUP:
      for (@$hlist)
      { if (! /^[^:]+/)
	{ warn "$::cmd: skipping bogus header record [$_]";
	  next HDR_SUP;
	}

	if (hdrkey($&) eq $cfield)
	{ s/^/X-Original-/;
	}
      }

    push(@$hlist,$htext);
  }
  elsif ($how eq REPLACE)
  { $hlist=[ grep(! /^[^:]+/ || hdrkey($&) ne $cfield, @$hlist) ];
    push(@$hlist,$htext);
    $this->{HDRLIST}=$hlist;
  }
  else
  { @c=caller;
    warn "don't know how to add header [$field,$body] by method \"$how\" from [@c]";
  }

  $this->{SYNCHED}=0;
}

# (this,fname,[keep]) -> keep ? handle-at-body : close-ok
sub FileExtract
{ local($cs::RFC822::This)=shift;
  ::need(cs::Source);
  my($fname,$keep)=@_;
  my($s)=cs::Source->new(PATH, $fname);
  _extract($s);
}
sub FILEExtract	# (this,FILE) -> hdrs,@bodylines
{ local($cs::RFC822::This)=shift;
  ::need(cs::Source);
  my($FILE)=@_;
  my($s)=cs::Source->new(FILE, $FILE);
  _extract($s);
}
sub SourceExtract
{ local($cs::RFC822::This)=shift;
  _extract(@_);
}
sub ArrayExtract
{ local($cs::RFC822::This)=shift;
  ::need(cs::Source);
  my(@a)=@_;
  my($s)=cs::Source->new(ARRAY, \@a);
  _extract($s);
}
# NOTE: expects a local($this) to be in scope!
sub _extract
{ my($s,$leadingFrom_)=@_;
  $leadingFrom_=1 if ! defined $leadingFrom_;

  local($_);

  my $first = 1;
  HDR:
    while (defined ($_=$s->GetContLine()) && length)
    { chomp;
      last HDR if ! length;

      if ($first)
      { if (/^From / && $leadingFrom_)
	{ s/^From /From-: /;
	}

	$first=0;
      }

      last HDR unless /^[^:\s]+:/;

      $cs::RFC822::This->Add($_);
    }

  1;
}

sub Del
{ my($this,$key,$keep)=@_;
  $keep=0 if ! defined $keep;

  my(@nhdrs,$match);
  local($_);

  $key=&hdrkey($key);
  my($hdrlist)=$this->{HDRLIST};
  for (@$hdrlist)
  { /^[^:]*/;
    if ($key eq &hdrkey($&))
    { $match=1;
      push(@nhdrs,"X-Deleted-$_") if $keep;
    }
    else
    { push(@nhdrs,$_);
    }
  }

  if ($match)
  { # print STDERR "changed after Del($key) to [",
    # 		join("|",@nhdrs), "]\n";
    $this->{HDRLIST}=[ @nhdrs ];
    $this->{SYNCHED}=0;
  }
}

# Get key from field.
sub hdrkey
{ local($_)=shift;
  tr/-/_/;
  uc($_);
}

# Get normal form of field name.
sub norm
{ local($_)=&hdrkey(shift);

  ## warn "norm($_) -> ...\n";
  $_=lc($_);
  s/\b[a-z]/\u$&/g;
  tr/_/-/;
  ## warn "$_\n";
  $_;
}

# parse an RFC822 address list returning a list of pairs
#	(addr-without-<>, full-text, ...)
sub ngSet($)	# newsgrouptext -> cs::Set
{ local($_)=@_;

  my $ng = new cs::Set;

  for (grep(length,split(/[\s,]+/)))
  { $ng->STORE($_,$_);
  }

  $ng;
}

sub Addrs
{ my($this,@hdrlist)=@_;

  my $addrs = new cs::Set;

  HEAD:
  for my $head (@hdrlist)
  { my @h = $this->Hdr($head);
    for my $h (@h)
    { $addrs->AddSet(addrSet($h));
    }
  }

  $addrs;
}

sub addrSet($)	# addrlisttext -> cs::Set
{ local($_)=shift;

  my $oaddrs = $_;	# waste, usually, but useful for warning

  my $addrs = new cs::Set;

  my($text,$addr,$atext);
  my($comment,$tail);

  $text='';
  $addr='';
  $atext='';

  ## warn "addrSet($_)";
  TOKEN:
  while (1)
  {
    ## warn "parse at [$_]\n";
    if (/^,(\s*,)*/ || !length)
	  # end of currently building address
	  { ## warn "comma - tidy up";
	    if (length)
		  { # print STDERR "delim=$&\n";
		    $_=$';
		  }
	    else	{ # print STDERR "eos\n";
		  }

	    $text =~ s/^\s+//;
	    $text =~ s/\s+$//;

	    $atext =~ s/^\s+//;
	    $atext =~ s/\s+$//;

	    $addr=$atext if ! length $addr;

	    $addr =~ s/\@\([^\@]+\)$/\@\L$1/;
	    if (length $addr)
		  { $addrs->STORE($addr,$text);
		  }

	    $addr='';
	    $text='';
	    $atext='';

	    last TOKEN if ! length;
	  }
    elsif (/^\(/)
	  # comment
	  { ## warn "comment";
	    ($comment,$tail)=parse_comment($_);
	    $text.=$comment;
	    ## warn "comment=$comment\n";
	    $_=$tail;
	  }
    elsif (/^<([^\@>]*(\@[^>]*)?)>/)
	  { ## warn "addr=$&";
	    $text.=$&;
	    $atext.=$&;
	    $addr=$1;
	    $_=$';
	  }
    elsif (/^\s+/)
	  { ## warn "token=$&";
	    $text.=' ';
	    $_=$';
	  }
    elsif (/^\\./			# q-pair
	|| /^"(\\(.|\n)|[^"\\]+)*"/	# quoted string
	|| /^[^\\"<\(,]+/)	# plain text
	  { ## warn "token=$&\n";
	    $text.=$&;
	    $atext.=$&;
	    $_=$';
	  }
    else	{ warn "unknown at [$_], original text was:\n$oaddrs\n\n ";
	    $text.=substr($_,0,1);
	    $atext.=substr($_,0,1);
	    substr($_,0,1)='';
	  }
  }

  $addrs;
}

sub parse_comment
	{ local($_)=shift;
	  my($comment)='';
	  my($subcomment,$tail);

	  if (! /^\(/)
		{ warn "parse_comment on a non-comment \"$_\"";
		  return ('()',$_);
		}

	  $comment=$&;
	  $_=$';

	  TOKEN:
	    while (length)
		{ last TOKEN if /^\)/;

		  if (/^\(/)
			{ ($subcomment,$tail)=parse_comment($_);
			  $comment.=$subcomment;
			  $_=$tail;
			}
		  elsif (/^\\./)
			{ $comment.=$&; $_=$'; }
		  elsif (/^[^\(\)\\]+/)
			{ $comment.=$&; $_=$'; }
		  else	{ $comment.=substr($_,0,1);
			  substr($_,0,1)='';
			}
		}

	  s/^\)//;		# eat closure if present

	  $comment.=')';	# return well-formed comment

	  ($comment,$_);
	}

sub addr2fullname
{ my($addrtext)=@_;
  my($addr,$fullname);

  if ($addrtext =~ /<\s*(\S.*\S)\s*>/)
	{ $addr=$1;
	  $fullname="$` $'";
	}
  elsif ($addrtext =~ /\(\s*(\S.*\S)\s*\)/)
	{ $addr="$` $'";
	  $fullname=$1;
	}
  else	{ $addr=$addrtext;
	  $fullname="";
	}

  $fullname =~ s/^"\s*(.*)\s*"$/$1/;
  $fullname =~ s/^'\s*(.*)\s*'$/$1/;

  wantarray ? ($fullname,$addr) : $fullname;
}

$cs::RFC822::_Msgid_count=0;
sub msgid	# void -> msgid
	{ my($sec,$min,$hour,$mday,$mon,$year,@etc)=localtime(time);

	  $cs::RFC822::_MsgidCount++;
	  $year+=1900;
	  sprintf("<%04d%02d%02d%02d%02d%02d-%s-%d-%05d@%s>",
		$year,$mon+1,$mday,$hour,$min,$sec,
		$'ENV{'USER'},
		$cs::RFC822::_MsgidCount,
		$$,
		$'ENV{'HOSTNAME'});
	}

sub ForceMsgID	# void -> msgid
	{ my($this)=shift;
	  my($msgid);

	  if (! defined ($msgid=$this->Hdr(Message_ID)))
		{ Add($this,"Message-ID: ".($msgid=msgid()));
		}

	  $msgid;
	}

sub msgids	# text -> message-ids
	{ local($_)=shift;
	  my(@ids);

	  while (/$cs::RFC822::msgidptn/o)
		{ push(@ids,$&);
		  $_=$';
		}
	  
	  wantarray ? @ids : shift(@ids);
	}

sub from_	# (addr,gmtime)
{ my($addr,$time)=@_;
  my($sec,$min,$hr,$mday,$mon,$yr,$wday,@etc)=gmtime($time);

  sprintf("%s %s %s %2d %02d:%02d:%02d GMT %4d\n",
	  $addr,
	  (Sun,Mon,Tue,Wed,Thu,Fri,Sat)[$[+$wday],
	  (Jan,Feb,Mar,Apr,May,Jun,Jul,Aug,Sep,Oct,Nov,Dec)[$[+$mon],
	  $mday,
	  $hr,$min,$sec,
	  1900+$yr
	 );
}

sub ForceFrom_
{ my($this)=shift;
  local($_);

  if (! defined($_=&Hdr($this,FROM_)))
  { my $addrset = $this->Addrs(FROM);
    my(@addrs) = $addrset->KEYS();

    &Add($this,'From-: '.from_($addrs[0],time));
  }
}

sub gm2date
{ my($gmt)=@_;
  $gmt=time if ! defined $gmt;

  ::need(cs::Date);
  my $D = cs::Date->new($gmt);
  my $tm = $D->Tm(0);

  # XXX - add localtime+tz support sometime
  sprintf("%s, %d %s %d %02d:%02d:%02d +0000",
	  $cs::Date::Wday_names[$tm->{WDAY}],
	  $tm->{MDAY},
	  $cs::Date::Mon_names[$tm->{MON}-1],
	  $tm->{YEAR},
	  $tm->{HH},
	  $tm->{MM},
	  $tm->{SS}
	 );
}

sub date2gm
{ local($_)=@_;

  ::need(cs::Date);

  my($gmt);

  #     dow      ,     dom     mon                yr        hh      mm     ss          offset       tz
  if (/^([a-z]{3},\s*)?(\d+)\s+([a-z]{3})[a-z]*\s+0*(\d+)\s+(\d\d?):(\d\d)(:(\d\d))?\s*([-+]?\d{4}|[a-z]+)?/i)
  { my($dom,$mon,$yr,$hh,$mm,$ss,$offset)
	  =($2,$3,$4,$5,$6,$7,$8);

    if ($ss =~ /\d+/)	{ $ss=$&+0; }
    else		{ $ss=0; }

    my($mnum);
    return undef unless defined($mnum=cs::Date::mon2mnum($mon));

    # catch y2k stupidities (and legit 2 digit form)
    if (length($yr) < 4)	{ $yr+=($yr < 70 ? 2000 : 1900); }
    if ($yr < 1970)
    { warn "$::cmd: bogus date \"$_\" yields year == $yr, returning undef";
      return undef;
    }

    $gmt=cs::Date::dmy2gmt($dom,$mnum+1,$yr,0)
	+($hh*60+$mm)*60+$ss
	-tzone2minutes($offset);
  }
  else
  { warn "\"$_\" doesn't look like a Date: line\n";
    return undef;
  }

  $gmt;
}

sub from_2gm
{ local($_)=@_;

  ::need(cs::Date);
  cs::Date::ctime2gm($_);
}

sub PickHdr	# (header-names) -> first-non-empy
{ my($this)=shift;
  local($_);
  my($hdr);

  for $hdr (@_)
	{ return $_ if defined($_=$this->Hdr($hdr));
	}

  undef;
}

sub GetAddrs
{ my($this,@hdrs)=@_;

  return new cs::Set if ! @hdrs;

  my $addrs = addrSet(scalar($this->Hdr(shift(@hdrs))));

  for my $hdr (@hdrs)
  { $addrs->AddSet(addrSet(scalar($this->Hdr($hdr))));
  }
}

sub Reply_To(\%)	{ PickHdr(shift,REPLY_TO,FROM); }
sub Errors_To		{ PickHdr(shift,ERRORS_TO,FROM); }
sub Followups_To	{ PickHdr(shift,FOLLOWUPS_TO,NEWSGROUPS); }

sub Reply
{ my($this,$how,$myaddrs)=@_;
  $how=AUTHOR if ! defined $how;
  $myaddrs='' if ! defined $myaddrs;

  my $rep = new cs::RFC822;

  my $to = new cs::Set;
  my $cc = new cs::Set;
  my $ng = new cs::Set;
  my $bcc= new cs::Set;

  for (ref $how ? @$how : $how)
	{ if ($_ eq AUTHOR)
		{ $to->AddSet(addrSet($this->Reply_To()));
		}
	  elsif ($_ eq ALL)
		{ $to->AddSet(addrSet($this->Reply_To()));
		  $cc->AddSet(addrSet($this->Hdr(TO)
				     .", ".$this->Hdr(CC)));

		  $ng->AddSet(ngSet($this->Followups_To()));
		}
	  elsif ($_ eq ERROR)
		{ $to->AddSet(addrSet($this->Errors_To()));
		}
	  else
	  { warn "$::cmd: unknown reply style \"$_\"";
	  }
	}

  # clean duplicates
  map($cc->DELETE($_),$to->KEYS());

  # clean self references
  if (length $myaddrs)
	{ map($cc->DELETE($_),addrSet($myaddrs)->KEYS());
	}

  $rep->Add(TO,join(",\n\t",$to->Values()))	if $to->KEYS();
  $rep->Add(CC,join(",\n\t",$cc->Values()))	if $cc->KEYS();
  $rep->Add(BCC,join(",\n\t",$cc->Values()))	if $bcc->KEYS();
  $rep->Add(NEWSGROUPS,join(",",$ng->Values()))	if $ng->KEYS();

  $rep->Add(SUBJECT,"Re: ".normsubject(scalar($this->Hdr(SUBJECT))));

  { my $kw;
    if (length($kw=$this->Hdr(KEYWORDS)))
	{ $rep->Add(KEYWORDS,$kw);
	}
  }

  { my $ref;
    my @ref;
    my $msgid;

    if (length($ref=$this->Hdr(REFERENCES)))
	{ push(@ref,
		grep(length,
			split(/\s+/,$ref)));
	}
    else
    # XXX - hack to catch idiot mailers
    { my($irt);

      if (($irt=$this->Hdr(IN_REPLY_TO))
	=~ /<[^@>]*@[^@]*>/)
	{ push(@ref,$&);
	}
    }

    if (length($msgid=$this->Hdr(MESSAGE_ID)))
	{ push(@ref,$msgid);
	}

    $rep->Add(REFERENCES,join("\n\t",@ref)) if @ref;
  }

  $rep;
}

sub normsubject($)	# subject -> cleaner form
	{ local($_)=shift;

	  s/^\s+//;
	  s/^(re\s*(\[\d+\]\s*)?:+\s*)+//i;
	  s/\s+$//;
	  s/\s+/ /g;

	  $_;
	}

sub WriteItem(\%\%)	# ($this,Sink,[1,],@text...)
{ my($this,$sink)=(shift,shift);

  # optional From_ line
  my $needFrom_ = 0;
  if (@_ && $_[0] eq '1')
  { $needFrom_=1;
    shift(@_);
  }

  if (! ref $sink)
  # assume we got a FILE
  { my($FILE)=$sink;

    $FILE=caller(0)."::$FILE" unless $FILE =~ /('|::)/;
    ::need(cs::Sink);
    $sink=cs::Sink->new(FILE, $FILE);
  }

  # warn "$this -> WriteItem(): sink=".cs::Hier::h2a($sink);

  if ($needFrom_)
  { ## my(@c)=caller;warn "[@c]";
    $this->ForceFrom_();
    $sink->Put("From ", $this->Hdr(From_), "\n");
  }

  my(@h)=@{$this->{HDRLIST}};

#	  warn "XXX: WriteItem: no headers!!\n".cs::Hier::h2a($this,1)
#		if ! @h;

  for (@h)
  { s/\n+([^ \t])/\n\t$1/g;
    if (/^[^\s:]+:\s*/)
    { $sink->Put($_, "\n") if length $';
    }
    else
    { warn "$::cmd: bad header [$_]";
    }
  }

  $sink->Put("\n", @_);
}

1;

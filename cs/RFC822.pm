#!/usr/bin/perl
#
# Code to support RFC822-style message headers.
# Recoded in Perl5.
#	- Cameron Simpson <cs@zip.com.au> 16oct94
#
# Cleaner parseaddrs().	- cameron, 17mar97
#

=head1 NAME

cs::RFC822 - handle RFC822 data (internet standard email headers)

=head1 SYNOPSIS

use cs::RFC822;

=head1 DESCRIPTION

This module implements methods
for dealing with RFC822 data.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use Time::Local;
use cs::Misc;

package cs::RFC822;

=head1 GENERAL FUNCTIONS

=over 4

=cut

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

=item tzone2minutes(I<zone>)

Convert the standard timezone name I<zone>
to an offset in minutes from GMT.

=cut

sub tzone2minutes($)
{ local($_)=@_; tr/a-z/A-Z/;
  if (/^([-+]?)([01][0-9])([0-5][0-9])$/)
		{ return ($1 eq '-' ? -1 : 1)*($3+60*$2)*60;
		}
  return undef if ! exists $cs::RFC822::tzones{$_};
  $cs::RFC822::tzones{$_};
}

=item hdrkey(I<headername>)

Convert a I<headername> into a perl bareword string
by uppercasing the letters and translating dashes into underscores.

=cut

sub hdrkey
{ local($_)=shift;
  tr/-/_/;
  uc($_);
}

=item norm(I<hdrkey>)

Convert a I<hdrkey> (or RFC822 header name)
into a vanilla header name
by lowercasing the letters,
translating underscores into dashes
and then uppercasing the first letter of any word.

=cut

sub norm
{ local($_)=&hdrkey(shift);

  ## warn "norm($_) -> ...\n";
  $_=lc($_);
  s/\b[a-z]/\u$&/g;
  tr/_/-/;
  ## warn "$_\n";
  $_;
}

sub ngSet($)	# newsgrouptext -> cs::Flags
{ local($_)=@_;

  ::need(cs::Flags);
  cs::Flags->new(grep(length,split(/[\s,]+/)));
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
    else
    { $comment.=substr($_,0,1);
      substr($_,0,1)='';
    }
  }

  s/^\)//;		# eat closure if present

  $comment.=')';	# return well-formed comment

  ($comment,$_);
}

=item addrSet(I<addresses>)

Parse the string I<addresses>
and return a hashref mapping B<I<user>@I<host>> to full address.

=cut

sub addrSet($)	# addrlisttext -> hashref
{ local($_)=shift;

  my $oaddrs = $_;	# waste, usually, but useful for warnings

  my $addrs = {};

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
      { # warn "delim=$&\n";
	$_=$';
      }
      else
      { # warn "eos\n";
      }

      $text =~ s/^\s+//;
      $text =~ s/\s+$//;

      $atext =~ s/^\s+//;
      $atext =~ s/\s+$//;

      $addr=$atext if ! length $addr;

      $addr =~ s/\@\([^\@]+\)$/\@\L$1/;
      if (length $addr)
      { $addrs->{$addr}=$text;
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
    elsif (/^<(("[^"]*"|[^"\@>])*(\@("[^']*"|[^>])*)?)>/)
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
    else
    { warn "unknown at [$_], original text was:\n$oaddrs\n\n ";
      $text.=substr($_,0,1);
      $atext.=substr($_,0,1);
      substr($_,0,1)='';
    }
  }

  $addrs;
}

=item addr2fullname(I<address>)

Extract the full name component from I<address>
(i.e. the text outside the B<I<...>> or within the B<(I<...>)>,
depending).
In a scalar context, return the full name.
In an array context return the full name and the remaining address text
as two strings.

=cut

sub addr2fullname($)
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
  else
  { $addr=$addrtext;
    $fullname="";
  }

  $fullname =~ s/^"\s*(.*)\s*"$/$1/;
  $fullname =~ s/^'\s*(.*)\s*'$/$1/;

  wantarray ? ($fullname,$addr) : $fullname;
}

=item msgid()

Create an unique message-id.

=cut

$cs::RFC822::_Msgid_count=0;
sub msgid()
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

sub msgids	# text -> message-ids
{ local($_)=shift;
  my(@ids);

  while (/$cs::RFC822::msgidptn/o)
  { push(@ids,$&);
    $_=$';
  }
  
  wantarray ? @ids : shift(@ids);
}

=item from_(I<address>,I<time>)

Create a UNIX envelope "From_" line body value
from the I<address> and optional UNIX time_t I<time>.

=cut

sub from_($;$)
{ my($addr,$time)=@_;
  $time=time if ! defined $time;

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

=item normsubject(I<subject>)

Clean excess whitepsace and leading "B<Re: >" prefixes from the string I<subject>.

=cut

sub normsubject($)	# subject -> cleaner form
{ local($_)=@_;

  s/^\s+//;
  s/^(re\s*(\[\d+\]\s*)?:+\s*)+//i;
  s/\s+$//;
  s/\s+/ /g;

  $_;
}

=back

=head1 OBJECT CREATION

=over 4

=item new cs::RFC822 I<source>

Create a new B<csRFC822> object.
If the optional parameter I<source> is supplied
then if it is an object reference
use it as a B<cs::Source> object and extract any headers from it,
leaving it positioned at the start of the message body.
If I<source> is a scalar consider it a filename
and extract headers from that file.

=cut

sub new
{ my($class,$src)=@_;
  my($this)={ cs::RFC822::HDRLIST	=> [],
	      cs::RFC822::HDRS    	=> {},
	      cs::RFC822::SYNCHED	=> 1,
	    };

  bless $this, $class;

  if (! defined $src)
  {}
  elsif (ref $src)
  { $this->SourceExtract($src);
  }
  else
  { $this->FileExtract($src);
  }

  $this;
}

sub DESTROY {}

sub _HdrList(){ shift->{cs::RFC822::HDRLIST}; }
sub _Hdrs(){ shift->{cs::RFC822::HDRS}; }
sub _Synched(){ shift->{cs::RFC822::SYNCHED}; }

=back

=head1 OBJECT METHODS

=over 4

=cut

sub Debug
{ my($this)=shift;
    my($p,$f,$l)=caller(0);
    $f =~ s:.*/::;
    warn "$f:$l: @_ this=$this, synched=$this->{cs::RFC822::SYNCHED}, Hdrs=$this->{cs::RFC822::HDRS}, To="
	.(defined($this->{cs::RFC822::HDRS}->{TO})
		? "[$this->{cs::RFC822::HDRS}->{TO}]"
		: 'UNDEF');
}

# for tie - XXX - how to do firstkey, next, last?
# sub fetch { &Hdr(@_); }
# sub store { my($this)=shift; &Add($this,$_[0].': '.$_[1]); }
# sub delete { &Del(@_); }

=item Hdr(I<header>,I<first>)

Return an array of the bodies of the header fields named I<header>.
If the optional parameter I<first> is true,
return only the first such header.

=cut

sub Hdr($$;$)
{ my($this,$key,$first)=@_;
  $first=0 if ! defined $first;
  # Debug($this,"Hdr($key)");

  $key=&hdrkey($key);

  # !$first && scalar conext => arrayref returned, including fieldnames
  if (! $first && ! wantarray)
  { $this->Sync();
    my($hash)=$this->{cs::RFC822::HDRS};
    return undef unless exists $hash->{$key};
    return $hash->{$key};
  }

  my(@bodies)=();
  my($hdrlist)=$this->_HdrList();;

  for my $hdr (@$hdrlist)
  { $hdr =~ /^([^:]*):\s*/;
    push(@bodies,$') if $key eq hdrkey($1);
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

=item Hdrs()

Return the arrayref storing the headers in a scalar context,
or the array itself in a list context.

=cut

sub Hdrs
{ my($this)=shift;
  my($hdrlist)=$this->{cs::RFC822::HDRLIST};
  wantarray ? @$hdrlist : $hdrlist;
}

=item HdrNames()

Return the names of the fields present in the headers.

=cut

sub HdrNames
{ my($this)=shift;

  $this->Sync();
  my($hdrhash)=$this->{cs::RFC822::HDRS};
  my(@names);

  for (keys %$hdrhash)
  { push(@names,norm($_));
  }

  @names;
}

sub Sync
{ my($this)=@_;
  # Debug($this,"Sync");

  return if $this->{cs::RFC822::SYNCHED};

  my(%hash);

  my($hdr,$key,$body);
  my($hdrlist)=$this->{cs::RFC822::HDRLIST};

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

  $this->{cs::RFC822::HDRS}=\%hash;
  $this->{cs::RFC822::SYNCHED}=1;

  # Debug($this,"after Sync");
}

=item Add(I<header>,I<how>)

=item Add(I<headername>,I<headerbody>,I<how>)

Add the specified I<header> to the object
in the fashion specified by the optional parameter I<how>
(which defaults to B<ADD>).
I<header> may be
an arrayref to an array of the form B<(I<headername>,I<headerbody>)>,
a complete header line ("B<I<headername>: I<headerbody>>"),
or a header name,
in which last case an extra parameter I<headerbody> is expected
before I<how>.

I<headerbody>
may also be an arrayref
in which case the body is the concatenation of the array elements
separated by "B<,\n\t>",
or a hashref
in which case the body is the concatenation of the hash values
separated by "B<,\n\t>".

I<how> is one of the strings:
B<ADD>, to append the header to the list;
B<PREPEND>, to prepend the header to the list;
B<SUPERCEDE>, to rename all existing headers named I<headername>
to be "B<X-Original-I<headername>>" and the append the header to the list;
B<REPLACE>, to discard all existing headers named I<headername>
and append the header to the list.

If I<how> is B<SUPERCEDE>
an optional prefix string may be supplied after I<how>
to use instead of "B<X-Original->" above.
Note: a zero length prefix string makes B<SUPERCEDE> act like B<REPLACE>,
not B<ADD>.

=cut

sub Add
{ my($this)=shift;
  local($_)=shift;

  my(@c);
  my($field,$body);

  if (ref eq ARRAY)
  { ($field,$body)=@$_;
  }
  elsif (/^[-\w_]+$/)
  { ($field=$_) =~ tr/_/-/;
    $body=shift;
    if (ref($body) eq ARRAY)
    { $body=join(",\n\t", @$body);
    }
    elsif (ref($body) eq HASH)
    { $body=join(",\n\t", sort map($body->{$_}, keys %$body));
    }
  }
  elsif (! /^([^:\s]+):\s*/)
  { @c=caller;
    warn "tried to add bad header ($_) from [@c]";
    return;
  }
  else
  { ($field,$body)=($1,$');
  }

  my($how,$suppfx)=@_;
  $how=ADD if ! defined $how;
  if ($how ne SUPERCEDE)
  { if (defined $suppfx)
    { my(@c)=caller;
      warn "$::cmd: extract argument \"$suppfx\" after $how from [@c]";
    }
  }
  elsif (! defined $suppfx)
  { $suppfx="X-Original-";
  }
  elsif (! length $suppfx)
  { $how=REPLACE;
  }

  # clean up
  $_=$body;

  chomp; s/\s+$//;
  s/\n([^ \t])/\n\t$1/g;	# enforce breaks

  $body=$_;

  my($htext)=norm($field).": $body";
  my($hlist)=$this->{cs::RFC822::HDRLIST};
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
      { $_=$suppfx.$_;
      }
    }

    push(@$hlist,$htext);
  }
  elsif ($how eq REPLACE)
  { $hlist=[ grep(! /^[^:]+/ || hdrkey($&) ne $cfield, @$hlist) ];
    push(@$hlist,$htext);
    $this->{cs::RFC822::HDRLIST}=$hlist;
  }
  else
  { @c=caller;
    warn "don't know how to add header [$field,$body] by method \"$how\" from [@c]";
  }

  $this->{cs::RFC822::SYNCHED}=0;
}

=item FileExtract(I<file>)

Extract the headers from the named I<file>.

=cut

sub FileExtract($$)
{ local($cs::RFC822::This)=shift;
  my($fname)=@_;

  ::need(cs::Source);
  my($s)=cs::Source->new(PATH, $fname);

  _extract($s);
}

=item FILEExtract(I<FILE>)

Extract the headers from the perl filehandle I<FILE>,
leaving the file handle positioned at the start of the body.

=cut

sub FILEExtract($$)
{ local($cs::RFC822::This)=shift;
  my($FILE)=@_;

  ::need(cs::Source);
  my($s)=cs::Source->new(FILE, $FILE);

  _extract($s);
}

=item SourceExtract(I<source>)

Extract the headers from the specified I<source>,
leaving the I<source> positioned at the start of the body.

=cut

sub SourceExtract
{ local($cs::RFC822::This)=shift;
  _extract(@_);
}

=item ArrayExtract(I<lines>)

Extract the headers from the array of I<lines>.

=cut

sub ArrayExtract
{ local($cs::RFC822::This)=shift;
  my(@a)=@_;

  ::need(cs::Source);
  my($s)=cs::Source->new(ARRAY, \@a);

  _extract($s);
}

# NOTE: expects a local($cs::RFC822::This) to be in scope!
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

=item Del(I<headername>,I<keep>)

Delete all headers named I<headername>.
If the optional parameter I<keep> is true,
just rename the headers to "B<X-Deleted->I<headername>".

=cut

sub Del($$;$)
{ my($this,$key,$keep)=@_;
  $keep=0 if ! defined $keep;

  my(@nhdrs,$match);
  local($_);

  $key=&hdrkey($key);
  my($hdrlist)=$this->{cs::RFC822::HDRLIST};
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
    $this->{cs::RFC822::HDRLIST}=[ @nhdrs ];
    $this->{cs::RFC822::SYNCHED}=0;
  }
}

=item Addrs(I<headernames>)

Extract the email addresses from the headers
specified by the array I<headenames>
and return a hashref
keyed on the B<I<user>@I<host>> component.

=cut

sub Addrs
{ my($this,@hdrlist)=@_;

  my $addrs = {};

  HEAD:
  for my $head (@hdrlist)
  { my @h = $this->Hdr($head);
    for my $h (@h)
    { ::addHash($addrs,addrSet($h));
    }
  }

  $addrs;
}

=item ForceMsgID()

Generate and insert a B<Message-ID> field
if none is present.

=cut

sub ForceMsgID($)
{ my($this)=@_;
  my($msgid);

  if (! defined ($msgid=$this->Hdr(Message_ID)))
  { Add($this,"Message-ID: ".($msgid=msgid()));
  }

  $msgid;
}

sub ForceFrom_($)
{ my($this)=@_;

  local($_);

  if (! defined($_=Hdr($this,FROM_)))
  { my $addrset = $this->Addrs(FROM);
    my(@addrs) = $addrset->KEYS();

    Add($this,'From-: '.from_($addrs[0],time));
  }
}

sub gm2date(;$)
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
    if (length($yr) < 4)
    { $yr+=($yr < 70 ? 2000 : 1900); }

    if ($yr < 1970)
    { warn "$::cmd: bogus date \"$_\" yields year == $yr, returning undef";
      return undef;
    }

    $gmt=cs::Date::dmy2gmt($dom,$mnum+1,$yr,0)
	+($hh*60+$mm)*60+$ss
	-tzone2minutes($offset);
  }
  else
  { warn "$::cmd: \"$_\" doesn't look like a Date: line\n";
    return undef;
  }

  $gmt;
}

sub from_2gm($)
{ local($_)=@_;

  ::need(cs::Date);
  cs::Date::ctime2gm($_);
}

sub PickHdr	# (header-names) -> first-non-empy
{ my($this)=shift;

  local($_);

  for my $hdr (@_)
  { return $_ if defined($_=$this->Hdr($hdr)) && length;
  }

  undef;
}

sub GetAddrs
{ my($this,@hdrs)=@_;

  return {} if ! @hdrs;

  my $addrs = addrSet(scalar($this->Hdr(shift(@hdrs))));

  for my $hdr (@hdrs)
  { ::addHash($addrs,addrSet(scalar($this->Hdr($hdr))));
  }
}

sub Reply_To($)		{ shift->PickHdr(REPLY_TO,FROM); }
sub Errors_To($)	{ shift->PickHdr(RETURN_PATH,ERRORS_TO,SENDER,FROM); }
sub Followups_To($)	{ shift->PickHdr(FOLLOWUPS_TO,NEWSGROUPS); }

sub Reply($;$$)
{ my($this,$how,$myaddrs)=@_;
  $how=AUTHOR if ! defined $how;
  $myaddrs='' if ! defined $myaddrs;

  ::need(cs::Flags);

  my $rep = new cs::RFC822;

  my $to = {};
  my $cc = {};
  my $ng = cs::Flags->new();;
  my $bcc= {};

  for (ref $how ? @$how : $how)
  { if ($_ eq AUTHOR)
    { ::addHash($to,addrSet($this->Reply_To()));
    }
    elsif ($_ eq ALL)
    { ::addHash($to,addrSet($this->Reply_To()));
      ::addHash($cc,addrSet($this->Hdr(TO)
			            .", "
				    .$this->Hdr(CC)));

      $ng->Set(ngSet($this->Followups_To())->Members());
    }
    elsif ($_ eq ERROR)
    { ::addHash($to,addrSet($this->Errors_To()));
    }
    else
    { warn "$::cmd: unknown reply style \"$_\"";
    }
  }

  # clean duplicates
  map(delete $cc->{$_}, keys %$to);

  # clean self references
  if (length $myaddrs)
  { map(delete $cc->{$_}, keys %{addrSet($myaddrs)});
  }

  $rep->Add(TO,$to)			if keys %$to;
  $rep->Add(CC,$cc)			if keys %$cc;
  $rep->Add(BCC,$bcc)			if keys %$bcc;
  { my @ng = $ng->Members();
    $rep->Add(NEWSGROUPS,\@ng)		if @ng;
  }

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

sub WriteItem	# ($this,Sink,[1,],@text...)
{ my($this,$sink)=(shift,shift);

  # optional From_ line
  my $needFrom_ = 0;
  if (@_ && $_[0] eq '1')
  { $needFrom_=1;
    shift(@_);
  }

  if (! defined $sink)
  { my@c=caller;die"\$sink undefined from [@c]";
  }

  if (! ref $sink)
  # assume we got a FILE
  { my($FILE)=$sink;

    $FILE=caller(0)."::$FILE" unless $FILE =~ /('|::)/;
    ::need(cs::Sink);
    $sink=cs::Sink->new(FILE, $FILE);
  }

  if ($needFrom_)
  { ## my(@c)=caller;warn "[@c]";
    $this->ForceFrom_();
    $sink->Put("From ", $this->Hdr(From_), "\n");
  }

  my(@h)=@{$this->{cs::RFC822::HDRLIST}};

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

  my $n = $sink->Put("\n", @_);

  if (! defined $n)
  { my@c=caller;
    warn "Put() returns undef from [@c]";
    return undef;
  }

  return $n;
}

=back

=head1 SEE ALSO

RFC822 - Standard for the Format of ARPA Internet Text Messages

cs::Source(3), cs::Sink(3), cs::MIME(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;

#!/usr/bin/perl
#
# Hierachical data structure <=> ASCII form.
# In fact, the expressions written are very nearly perl syntax.
# This will break when reference tracking comes in.
# Also has assorted hierarchical utility functions.
#	- Cameron Simpson <cs@zip.com.au> 14mar95
#
# Added tags to fix recursive references.	- cameron 21feb96
# Fixed tag parsing bug.			- cameron 02jun96
# Added ptrtags parameter to disable pointer tags. - cameron 28nov96
# Write to cs::Sink.				- cameron 11jun98
#
# Bugs:	h2a() collapses &scalar into just scalar.
#
# h2a(ref[,useindent]) => ASCII-form
# a2h(text) => wantarray ? (ref,unparsed-portion) : ref
#		or undef on syntax error
# hcmp(ref1,ref2) Compare two hierachies.
#		 Return 0 on match.
#		 BUG: should return signed on mismatch, for sorting,
#		      though the depth first approach makes this dubious.
# copy(ref) -> ref2
#	Copy structure undef ref, returning duplicate under ref2.
#	Inefficient.
# wordlist
#

=head1 NAME

cs::Hier - manipulate data hierachies

=head1 SYNOPSIS

use cs::Hier;

=head1 DESCRIPTION

This module transcribes and manipulates data structures.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

package cs::Hier;

$cs::Hier::_UseIndent=1;
$cs::Hier::PtrTags=0;
$cs::Hier::PackageTags=1;
$cs::Hier::Indent=0;
$cs::Hier::Prettier=0;

=head1 GENERAL FUNCTIONS

=over 4

=item h2a(I<ref>,I<useindent>)

Return a textual representation of a data structure references by I<ref>.
I<useindent> is an optional parameter
indicating whether to generate multiline indented text or single line text,
defaulting to 1.

=cut

sub h2a	# scalar or ref -> text version
{
  my $a = '';

  { ::need(cs::Sink);
    my($s)=new cs::Sink (SCALAR,\$a);
    h2s($s,@_);
  }

  $a;
}

=item h2s(I<sink>,I<ref>,I<useindent>)

Transcribe the data structure references by I<ref>
to the B<cs::Sink> I<sink>.
I<useindent> is an optional parameter
indicating whether to generate multiline indented text or single line text,
defaulting to 1.

=cut

sub h2s
{ my($s,$ref,$ui,$pt,$pk,$preindent)=@_;
  $preindent=$cs::Hier::Indent if ! defined $preindent;

  my($oui)=$cs::Hier::_UseIndent;
  my($opt)=$cs::Hier::PtrTags;
  my($opk)=$cs::Hier::PackageTags;
  local($cs::Hier::_UseIndent)=defined $ui ? $ui : $oui;
  local($cs::Hier::PtrTags)=defined $pt ? $pt : $opt;
  local($cs::Hier::PackageTags)=defined $pk ? $pk : $opk;

  if (! ref $ref)
  { $s->Put(_scalar2a($ref));
    return;
  }

  if ("$ref" !~ m|\(([^)]+)\)$|)
  { my(@c)=caller;
    die "$::cmd: can't parse \"$ref\" from [@c]";
  }

  my($refkey)=$1;

  if ($cs::Hier::_Active{$refkey})
  {
    ## warn "$::cmd: recursive structure at $refkey\n"
    ##	if ! $cs::Hier::PtrTags;
    $s->Put("&($refkey)");
    return;
  }

  $cs::Hier::_Active{$refkey}=1;

  my($package,$type);

  if ("$ref" =~ /^((\w|::)+)=(\w+)/)
  { $package=$1; $type=$3;
  }
  else
  { $package=''; $type=ref($ref);
  }

  my($a)='';

  { local($cs::Hier::Indent)=$preindent;

    if ($cs::Hier::PackageTags)	{ $package.='::' if length $package; }
    else		{ $package=''; }
    $package.="($refkey)" if $cs::Hier::PtrTags;
    $cs::Hier::Indent+=length($package);

    $s->Put($package);

    if ($type eq SCALAR)	{ $s->Put("\\"._scalar2a($$ref)); }
    elsif ($type eq ARRAY)	{ _array2s($s,$ref); }
    elsif ($type eq HASH)	{ _hash2s($s,$ref); }
    elsif ($type eq REF)	{ $s->Put("\\");
				  h2s($s,$$ref);
				}
    else
    { warn "$::cmd: h2a($ref): can't convert references of type $type"
	if $type ne CODE && $type ne GLOB;
      $s->Put("\\"._scalar2a("UNCONVERTABLE: $ref"));
    }
  }

  $cs::Hier::_Active{$refkey}=0;
}

=item pushCHANGELOG(I<hashref>,I<logline>[,I<fieldname>])

Prepend the supplied I<logline> string to the B<CHANGELOG> field of the hash
referenced by I<hashref>.
The optional argument I<fieldname> may be supplied to override the default
"B<CHANGELOG>" field name.

=cut

sub pushCHANGELOG($$;$)
{ my($h,$log,$f)=@_;
  $f=CHANGELOG if ! defined $f;

  my @pw = getpwuid($>);
  my $mylogin=$pw[0];
  if (! defined $mylogin)
  { warn "$::cmd: can't look up login for euid == $>";
    $mylogin="??(euid=$>)";
  }
  my @tm = localtime(time);
  my $ochlog = $h->{$f};
  $h->{$f}=sprintf("%04d-%02d-%02d, %s: %s\n", $tm[5]+1900, $tm[4]+1, $tm[3], $mylogin, $log).$ochlog;
}

=item setField(I<hashref>,I<field>,I<newvalue>[,I<changelogfield>])

Set the specified I<field>
of the hash referenced by I<hashref>
to the new value I<newvalue>.
If the field is not the changelog
and the old value and the new value differ,
prepend a changelog entry to the field "B<CHANGELOG>".
The optional argument I<fieldname> may be supplied to override the default
"B<CHANGELOG>" field name.

=cut

sub setField($$$;$)
{ my($h,$f,$v,$chf)=@_;
  $chf=CHANGELOG if ! defined $chf;

  my $ov;
  my $doch = ($f ne $chf && ($ov=$h->{$f}) ne $v);

  $h->{$f}=$v;

  if ($doch)
  {
    my $hv = h2a($v,0);
    my $hov = h2a($ov,0);
    my $log = "$f: $hov -> $hv";
    ##warn "setField: hashref=".cs::Hier::h2a($h,0);
    ##warn "pushCHANGELOG(..,$log,chf=$chf)";
    pushCHANGELOG($h,$log,$chf);
  }
}

=item getKVLine(I<source>,I<noparse>)

Read a single key-value entry from the B<cs::Source> I<source>,
returning a reference to an array of (I<key>, I<value>).
I<noparse> indicates whether to defer parsing the I<value>,
defaulting to 0.
If I<noparse> is set
then the textual representation of the value is returned as I<value>,
otherwise the line is parsed and the actual value returned.
If I<trace> is supplied, lines are printed to stderr as they are read;
I<trace> defaults to 0.
On EOF or error
returns the string B<EOF> or B<ERROR>, respectively,
instead of the arrayref.

=cut

sub getKVLine($;$$)
{ my($s,$noParse,$trace)=@_;
  $noParse=0 if ! defined $noParse;
  $trace=0 if ! defined $trace;

  local($_);

  KVLINE:
  while (1)
  {
    return EOF if ! defined ($_=$s->GetContLine()) || ! length;

    chomp;
    warn "[$_]" if $trace;

    if (! /^("(\\.|[^\\"])*"|[^"\s]\S*)\s+(\S)/)
    { warn "$::cmd: bad data \"$_\"";
      return ERROR;
    }

    next KVLINE if /^\S*$/;

    my($key,$text)=($1,$3.$');
    ## warn "$::cmd: getKVLine: key=$key, text=[$text]";

    $key=a2h($key);
    if (! $noParse)
    { my $unparsed;
      ($text,$unparsed)=a2h($text);
      $unparsed =~ s/[ \t\r\n]+$//;
      if (length $unparsed)
      { warn "$::cmd: key \"$key\": unparsed data: $unparsed\n";
      }
    }

    return [ $key, $text ];
  }
}

=item putKVLine(I<sink>,I<key>,I<value>)

Transcribe I<key> and I<value> to the B<cs::Sink>
as a multiline indented transcription.

=cut

sub putKVLine
{ my($s,$key,$value)=@_;

  $s->Put(sprintf("%-15s ", _scalar2a($key)));
  h2s($s,$value,1,0,0,16);
  $s->Put("\n");
}

sub _sep(;$)
{ my($sepch)=@_;
  $sepch=',' if ! defined $sepch;
  $cs::Hier::_UseIndent ? "$sepch\n".(' ' x $cs::Hier::Indent) : "$sepch ";
}

sub _scalar2a
{ local($_)=@_;

  if (! defined || ! length)
  { return '""';
  }
  elsif (/[^-+_\w.\/\@]/)
  { s/["\\]/\\$&/g;
    s/[^\n\040-\176]/sprintf("\\x%02x",ord($&))/eg;
    if ($cs::Hier::Prettier)
    { s/\s+$//;
      s/\n/_sep('').' '/eg;
      s/\r//g;
    }
    else
    { s/\n/\\n/g;
      s/\r/\\r/g;
    }
    s/\t/\\t/g;
    return "\"$_\"";
  }
  else
  { return $_;
  }
}

sub _array2s
{ my($s,$a)=@_;
  local($cs::Hier::Indent)=$cs::Hier::Indent+1;

  $s->Put('[');

  if (@$a)
  { my(@a)=@$a;
    h2s($s,shift(@a));
    for my $el (@a)
    { $s->Put(_sep());
      h2s($s,$el);
    }
  }

  $s->Put(']');
}

sub _hash2s
{ my($s,$h)=@_;
  my(@entries,$key);

  local($cs::Hier::Indent)=$cs::Hier::Indent+1;

  $s->Put('{');

  my(@k)=sort keys %$h;
  ## warn "HIER: keys=[@k]";
  if (@k)
	{ my($key)=shift(@k);
	  my($akey)=_scalar2a($key);

	  $s->Put("$akey => ");
	  { local($cs::Hier::Indent)=$cs::Hier::Indent+length($key)+4;
	    h2s($s,$h->{$key});
	  }

	  for $key (@k)
		{ $akey=_scalar2a($key);
		  $s->Put(_sep()."$akey => ");
		  { local($cs::Hier::Indent)
			=$cs::Hier::Indent+length($key)+4;
		    h2s($s,$h->{$key});
		  }
		}
	}

  $s->Put('}');
}

=item a2h(I<text>)

Convert the I<text> to a reference to a data structure.
Return the reference in a scalar context,
or an array of the reference and any unparsed text
in an array context.

=cut

sub a2h	# string -> (scalar or ref, unparsed)
{ local(%cs::Hier::_Prior);
  _a2h(@_);
}
sub _a2h
{ local($_)=@_;
  my @both;
  my($package);

  ## warn "_a2h($_)";

  # check for reference to prior object
  if (/^&\(([^)+])\)/)
  { @both=($cs::Hier::_Prior{$1},$');
  }
  else
  {
    # grab leading package:: prefix for blessed things
    if (/^(\w+(::\w+)*)::\s*/)
    { $package=$1; $_=$';
    }

    my($refkey);

    if (/^\(([^)]+)\)/)
    { $refkey=$1; $_=$';
    }

    if (/^\{/)		{ @both=_a2hash($_); }
    elsif (/^\[/)	{ @both=_a2array($_); }
    elsif (/^\\\s*/)	{ $_=$';
			  @both=_a2h($_); 
			  my($dummy)=$both[0];
			  $both[0]=\$dummy;
			}
    else		{ @both=_a2scalar($_); }

    # note reference for later
    if (defined $refkey && ref $both[0])
    { $cs::Hier::_Prior{$refkey}=$both[0];
    }

    # bless reference if necessary
    if (defined $package && ref $both[0])
    { bless $both[0], $package;
    }
  }
  
  ## warn "$::cmd: both=".cs::Hier::h2a(\@both,0)."\n";

  wantarray ? @both : shift @both;
}

sub _a2scalar	# string -> (value,unparsed)
{ local($_)=@_;
  my($sofar,$unparsed,$match);

  ## warn "$::cmd: _a2scalar($_)<BR>\n";

  if (/^"(([^"\\]|\\.)*)"/)
  { $unparsed=$';
    $_=$1;
    $sofar='';

    while (/\\(x..|u....|[^ux])/)
    { $sofar.=$`;
      $match=$1;
      $_=$';

      if ($match eq '0')	{ $match="\0"; }
      elsif ($match eq 'n')	{ $match="\n"; }
      elsif ($match eq 'r')	{ $match="\r"; }
      elsif ($match eq 't')	{ $match="\t"; }
      elsif ($match =~ /^[ux]/)	{ $match=chr(hex($match)); }
      else			{ }

      $sofar.=$match;
    }

    $sofar.=$_;
  }
  else
  {
    ## warn "$::cmd: bareword at [$_]<BR>\n";

    if (/^[-+\/\w\.\@_]+/)
    {
      $sofar=$&;
      $unparsed=$';
    }
    else
    { $unparsed=$_;
    }
  }

  ## warn "$::cmd: sofar=[$sofar], unparsed=[$unparsed]<BR>\n";

  ($sofar,$unparsed);
}

sub _a2array	# string -> (\@array,unparsed)
{ local($_)=@_;

  return undef unless /^\[\s*/;
  $_=$';

  my($a)=[];
  my($el,$tail);

  while (length && ! /^\]/)
  { ($el,$tail)=&_a2h($_);
    if (! defined $el)
    { warn "$::cmd: _=[$_]\nsyntax error, returning undef from _a2array";
      return undef;
    }

    push(@$a,$el);
    $_=$tail;
    s/^\s*,?\s*//;
  }

  if (/^\]/)
  { $_=$';
  }

  ($a,$_);
}

sub _a2hash	# string -> (\%hash,unparsed)
{ local($_)=@_;

  ## warn "$::cmd: _a2hash($_)<BR>\n";

  if (! /^\{\s*/)
	{ warn "$::cmd: _a2hash($_): missing \"{\"";
	  return undef;
	}

  $_=$';
  
  ## warn "$::cmd: ready at [$_]<BR>\n";

  my($h)={};
  my($key,$value,$tail);

  ENTRY:
    while (length && ! /^\}/)
	{ ($key,$tail)=_a2scalar($_);
	  last ENTRY if ! defined $key;
	  $tail =~ /^\s*=>?\s*/ || return undef;	# => or =
	  $tail=$';
	  ($value,$tail)=_a2h($tail);
	  last ENTRY if ! defined $value;

	  ## warn "$::cmd: matched [$key]=[$value]<BR>\n";

	  $h->{$key}=$value;

	  $_=$tail;
	  s/^\s*,?\s*//;
	}

  if (/^\}/)
	{ $_=$';
	}

  ($h,$_);
}

##=item wordlist(I<something>)
##
##DEPRECIATED.
##Convert I<something> into a reference to an array of strings.
##If I<something> is an arrayref, leave unchanged.
##If I<something> is a string, split on whitespace.
##Otherwise convert to text with B<h2a> and return.
##I can't imagine what I wrote this for :-(
##
##=cut
##
##sub wordlist
##{ local($_)=@_;
##  
##  my $r;
##
##  if (ref eq ARRAY)
##  { $r=$_;
##  }
##  elsif (! ref)
##  { $r=[ grep(length,split(/\s+/,$_)) ];
##  }
##  else
##  { warn "$::cmd: Hier::wordlist($_): can't convert to words\n";
##    $r=[ &h2a($_) ];
##  }
##
##  ## warn "$::cmd: wordlist(",&h2a($_),") => ",&h2a($r),"\n";
##
##  $r;
##}

##sub load
##{ my($fname,$silent)=@_;
##  my($h,$junk);
##  my($s);
##
##  ::need(cs::Source);
##
##  if (ref $fname)
##	{ $s=$fname;
##	}
##  elsif (! defined ($s=new cs::Source PATH, $fname))
##	{ $silent
##	    || ! -e $fname
##	    || warn "$::cmd: can't open $fname for read: $!\n";
##	  return undef;
##	}
##
##  ($h,$junk)=&a2h(join('',$s->GetAllLines()));
##
##  $junk =~ s/^\s+//;
##  ($silent || length($junk) == 0) || warn "$::cmd: unparsed stuff from $fname: \"$junk\"\n";
##
##  $h;
##}
##
##sub save
##{ my($fname,$h,$silent)=@_;
##  my($s);
##
##  ::need(cs::Sink);
##
##  if (ref $fname)
##  { $s=$fname;
##  }
##  elsif (! defined ($s=new cs::Sink PATH, $fname))
##  { $silent || warn "$::cmd: can't open $fname for write: $!\n";
##    return undef;
##  }
##
##  $s->Put(h2a($h));
##  $s->Put("\n") if $cs::Hier::_UseIndent;
##}

=item loadhash(I<source>)

Load a hash from a B<cs::Source> I<source>.
If I<source> is not an object reference
consider it a filename and open it.
Return a hashref.

=cut

sub loadhash($)
{ my($fname)=@_;
  
  my $s;

  if (! ref $fname)
  { ::need(cs::Source);
    $s=cs::Source->new(PATH,$fname);
    if (! defined $s)
    { warn "$::cmd: open($fname): $!";
      return undef;
    }
  }
  else
  { $s=$fname;
  }

  my $hash = {};
  local($_);
  my($n);

  my $a;

  HASHLINE:
    while (defined ($a=getKVLine($s)))
    {
      if (! ref $a)
      { warn "$::cmd: getKVLine returns \"$a\"" if $a ne EOF;
	last HASHLINE;
      }

      if (! exists $hash->{$a->[0]})
      { $hash->{$a->[0]}=$a->[1];
      }
      else
      { $hash->{$a->[0]}=overlayrefs($hash->{$a->[0]},$a->[1]);
      }
    }

  $hash;
}

sub overlayrefs($$)
{ my($av,$bv)=@_;

  $av = [$av] if ! ref $av;
  $bv = [$bv] if ! ref $bv;

  my($at,$bt)=(::reftype($av),::reftype($bv));
  if ($at ne $bt)
  { warn "$::cmd: type mismatch between $av and $bv";
  }
  elsif ($at eq ARRAY)
  { $av=overlayARRAY($av,$bv);
  }
  elsif ($at eq HASH)
  { $av=overlayHASH($av,$bv);
  }
  else
  { warn "$::cmd: unsupported ref type \"$at\", keeping first\n";
  }

  return $av;
}

sub overlayARRAY($$)
{ my($a,$b)=@_;
  push(@$a,@$b);
  return $a;
}

sub overlayHASH($$)
{ my($a,$b)=@_;

  for my $k (keys %$b)
  { if (! exists $a->{$k})
    { $a->{$k}=$b->{$k};
    }
    else
    { $a->{$k}=overlayrefs($a->{$k},$b->{$k});
    }
  }

  return $a;
}

=item savehash(I<sink>,I<hashref>)

Write the I<hashref> to the B<cs::Sink> I<sink>.
if I<sink> is not an object reference
consider it a filename and try to open it.
Returns B<undef> on error
or the number of keys written (which may be zero) on success.

=cut

sub savehash($$)
{ my($fname,$hash)=@_;
  
  my $s;

  if (! ref $fname)
  { ::need(cs::Sink);
    $s = cs::Sink->new(PATH,$fname);
    if (! defined $s)
    { warn "$::cmd: open($fname): $!";
      return undef;
    }
  }
  else
  { $s=$fname;
  }

  my(@keys)=sort keys %$hash;

  for (@keys)
  { putKVLine($s,$_,$hash->{$_});
  }

  scalar(@keys);
}

=item edithash(I<hashref>,I<editor>)

Transcribe the hash to a file, run an editor, return the modified hash.

=cut

sub edithash($;$)
{ my($h,$editor)=@_;
  $editor = cs::Misc::editor() if ! defined $editor;

  my $tmp;

  { my $s;

    ::need(cs::Sink);
    $s = cs::Sink::tmpSink();
    return undef if ! defined $s;

    savehash($s,$h);

    $tmp=$s->Path();
  }

  system("$editor $tmp");

  $h=loadhash($tmp);

  # clumsy catch for disc full :-)
  return undef if ! keys %$h;

  unlink($tmp) || warn "$::cmd: warning: can't unlink $tmp: $!\n";

  $h;
}

=item cleanhash(I<hashref>)

Recursively remove empty hashes and arrays from a data structure.

=cut

sub cleanhash
{ my($h,$recurse)=@_;
  $recurse=0 if ! defined $recurse;

  my($f,$v,@k);

  # clean empty fields
  KEY:
    for (keys %$h)
    { next KEY if ! defined $h->{$_}
	       || ref($f=$h->{$_}) ne HASH;

      for (keys %$f)
      { if (! defined $f->{$_})
	{ delete $f->{$_};
	}
	else
	{ $v=$f->{$_};
	  if (ref($v) eq ARRAY && @$v == 0)
	  { delete $f->{$_};
	  }
	  elsif (ref($v) eq HASH)
	  { if ($recurse)
	    { cleanhash($v,$recurse);
	    }

	    @k=keys %$v;
	    if (@k == 0)
	    { delete $f->{$_};
	    }
	  }
	  elsif (! ref($v) && ! length $v)
	  { delete $f->{$_};
	  }
	}
      }
    }
}

sub emaciate
{ my($var)=@_;

  if (::reftype($var) eq HASH)
  {
    for (keys %$var)
    { if (! ref $var->{$_})
      { delete $var->{$_} if ! defined $var->{$_};
      }
      else
      { emaciate($var->{$_});
	my $t = reftype($var->{$_});
	if ($t eq ARRAY)
	{ delete $var->{$_} if ! @{$var->{$_}};
	}
      }
    }
  }
}

sub reftype { ::reftype(@_); }

=item hcmp(I<value1>,I<value2>)

Compare two data structures for equality by value.
Returns 0 if they are identical and a signed value
to indicate difference
(for use with the B<sort> operator).

=cut

# compare two structures - return 0 for match
# some hacks to "compare" different types - a least a consistent ordering
# is returned, which should render sorts stable
# caveat - no loops!
sub hcmp
{ my($s1,$s2)=@_;
  my($cmp);

  if (! ref $s1)
    { if (! ref $s2)
	# neither is a ref - fall through to native comparison
	{
	  $cmp=$s1 cmp $s2;
	  return $cmp;
	}
      else
      # s2 a ref, s1 not a ref
      {
	return 1;	# scalars < references
      }
    }
  elsif (! ref $s2)
    { # warn "$::cmd: ref/nonref($s2)";
      return -1;	# references > scalars
    }
  elsif ("$s1" eq "$s2")
	# common refs - match ok!
	{ return 0;
	}
  else
  # distinct refs - fall through
  {}

  my($rt1,$rt2)=(reftype($s1),reftype($s2));

  # different types - order on type
  $cmp=( $rt1 cmp $rt2 );
  # warn "$::cmd: cmp [$rt1],[$rt2] gives undef!" if ! defined $cmp;

  if ($cmp != 0)
	{ # warn "$::cmd: $rt1/$s1 cmp $rt2/$s2";
	  return $cmp;
	}

  if ($rt1 eq SCALAR)
	{
	  $cmp=$$s1 cmp $$s2;
	# warn "$::cmd: terminal2: $$s1 cmp $$s2";
	  return $cmp;
	}

  if ($rt1 eq ARRAY)
    # unusual result - short arrays order before long arrays
    # regardless of array contents
    { $cmp = @$s1 <=> @$s2;
      # warn "$::cmd: <=> lengths [@$s1],[@$s2] gives undef!" if ! defined $cmp;

      if ($cmp != 0)
	{ # warn "$::cmd: differing array lengths";
	  return $cmp;
	}

      my($i);

      for $i ($[..$#$s1)
	{ $cmp=hcmp($s1->[$i],$s2->[$i]);
	# warn "$::cmd: hcmp($s1 sub $i,$s2 sub $i) gives undef!" if ! defined $cmp;

	  if ($cmp != 0)
		{ # warn "$::cmd: element mismatch($s1->[$i],$s2->[$i])";
		  return $cmp;
		}
	}

      return 0;
    }

  # sanity check
  die "$::cmd: can't deal with type $rt1" if $rt1 ne HASH;

  my(@k1)=sort keys %$s1;
  my(@k2)=sort keys %$s2;

  # smaller hashes < larger hashes
  $cmp=(@k1 <=> @k2);
  # warn "$::cmd: <=> lengths [@k1],[@k2] gives undef!" if ! defined $cmp;

  if ($cmp != 0)
	{ # warn "$::cmd: differing hash sizes";
	  return $cmp;
	}

  # compare keys of hashes first
  $cmp=hcmp(\@k1,\@k2);
  # warn "$::cmd: hcmp [@k1],[@k2] gives undef!" if ! defined $cmp;
  if ($cmp != 0)
	{ # warn "$::cmd: differing hash key sets [@k1] vs [@k2]";
	  return $cmp;
	}

  my($key);

  # ok, try values
  for $key (@k1)
	{
	  $cmp=hcmp($s1->{$key},$s2->{$key});
	# warn "$::cmd: hcmp $s1 key $key,$s2 key $key gives undef!" if ! defined $cmp;
	  if ($cmp != 0)
		{ # warn "$::cmd: differing key elements for key=[$key]";
		  return $cmp;
		}
	}

  # identical things!
  return 0;
}

=item hdup(I<value>)

Return a complete duplicate of some I<value>.
Note that any class B<bless>ing is lost.

=cut

# duplicate a hierachy - note: the dup is unblessed
sub hdup($);
sub hdup($)
{ my($r1)=@_;

  # simple case
  return $r1 if ! ref $r1;

  my($type)=reftype($r1);

  # new object
  my($r2);

  if ($type eq SCALAR)
  { my($v)=$$r1;

    # I hope this does the right thing
    $r2=\$v;
  }
  elsif ($type eq ARRAY)
  { $r2=[];
    for (@$r1)
    { push(@$r2,hdup($_));
    }
  }
  elsif ($type eq HASH)
  { $r2={};
    for (keys %$r1)
    { $r2->{$_}=hdup($r1->{$_});
    }
  }
  else
  { die "$::cmd: can't dup objects of type \"$type\"";
  }

  $r2;
}

=item diff(I<old>,I<new>)

Compare two hashrefs at the top level and return a hashref
loosely describing the differences.
The resulting hashref has
B<undef> for fields which were in I<old> and are not in I<new>
and the value from I<new> for fields which are in I<new> and not in I<old>
or in I<new> and differ from the value in I<old>.

=cut

# summarise changes between two records
sub diff
{ my($old,$new)=@_;
  
  my $diff = {};

  # find deletions
  for (keys %$old)
  { $diff->{$_}=undef if ! exists $new->{$_};
  }

  # find additions or changes
  for (keys %$new)
  { $diff->{$_}=$new->{$_}
	  if ! exists $old->{$_}
	  || hcmp($old->{$_},$new->{$_}) != 0;
  }

  $diff;
}

=item apply(I<old>,I<diff>,I<dodel>,I<subkeys>)

Apply the changes returned from B<diff> above
to the record I<old>.

=cut

# apply changes to a record
sub apply($$;$$)
{ my($this,$diff,$dodel,$subkeys)=@_;
  $dodel=1 if ! defined $dodel;
  $subkeys=0 if ! defined $subkeys;

  KEY:
    for (keys %$diff)
    {
      if (defined $diff->{$_} || $dodel)
      { setSubKey($this,$diff->{$_},
		  ($subkeys ? split(m:/:) : $_));
      }
    }
}

# get value stored at end of keychain
sub getSubKey
{ my($p,@k)=@_;

  ## warn "$::cmd: getSubKey(".cs::Hier::h2a($p,0).",[@k])<BR>\n";

  local($_);

  for (@k)
  { 
    if (! exists $p->{$_}
     || ! defined $p->{$_})
    { return undef;
    }

    $p=$p->{$_}
  }

  $p;
}

# store value at end of keychain (undef ==> delete)
sub setSubKey
{ my($p,$value,@k)=@_;

  if (@k < 1)
  { warn "$::cmd: ignoring empty keychain";
    return;
  }

  my($key);

  while (@k > 1)
  { $key=shift(@k);
    if (! exists $p->{$key}
     || ! defined $p->{$key}
     || reftype $p->{$key} ne HASH)
    { $p->{$key}={};
    }

    $p=$p->{$key}
  }

  $key=shift(@k);

  if (defined $value)
	{ $p->{$key}=$value;
	}
  else	{ delete $p->{$key};
	}
}

sub fleshOut
{ my($tplt,$var)=@_;

  die "$::cmd: tplt should be a hash ref [$tplt]" if ::reftype($tplt) ne HASH;
  die "$::cmd: var should be a hash ref [$var]" if ::reftype($var) ne HASH;

  my($t);

  for (keys %$tplt)
  { $t=::reftype($tplt->{$_});

    if (! defined $t)
    # scalar
    { $var->{$_}=$tplt->{$_} if ! exists $var->{$_};
    }
    elsif ($t eq HASH)
    # {}
    { $var->{$_}={} if ! exists $var->{$_};
      if (::reftype($var->{$_}) eq HASH)
      { fleshOut($tplt->{$_},$var->{$_});
      }
      else
      {
      }
    }
    elsif ($t eq ARRAY)
	  # []
    { if (! exists $var->{$_})
      { $var->{$_}=[];
      }
      elsif (::reftype($var->{$_}) eq ARRAY)
      {
      }
      else
      { $var->{$_}=[$var->{$_}];
      }
    }
  }
}

=back

=head1 SEE ALSO

B<cs::Source>(3),
B<cs::Sink>(3)

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;

#!/usr/bin/perl
#
# Mail dispatch program, since I no longer use Rourke mail for this.
#	- Cameron Simpson, April 1992
#
# Rewritten in perl, added .mailrc. - Cameron, 12sep92
# Got aliases working properly, added header rewrite code. - Cameron, 18oct92
# Added the Dumb PreProcessor code for header rewrites. - Cameron, 02nov93
# Dubious mailto: handling. - Cameron, 13jan99
#

use strict qw(vars);

require 'cs/tty.pl';
require 'cs/dpp.pl';
require 'flush.pl';
require 'open2.pl';

use cs::Misc;
use cs::RFC822;
use cs::Env;
use cs::NNTP;
use cs::MIME;
use cs::CacheSource;
use cs::Shell;
use cs::Mail::Misc;
use cs::Mail::Rc;

($::cmd=$0) =~ s:.*/::;

$::AnonHost='anon.penet.fi';
$::AnonAddr="anon\@$::AnonHost";
$::AnonPasswd="$ENV{HOME}/private/anon.passwd";

@::FileOut=('+out');

$::usage="Usage: $::cmd [-{d|D} hdrname] [-e] [-h] [-H file] [+h header] [+H header] \\
		[-n newsgroups] [-N] [-q] [-s subject] [-S] [--] \\
		[addresses|newsgroups]...
	-A		Anonymous.
	-a attachment	Attach file to message.
	-B		Blind: use Bcc: instead of To: for addresses.
	-d hdrname	Disable all headers called \`hdrname',
			changing them to X-Original-hdrname.
	-D hdrname	Delete all headers called \`hdrname'.
	-e		Enter editor immediately.
	-f file		Read from file instead of stdin.
	-F file		As above but delete file when finished.
	-h		Read headers from standard input.
	-H file		Read headers from file.
	+h header	Add header, replacing any existing instance.
	+H header	Add header, replacing any existing instance.
			Strings of the form {hdrname} are replaced with
			the body of that header, or the value of the
			environment variable \$hdrname if the header
			is missing.
	-i filename	Interpolate file into text.
	-m mailrc	Specify mailrc (default: \$MAILRC: $ENV{MAILRC}).
	+m mailrc	Specify additional mailrcs.
	-n newsgroups	Replace any exist newsgroups.
	-N		No mailing or posting, emit generated item to stdout.
	-p profile	Choose identity profile.
	-q		Query alias expansions for supplied addresses.
			Lists all aliases if no addresses supplied.
	-R replyto	Reply to article in file replyto.
	-s subject	Replace any existing subject.
	-S		Suppress automatic signature.
	-t		Pretend input is a tty.
	-uu file	Append file in uuencoded form at the end.
";

sub repHdr;
sub DefHdr;

# Rig environment variables.
#
cs::Env::dflt(TMPDIR,'/tmp',
	      MAILBOX,$ENV{OUTMAIL},
	      MAILRC,"$ENV{HOME}/.mailrc"
	     );

$::Tmp="$ENV{TMPDIR}/$::cmd.$$";

$::Xit=mail(@ARGV);

exit $::Xit;

sub mail
{ my(@ARGV)=@_;
  my($Xit)=0;

  # Initial settings.
  #
  my($hdrs)=new cs::RFC822;
  $hdrs->Add("X-Mailer: m, by Cameron Simpson");
  $hdrs->Add("Date: ".cs::RFC822::gm2date());

  local($::tty);
  my($do_mail,$do_post);

  my($anonymous,$autoedit,$query,$text,$gothdrs,
     $sigptn,$tmpsync,$inputfile,$replytofile, $blind,
     $destroyinputfile,$noaction,@to,@cc,@newsgroups,@references,
     @xtraMAILRCs,@uufiles,@attachments,$profile);

  local($_);
  local($::hassig,$::wantsig)=(0,1);

  $autoedit=0;
  $blind=0;
  $query=0;
  $text='';
  $gothdrs=0;
  $::hassig=length($ENV{SIGNATURE}) == 0;	# no sig ==> got it
  $::has_uufiles=0;
  $tmpsync=1;
  $::tty=(-t STDIN && -t STDOUT);
  $destroyinputfile=0;
  $noaction=0;
  @to=();
  @cc=();
  @newsgroups=();
  @references=();
  @xtraMAILRCs=();
  @uufiles=();
  @attachments=();

  $profile=$ENV{USER};
  apply_profile($profile);

  my($badopts)=0;
 ARGV:
  while (defined($_=shift(@ARGV)))
	{ if (m:^/.*/$:)	{ $sigptn=$_; }
	  elsif (!/^[-+]./)	{ unshift(@ARGV,$_); last ARGV; }
	  elsif ($_ eq '--')	{ last ARGV; }
	  elsif ($_ eq '-a')	{ push(@attachments,shift(@ARGV)); }
	  elsif ($_ eq '-A')	{ $profile='anonymous'; $::wantsig=0;
				  apply_profile($profile);
				}
	  elsif ($_ eq '-B')	{ $blind=1; }
	  elsif ($_ eq '-d')	{ $hdrs->Del(shift(@ARGV)); }
	  elsif ($_ eq '-D')	{ $hdrs->Del(shift(@ARGV),1); }
	  elsif ($_ eq '-e')	{ $autoedit=1; $::tty=1; }
	  elsif ($_ eq '-f')	{ $inputfile=openstdin(shift(@ARGV));
				  $destroyinputfile=0;
				}
	  elsif ($_ eq '-F')	{ $inputfile=openstdin(shift(@ARGV));
				  $destroyinputfile=1;
				}
	  elsif ($_ eq '-h')	{ gethdrs($hdrs,STDIN) if ! $gothdrs;
				  $gothdrs=1;
				}
	  elsif ($_ eq '-H')	{ $_=shift(@ARGV);
				  if (!open(FILE,"< $_\0"))
					{ warn "$::cmd: can't read headers from $_: $!\n";
					}
				  else
				  { gethdrs($hdrs,FILE);
				    close(FILE);
				    $gothdrs=1;
				  }
				}
	  elsif ($_ eq '+h')	{ { my($h)=shift(@ARGV);
				    my($field)=($h =~ /^([^:]*)/);
				    $hdrs->Del($field,1);
				    $hdrs->Add($h);
				  }
				}
	  elsif ($_ eq '+H')	{ { my($h)=shift(@ARGV);
				    syncdpp($hdrs);
				    $h=dpp'preproc($h);
				    my($field)=($h =~ /^([^:]*)/);
				    $hdrs->Del($field,1);
				    $hdrs->Add($h);
				  }
				}
	  elsif ($_ eq '-I')	{ $text.=dpp'preproc(shift(@ARGV))."\n";
				}
	  elsif ($_ eq '-i')	{ $text.=interp(shift(@ARGV)); }
	  elsif ($_ eq '-m')	{ $ENV{MAILRC}=shift(@ARGV); }
	  elsif ($_ eq '+m')	{ push(@xtraMAILRCs,shift(@ARGV)); }
	  elsif ($_ eq '-n')	{ $_=shift(@ARGV);
				  s/\n(\S)/\n\t$1/g;
				  push(@newsgroups,$_);
				  $do_post=1;
				}
	  elsif ($_ eq '-N')	{ $noaction=1; }
	  elsif ($_ eq '-p')	{ $profile=shift(@ARGV);
				  apply_profile($profile);
				}
	  elsif ($_ eq '-q')	{ $query=1; }
	  elsif ($_ eq '-R')	{ push(@::FileOut,'+replied');

				  $replytofile=shift(@ARGV);
				  if (!open(REPLYTOFILE,"< $replytofile\0"))
					{ warn "$::cmd: can't read $replytofile: $!\n";
					}
				  else
				  { my(@replytolines)=<REPLYTOFILE>;
				    close(REPLYTOFILE);

				    # trim trailing blank lines
				    while (@replytolines
					&& $replytolines[$#replytolines]
					     =~ /^\s*$/)
					{ pop(@replytolines);
					}

				    { my $arthdrs = new cs::RFC822;

				      # suck out the headers
				      $arthdrs->ArrayExtract(@replytolines);
				      # make a reply header set
				      my $rephdrs = $arthdrs->Reply(ALL,$ENV{EMAIL});

				      # apply to the live headers
				      for ($rephdrs->HdrNames())
					{ $hdrs->Add($_,
						      $rephdrs->Hdr($_),
						     SUPERCEDE);
					}

				      # attribution
				      my $attr="";

				      # Date attribution
				      { my $date = $arthdrs->Hdr(DATE);
					if (defined $date)
					{ $date=cs::Date::txt2gm($date);
					}

					if (defined $date)
					{ $attr.="\n  " if length $attr;
					  $attr="On ".cs::Date::humanDate($date,1);
					}
				      }

				      { my $msgid = $arthdrs->Hdr(MESSAGE_ID);
					if (length $msgid)
					{ $attr.=", " if length $attr;
					  $attr.="in message $msgid";
					}
				      }

				      $attr.="\n  " if length $attr;
				      $attr.=$arthdrs->Hdr(FROM)." wrote:";

				      $text.="$attr\n";

				      for (@replytolines)
					{ $text.="$ENV{PFX}$_";
					}

				      $text.="\n";
				    }
				  }
				}
	  elsif ($_ eq '-s')	{ $_=shift(@ARGV);
				  s/\n(\S)/\n\t$1/g;
				  repHdr($hdrs,Subject,$_);
				}
	  elsif ($_ eq '-S')	{ $::hassig=1; }
	  elsif ($_ eq '-t')	{ $::tty=1; }
	  elsif ($_ eq '-uu')	{ push(@uufiles,shift(@ARGV)); }
	  else
	  { warn "$::cmd: $_: unrecognised option\n";
	    $badopts=1;
	  }
	}

die $::usage if $badopts;

my(@MAILRCs)=($ENV{MAILRC},@xtraMAILRCs);
my($rc)=new cs::Mail::Rc;

if ($query)
	{ loadmailrcs($rc,@MAILRCs);

	  if (! @ARGV)
		{ for (sort $rc->Aliases())
			{ print "$_ -> ", $rc->Alias($_), "\n";
			}
		}
	  else
	  {
	    for (@ARGV)
		{ print "$_ -> " if $::tty;
		  print $rc->ExpandAliasText($_,$::tty ? ', ' : ' '),
			"\n";
		}
	  }

	  exit 0;
	}

if (defined($::tty))
	{}
else	{ $::tty = -t STDIN; }

# Intuit nature of article if no clues yet.
if (!defined($do_mail) && !defined($do_post))
	{ $do_post=($::cmd =~ /^post/i);
	  $do_mail=!$do_post;
	}

if (@ARGV)
{ for (@ARGV)
	{ s/^mailto://i; }

  if ($do_mail)	{ push(@to,@ARGV); }
  else		{ push(@newsgroups,@ARGV); }
}

if (@to)		{ repHdr($hdrs,($blind ? "BCC" : "To"),
					join(",\n\t",@to),
					$blind); }
if (@cc)		{ repHdr($hdrs,CC,join(",\n\t",@cc)); }
if (@newsgroups)	{ repHdr($hdrs,Newsgroups,join(',',@newsgroups)); }
if (@references)	{ repHdr($hdrs,References,join("\n\t",@references)); }

my(@need_hdrs)=();

if ($do_mail)	{ push(@need_hdrs,'to'); }
push(@need_hdrs,'subject');
# if ($do_mail)	{ push(@need_hdrs,'cc'); }
if ($do_post)	{ push(@need_hdrs,'newsgroups'); }

if (! $gothdrs)
{ DefHdr($hdrs,From,"$ENV{NAME} <$ENV{EMAIL}>");

  for (Reply_To,Errors_To)	## Return_Receipt_To
  { DefHdr($hdrs,$_,$ENV{REPLY_TO});
  }

  if (length $ENV{ORGANIZATION})
  { DefHdr($hdrs,Organization,$ENV{ORGANIZATION});
  }

}

if ($::tty)
{ need_hdrs($hdrs,@need_hdrs);
  print "\n";
}

$Xit=0;

&onint('abort');

if ($autoedit)		{ dotcmd('.e',\$hdrs,\$text,$sigptn); }

INPUT:
while (<STDIN>)
{ if ($::tty && /^\./)	{ chop;
			  last INPUT if $_ eq '.';
			  dotcmd($_,\$hdrs,\$text,$sigptn);
			}
  else			{ $text.=$_; }
}

loadmailrcs($rc,@MAILRCs);

# make a message-id if missing
$hdrs->ForceMsgID;

# add signature if missing
addsig(\$text,$sigptn);
adduufiles(\$text,@uufiles);
addattachments($hdrs,$text,@attachments);

if ($noaction)
{ $hdrs->WriteItem(STDOUT,$text);
  exit($Xit);
}

# extract addresses
my($origTo,$To,$origCC,$CC,$origBcc,$Bcc);
$origTo=$hdrs->Hdr(To);   $To=(defined $origTo
				? $rc->ExpandAliasText($origTo)
				: '');
$origCC=$hdrs->Hdr(CC);   $CC=(defined $origCC
				? $rc->ExpandAliasText($origCC)
				: '');
$origBcc=$hdrs->Hdr(BCC); $Bcc=(defined $origBcc
				? $rc->ExpandAliasText($origBcc)
				: '');

repHdr($hdrs,TO,$To,1);
if (length $CC)	{ repHdr($hdrs,CC,$CC,1); }
else		{ $hdrs->Del(CC); }
if (length $Bcc){ repHdr($hdrs,BCC,$Bcc,1); }
else		{ $hdrs->Del(BCC); }

my($addrs)=cs::RFC822::addrSet(join(', ',grep(length,$To,$CC,$Bcc)));

# add in newsgroups
my($ng)=$hdrs->Hdr(NewsGroups);

if (defined $ng)
{ for (grep(length,split(/[\s,]+/,$ng)))
  { $addrs->{"$_\@USENET"}="$_\@USENET";
  }
}

$::tty && warn "filing ...\n";
for my $folder (@::FileOut)
{ file($hdrs,$text,$folder);
}

# hide this before sending
$hdrs->Del(BCC);

# divide up the addresses
my(%anon,%news,%fax,$stashed);

for (keys %$addrs)
{ $stashed=1;
  if (/^an\d+\@$::AnonHost$/oi)	{ &stashaddr(\%anon,$_,$addrs->{$_}); }
  if (/\@anon$/oi)	{ &stashaddr(\%anon,$`,$`); }
  elsif (/\@usenet$/i)	{ &stashaddr(\%news,$`,$`); }
  elsif (/\@fax$/i)	{ &stashaddr(\%fax,$`,$addrs->{$_}); }
  elsif ($anonymous)	{ &stashaddr(\%anon,$_,$addrs->{$_}); }
  else
  { $stashed=0; }

  delete $addrs->{$_} if $stashed;
}

via('mail',$hdrs,$text,$addrs) || ($Xit=1);
via('anon',$hdrs,$text,\%anon) || ($Xit=1);
via('news',$hdrs,$text,\%news) || ($Xit=1);
via('fax',$hdrs,$text,\%fax)   || ($Xit=1);

unlink($::Tmp);

if ($Xit == 0)
{ if ($destroyinputfile && !unlink($inputfile))
  { warn "$::cmd: unlink($inputfile): $!\n";
    $Xit=1;
  }
}
else
{ if ($destroyinputfile)
  { warn "$::cmd: warning: $inputfile not removed\n";
    $Xit=1;
  }
}

  return $Xit;
}

########################################################################

# add contents of file to $text
sub interp	# (filename) -> ok
{ my($interp)=@_;

  if (!open(INTERP,"< $interp\0"))
  { warn "$::cmd: can't open $interp: $!\n";
    return undef;
  }

  my($text)='';
  while (defined($_=<INTERP>))
  { $text.=$ENV{PFX}.$_;
  }

  $text."\n";
}

sub DefHdr	# (hdr,body) -> void
{ my($this,$hdr,$body)=@_;
  local($_);

  if (! defined ($_=$this->Hdr($hdr)))
  { $this->Add(cs::RFC822::norm($hdr).': '.$body);
  }
}

sub need_hdrs	# (hdrs,@header-names) -> void
{ my($H)=shift;
  local($_);

  for my $hdr (@_)
  { my $Hdr = cs::RFC822::norm($hdr);
    if (defined($_=$H->Hdr($hdr)))
    { print $Hdr, ': ', $_, "\n";
    }
    else
    { defined($_=&prompt($Hdr.': ')) || die "EOF reached\n";
      s/\s+$//;
      if (length)
      { $H->Add($Hdr.': '.$_);
      }
    }
  }
}

sub prompt
{ local($_);
  printflush(STDOUT,@_);
  return undef if ! defined ($_=<STDIN>);
  $_;
}

sub dotcmd
{
  local($_)=shift;
  my($phdrs,$ptext,$sigptn)=@_;

  s/^\.\s*//;
  if (!/^(\w+)\s*/)
  { warn "bad dot command syntax\n"
	."	.e	Editor message.\n";
  }
  else
  { my($op)=$1;
    $_=$';
    my(@args)=grep(length,split(/\s+/));

    if ($op eq 'e')
    { addsig($ptext,$sigptn);
      totmp($::Tmp,$$phdrs,$$ptext);
      system($ENV{EDITOR},'+$',$::Tmp);
      ($$phdrs,$$ptext)=fromtmp($::Tmp);
      print "(continue)\n";
    }
    elsif ($op eq 'i')
    { for my $interp (@args)
      { $$ptext.=interp($interp);
	print "Interpolated $interp\n";
      }
    }
    else
    { warn ".$op: unknown dot command\n",
		   "	.e		Edit message.\n";
		   "	.i files	Interpolate files.\n";
    }
  }
}

sub fromtmp	# (tmpfile) -> (hdrs,text)
{
  my($tmpfile)=@_;
  my($ok)=0;

  my($s)=new cs::Source PATH, $tmpfile;

  if (! defined $s)
  { print STDERR
	  ("$::cmd: can't reopen $tmpfile, state remains as before\n");
    return 0;
  }

  my($hdrs)=new cs::RFC822;
  my($text)='';

  $hdrs->SourceExtract($s);
  $text=join('',$s->GetAllLines());

  ($hdrs,$text);
}

sub totmp	# (mode,tmpfile) -> ok
{ my($tmpfile,$hdrs,$text,$append)=@_;
  $append=0 if ! defined $append;

  my($TMP)=(new cs::Sink(($append ? APPEND : PATH), $tmpfile));

  if (! defined $TMP)
  { err("$::cmd: can't open($tmpfile): $!\n");
    return 0;
  }

  $hdrs->WriteItem($TMP,$text);

  1;
}

sub abort
{ my($sig)=shift;

  $::Xit=1;

  if ($sig)	{ warn "$::cmd: SIG$sig, aborting\n"; }
  if (@_)	{ warn "$::cmd: ".join('',@_)."\n"; }

  my($H,$text)=fromtmp($::Tmp);
  if (! $::inAbort)
  { local($::inAbort)=1;

    file($H,$text,'+dead-letter') if ref $H;
  }
  elsif (! totmp($ENV{DEADMAIL},$H,$text,1))
  { err("$::cmd: can't append to $ENV{DEADMAIL} ($!), suppressing cleanup\n");
    err("$::cmd: original still in $::Tmp\n");
    undef $::Tmp;
  }
  else
  { print "$::cmd: mail saved in $ENV{DEADMAIL}\n";
  }

  cleanup($sig);
}

sub cleanup
{ my($sig)=@_;

  warn "$::cmd: cleaning up on receipt of SIG$sig...\n" if $sig;
  warn "$::cmd: can't unlink $::Tmp: $!\n" if defined($::Tmp) && !unlink($::Tmp);
  exit $::Xit;
}

sub onint
{ $SIG{'HUP'}=$_[0];
  $SIG{'INT'}=$_[0];
  $SIG{'TERM'}=$_[0];
}

sub gethdrs	# (FILE) -> void
{ my($H,$FILE)=@_;
  my(@h);

  H:
  while (<$FILE>)
  { last H if /^$/;
    if ($. == 1 && /^From /i)	{ $_="From-: $'"; }
    push(@h,$_);
  }

  $H->ArrayExtract(@h);
}

sub addsig
{ my($ptext,$sigptn)=@_;
  return if $::hassig;

  $::hassig=1;

  my(@sigcmd)='sig';

  push(@sigcmd,$sigptn) if defined $sigptn;
  my($sigcmd)=scalar(cs::Shell::quote(@sigcmd));

  if ($::tty && open(SIGNATURE," $sigcmd |"))
  {
    $$ptext.=join('',<SIGNATURE>);
    close(SIGNATURE);
  }
  elsif (open(SIGNATURE,"< $ENV{SIGNATURE}\0"))
  {
    $$ptext.=join('',<SIGNATURE>);
    close(SIGNATURE);
  }
  else
  { warn "$::cmd: can't generate signature\n";
  }
}

sub ismime
{ my($H,$type)=shift;

  $H->Add("MIME-Version: 1.0");
  $H->Add("Content-Type: $type");
}

sub text2qp
{ my($text)=@_;
  my($qtext)='';

  my($sink)=(new cs::Sink SCALAR, \$qtext);
  return undef if ! defined $sink;
  $sink=new cs::MIME::QuotedPrintable Encode, $sink, 1;
  $sink->Put($text);
  $sink=undef;	# close and release

  # warn "qtext=[$qtext]\n";
  $qtext;
}

sub addattachments
{ my($H,$text,@a)=@_;

  return unless @a;

  my($sep)="==multipart==";

  ismime($H,"multipart/mixed; boundary=\"$sep\"");

  my($ntext)='';
  my($tsink)=(new cs::Sink SCALAR, \$ntext);

  $tsink->Put(	"\r\nThis is a MIME message.\r\n",
		"\r\n--$sep\r\n",
		"Content-Type: text/plain; charset=us-ascii\r\n",
		"Content-Transfer-Encoding: quoted-printable\r\n",
		"\r\n");

  { my($body)=(new cs::MIME::QuotedPrintable Encode, $tsink, 1);
    $body->Put($text);
  }

  my($a,$adata);

  ATTACH:
  for my $file (@a)
  { if (! defined ($a=new cs::Source PATH, $file))
    { warn "$::cmd: can't open $file: $!\n";
      next ATTACH;
    }

    $tsink->Put(  "\r\n--$sep\r\n",
		  "Content-Type: text/plain; name=\"$file\"\r\n",
		  "Content-Transfer-Encoding: quoted-printable\r\n",
		  "Content-Disposition: attachment; filename=\"$file\"\r\n",
		  "\r\n");

    { my($asink)=(new cs::MIME::QuotedPrintable Encode, $tsink, 1);

      while (defined ($adata=$a->Read()) && length($adata))
      { $asink->Put($adata);
      }
    }
  }

  $tsink->Put("\r\n--$sep--\r\n");

  undef $tsink;

  $text=$ntext;
}

# take a filename and a source and return a type/subtype, an encoding,
# andf an encoded source
sub mencode
{ my($src,$fname)=@_;
}

sub adduufiles
{
  return if $::has_uufiles;

  my($ptext,@files)=@_;

  UUFILE:
    for (@files)
	{ if (!open(UUFILE,"< $_\0"))
		{ warn "$::cmd: can't open $_: $!\n";
		  next UUFILE;
		}

	  $$ptext.="\n".join('',&uuencode(UUFILE,0600,$_));
	}

  $::has_uufiles=1;
}

sub uuencode	# (FILE,mode,file)
{ my($F,$mode,$file)=@_;
  my($buf);
  my($count,$i);
  my(@uu);

  push(@uu,sprintf("begin %03o %s\n",$mode & 0777,$file));
  $count=0;
  while (($i=read($F,$buf,45)) == 45)
  { push(@uu,pack('u',$buf));
    $count+=45;
  }

  if ($i > 0)
  { push(@uu,pack('u',$buf));
  }

  push(@uu," \nend\n");

  @uu;
}

sub syncdpp	# void
{ my($H)=@_;
  my($key);

  for $key (grep(/^[a-z_]+$/,keys %dpp'symbol))
  { &dpp'undefine($key);
  }

  for $key (keys %'ENV)
  { &dpp'define($key,$'ENV{$key});
  }

  for $key ($H->HdrNames())
  { $key =~ tr/-A-Z/_a-z/;
    &dpp'define($key,scalar($H->Hdr($key)));
  }
}

sub openstdin	# inputfile
{ my($newstdin)=shift;

  if (!open(STDIN,"< $newstdin\0"))
  { die "$::cmd: can't open($newstdin): $!\n";
  }

  $newstdin;
}

sub loadmailrcs	# @mailrcs
{ my($rc)=shift;

  for my $M (@_)
  {
    -e "$M.db" && $rc->LoadDB("$M.db",1);
    -s $M && $rc->Load($M,1);
  }
}

sub repHdr
{ my($this,$field,$body,$noOrig)=@_;
  $noOrig=0 if ! defined $noOrig;

  ## my @c = caller;
  ## warn "DEL $field, noOrig=$noOrig from [@c]";

  $field=cs::RFC822::norm($field);
  $this->Del($field, ! $noOrig);
  $this->Add("$field: $body\n") if length $body;
}

# XXX - to move into RFC822.pm
sub dupHdr
{ my($h)=@_;
  my($h2)={};

  for (keys %$h)
  { $h2->{$_}=$h->{$_};
  }

  bless $h2, cs::RFC822;
}

sub stashaddr
{ my($into,$as,$what)=@_;

  if (! defined $$into{$as})
  { $$into{$as}=$what;
  }
  elsif ($$into{$as} ne $what)
  { warn "warning: \"$as\" already set to \"$$what{$as}\",\n\trejecting secondary value of \"$what\"\n";
  }
}

sub via
{ my($method,$hdrs,$text,$addrs)=@_;

  return 1 unless keys %$addrs;

  $hdrs=dupHdr($hdrs);

  if ($method eq 'mail')	{ viamail($hdrs,$text,$addrs); }
  elsif ($method eq 'anon')	{ viaanon($hdrs,$text,$addrs); }
  elsif ($method eq 'news')	{ vianews($hdrs,$text,$addrs); }
  elsif ($method eq 'fax')	{ viafax($hdrs,$text,$addrs); }
  else
  { warn "$::cmd: don't know how to dispatch via method \"$method\"\n";
    warn "\tnothing sent to [", join(' ',sort keys %$addrs), "]\n";
    0;
  }
}

sub viaanon
{ my($hdrs,$text,$addrs)=@_;
  my(@addrs)=keys %$addrs;

  $::tty && warn "queuing anonymous addresses (@addrs)...\n";

  $hdrs->Add("X-Anon-To: ".
	join(",\n\t", @addrs));
  if (open(APASSWD,"< $::AnonPasswd\0"))
  { $_=<APASSWD>;
    close(APASSWD);
    chomp;
    if (length)
    { $hdrs->Add("X-Anon-Password: $_");
    }
  }

  $hdrs->Add("To: $::AnonAddr");
  viamail($hdrs,$text,{ $::AnonAddr => $::AnonAddr },'+out');
}

sub viamail
{ my($hdrs,$text,$addrs,$mbox)=@_;
  my(@addrs)=keys %$addrs;

  # log in outgoing folder
  if (defined $mbox)
  { my $s;

    $s=new cs::Sink (PIPE,"filemail '$mbox'");
    if (! defined $s)
    { abort(0,"can't pipe to \"filemail $mbox\": $!");
    }
    else
    { $hdrs->WriteItem($s,$text);
    }
  }

  # add domain to addresses
  for my $i (0..$#addrs)
  { $addrs[$i].="\@$ENV{SITENAME}" if $addrs[$i] !~ /\@/;
  }

  $::tty && warn "queuing for smtpsend (@addrs) ...\n";

  # construct message text
  my $msgtxt = '';
  { my $sink = new cs::Sink (SCALAR,\$msgtxt);
    $hdrs->WriteItem($sink,$text);
  }

  my $msgsrc = new cs::Source (SCALAR, \$msgtxt);
  cs::Mail::Misc::smtpsend($msgsrc, @addrs);
}

sub vianews
{ my($hdrs,$text,$addrs)=@_;
  my(@addrs)=keys %$addrs;

  for (@addrs)
	{ s/\@usenet$//oi;
	}

  $::tty && warn "queuing for news (@addrs) ...\n";

  $hdrs->Del(NewsGroups);
  $hdrs->Add('NewsGroups: '.join(',',@addrs));
 
  my($s);

  if (! defined ($s=new cs::NNTP))
  { abort(0,"can't connect to NNTP server");
  }
  elsif (! $s->CanPost())
  { abort(0,"posting is forbidden");
  }

  totmp($::Tmp,$hdrs,$text);
  if (! $s->PostFile($::Tmp))
  { abort(0,"posting fails");
  }

  1;
}

sub viafax
{ my($hdrs,$text,$addrs)=@_;

  my(@addrs)=keys %$addrs;
  my($fulladdr,$who,$faxno,$org,$voice,$subject);
  my($ok)=1;

  $::tty && warn "queuing for fax (@addrs) ...\n";

  FAX:
  for (@addrs)
  { $fulladdr=$addrs->{$_};
    # warn "fulladdr=\"$fulladdr\"\n";
    if ($fulladdr =~ /^\s*(\S.*\S)\s+<([^@>]+)\@FAX>\s*$/i)
    { ($who,$faxno)=($1,$2);
    }
    elsif ($fulladdr =~ /^\s*(\S+)\@FAX\s*$/i)
    { ($who,$faxno)=('To Whom It May Concern',$1);
    }
    else
    { warn "$'cmd: can't parse FAX address: $fulladdr\n";
      $ok=0;
      next FAX;
    }

    $subject=$hdrs->Hdr(Subject);

    $::tty && warn "passing to faxtxt for $faxno ...\n";

    totmp($::Tmp,$hdrs,$text);
    { my($s,$w)=($subject,$who);
      $s =~ s/'/'\\''/g;
      $w =~ s/'/'\\''/g;
      system("faxtxt -n '$faxno' -s '$s' -w '$w' $::Tmp");
    }
    if ($? != 0)
    { warn "$::cmd: faxtxt fails, exit status $?\n";
      $ok=0;
      next FAX;
    }
  }

  $ok;
}

sub file
{ my($hdrs,$text,@maildropArgs)=@_;

  my($DROP)=(new cs::Sink PIPE, "maildrop @maildropArgs");
  if (! defined $DROP)
  { abort(0,"can't pipe to maildrop: $!");
  }
  $hdrs->WriteItem($DROP,$text);
}

sub apply_profile
{ my($profile)=@_;

  # my($pdb)=cs::Persist::db("$ENV{MAILDIR}/profiles/$profile");
}

#!/usr/bin/perl
#
# Miscellaneous mail things.
#	- Cameron Simpson <cs@zip.com.au> 24jun99
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;

# in case
$ENV{MAILDIR}="$ENV{HOME}/private/mail" unless defined $ENV{MAILDIR};
$ENV{MAILRCDIR}="$ENV{HOME}/rc/mail" unless defined $ENV{MAILRCDIR};

package cs::Mail::Misc;

# munch those [font?[QB]blah] encodings
sub decodeFontisation($)
{ local($_)=@_;

  s/=\?([-\w]+)\?([QB])\?(.+)\?=/
      (lc($1) eq 'iso-8859-1' ? "" : "[$1 ")
      .(
	$2 eq 'Q'
	? cs::MIME::QuotedPrintable::decode($3,1)
	: $2 eq 'B'
	  ? cs::MIME::Base64::decode($3,1)
	  : "?$2?$3?"
       )
      .(lc($1) eq 'iso-8859-1' ? "" : "]")
       /eg;

  $_;
}

# apply the above munching to a header line
sub unFontisehdr
{ my($H,$hdr)=@_;

  my $oBody = $H->Hdr($hdr);
  my $Body;

  $Body=decodeFontisation($oBody);
  if ($Body ne $oBody)
  { $H->Add($hdr,$Body,SUPERCEDE);
    ## warn "change $hdr: line from [$oBody] to [$Body]\n";
  }
}

sub normaddr($)
{ local($_)=@_;

  my($oa)=$_;

  while (
	 # who%where@gateway
	 s/(.*)%(.*)\@.*/$1\@$2/
      || 
	 # where!who@gateway
	 s/(.*)!(.*)\@.*/$2\@$1/
	)
 {}

  # local -> local@sitename
  if (length $ENV{SITENAME})
  { s/^[^\@]+$/$&\@$ENV{SITENAME}/o;
  }

  # @*: prefixes
  s/(\@[^:\@]+:)+(.*\@.*)/$2/;

  # local@host.sitename -> local@sitename
  if (length $ENV{SITENAME})
  { s/\@[^@]*$ENV{SITENAME}$/\@$ENV{SITENAME}/o;
  }

  # downcase
  $_=lc($_);

  if ($oa ne $_ || $oa =~ /[%!]/)
  { # warn "$oa -> $_";
  }

  $_;
}

# expects simple plain "foo@where, bar@else" address list string
sub mailto
{ my($addrs,$subj,@content)=@_;

  my(@addrs)=grep(length,split(/[\s,]+/,$addrs));
  die "$0: no addrs!" if ! @addrs;

  my $msg = "To: ".join(", ", @addrs)."\n"
	  . "Subject: $subj\n"
	  . "\n"
	  . join("",@content)."\n";

  ::need(cs::Source);
  smtpsend(cs::Source->new(SCALAR,\$msg), @addrs);
}

sub smtpsend
{ my($src,@addrs)=@_;

  ::need(Net::SMTP);

  my $host = defined $ENV{SMTPSERVER} && length $ENV{SMTPSERVER}
	   ? $ENV{SMTPSERVER}
	   : 'smtp'	# guess
	   ;

  ## warn "smtp host = \"$host\"";

  my $ok = 1;

  my @pw = getpwuid($<);
  if (! @pw)
  { warn "$::cmd: can't look up passwd entry for uid $<: $!\n";
    $ok=0;
  }
  else
  { my $smtp = Net::SMTP->new($host);

    if (! defined $smtp)
    { warn "$::cmd: can't connect to SMTP server \"$host\": $!\n";
      $ok=0;
    }
    else
    {
      ## warn "smtp obj = $smtp";
      if (! $smtp->mail("$pw[0]\@$ENV{MAILDOMAIN}"))
      { warn "$::cmd: problem announcing sender \"$pw[0]\@$ENV{MAILDOMAIN}\"\n";
	$ok=0;
      }
      else
      {
	ADDR:
	for my $addr (@addrs)
	{
	  if (! $smtp->to($addr))
	  { warn "$::cmd: problems with address $addr\n";
	    $ok=0;
	  }
	}
      }
    }
    ## warn "smtp set up: ok=$ok";

    if ($ok)
    {
      if (! $smtp->data())
      { warn "$::cmd: can't commence sending data\n";
	$ok=0;
      }
      else
      {
	local($_);

	DATUM:
	while (defined($_=$src->GetLine()) && length)
	{ if (! $smtp->datasend($_))
	  { warn "$::cmd: trouble sending data\n";
	    $ok=0;
	    last DATUM;
	  }
	}
	if ($ok && ! $smtp->datasend())
	{ warn "$::cmd: trouble ending data\n" if $ok;
	  $ok=0;
	}
      }
    }

    if (! $smtp->quit())
    { warn "$::cmd: trouble QUITting\n";
      $ok=0;
    }
  }

  return $ok;
}

1;

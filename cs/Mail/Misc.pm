#!/usr/bin/perl
#
# Miscellaneous mail things.
#	- Cameron Simpson <cs@zip.com.au> 24jun99
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

# in case
$ENV{MAILDIR}="$ENV{HOME}/etc/mail" unless defined $ENV{MAILDIR};
$ENV{MAILRCDIR}="$ENV{HOME}/etc/rc/mail" unless defined $ENV{MAILRCDIR};

package cs::Mail::Misc;

use cs::Shell;

# munch those [font?[QB]blah] encodings
sub decodeFontisation
{ local($_)=shift;

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

  my $oBody = $::H->Hdr($hdr);
  my $Body;

  $Body=decodeFontisation($oBody);
  if ($Body ne $oBody)
  { $::H->Add($hdr,$Body,SUPERCEDE);
    ## warn "change $hdr: line from [$oBody] to [$Body]\n";
  }
}

sub normaddr
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

1;

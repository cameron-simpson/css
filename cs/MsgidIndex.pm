#!/usr/bin/perl
#
# Index data keyed on RFC822 Message-IDs.
#	- Cameron Simpson <cs@zip.com.au> 06jun1997
#

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::DeepIndex;

package cs::MsgidIndex;

@cs::MsgidIndex::ISA=(cs::DeepIndex);

sub _KeyChain
	{ my($this,$rawkey)=@_;
	  my($okey)=$rawkey;

	  $rawkey =~ s/^<//;
	  $rawkey =~ s/>$//;
	  $rawkey =~ s/.*\@//;
	  $rawkey =~ s/\s+//g;

	  my(@s)=split(/\.+/,$rawkey);
	  warn "s=[@s]";
	  my(@keychain)=(map(lc($_),grep(length,@s)),$okey);
	  warn "bad keys in chain: [@keychain]"
		if grep($_ eq '.',@keychain);

	  @keychain;
	}

1;

#!/usr/bin/perl
#
# Handle mail profiles.
#	- Cameron Simpson <cs@zip.com.au> 04jun98
#

use strict qw(vars);

BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Persist;
use cs::Flags;

package cs::Mail::Profile;

@cs::Mail::Profile::ISA=qw();

sub new
	{ my($class,$profile,$rw)=@_;
	  $rw=0 if ! defined $rw;

	  my($this);

	  if (ref $profile)
		{ $this=$profile;
		}
	  else
	  { my($path)=($profile =~ m:^/:
			? $profile
			: "$ENV{MAILDIR}/$profile");

	    $this=cs::Persist::db($path,$rw);
	  
	    return undef if ! defined $this;
	  }

	  $this->{FLAGS}=[] if ! exists $this->{FLAGS};
	  bless $this->{FLAGS}, cs::Flags;

	  bless $this, $class;
	}

sub Name{ my($this)=@_;
	  exists $this->{FULLNAME} ? $this->{FULLNAME} : $ENV{NAME};
	}

sub Email{my($this)=@_;
	  exists $this->{EMAIL}
		? $this->{EMAIL}
		: exists $ENV{EMAIL}
			? $ENV{EMAIL}
			: "$ENV{USER}\@$ENV{SITENAME}";
	}

sub From{ my($this)=@_;
	  $this->Name()." <".$this->Email().">";
	}

sub ReplyTo
	{ my($this)=@_;
	  exists $this->{REPLY_TO} ? $this->{REPLY_TO} : $this->From();
	}

sub ErrorsTo
	{ my($this)=@_;
	  exists $this->{ERRORS_TO} ? $this->{ERRORS_TO} : $this->From();
	}

sub Check	{ shift->{FLAGS}->TestAll(@_); }
sub WantSig	{ shift->Check(WANTSIG); }

sub SetHdrs	{ my($this,$H)=@_;

		  $H->Add([FROM,$this->From()],REPLACE);
		  $H->Add([REPLY_TO,$this->ReplyTo()],REPLACE);
		  $H->Add([ERRORS_TO,$this->ErrorsTo()],REPLACE);
		  $H->Add([RETURN_PATH,$this->ErrorsTo()],REPLACE);
		}

1;

#!/usr/bin/perl
#

BEGIN { $ENV{PATH}="/usr/local/script:/usr/local/bin:$ENV{PATH}";
	open(STDERR,"|/usr/local/script/mailif -s budrep.cgi.err cameron");
	unshift(@INC,'/u/cameron/etc/pl');
      }

use strict qw(vars);

use CISRA::Web;
use CISRA::DB;
use CISRA::Groups;
use CISRA::UserData;
use cs::CGI;
use cs::HTML::Form;

my $Q = new cs::CGI;
my $F = $Q->Query();

my @errs;

my(@html)=();

if (! exists $F->{ACCTCODE})
	{ push(@errs,"no ACCTCODE");
	}
else	{ $::pcode=$F->{ACCTCODE};
	}

if (! exists $F->{MCODE})
	{ push(@errs,"no MCODE");
	}
else	{ $::mcode=$F->{MCODE};
	}

if (! @errs)
	{
	  $::ADB=CISRA::Groups::acctDB();
	  if (! exists $::ADB->{$::pcode})
	  	{ push(@errs,"invalid account code \"$::pcode\"")
		}
	  else	{ $::G=$::ADB->{$::pcode};
		  $::M=$::G->{MEMBERS};

		  # gather up names
		  for my $rep ('leader', 'manager')
		  { my $r=$::M->{$rep};
		    $::Rep{$rep}=( ref $r ? [ @$r ] : [ $r ] );
		  }
		}
	}

if (! @errs)
	{
	  $::StateDB=CISRA::DB::db(['timesheets','rep','bud','state'],1);

	  { my $dbp = $::StateDB;

	    $dbp->{$::mcode}={} if ! exists $dbp->{$::mcode};
	    $dbp=$dbp->{$::mcode};
	    my $st = $dbp;
	    $dbp->{$::pcode}={} if ! exists $dbp->{$::pcode};
	    $dbp=$dbp->{$::pcode};
	    $dbp->{APPROVAL}={} if ! exists $dbp->{APPROVAL};
	    $dbp=$dbp->{APPROVAL};

	    if (exists $F->{APPROVED})
		{ my $approval;	# who first

		  if (! exists $dbp->{BYLEADER})
			{ $approval='leader';
			}
		  elsif (! exists $dbp->{BYMANAGER})
			{ $approval='manager';
			}
		  else	{ push(@errs,
			       "already approved, rejecting approval by \"$F->{APPROVED}\"",
			      );
			}

		  if (! @errs)
		  { if (! exists $::M->{$approval})
			{ push(@html,
				"no $approval for project $::pcode",
				[BR],"\n",
				[PRE,"G=",cs::Hier::h2a($::G,1)],"\n");
			}
		    else
		    { my $appr = $F->{APPROVED};
		      my @m    = @{$::Rep{$approval}};

		      if (! grep($_ eq $appr, @m))
			{ push(@errs,
				"Sorry, \"$appr\" is not allowed to approve reports for project $::pcode as \"$approval\".");
			}
		      else
			{
			  push(@html,[PRE,"dbp=".cs::Hier::h2a($dbp,1)]);
			  if (! exists $dbp->{BYLEADER})
				{ $dbp->{BYLEADER}=$appr;
				  if (grep($_ eq $appr, @{$::Rep{'manager'}}))
				  { $dbp->{BYMANAGER}=$appr;
				  }
				  else
				  { notify($::pcode,$::mcode,$appr,LEADER);
				  }
				}
			  else	{ $dbp->{BYMANAGER}=$appr;
				}

			  push(@html,[PRE,"dbp approved=".cs::Hier::h2a($dbp,1)]);

			  push(@html,
				"Thanks, report for project $::pcode approved by $approval.\n");
			}
		    }
		  }

		  push(@html,
			[PRE,"Post approve, state=".cs::Hier::h2a($st,1) ]);
		}
	    else
	    # emit form for approval, or "it's approved!"
	    {
	      if (exists $dbp->{BYMANAGER})
		{ push(@html,'Approved.');
		}
	      else
	      {
		my $form=new cs::HTML::Form "/cgi-bin/budrep-button.cgi";
		$form->Hidden(ACCTCODE,$::pcode);
		$form->Hidden(MCODE,$::mcode);
		$form->TextField(APPROVED);
		$form->MarkUp([BR]);
		$form->Submit(SUBMIT,
			    exists $dbp->{BYLEADER}
				? 'Manager Approval'
				: 'Leader Approval'
			   );
		push(@html,$form->Close());
	      }
	    }
	  }

	  undef $::StateDB;
	  CISRA::DB::finish();
	}

if (@errs)
	{ @html=( [HR], "\n",
		  [B,"Errors with $0"], [BR], "\n",
		  [UL, map([LI,$_],@errs)], "\n",
		  [HR], "\n",
		  [PRE,"F=", cs::Hier::h2a($F,1)],
		);
	}

$Q->Print(\@html);

exit 0;

sub notify
	{ my($pcode,$mcode,$bywho,$bywhat)=@_;
	warn "NOTIFY(@_)";

	  if (! exists $::ADB->{$pcode})
		{ warn "no ADB entry for pcode \"$pcode\"";
		  return;
		}

	  my $G = $::ADB->{$pcode};
	  my $gname = $G->{NAME};

	  my $whom;
	  my $what;

	  if ($bywhat eq LEADER){ $what=MANAGER;
				  $whom="$gname-manager";
				}
	  else			{}

	  return if ! defined $whom;

	  if (! open(NOTIFY,"| fmt | /usr/local/script/mailif -s 'timesheet report authorisation' '$whom'"))
		{ warn "$0: can't pipe to mailif: $!";
		  return;
		}

	  print NOTIFY "The project \"$gname\" (acct code $pcode) has been authorised by \"".$bywho."\"\n";
	  print NOTIFY "(the team ".lc($bywhat)."). As the team ".lc($what)." you should now verify this\n";
	  print NOTIFY "and authorise the report yourself, if it's correct.\n\n";
	  print NOTIFY "The report is available at:\n\n";
	  print NOTIFY "\t$CISRA::Web::Root/rep/bud/$mcode/projects/$pcode.shtml\n";

	  close(NOTIFY);
	}

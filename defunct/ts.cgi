#!/usr/bin/perl -w
#
# Collect timesheet information for a week.
#	- Cameron Simpson <cs@cskk.id.au> 17dec96
#

use strict qw(vars);

BEGIN	{ $ENV{PATH}="/usr/local/script:/usr/local/bin:$ENV{PATH}";
	  ##open(STDERR, ">>/tmp/log.ts");
	  open(STDERR,"| sed '/^Use of uninitialized value during global destruction.\$/d' | mailif -s ts.err cameron");
	  warn "BEGIN $0";
	  $ENV{PATH}="$ENV{PATH}:/usr/local/bin:/usr/local/script";
	  unshift(@INC,'/u/cameron/etc/pl');
	  $::_start=time;
	}

use cs::CGI;
use CISRA::Web;
#use cs::OTP::CGI;
use CISRA::TimeSheets;
use CISRA::TimeSheets::CGI;

$::_start_used=time;

umask 002;

my($Q) = new cs::CGI;
my($Xit)=main($Q,@ARGV);
$::_start_main=time;
CISRA::TimeSheets::finish();	# timely cleanup

$::_start_finished=time;
warn "leaving, Xit=$Xit, use time=".($::_start_used-$::_start)
   ." runtime=".($::_start_main-$::_start_used)
   ." finishtime=".($::_start_finished-$::_start_main);

exit $Xit;

sub main
	{ my($Q,@ARGV)=@_;

	  # $auth=new cs::OTP::CGI $Q, 'OTPid', $CISRA::TimeSheets::CookieFile;

	  my(@html)=();

	  my(@errs)=();

	  ########################
	  # collect form info
	  my($LOGIN,$SUBMIT,$CODE,$DEBUG);

	  $LOGIN=$Q->Value('login');		$LOGIN='' if ! defined $LOGIN;
	  $CODE  =$Q->Value('sheetcode');	$CODE='' if ! defined $CODE;

	  $SUBMIT='';
	  if (defined $Q->Value('SUBMIT_FINAL')) { $SUBMIT=FINAL; }
	  elsif (defined $Q->Value('SUBMIT_MORE')) { $SUBMIT=MORE; }

	  # open(STDERR,"|/usr/local/script/mailif -s 'ts.cgi-$LOGIN/$CODE/$SUBMIT' cameron");

	  my $TS;

	  # no date? pretend no sheet, either (shouldn't happen anyway)
	  if (! length $CODE || ! length $LOGIN)
		{ $SUBMIT='';
		}

	  # gather together the timesheet data if there is any
	  if (defined $SUBMIT && ($SUBMIT eq FINAL || $SUBMIT eq MORE))
		{ ($TS,@errs)=CISRA::TimeSheets::CGI::extractTimesheet($Q, $LOGIN);
		}

	  if ($SUBMIT eq FINAL)
		{ 
		  if (@errs)
			{ push(@html,[B,"Warning"],":\n",
				"the following possible errors were noted with your timesheet:\n",
				[UL,
				  map([LI,$_],@errs)], "\n",
				"The timesheet will be submitted anyway,\n",
				"however you should perhaps go back and fix things up.", [BR], "\n",
				);
			}

		  push(@html,"Saving timesheet ...",[BR],"\n");
		  push(@html,$TS->HTMLReport());
		  if (! $TS->Save())
			{ my($e)="$!";
			  warn "can't save: $e";
			  push(@html,"Can't save your timesheet, possible error: $e.",[BR],"\n",
				"You may want to check with a system administrator before retrying this.",[P],"\n");
			  push(@errs,"Couldn't save timesheet: $e");
			}
		  else
		  { push(@html,"Timesheet saved. Thanks.",[P],"\n");
		    undef $TS;
		  }

		  # enter "new sheet" mode
		  $CODE='';
		  $SUBMIT='';
		}

	  # the form to hold the query
	  my($F)=$Q->Form($Q->ScriptURL(),POST);

	  ###################################
	  # do we need the user's login name?
	  my $U;

	  if (! length $LOGIN)
		{}
	  elsif (! defined ($U=CISRA::UserData::user($LOGIN)))
		{ $F->MarkUp(
			"The login ",
			[TT,$LOGIN],
			" is not recognised.",[BR],"\n");
		  $LOGIN='';
		}
	  elsif (! $U->Alive())
		{ $F->MarkUp(
			"WARNING: The login ",
			[TT,$LOGIN],
			" is not current.",[BR],"\n");
		  $LOGIN='';
		}

	  $DEBUG=($LOGIN eq 'cameron');

	  if (! length $LOGIN)
		{
		  $F->MarkUp([B, "Timesheet Login"],
		 		" [", [A, {HREF => '/timesheets/help_log.html'}, "Help!"], "]",
				[BR],"\n",
			# "Pending fixing some bizarre caching problems with the OTP\n",
			# "stuff, we're using this simple-minded login scheme for now.\n",
			# "- Cameron\n",[P],"\n",
			"Username: ");
		  $F->TextField('login','',16,16);
		  $F->MarkUp([P],"\n");
		}
	  else	{ $F->MarkUp("Login: ",[TT,$LOGIN],[BR],"\n");
		  $F->Hidden('login',$LOGIN);
		}

	  if (length $CODE)
		{
		  $TS=new CISRA::TimeSheets::CGI $LOGIN, $CODE;

		  @errs=$TS->SanityCheck();

		  if (@errs)
			{ $F->MarkUp(
				"The following problems exist in the timesheet at present:\n",
				[UL,
				  map([LI,$_],@errs)
				], "\n", [P], "\n",
				);
			}

		  $TS->ExtendForm($F);
		}
	  # new sheet altogether
	  elsif (length $LOGIN)
		{ 
		  $F->MarkUp([H1,"Select Timesheet To Edit"], "\n");
		  CISRA::TimeSheets::CGI::extendForm_PickTimesheet($F,$LOGIN);
		}

	  push(@html,$F->Close());
	  undef $F;

	  warn "html=[@html]";
	  $Q->BodyAttr( BGCOLOR => 'white',
			TEXT	=> 'black',
			LINK	=> 'purple',
			VLINK	=> 'blue',
			ALINK	=> 'red',
		       );
	  @html=(CISRA::Web::heading('ts'),@html);
	  $Q->Print(\@html);

	  return 0;
	}

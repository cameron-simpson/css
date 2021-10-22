#!/usr/bin/perl -w
#
# Display data for performance eval.
#	- Cameron Simpson <cs@cskk.id.au> 21dec98
#

use strict qw(vars);

BEGIN	{ $ENV{PATH}="$ENV{PATH}:/usr/local/bin:/usr/local/script";
	  -t STDERR || open(STDERR,"|/usr/local/script/mailif -s ts.cgi-err.test cameron");
	  unshift(@INC,'/u/cameron/etc/pl');
	  $::_start=time;
	}

use cs::CGI;
use CISRA::Web;
#use cs::OTP::CGI;
use CISRA::UserData;
use CISRA::TimeSheets;
use CISRA::TimeSheets::CGI;

$::_start_used=time;

umask 002;

my($Q) = new cs::CGI;
my($Xit)=main($Q,@ARGV);
$::_start_main=time;

$::_start_finished=time;
warn "leaving, Xit=$Xit, use time=".($::_start_used-$::_start)
   ." runtime=".($::_start_main-$::_start_used)
   ." finishtime=".($::_start_finished-$::_start_main);

exit $Xit;

sub main
	{ my($Q,@ARGV)=@_;

	  my $TSDB = CISRA::TimeSheets::db();
	  my @allUsers = keys %$TSDB;

	  my(@html)=([H1,"Performance Evaluation Timesheet Summary Report"],
		     "\n");

	  if (defined $Q->Value(SUBMIT))
		# produce report with form at the bottom
		{ my @ulist = sort $Q->Value(USERS);
		  my @codes = $Q->Value(CODES);

		  my @ndx = ( map([A,{HREF => "#rep-$_"}, "Report for $_"],
				  @ulist),
			      [A,{HREF => "#query"},"New Query"]
			    );

		  # 10 10-10    10 60
		  # week        summ
		  #             plan
		  #    projname    sched
		  #    hrs code    summ
		  USER:
		  for my $user (@ulist)
		  { push(@html,[H2,[A,{NAME => "rep-$user"},"Report for $user"]], "\n");
		    next USER if ! exists $TSDB->{$user};

		    my $tsu = $TSDB->{$user};
		    my $U = CISRA::UserData::user($user);

		    my @tr;

		    CODE:
		    for my $code (@codes)
		    {
		      if (! exists $tsu->{$code})
		      { push(@tr, [TR, [TH,{COLSPAN => 2},$code], [TD, [B, MISSING]]]);
			next CODE;
		      }

		      my $week = $tsu->{$code};
		    }
		  }
		}

	  push(@html,[H2,[A,{NAME => "query"},"New Query"]],"\n");

	  # the form to hold the query
	  my($F)=$Q->Form($Q->ScriptURL(),GET);

	  my(@errs)=();

	  ########################
	  # collect form info
	  my($LOGIN,$SUBMIT,$CODE,$DEBUG);

	  $LOGIN=$Q->Value('login');		$LOGIN='' if ! defined $LOGIN;
	  $CODE  =$Q->Value('sheetcode');	$CODE='' if ! defined $CODE;

	  $SUBMIT='';
	  if (defined $Q->Value('SUBMIT_FINAL')) { $SUBMIT=FINAL; }
	  elsif (defined $Q->Value('SUBMIT_MORE')) { $SUBMIT=MORE; }

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
		  $F->MarkUp([B, "Timesheet Login"],[BR],"\n",
			"Pending fixing some bizarre caching problems with the OTP\n",
			"stuff, we're using this simple-minded login scheme for now.\n",
			"- Cameron\n",[P],"\n",
			"Login: ");
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

#!/usr/bin/perl -w

($cmd=$0) =~ s:.*/::;

$Now=time;
$NowTm=cs::Date::time2tm($Now,0);

$StatsDir='/home/docs/stats';
$TopLevel="$StatsDir/lib/country-codes.txt";
$DAY=60*60*24;	# seconds in a day
$Codes=load_codes($TopLevel);

use CGI ':standard';
use cs::Date;
use cs::Net;
use cs::WWW::Log;
use cs::Pathname;
use cs::Upd;
use cs::Misc;
use cs::Hier;
use cs::Stats;
use cs::Source;

@TABLES=(URLS,LHOSTS,RHOSTS,URLDIRS,TOPDOM,URLTYPES);
%TableUnits=(	URLS => 'bytes',
		LHOSTS	=> 'bytes',
		RHOSTS	=> 'bytes',
		URLDIRS	=> 'bytes',
		TOPDOM	=> 'bytes',
		URLTYPES=> 'bytes',
		TIMESLICE=>'bytes',
		IBPS	=> 'bps',
		OBPS	=> 'bps',
	    );
%Reports=(	URLS	=> { TITLE => 'data transfer per URL',
			     TABLES => [URLS],
			   },
		UDIRS	=> { TITLE => 'directories of URLs',
			     TABLES => [URLDIRS],
			   },
		RHOSTS	=> { TITLE => 'remote hosts requested',
			     TABLES => [RHOSTS],
			   },
		LHOSTS	=> { TITLE => 'local hosts requesting',
			     TABLES => [LHOSTS],
			   },
		LINKIN	=> { TITLE => 'incoming link traffic (bps)',
			     TABLES => [IBPS],
			   },
		LINKOUT	=> { TITLE => 'outgoing link traffic (bps)',
			     TABLES => [OBPS],
			   },
		HOURS	=> { TITLE => 'hourly traffic',
			     TABLES => [TIMESLICE],
			   },
		DHOURS	=> { TITLE => 'traffic across a 24 hour period',
			     TABLES => [TIMESLICE],
			   },
		WHOURS	=> { TITLE => 'hourly traffic across a week hour period',
			     TABLES => [TIMESLICE],
			   },
		WEEKLY	=> { TITLE => 'weekly summary',
			     TABLES => [ @TABLES ],
			   },
	 );
%DataSets=(	'www'	=> { TITLE => 'Traffic to our external web server',
			     LOGFORMAT => WWW,
			   },
		'photoessentials'=> { TITLE => 'Traffic to our external PhotoEssentials web server',
			     LOGFORMAT => WWW,
			   },
		'ftp'	=> { TITLE => 'Traffic to our external FTP server',
			     LOGFORMAT => WUFTPD,
			   },
		'proxy-log' => { TITLE => 'Traffic by our proxy on request',
			     LOGFORMAT => WWW,
			   },
		'cache-log' => { TITLE => 'Traffic to our internal proxy cache',
			     LOGFORMAT => WWW,
			   },
		'cisco'	=> { TITLE => 'Traffic across our internet link',
			     LOGFORMAT => CISCO,
			   },
	  );
%Components=(	'index.html' => { TITLE => 'Overview' },
		'table.html' => { TITLE => 'Table form' },
		'table.txt' => { TITLE => 'Table form (plain text)' },
		'piegraph.gif' => { TITLE => 'Pie Graph' },
		'bargraph.gif' => { TITLE => 'Bar Graph' },
	    );
%LogPatterns=(	CUSTOM	=> { TITLE => 'Custom Pattern Selection',
			      PATTERN => '',
			    },
		EVERYTHING=>{ TITLE => 'All the data',
			      PATTERN => '*/*/*',
			    },
	     );
$lp=\%LogPatterns;
$lp->{YESTERDAY}={ TITLE => 'Yesterday', PATTERN => ptn_yesterday() };
$lp->{LAST7}={ TITLE => 'The previous 7 days', PATTERN => ptn_last7() };
$lp->{LASTWEEK}={ TITLE => 'The previous week (sun..sat)', PATTERN => ptn_lastWeek() };
$lp->{LASTMONTH}={ TITLE => 'The previous calendar month', PATTERN => ptn_lastMonth() };

$Q=new CGI;

@errs=();

# requested statistics
$Ordering=HITS;
$DX=1000; $DY=1000;

# path_info: dataset/report/{table.html/piegraph.gif/tabel.txt}
$path=$Q->path_info();
$path='' if ! defined $path;
if ($path =~ m:/$:)	{ $path.='index.html'; }
$path=cs::Pathname::norm($path);	# parse ".." etc
@path=grep(length,split(m:/+:,$path));

if (@path)
  { $DataSet=shift(@path);
    if (@path)
      { $Report=shift(@path);
	if (@path)
	  { $Component=shift(@path);
	  }
      }
  }

$LogPattern=$ENV{QUERY_STRING};
push(@errs,"QS=[$LogPattern]");

# ok, now allow for form input to over-ride things
if (defined ($_=$Q->param(DATASET)))
	{ $DataSet=$_; }
if (defined ($_=$Q->param(REPORT)))
	{ $Report=$_; }
if (defined ($_=$Q->param(COMPONENT)))
	{ $Component=$_; }
if (defined ($_=$Q->param(LOGPATTERN)))
	{ $LogPattern=$_; }
if (defined ($_=$Q->param(DX)))
	{ $DX=$_; }
if (defined ($_=$Q->param(DY)))
	{ $DY=$_; }
else	{ $DY=$DX; }

# sanity check the request
if (! -d "$StatsDir/$DataSet/.")
	{ push(@errs,"no such data set \"$DataSet\"");
	}
else	{ $LogFormat=$DataSets{$DataSet}->{LOGFORMAT};
	}

if (! exists $Reports{$Report})
	{ push(@errs,"unknown report type \"$Report\"");
	}

if ($Component eq 'index.html')		{ $Format=HTML;
					  $ContentType='text/html';
					}
elsif ($Component eq 'table.html')	{ $Format=HTML;
					  $ContentType='text/html';
					}
elsif ($Component eq 'table.txt')	{ $Format=TEXT;
					  $ContentType='text/plain';
					}
elsif ($Component eq 'piegraph.gif')	{ $Format=PIEGRAPH;
					  $ContentType='image/gif';
					}
elsif ($Component eq 'bargraph.gif')	{ $Format=BARGRAPH;
					  $ContentType='image/gif';
					}
else	{ push(@errs,"bad report request \"$Component\"");
	}

if ($Report eq WEEKLY)
	{ $Format=HTML;
	  $LogPattern=log_range(time-7*$DAY,7);
	  @Logs=match_logs($LogPattern);
	}
else
{
  @ptn=();
  for (grep(length,split(/[\s,]+/,$LogPattern)))
	{ $PTN=uc($_);
	  if (defined $LogPatterns{$PTN})
		{ push(@ptn,
			grep(length,
				split(/[\s,]+/,
					$LogPatterns{$PTN}->{PATTERN})));
		}
	  elsif (m:^(\*|199[67])-(\*|\d\d)\/(\*|\d\d)\*?\/(\*|\d\d)$:)
		{ s:/(\d\d)/:/$1*/:;	# allow for dow extension
		  push(@ptn,$_);
		}
	  else	{ push(@errs,"invalid year-mm/dd/hh log spec \"$_\"");
		}
	}

  $LogPattern=join(',',@ptn);
  if (! (@Logs=match_logs($LogPattern)))
	{ push(@errs,"$DataSet: no logs matching pattern \"$LogPattern\"");
	}
}

if (! length $DataSet
 || ! length $Report
 || ! length $Component
 || ! length $LogPattern)
	{ report_form($Q);
	print "[", join('|',$DataSet,$Report,$Component,$LogPattern), "]<BR>\n";

fail(@errs) if @errs;
	  exit 0;
	}

# activate the relevant tables
for (@{$Reports{$Report}->{TABLES}})
	{ $Data{$_}=new cs::Stats $TableUnits{$_};
	}

out('');

print header($ContentType);

$title="${DataSet}{$LogPattern} - $Reports{$Report}->{TITLE}";
if ($Format eq HTML)
	{ print title($title), "\n", h1($title), "\n";
	}

if ($Component eq 'index.html')
	{ print "[ ", comphref('table.txt','plain text'), "\n";
	  print "| ", comphref('table.html','HTML table'), "\n";
	  print "| ", comphref('piegraph.gif','Pie Graph'), "\n";
	  print "| ", comphref('bargraph.gif','Bar Graph'), "\n";
	  print "]\n";
	}
else	{ report('-',$Report,$Format,$Ordering);
	}

exit 0;

################################

# put off reading the logs until needed
# this way wrappers can be fast
sub need_logs
	{
	  return if $got_logs;

	  for (@Logs)
		{ load_logfile($_);
		}

	  $got_logs=1;
	}

sub makemap
	{ my($omap,$key)=@_;
	  my($nmap)={};
	  for (keys %$omap)
		{ $nmap->{$_}=$omap->{$_}->{$key};
		}
	  $nmap;
	}
sub comphref
	{ my($component,$title)=@_;
	  a({ HREF => repref(undef,undef,$component) }, $title );
	}
sub repref
	{ my($dataset,$report,$component,$query)=@_;
	  $query=$LogPattern	if ! defined $query;
	  $component=$Component	if ! defined $component;
	  $report=$Report	if ! defined $report;
	  $dataset=$DataSet	if ! defined $dataset;

	  $Q->url()."/$dataset/$report/$component?$query";
	}

sub fail
	{ my(@why)=@_;

	  print header(-title=>"Failure trying to process request"), "\n";
	  print h1("Failure trying to process request"), "\n",
		ul(map(li($_),@why)), "\n";

	  exit 0;
	}

sub match_logs
	{ my($ptn)=shift;
	  my(@matched)=();
	  my($glob);
	  local($_);

	  for (split(/,/,$ptn))
		{ $glob="$StatsDir/$DataSet/logs/$_";
		  # print STDERR "match $glob ...\n";
		  push(@matched,grep(-f $_,glob($glob)));
		}

	  @matched;
	}

sub log_range
	{ my($start,$days)=@_;

	  @ptns=();
	  while ($days > 0)
		{ push(@ptns,date_ptn($start));
		  $start+=$DAY;
		  $days--;
		}

	  wantarray ? @ptns : join(',',@ptns);
	}

sub date_ptn
	{ my($when)=@_;
	  my($tm)=cs::Date::time2tm($when,1);
	  sprintf("%04d-%02d/%02d%3s/*",
			$tm->{YEAR},$tm->{MON},$tm->{MDAY},
			$cs::Date::wday_names[$tm->{WDAY}]);
	}

sub ptn_yesterday
	{ my($y)=$Now-24*3600; ptn_day($y); }
sub ptn_last7
	{ my($n);
	  my(@p)=();
	  for $n (1..7) { push(@p,ptn_day($Now-$n*24*3600)); }
	  join(',',@p);
	}
sub ptn_lastWeek
	{ my($howmany)=shift;
	  $howmany=1 if ! defined $howmany;
	  my($d,$tm,$n);
	  my(@p)=();
	  $n=1;
	  FINDSUN:
	    while (1)
		{ $d=$Now-$n*24*3600;
		  $tm=cs::Date::time2tm($d,0);
		  last FINDSUN if $tm->{WDAY} == 0;
		  $n++;
		}

	  # skip back an arbitrary number of weeks
	  while (--$howmany > 0) { $n+=7; }

	  for (0..6) { push(@p,ptn_day($Now-($n+$_)*24*3600)); }

	  join(',',@p);
	}
sub ptn_lastMonth
	{ my($howmany)=shift;
	  $howmany=1 if ! defined $howmany;
	  my($tm)=cs::Date::time2tm($Now);

	  while ($howmany-- > 0)
	  	{ if ($tm->{MON} == 1)	{ $tm->{MON}=12; $tm->{YEAR}--; }
	  	  else			{ $tm->{MON}--; }
		}

	  sprintf("%4d-%02d/*/*", $tm->{YEAR}, $tm->{MON});
	}
sub ptn_day
	{ my($tm)=cs::Date::time2tm(shift,0);
	  sprintf("%4d-%02d/%02d*/*", $tm->{YEAR},
				      $tm->{MON},
				      $tm->{MDAY});
	}

sub report_form
	{
	  print header(-title=>"Request Report"), "\n";
	  print h1("Request Report"), "\n";
	  print start_form(),
		  "<TABLE>\n",
		    "<TR><TD>DataSet<TD>\n",
			popup_menu(-name=>DATASET,
					-values=>[sort keys %DataSets],
					-default=>'cisco',
					-labels=>makemap(\%DataSets,TITLE)),
			"\n",
		    "<TR><TD>Report<TD>\n",
			popup_menu(-name=>REPORT,
					-values=>[sort keys %Reports],
					-default=>LINKIN,
					-labels=>makemap(\%Reports,TITLE)),
			"\n",
		    "<TR><TD>Report Format<TD>\n",
			popup_menu(-name=>COMPONENT,
					-values=>[sort keys %Components],
					-default=>'table.html',
					-labels=>makemap(\%Components,TITLE)),
			"\n",
		    "<TR><TD>Sample Space<TD>\n",
			popup_menu(-name=>LOGPATTERN,
					-values=>[sort keys %LogPatterns],
					-default=>'custom',
					-labels=>makemap(\%LogPattern,TITLE)),
			"\n",
		  "</TABLE>\n",
			submit(-name=>'submit',-value=>'Submit Request'),
		end_form();
	}

sub pick_dataset
	{
	  print header(-title=>"Which dataset do you want to process?"), "\n";
	  print h1("Which dataset do you want to process?"), "\n",
	        ul(map(li($_),@DataSets)), "\n";
	}

sub pick_report
	{
	  print header(-title=>"Which report do you want to produce?"), "\n";
	  print h1("Which report do you want to produce?"), "\n",
		"Available reports:\n",
	        ul(map(li(describe_report($_)),sort keys %Reports)), "\n";
	}

sub describe_report
	{ my($rkey)=@_;

	  # print STDERR "describe_report($rkey)\n";
	  if (! defined $Reports{$rkey})
		{ return "$rkey - unknown report!";
		}

	  "$rkey - $Reports{$rkey}->{TITLE}";
	}

sub report
	{ my($where,$table,$format,$ordering)=@_;

	  local($ReportHandle);

	  if ($where eq '-')
		{ $ReportHandle=STDOUT;
		}
	  else
	  { $ReportHandle=ReportTo;

	    my($wdir)=cs::Pathname::dirname($where);
	    cs::Pathname::makedir($wdir);

	    if (! open($ReportHandle,"> $where\0"))
				{ warn "$cmd: can't write to $where: $!";
				  return undef;
				}
	  }

	  my($oldSelect)=select($ReportHandle);

	  my($retval);

	  need_logs();
	  $table="rep$table";
	  $retval=&$table($format,$ordering);

	  select($oldSelect);

	  $retval;
	}

sub repTable
	{ my($format,$ordering,$data,$title,$labelfn)=@_;
	  my(@keys)=reverse $data->Keys($ordering);
	  my($key);

	  if ($format eq TEXT)
		{ print "$title\n";
		  print "\n";
	  	  print "       Data    Hits Directory\n";

	  	  for $key (@keys)
			{ printf("%11d %7d %s\n",
				$data->Count($key),
				$data->Hits($key),
				$data->LabelKey($key,$labelfn));
			}
		}
	  elsif ($format eq HTML)
		{ print "<TABLE\n";
		  print "  <CAPTION><BIG><B>$title</B></big></CAPTION>\n";
		  print "  <TR><TH ALIGN=RIGHT>Data\n";
		  print "      <TH ALIGN=RIGHT>Hits\n";
		  print "      <TH ALIGN=LEFT >Entry\n";

	  	  for $key (@keys)
			{ print "<TR>",
				"<TD ALIGN=RIGHT>", $data->Count($key),
				"<TD ALIGN=RIGHT>", $data->Hits($key),
				map(("<TD ALIGN=LEFT>", $_), $data->LabelKey($key,$labelfn)),
				"\n";
			}

		  print "</TABLE>\n";
		}
	  elsif ($format eq PIEGRAPH)
		{ my($im)=$data->PieGraph(0.02,$DX,$DY,$labelfn);
		  $im->Write($ReportHandle);
		}
	  elsif ($format eq BARGRAPH)
		{ my($im)=$data->BarGraph($DX,$DY,$labelfn);
		  $im->Write($ReportHandle);
		}
	  else
	  { warn "can't print report in format \"$format\"";
	  }
	}

sub slice2hour
	{ shift; int(shift(@_)/12);	# 5 minute timeslices
	}
sub slice2dayhour
	{ slice2hour(@_)%24;
	}
sub slice2weekhour
	{ slice2hour(@_)%(7*24);
	}
sub repHOURS
	{ my($format,$ordering)=@_;
	  my($hourly)=$Data{TIMESLICE}->Remap(\&slice2hour);
	  my(@hkeys)=$hourly->Keys();
	  for $i (cs::Math::min(@hkeys)
		..cs::Math::max(@hkeys))
		{ $hourly->Hit($i,0,0); }
	  $hourly->Order(\&cs::Math::numeric);
	  repTable($format,$ordering,$hourly,'Transfer by hour');
	}
sub repDHOURS
	{ my($format,$ordering)=@_;
	  my($hourly)=$Data{TIMESLICE}->Remap(\&slice2dayhour);
	  for $i (0..23) { $hourly->Hit($i,0,0); }
	  $hourly->Order(\&cs::Math::numeric);
	  repTable($format,$ordering,$hourly,'Transfer by daily period');
	}
sub repWHOURS
	{ my($format,$ordering)=@_;
	  my($hourly)=$Data{TIMESLICE}->Remap(\&slice2weekhour);
	  for $i (0..7*24-1) { $hourly->Hit($i,0,0); }
	  $hourly->Order(\&cs::Math::numeric);
	  repTable($format,$ordering,$hourly,'Transfer by week period');
	}
sub LabelTime
	{ my($this,$time)=@_;

	  return undef if int($time/240)%15;

	  my($tm)=cs::Date::time2tm($time,1);

	  my($hh)=$tm->{HH};

	  return undef unless $hh%4 == 0;

	  my($label)=sprintf("%02d:%02d",$tm->{HH},$tm->{MM});

	  if ($hh == 0)
		{ $label=$cs::Date::Wday_names[$tm->{WDAY}]
			.' '
			.$tm->{MDAY}
		       .'/'.$cs::Date::Mon_names[$tm->{MON}-1];
		}

	  $label;
	}
sub repLINKIN
	{ my($format,$ordering)=@_;
	  $Data{IBPS}->Order(\&cs::Math::numeric,1);
	  repTable($format,$ordering,$Data{IBPS},'incoming link traffic',\&LabelTime);
	}
sub repLINKOUT
	{ my($format,$ordering)=@_;
	  $Data{OBPS}->Order(\&cs::Math::numeric,1);
	  repTable($format,$ordering,$Data{OBPS},'outgoing link traffic',\&LabelTime);
	}
sub repURLS
	{ my($format,$ordering)=@_;
	  repTable($format,$ordering,$Data{URLS},'Transfer by URL');
	}
sub repUDIRS
	{ my($format,$ordering)=@_;
	  repTable($format,$ordering,$Data{URLDIRS},'Transfer by URL directory');
	}
sub repRHOSTS
	{ my($format,$ordering)=@_;
	  repTable($format,$ordering,$Data{RHOSTS},'Transfer by requested host');
	}
sub repLHOSTS
	{ my($format,$ordering)=@_;
	  repTable($format,$ordering,$Data{LHOSTS},'Transfer by requesting host');
	}

sub P { print "<P>\n"; }
sub BR{ print "<BR>\n"; }
sub repWEEKLY
	{ my($format,$ordering)=@_;	# ignored

	  repTOTALS(); P();
	  repTable($format,$ordering,$Data{TOPDOM},'Top level domains',\&LabelTopLevel);
	  repLHOSTS($format,$ordering);
	  repUDIRS($format,$ordering);

	  my($type);

	  repTable($format,$ordering,$Data{URLTYPES},'URL Types');

	  for $type (HTML,GIF,JPEG)
		{ $grepptn='\.'.lc($type).'$';
		  repGREP($format,$ordering,$Data{URLS},\&grepfn,"Transfer by file type ($type)");
		}

	  repURLS($format,$ordering);
	}

sub grepfn
	{ my($this,$key)=@_;

	  $key =~ /$grepptn/i;
	}

sub repGREP
	{ my($format,$ordering,$stats,$grepfn,$title)=@_;
	  my($o)=$stats->Grep($grepfn);

	  repTable($format,$ordering,$o,$title);
	}

sub repTOTALS
	{
	  my($url);
	  my($tHits,$tData)=(0,0);

	  for $url ($Data{URLS}->Keys())
		{ $tHits+=$Data{URLS}->Hits($url);
		  $tData+=$Data{URLS}->Count($url);
		}

	  printf("Total Hits: %7d Total Data: %11d\n",$tHits,$tData);

	  1;
	}

sub LabelTopLevel
	{ my($this,$key)=@_;

	  defined $Codes->{$key}
		? ($key, $Codes->{$key})
		: $key;
	}

sub repHITS
	{ my($url,$udir);
	  local($UDData)=new cs::Stats;
	  local($UDHits)=new cs::Stats;

	  for $url (sort $UData->Keys())
		{ if ($url =~ m:/+$/:)
			{ $udir=$`; }
		  else	{ $udir=cs::Pathname::dirname($url); }

		  $UDHits->Hit($udir,$UData->Hits($url));
		  $UDData->Hit($udir,$UData->Count($url));
		}

	  print "Directories by data transfer (bytes)\n";
	  print "       Data    Hits Directory\n";
	  for $url (reverse $UDData->Keys($Ordering))
		{ printf("%11d %7d %s\n",
			$UDData->Count($url),$UDHits->Count($url),$url);
		}

	  print "Pages by data transfer (bytes)\n";
	  print "       Data    Hits File\n";
	  PAGE:
	    for $url (reverse $UData->Keys($Ordering))
		{ next PAGE unless $url =~ /\.html?$/oi;
		  printf("%11d %7d %s\n",
			$UData->Count($url),$UData->Hits($url),$url);
		}

	  print "Files by data transfer (bytes)\n";
	  print "       Data    Hits File\n";
	  for $url (reverse $UData->Keys($Ordering))
		{ printf("%11d %7d %s\n",
			$UData->Count($url),$UData->Hits($url),$url);
		}

	  print "\n";

	  1;
	}

sub load_codes
	{ my($file)=shift;
	  my($s);

	  if (! defined ($s=new cs::Source PATH, $file))
		{ warn "$cmd: can't open $file: $!";
		  return undef;
		}

	  local($_);
	  my($Codes)={};

	  while (defined ($_=$s->GetLine()) && length)
		{ if (/^([a-z][a-z])\s+(\S.*\S)/oi)
			{ $Codes->{uc($1)}=$2;
			}
		}

	  $Codes;
	}

sub load_logfile
	{ my($fname,$wantfn)=@_;
	  my($s);

	  return undef if ! defined ($s=new cs::Source PATH, $fname);

	  load_log($s,$fname,$wantfn);
	}

sub load_log
	{ local($s,$fname,$wantfn)=@_;

	  print STDERR "load_log($fname)...\n";

	  $wantfn=sub{1} if ! defined $wantfn;

	  local($_);
	  my($L);

	  LOG:
	    while (defined ($_=$s->GetLine()) && length)
		{ if ($LogFormat eq WWW)
			{ wwwlogline($_);
			}
		  elsif ($LogFormat eq CISCO)
			{ ciscologline($_);
			}
		  else
		  { die "don't know how to process LogFormat \"$LogFormat\"";
		  }
		}
	}

sub ciscologline
	{ local($_)=shift;
	  my($L);

	  return unless defined ($L=cs::Hier::a2h($_));

	  # catch duplicates
	  return if defined $_ciscoTimes{$L->{TIME}};
	  $_ciscoTimes{$L->{TIME}}=1;

	  if ($Data{IBPS} || $Data{OBPS})
	  	{ my($ibps,$obps)=(0,0);

		  for (keys %{$L->{CHANNELS}})
			{ $ibps+=$L->{CHANNELS}->{$_}->{INRATE};
			  $obps+=$L->{CHANNELS}->{$_}->{OUTRATE};
			}

		  if ($Data{IBPS})
			{ $Data{IBPS}->Hit($L->{TIME},$ibps);
			}
		  if ($Data{OBPS})
			{ $Data{OBPS}->Hit($L->{TIME},$obps);
			}
		}
	}

sub wwwlogline
	{ local($_)=shift;
	  my($L);

	  return unless defined ($L=new cs::WWW::Log $_);

	  # skip errors and noise
	  return unless $L->{SIZE} > 0;

	  # weekends
	  # return if $L->{TM}->{WDAY} == 0 || $L->{TM}->{WDAY} == 6;

	  # print "hh:mm=$t_hh:$t_mm\n";

	  # outside work hours
	  # return if $L->{TM}->{HH} < 8 || $L->{TM}->{HH} > 17;

	  # XXX - too slow
	  # $L->{HOST}=hname($L->{HOST});

	  { local($_)=$L->{URL};
	    local($HOST)=$L->{HOST};
	    if (! defined $HOST)
		{ print STDERR "L=", cs::Hier::h2a($L,1,0), "\n";
		die;
		}

	    return if ! &$wantfn($L);

	    if ($Data{TIMESLICE})
		{ my($slice)=$L->{TIME};
		  $slice=int($slice/300); # five-minute chunks
		  $Data{TIMESLICE}->Hit($slice,$L->{SIZE});
		}

	    if ($Data{LHOSTS})
		{ $Data{LHOSTS}->Hit($HOST,$L->{SIZE});
		  if ($Data{TOPDOM}
		   && $HOST =~ m:\.([^.]+)$:o)
			{ $d=$1;
			  if ($d =~ /^\d+$/)
				{ $d='NUMERIC';
				}
			  else	{ $d=uc($d);
				}

			  $Data{TOPDOM}->Hit($d,$L->{SIZE});
			}
		}
	  }

##### print STDERR "L=", cs::Hier::h2a($L,1), "\n";
	  if ($Data{RHOSTS} || $Data{URLDIRS} || $Data{URLS})
	    {
	      if (defined $L->{RQ} && ($rq=$L->{RQ})->{REQUEST} eq GET)
		{ $url=$rq->{URL};
		  $url.='index.html' if $url =~ m:/$:;

		  # out($url);
		  if ($Data{RHOSTS}
		   && $url =~ m|^http://([^/?#]+)|io)
			{ $rhost=lc($1);
			  $rhost =~ s/:0*80$//;
			  $Data{RHOSTS}->Hit($rhost,$L->{SIZE});
			}

		  $Data{URLS} && $Data{URLS}->Hit($url,$L->{SIZE});

		  ($urldir=$url) =~ s:/+[^/]*$::;
		  $urldir='/' if ! length $urldir;
		  $Data{URLDIRS} && $Data{URLDIRS}->Hit($urldir,$L->{SIZE});
		  if ($Data{URLTYPES})
			{ my($type);

			  if ($url =~ m:\.([^/.]+)$:) { $type=lc($1); }
			  else			    { $type=OTHER; }

			  $Data{URLTYPES}->Hit($type,$L->{SIZE});
			}
		}
	    }
	}

sub hname
	{ my($h)=shift;

	  return $h unless $h =~ /^\d+\.\d+\.\d+\.\d+$/;

	  if (! defined $_HName{$h})
	  	{ my(@names);
		  my($t)=time;
		  my($a)=cs::Net::a2addr($h);

		  if (! defined $a)
			{ warn "$h: not converted to address\n";
			  return $h;
			}

		  print STDERR "looking up $h ...";

		  # sanity check because idiots put crud in the DNS
	  	  @names=grep(/^[-\w]+(\.[-\w]+)$/,
			      cs::Net::hostnames($a));

		  print STDERR " [@names]";
		  print " (", time-$t, " seconds)\n";
		  if (@names)
			{ $_HName{$h}=shift(@names);
			}
		  else	{ $_HName{$h}=$h;
			}
		}

	  $_HName{$h};
	}

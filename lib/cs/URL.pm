#!/usr/bin/perl
#
# Code to handle URLs.
#	- Cameron Simpson <cs@zip.com.au> 11jan1996
#

=head1 NAME

cs::URL - manipulate URLs

=head1 SYNOPSIS

use cs::URL;

=head1 DESCRIPTION

This module implements methods for dealing with URLs.

=cut

use strict qw(vars);

##BEGIN { use cs::DEBUG; cs::DEBUG::using(__FILE__); }

use cs::Misc;
use cs::Net::TCP;
use cs::Upd;
use cs::Source;
use cs::HTML;
use cs::HTTP;
use cs::HTTP::Auth;
use cs::HTTPS;

package cs::URL;

=head1 GENERAL FUNCTIONS

=over 4

=item get(I<url>,I<follow>)

Create a B<cs::URL> object from the I<url> supplied
and call the B<Get> method below.
If the optional argument I<follow> is true
then redirections (301 and 302 response codes)
will be followed.

=cut

sub get($;$$$)
{ my($url,$follow,$sinkfile,$ignerrs)=@_;
  $follow=0 if ! defined $follow;
  $ignerrs=[] if ! ref $ignerrs;

  my($U)=new cs::URL $url;
  return () if ! defined $U;
  $U->Get($follow,$sinkfile,$ignerrs);
}

=item head(I<url>)

Create a B<cs::URL> object from the I<url> supplied
and call the B<Head> method below.

=cut

sub head($)
{ my($url)=@_;

  my($U)=new cs::URL $url;
  return () if ! defined $U;
  $U->Head();
}

=item urls(I<url>,I<results>,I<inline>)

Return all URLs reference from the page I<url>
via the hashref I<results>,
which on resturn will have URLs as the hash keys
and the title of each link as the hash value.
If the optional argument I<inline> is true,
return ``inline'' URLs
(i.e. specified by B<SRC=> and B<BACKGROUND=> attributes)
rather than references (B<HREF=>).

=cut

sub urls($$;$)
{ my($url,$urls,$inline)=@_;
  $inline=0 if ! defined $inline;

  my($U)=new cs::URL $url;
  return 0 if ! defined $U;

  $U->URLs($urls,$inline);
  1;
}

=item urlPort(I<scheme>,I<port>)

Given a I<scheme> and I<port>,
return the numeric value of I<port>.
If the I<port> parameter is omitted,
return the default port number for I<scheme>.

=cut

sub urlPort($;$)
{ my($scheme,$port)=@_;
  $scheme=uc($scheme);

  (defined $port && length $port
      ? cs::Net::portNum($port)
      : length $scheme
	  ? grep($_ eq $scheme,HTTP,FTP,GOPHER,HTTPS,NEWS,SNEWS)
		  ? cs::Net::portNum($scheme)
		  : ''
	  : '');
}

=item undot(I<url>)

Given the text of an I<url>,
remove and B<.> or B<..> components.

=cut

sub undot($)
{ local($_)=@_;

  my $pfx = "";

  if (m(^\w+://[^/]+))
  { $pfx=$&; $_=$';
  }

  # strip newlines
  s:[\r\n]+\s*::g;

  # strip /dir/../
  s:^(/*)((\.\.?/))+:/:;
  while (s|/+[^/?#]+/+\.\./+|/|)
  {}

  $_=$pfx.$_;

  s/^\s+//;
  s/\s+$//;

  $_;
}

=item search(I<engine>,I<query>,I<maxhits>)

Return a URL string that can be used to query the specified search engine
with the supplied query string.
The optional parameter I<maxhits>
specifies the desire number of hits (or hits per page)
to return; not all search engines support such an option.

=cut

sub search($$;$)
{ my($engine,$query,$maxhits)=@_;
  $engine=uc($engine);
  $maxhits=100 if ! defined $maxhits;

  my $map = _getSearchEngineTable($query,$maxhits);
  if (! exists $map->{$engine})
  { warn "$::cmd: no search engine named \"$engine\"";
    return undef;
  }

  my @E = @{$map->{$engine}};
  my $url = shift(@E);
  my $data= shift(@E);
  my $long = "$url?"
	   . join("&",map(_enquery($_).(defined $data->{$_} ? "=" : "")._enquery($data->{$_}),
				sort keys %$data));

  return $long;
}

sub _enquery
{ cs::HTML::quoteQueryField(shift);
}

sub _getSearchEngineTable($$)
{ my($query,$maxhits)=@_;

  my $map={
	WEB =>	[ 'http://web/websearch/cgi-bin/htsearch.cgi',
		{ 'restrict'	=> '',
		  'exclude'	=> '',
		  'config'	=> 'htdig',
		  'method'	=> 'and',
		  'format'	=> 'builtin-long',
		  'sort'	=> 'score',
		  'words'	=> $query,
		  'page'	=> 1,
		},
		],
	JPL =>	[ 'http://search.jpl.nasa.gov:8080/cgi-bin/htsearch',
		{ 'words'	=> $query,
		},
		],
	WIKIPEDIA => [ 'http://www.wikipedia.org/w/wiki.phtml',
		{ 'search'	=> $query,
		},
		],
	EVERYTHING =>
		[ 'http://www.everything2.org/index.pl',
		{ 'node'	=> $query,
		  'soundex'	=> 1,
		  'match_all'	=> 1,
		},
		],
	DAYPOP =>	[ 'http://www.daypop.com/search',
		{ 'q'		=> $query,
		  't'		=> 'a',
		},
		],
	DMOZ =>	[ 'http://search.dmoz.org/cgi-bin/search',
		{ 'search'	=> $query,
		  'all'		=> 'no',
		  'cat'		=> '',
		  't'		=> 'b',
		},
		],
	SOURCEFORGE =>
		[ 'http://sourceforge.net/search/',
		{ 'type_of_search' => 'soft',
		  'exact'	=> 1,
		  'words'	=> $query,
		},
		],
	TAMARA =>	[ 'http://www.tamarasanime.com/search.cfm',
		{ 'dis'		=> '',
		  'query'	=> $query,
		  'hdr'		=> '',
		},
		],
	W3C_VALIDATE,
		[ 'http://validator.w3.org/check',
		{ 'uri'		=> $query,
		  'charset'	=> '(detect+automatically)',
		  'doctype'	=> '(detect+automatically)',
		  'ss'		=> '',
		  'outline'	=> '',
		  'sp'		=> '',
		  'noatt'	=> '',
		},
		],
	YAHOO =>	[ 'http://au.search.yahoo.com/search/aunz',
		{ 'o'		=> 1,
		  'p'		=> $query,
		  'd'		=> 'y',
		  'za'		=> 'default',
		  'h'		=> 'c',
		  'g'		=> '0',
		  'n'		=> $maxhits,
		},
		],
#	YAHOO =>	[ 'http://search.yahoo.com.au/search',
#		{ 'o'		=> 1,
#		  'p'		=> $query,
#		  'za'		=> 'default',
#		  'h'		=> 'c',
#		  'g'		=> 0,
#		  'n'		=> $maxhits,
#		},
#		],
	YAHOOGROUPS =>	[ 'http://groups.yahoo.com/search',
		{ 'query'	=> $query,
		  'submit'	=> 'Search',
		},
		],
	YAHOO_VIDEO =>	[ 'http://video.search.yahoo.com/search/video',
		{ 'ei'		=> 'UTF-8',
		  'p'		=> $query,
		},
		],
	FINDSAMETEXT => [ 'http://www.findsame.com/submit.cgi',
		{ 'rawtext'	=> $query,
		},
		],
	FINDSAMEURL => [ 'http://www.findsame.com/submit.cgi',
		{ 'url'		=> $query,
		},
		],
	SPEAKEASY_RPMFIND,
		[ 'http://speakeasy.rpmfind.net/linux/rpm2html/search.php',
		{ 'query'	=> $query,
		},
		],
	PBONE =>	[ 'http://rpm.pbone.net/index.php3',
		{ 'stat'	=> 3,
		  'search'	=> $query,
		},
		],
	SUNDOC =>	[ 'http://docs.sun.com/',
		{ 'q'		=> $query,
		},
		],
	SUNDOC2 =>[ 'http://search.sun.com/query.html',
		{ 'col'		=> 'www',
		  'op0'		=> '%2B',
		  'fl0'		=> '',
		  'ty0'		=> 'w',
		  'tx0'		=> $query,
		  'op1'		=> '%2B',
		  'fl1'		=> '',
		  'ty1'		=> 'w',
		  'tx1'		=> '',
		  'op2'		=> '-',
		  'fl2'		=> '',
		  'ty2'		=> 'w',
		  'tx2'		=> '',
		  'dt'		=> 'an',
		  'inthe'	=> '604800',
		  'amo'		=> '7',
		  'ady'		=> '4',
		  'ayr'		=> '2000',
		  'bmo'		=> '7',
		  'bdy'		=> '11',
		  'byr'		=> '2000',
		  'nh'		=> $maxhits,
		  'rf'		=> '0',
		  'lk'		=> '2',
		  'qp'		=> '',
		  'qt'		=> '',
		  'qs'		=> '',
		  'qc'		=> '',
		  'pw'		=> '455',
		  'qm'		=> '0',
		  'st'		=> '1',
		  'rq'		=> '0',
		  'ql'		=> 'a',
		}
		],
	JDK =>	[ 'http://www.siteforum.com/servlets/sfs',
		{ 'c'		=> 'Default',
		  't'		=> 'onSunSearched',
		  'b'		=> '960992632796',
		  'i'		=> '960992632796',
		  'l'		=> 0,
		  's'		=> 'sjI04o56Gsoc4BOA',
		  'top'		=> 0,
		  'project'	=> 'jdk13',
		  'keywords'	=> $query,
		  'und'		=> 1,
		  'maxcount'	=> $maxhits,
		}
		],
	ODP =>	[ 'http://search.dmoz.org/cgi-bin/osearch',
		{ 'search'	=> $query,
		  'cat'		=> '',
		  't'		=> 'b',
		  'fb'		=> 0,
		  'fo'		=> 0,
		  'all'		=> 'no',
		}
		],
	AARN =>	[ 'http://mirror.aarnet.edu.au/cgi-bin/SearchArchive',
	  	{ 'query_string'	=> $query,
		  'archive'		=> 'all',
		},
		],
	PLANETMIRROR =>
		[ 'http://www.planetmirror.com/cgi-bin/SearchArchive.pl',
		{ 'query'		=> $query,
		  'maxlines'		=> $maxhits,
		  'indexdir'		=> '%2Fserver%2Fwww%2Fhtdocs%2Fglimpse%2F7%2Findex',
		  'Go.x'		=> 22,
		  'Go.y'		=> 12,
		},
		],
	SYSTRANSLINKS =>
		[ 'http://www.systranlinks.com/systran/links',
		{ 'lp'		=> 'de_en',
		  'url'		=> $query,
		},
		],
	CISCO =>	[ 'http://www-search.cisco.com/pcgi-bin/search/public.pl',
		{ 'q'		=> $query,
		  'num'		=> $maxhits,
		  'searchselector' => 0,
		},
		],
	BRITANNICA =>
		[ 'http://search.britannica.com/bcom/search/results/1,5843,,00.html',
	  	{ 'p_query0'		=> $query,
		  'chooseSearch'	=> 0,
		},
		],
	JAVABUG =>[ 'http://search.java.sun.com/search/java/index.jsp',
		{
		  'qt'		=> $query,
		  'col'		=> 'obug',
		  'category'	=> '',
		  'state'	=> '',
		   'query'	=> $query,
		},
		],
	LYCOS =>
		[ 'http://lycospro.lycos.com/cgi-bin/pursuit',
	  	{ 
		  'mtemp'	=> 'nojava',
		  'etemp'	=> 'error_nojava',
		  'rt'		=> 1,
		  'npl'		=> 'matchmode=or&adv=1',
		  'query'	=> $query,
		  'maxhits'	=> $maxhits,
		  'cat'		=> 'lycos',
		  'npl1'	=> 'ignore=fq',
		  'fq'		=> '',
		  'lang'	=> '',
		  'rtwm'	=> 45000,
		  'rtpy'	=> 2500,
		  'rttf'	=> 1000,
		  'rtfd'	=> 5000,
		  'rtpn'	=> 2500,
		  'rtor'	=> 5000,
		},
		],
	HOTBOT =>	[ 'http://www.hotbot.com/text/default.asp',
	  	{ 'SM'	=> 'MC',
		  'MT' => $query,
		  'DC' => $maxhits,
		  'DE' => '2',
		  'AM0' => 'MC',
		  'AT0' => 'words',
		  'AW0' => '',
		  'AM1' => 'MN',
		  'AT1' => 'words',
		  'AW1' => '',
		  'savenummod' => '2',
		  'date' => 'within',
		  'DV' => '0',
		  'DR' => 'newer',
		  'DM' => '1',
		  'DD' => '1',
		  'DY' => '99',
		  'LG' => 'any',
		  'FS' => '',
		  'Domain' => '',
		  'RD' => 'RG',
		  'RG' => 'all',
		  'PS' => 'A',
		  'PD' => '',
		  'search' => 'SEARCH',
		  'NUMMOD' => '2',
		},
		],
	ANONYMIZER =>
		[ 'http://www.anonymizer.com/3.0/anonymizescript.cgi',
	  	{ 'url'	=> $query,
		  'Go'  => 'Go',
		},
		],
	NORTHERNLIGHT =>
		[ 'http://www.NorthernLight.com/nlquery.fcg',
		{ 'dx'		=> '1004',
		  'qr'		=> $query,
		  'qt'		=> '',
		  'pu'		=> '',
		  'qu'		=> '',
		  'si'		=> '',
		  'la'		=> 'All',
		  'qc'		=> 'All',
		  'd1'		=> '',
		  'd2'		=> '',
		  'rv'		=> '1',
		  'search.x'	=> '10',
		  'search.y'	=> '10'
		},
		],
	FTPSEARCH =>
		[ 'http://ftpsearch.lycos.com/cgi-bin/search',
	  	{ 'form'	=> 'medium',
		  'query'	=> $query,
		  'doit'	=> 'Search',
		  'type'	=> 'insensitive multiple substrings search',
		  'hits'	=> $maxhits,
		  'matches'	=> $maxhits,
		  'hitsprmatch'	=> $maxhits,
		  'limdom'	=> '',
		  'limpath'	=> '',
		  'f1'		=> 'Count',
		  'f2'		=> 'Mode',
		  'f3'		=> 'Size',
		  'f4'		=> 'Date',
		  'f5'		=> 'Host',
		  'f6'		=> 'Path',
		  'header'	=> 'all',
		  'sort'	=> 'date',
		  'strlen'	=> 200,
		},
		],
	LINUXGOOGLE =>
		[ 'http://www.google.com/linux',
	  	{ 'q'	=> $query,
	  	  'num'	=> $maxhits,
		},
		],
	UNISCI =>	[ 'http://unisci.com/cgi-local/AT-unisc1archives1search.cgi',
	  	{ 'sp'	=> 'sp',
		  'mode'=> 'simple',
		  'search'=> $query,
		},
		],
	FROOGLE =>[ 'http://froogle.google.com/froogle',
		{ 'as_q'	=> $query,
		  'num'		=> $maxhits,
		  'btnG'	=> 'Froogle+Search',
		  'as_epq'	=> '',
		  'as_oq'	=> '',
		  'as_eq'	=> '',
		  'price'	=> 'under',
		  'price0'	=> '',
		  'price1'	=> '',
		  'price2'	=> '',
		  'as_occt'	=> 'any',
		  'cat'		=> 0,
		},
		],
	GOOGLE =>	[ 'http://www.google.com/search',
	  	{ 'q'	=> $query,
		  'num'	=> $maxhits,
		  'sa'	=> 'Google Search',
		},
		],
	GOOGLENEWS =>	[ 'http://news.google.com.au/news',
		{ 'hl'	=> 'en',
		  'ned'	=> 'au',
	  	  'q'	=> $query,
		  'btnG'=> 'Search+News',
		},
		],
	EBAYITEM => [ 'http://cgi.ebay.com.au/ws/eBayISAPI.dll',
		  { 'ViewItem'		=> undef,
		    'item'		=> $query,
		  },
		],
	EBAY =>	[ 'http://search.ebay.com.au/search/search.dll',
		  { 'MfcISAPICommand'	=> 'GetResult',
		    'ht',		=> 1,
		    'SortProperty'	=> 'MetaEndSort',
		    'query'		=> $query,
		    'ebaytag1code'	=> 15,
		    'shortcut'		=> 2,
		    'maxRecordsReturned'=> 5*$maxhits,
		    'maxRecordsPerPage'	=> $maxhits,
		},
		],
	EBAYALL =>[ 'http://search.ebay.com/cgi-bin/texis/ebay/results.html',
	  	{ 'query'	=> $query,
		  'srchdesc'	=> 'y',
		  'maxRecordsReturned' => $maxhits*4,
		  'maxRecordsPerPage' => $maxhits,
		  'SortProperty' => 'MetaEndSort',
		  'ht'		=> 1,
		  'searchButton.x' => 1,
		  'searchButton.y' => 1,
		},
		],
	ONELOOK => [ 'http://www.onelook.com/',
		{ 'w'		=> $query,
		  'ls'		=> 'b',
		},
		],
	HTTP_WEBSTER => [ 'http://smac.ucsd.edu/cgi-bin/http_webster',
	  	{ 'isindex'	=> $query,
		  'method'	=> 'approx',
		},
		],
	FRESHMEAT =>
		[ 'http://freshmeat.net/search/',
	  	{ 'q'	=> $query,
		},
		],
	WEBSHOTS =>
		[ 'http://www.webshots.com/search/search.fcgi',
		{ 'words'	=> $query,
		  'x'		=> 0,
		  'y'		=> 0,
		},
		],
	GOOGLEIMAGESEARCH =>
		[ 'http://images.google.com/images',
		{ 'q'	=> $query,
		  'num'	=> $maxhits,
		  'hl'	=> 'en',
		  'safe'=> 'off',
		  'imgsafe' => 'off',
		},
		],
	YAHOOIMAGESEARCH =>
		[ 'http://images.search.yahoo.com/search/images',
		{ 'p'	=> $query,
		  'ei'	=> 'UTF-8',
		  'fl'	=> 0,
		  'x'	=> 'wrt',
		},
		],
	AVIMAGESEARCHER =>
		[ 'http://www.altavista.com/image/results',
		{ 'itag'	=> 'ody',
		  'q'		=> $query,
		  'kgs'		=> 1,
		  'kls'		=> 1,
		},
		],
	IMAGESURFER =>
		[ 'http://isurf.yahoo.com/cgi-bin/y/keyword_search.cgi',
	  	{ 'q'	=> $query,
		  'db'	=> '/data/global_keyword',
		},
		],
	'BYTE.COM',
		[ 'http://byte.com/search',
	  	{ 'queryText'	=> $query,
		},
		],
	'BYTE.DICT',
		[
		'http://www.techweb.com/encyclopedia/defineterm',
	  	{ 'term'	=> $query,
		},
		],
	METAGOPHER =>
		[ 'http://www.metagopher.com/nph-go.cgi',
	  	{ 'w'	=> $query,
		  'e'	=> 0,
		  't'	=> 0,
		  'n'	=> 1,
		  'h'	=> 1,
		  'g'	=> 1,
		  'i'	=> 0,
		},
		],
	MIRROR =>	[ 'http://mirror.aarnet.edu.au/cgi-bin/SearchArchive',
	  	{ 'query_string'	=> $query,
		  'archive'		=> 'all',
		},
		],
	METACRAWLER =>
		[ 'http://www.metacrawler.com/crawler',
	  	{ 'general'	=> 'index',
		  'method'	=> 0,	# 0=all, 1=any, 2=phrase
		  'target'	=> '',
		  'region'	=> '',	# ''=everywhere
		  'rpp'		=> $maxhits,	# results per page
		  'timeout'	=> 60,
		  'hpe'		=> $maxhits,	# results per source
		},
		],
	PGPKEY =>	[ 'http://wwwkeys.pgp.net:11371/pks/lookup',
	  	{ 'op'		=> 'index',
		  'search'	=> $query,
		},
		],
	YNEWS =>	[ 'http://search.main.yahoo.com/search/news',
	  	{ 'p'		=> $query,
		  'n'		=> $maxhits,
		},
		],
	MOVIEACTORS =>
		[ 'http://us.imdb.com/Nsearch',
	  	{ 'name'	=> $query,
		  'occupation'	=> 'All professions',
		  'submit4.x'	=> 1,
		  'submit4.y'	=> 1,
		},
		],
	MOVIECHARMALE =>
		[ 'http://us.imdb.com/Character',
	  	{ 'char'	=> $query,
		  'gender'	=> 'male',
		  'submit4.x'	=> 1,
		  'submit4.y'	=> 1,
		},
		],
	MOVIECHARFEMALE =>
		[ 'http://us.imdb.com/Character',
	  	{ 'char'	=> $query,
		  'gender'	=> 'female',
		  'submit4.x'	=> 1,
		  'submit4.y'	=> 1,
		},
		],
	IMDB =>
		[ 'http://us.imdb.com/find',
		{ 'q'		=> "$query;tt=on;nm=on;mx=$maxhits",
		},
		],
	EZYDVD =>	[ 'http://www.ezydvd.com.au/mech/search.zml',
		{ 'f'		=> 'title',
		  'q'		=> $query,
		  'x'		=> 0,
		  'y'		=> 0,
		},
		],
	LINUXNOW =>
		[ 'http://www.linuxnow.com/exec/search.cgi',
	  	{ 'arch'	=> '',
		  'type'	=> 'both',
		  'keywords'	=> $query,
		},
		],
	MOTORCYCLEONLINE =>
		[ 'http://www.motorcycle.com/cgi-bin/ffwcgi.en/www.motorcycle.com',
	  	{ 'key'		=> $query,
		  'go'		=> 'Search',
		},
		],
	RFCCONNECTED =>
		[ 'http://www.freesoft.org/Connected/cgi-bin/search.cgi',
	  	{ SEARCH_STRING	=> $query,
		  BASE_URL	=> '/Connected/RFC/index.html',
		  DEPTH		=> 0,
		  TYPE		=> REGEX,
		  CASE_INDEPENDANT => 1,
		  WHOLE_WORD	=> 0,
		},
		],
	RFCNEXOR =>
		[ 'http://web.nexor.co.uk/public/rfc/index/cgi-bin/search/form',
	  	{ 'query'	=> $query,
		  'regexp'	=> 'on',
		  'titlefield'	=> 'on',
		  'abstractfield'=>'on',
		  'authorfield'	=> 'on',
		  'site'	=> 'Australia',
		},
		],
	OZFTP =>	[ 'http://psy.uq.oz.au/cgi-bin/find.pl',
	  	{ 'url'		=> '',
		  'desc'	=> $query,
		},
		],
	YELLOWPAGES =>
		[ 'http://www.yellowpages.com.au/results/',
	  	{ 'N'		=> $query,
		  'C'		=> "",
		  'L'		=> "",
		  'S'		=> "",
		  'R'		=> "11 12 13 14 15 16",
		  'Y'		=> 1,
		  'SEARCH NOW'	=> 'ON',
		},
		],
	YAHOOIMAGESURFER =>
		[ 'http://ipix.yahoo.com/cgi-bin/y/keyword_search.cgi',
	  	{ 'q'		=> $query,
		},
		],
	ACRONYM =>
		[ 'http://www.ucc.ie/cgi-bin/acronym',
	  	{ ''		=> $query,
		},
		],
	WEBSTER =>
		[ 'http://humanities.uchicago.edu/cgi-bin/WEBSTER.sh',
	  	{ 'word'	=> $query,
		  FLOAT		=> ON,
		},
		],
	PALMGEAR =>
		[ 'http://www.palmgear.com/software/searchanswer.cfm',
	  	{ 'quicksearch2'=> $query,
		},
		],
	THESAURUS =>
		[ 'http://humanities.uchicago.edu/cgi-bin/ROGET.sh',
	  	{ 'word'	=> $query,
		},
		],
	GOOGLEDJMSGID => [ 'http://groups.google.com/groups',
		{ 'safe'	=> 'off',
		  'ie'		=> 'ISO-8859-1',
		  'as_umsgid'	=> $query,
		  'lr'		=> '',
		  'num'		=> $maxhits,
		  'hl'		=> 'en',
		},
		],
	GOOGLEDJ => [ 'http://groups.google.com/groups',
		{ 'as_q'	=> $query,
		  'hl'		=> 'en',
		  'lr'		=> '',
		  'safe'	=> 'off',
		  'site'	=> 'groups',
		  'num'		=> $maxhits,
		},
		],
	DEJANEWS => [ 'http://www.deja.com/dnquery.xp',
		{ 'QRY'		=> $query,
		  'ST'		=> 'MS',
		  'svcclass'	=> 'dnserver',
		  'DBS'		=> '1',
		},
		],
	OLDDEJANEWS => [ 'http://xp7.dejanews.com/dnquery.xp',
	  	{ 'query'	=> $query,
		  'defaultOp'	=> OR,
		  'svcclass'	=> 'dncurrent',
		  'maxhits'	=> $maxhits,
		  'format'	=> 'verbose',
		  'threaded'	=> 1,
		  'showsort'	=> 'score',
		  'agesign'	=> 1,
		  'ageweight'	=> 1,
		},
		],
	WEBCRAWLER =>
		[ 'http://www.webcrawler.com/cgi-bin/WebQuery',
	  	{ 'mode'	=> 'summaries',
		  'maxhits'	=> $maxhits,
		  'searchText'	=> $query,
		},
		],
	WEBSTER2,
		[ 'http://work.ucsd.edu:5141/cgi-bin/http_webster',
	  	{ 'isindex'	=> $query,
		},
		],
	MERRIAMWEBSTER =>
		[ 'http://www.m-w.com/netdict',
	  	{ 'va'		=> $query,
		},
		],
	ARCHIE =>	[ 'http://www.telstra.com.au/cgi-bin/archieplexform',
		{ 'query'	=> $query,
		  'type'	=> 'Case Insensitive Substring Match',
		  'order'	=> 'host',
		  'nice'	=> 'Nice',
		  'server'	=> 'archie.au',
		  'domain'	=> '',
		  'hits'	=> $maxhits,
		  # 'Submit'	=> 1,
		},
		],
	ARCHIESU =>
		[ 'http://www.gh.cs.su.oz.au/cgi-bin/archieplexform.pl',
		{ 'query'	=> $query,
				   # maybe 'Regular Expression Match' ?
		  'type'	=> 'Case Insensitive Substring Match',
		  'order'	=> 'date',
		  'nice'	=> 'Nice',
		  'server'	=> 'archie.au',
		  'domain'	=> '',
		  'hits'	=> $maxhits,
		  # 'Submit'	=> 1,
		},
		],
	GOOGLESUBMIT =>
		[ 'http://www.google.com/addurl',
		{ 'q'		=> $query,
		  'dq'		=> '',
		  'submit'	=> 'Add URL',
		},
		],
	GOOGLE_DE2EN,
		[ 'http://translate.google.com/translate',
		{ 'u'		=> $query,
		  'langpair'	=> 'de%7Cen',
		  'hl'		=> 'en',
		  'ie'		=> 'UTF-8',
		  'oe'		=> 'UTF-8',
		  'prev'	=> '%2Flanguage_tools',
		},
		],
	ALTAVISTASUBMIT => [ 
		'http://add-url.altavista.digital.com/cgi-bin/newurl',
		{ 'q'		=> $query,
		  'ad'		=> 1,
		},
		],
	ALTAVISTA =>
		[ 'http://www.altavista.com/cgi-bin/query',
		{ 'q'		=> $query,
		  'r'		=> '',
		  'kl'		=> 'XX',
		  'd0'		=> '',
		  'd1'		=> '',
		  'pg'		=> 'aq',
		  'Translate'	=> 'on',
		  'search.x'	=> 10,
		  'search.y'	=> 10,
		},
		],
	AVRAGING =>
		[ 'http://www.altavista.com/web/res_text',
		{ 'avkw'	=> 'xytx',
		  'amb'		=> 'txt',
		  'kls'		=> '0',
		  'kgs'		=> '0',
		  'q'		=> $query,
		},
		],
	SOFCOMTV => [ 'http://www.sofcom.com.au/cgi-bin/TV/Search.cgi',
	  	{ 'state'	=> 'Sydney',
		  'fta'		=> 1,		# free to air
		  'fox'		=> 1,		# FoxTel
		  'opt'		=> 1,		# Optus Vision
		  'type'	=> 'keyword',
		  'term'	=> $query,
		  'searchtype'	=> 'All',
		},
		],
	NETSCAPE => [ 'http://www10.netscape.com/search-bin',
	  	{ 'NS-search-page'	=> 'results',
		  'NS-query-pat'	=> '/text/NS-advquery.pat',
		  'NS-tocstart-pat',	=> '/text/HTML-advquery-tocstart.pat',
		  'NS-search-type'	=> 'NS-boolean-query',
		  'NS-collection'	=> 'netscape',
		  'NS-sort-by'		=> '',
		  'NS-query'		=> $query,
		  'submit'		=> OK,
		  'NS-max-records'	=> $maxhits,
		},
		],
	BUGTRAQ => [ 'http://www.geek-girl.com/cgi-bin/aglimpse/50/CompleteAct/manna/geek-girl.com/http-docs/bugtraq',
	  	{ 'query'	=> $query,
		  'whole'	=> 'on',
		  'errors'	=> 2,
		  'maxfiles'	=> $maxhits,
		  'maxlines'	=> 100,
		},
		],
	ROGET =>
		[ 'http://www.thesaurus.com/roget.ihnd',
	  	{ KEYWORDS	=> $query,
		  HEADER	=> 'roget-english-frames-head',
		  FOOTER	=> 'roget-english-frames-foot',
		  INDEX		=> 'roget.idx',
		  LANGUAGE	=> 'roget-english-frames',
		  MAXHITS	=> $maxhits,
		},
		],
	SCOUR,
		[ 'http://www.scour.com/Search/Search.phtml',
		{ 'query'	=> $query,
		  'index'	=> 'image',
		  'protocol'	=> 'all',
		  'x'		=> 14,
		  'y'		=> 2,
		},
		],
	ALTAVISTAYPAU,
		[ 'http://www.altavista.yellowpages.com.au/cgi-bin/telstra',
	  	{ 'q'		=> $query,
		  'pg'		=> 'aq',
		  'what'	=> 'web',
		  'fmt'		=> 'd',
		  'stq'		=> 0,
		},
		],
  };

  return $map;
}

=back

=head1 OBJECT CREATION

=over 4

=item new(I<url>,I<base>)

Create a new B<cs::URL> object from the I<url> string supplied.
If I<base> (a B<cs::URL> object or URL string) is supplied

=cut

sub new($$;$)
{ my($class)=shift;
  local($_)=shift;
  my($base)=shift;

  # turn base URL into object
  if (defined $base && ! ref $base)
  { my $nbase = new cs::URL $base;
    if (! defined $nbase)
    { warn "$::cmd: new $class \"$_\", \"$base\": second URL invalid!";
      undef $base;
    }
    else
    { $base=$nbase;
    }
  }

  my $this = {};

  my($scheme,$host,$port,$path,$query,$anchor);
  my $ok = 1;

  if (m|^(\w+):|)
  { $scheme=$1;
    $_=$';
  }
  elsif (defined $base)
  { $scheme=$base->Scheme();
  }
  else
  { $ok=0;
  }

  $port='';
  if (m|^//([^/:#?]+)(:(\d+))?|)
  { $host=$1;

    if (length($2))
    { $port=$3+0;
    }

    $_=$';
  }
  elsif (defined $base)
  { $host=$base->Host();
    $port=$base->Port();
  }
  else
  { $ok=0;
  }

  return undef if ! $ok;

  if ($scheme eq HTTP || $scheme eq FILE || $scheme eq FTP)
  {
    if ($scheme eq HTTP)
    { /^[^#?]*/;
      $path=$&;
      $_=$';
    }
    else
    { $path=$_;
      $_='';
    }

    if (substr($path,$[,1) ne '/')
    # relative path, insert base's path
    { if (defined $base)
      { my $dirpart = $base->Path();
	$dirpart =~ s:[^/]*$::;
	$dirpart="/" if ! length $dirpart;

	$path="$dirpart$path";

	# trim /.
	while ($path =~ s:/+\./:/:)
	{}

	# trim leading /..
	while ($path =~ s:^/+\.\./:/:)
	{}

	# trim /foo/..
	while ($path =~ s:/+([^/]+)/+\.\./:/:)
	{}
      }
    }
  }
  else
  { $path=$_;
    $_='';
  }

  if (/^\?([^#]*)/)
  { $query=$1; $_=$'; }

  if (/^\#(.*)/)
  { $anchor=$1; $_=$'; }

  $host=uc($host);
  $scheme=uc($scheme);

  # disambiguate FILE and FTP
  # a million curses on the idiot who decided to overload these!
  if ($scheme eq FILE)
  { if (length $host && $host ne LOCALHOST)
    { $scheme=FTP;
    }
  }
  elsif ($scheme eq FTP)
  { if (! length $host)
    { $scheme=FILE;
    }
  }

  $this->{cs::URL::SCHEME}=$scheme;
  $this->{cs::URL::HOST}=lc($host);
  $this->{cs::URL::PORT}=urlPort($scheme,$port);
  $this->{cs::URL::PATH}=cs::HTTP::unhexify($path);
  $this->{cs::URL::QUERY}=$query;
  $this->{cs::URL::ANCHOR}=$anchor;

  bless $this, $class;
}

=back

=head1 OBJECT METHODS

=over 4

=item Abs(I<relurl>)

DEPRECIATED. Use B<new cs::URL I<relurl>, $this> instead.
Return a new B<cs::URL> object
from the URL string I<relurl> with the current URL as base.

=cut

sub Abs($$)
{ my($base,$target)=@_;
  new cs::URL $target, $base;
}

##sub _OldAbs($$)
##{ my($base,$target)=@_;
##  # make target into an object
##  $target=new cs::URL $target if ! ref $target;
##
####  warn "base url = ".$base->Text()."\n"
####      ."targ url = ".$target->Text()."\n";
##
##  my($abs)=bless {};
##  for (keys %$target)
##  { $abs->{$_}=$target->{$_};
##  }
##
##  # short circuit
##  return $abs if $abs->IsAbs();
##
##  ## warn "NOT ABS ".$abs->Text();
##
##  # we need an absolute URL to resolve against
##  if (! $base->IsAbs())
##  {
##    my($context)=$base->Context();
##    ## warn "context=[".$context->Text()."]";
##
##    if (! defined $context)
##    {
###	  ## warn "$::cmd: Abs(\""
###	      .$base->Text()
###	      ."\",\""
###	      .$target->Text()
###	      ."\"): no context for resolving LHS";
##      return $target;
##    }
##
##    if (! $context->IsAbs())
##    {
###	  ## warn "$::cmd: non-absolute context (\""
###	      .$context->Text()
###	      ."\") for \""
###	      .$base->Text()
###	      ."\"";
##      return $target;
##    }
##
##    ## warn "call ABS from context";
##
##    $base=$context->Abs($base);
##
##    ## warn "ABS from CONTEXT(".$context->Text().")="
##    ##	.$base->Text();
##  }
##
##  my($dodgy,$used_dodge)=(0,0);
##
##  if (! defined $abs->{cs::URL::SCHEME}
##   && defined $base->{cs::URL::SCHEME})
##  { $abs->{cs::URL::SCHEME}=$base->{cs::URL::SCHEME};
##  }
##  elsif ($abs->{cs::URL::SCHEME} ne $base->{cs::URL::SCHEME})
##  {
##    $base=$target->Context($abs->{cs::URL::SCHEME});
##
##    ## my(@c)=caller;
##    ## warn "no context for ".cs::Hier::h2a($target,1)." from [@c]"
##    ##	if ! defined $base;
##
##    return $abs if ! defined $base;
##    $dodgy=! $base->IsAbs();
##  }
##
##  if (! defined $abs->{cs::URL::HOST}
##   && defined $base->{cs::URL::HOST})
##  { $used_dodge=1;
##
##    $abs->{cs::URL::HOST}=$base->{cs::URL::HOST};
##    ## warn "set HOST to $base->{cs::URL::HOST}\n";
##
##    if (defined $base->{cs::URL::PORT})
##    { $abs->{cs::URL::PORT}=$base->{cs::URL::PORT};
##    }
##    else
##    { delete $abs->{cs::URL::PORT};
##    }
##
##    # XXX - password code?
##    if (defined $base->{USER})
##    { $abs->{USER}=$base->{USER};
##    }
##    else
##    { delete $abs->{USER};
##    }
##  }
##
##  if ($abs->{PATH} !~ m:^/:)
##  { $used_dodge=1;
##
##    my($dirpart)=$base->{PATH};
##    $dirpart =~ s:[^/]*$::;
##    $dirpart="/" if ! length $dirpart;
##
##    $abs->{PATH}="$dirpart$abs->{PATH}";
####    warn "interim path = $abs->{PATH}\n";
##  }
##
##  # trim /.
##  while ($abs->{PATH} =~ s:/+\./:/:)
##  {}
##
##  # trim leading /..
##  while ($abs->{PATH} =~ s:^/+\.\./:/:)
##  {}
##
##  # trim /foo/..
##  while ($abs->{PATH} =~ s:/+([^/]+)/+\.\./:/:)
##  {}
##
##  if ($dodgy && $used_dodge)
##  {
##    warn "$::cmd: no default for scheme \"$abs->{cs::URL::SCHEME}\",\n";
##    warn "\tusing \"".$base->Text()."\" instead, despite scheme mismatch\n";
##  }
##
####  warn "RETURNING ABS = ".cs::Hier::h2a($abs,1);
##
##  $abs;
##}

=item IsAbs()

DEPRECIATED.
Test whether this URL is an absolute URL.
This is legacy support for relative URLs
which I'm in the process of removing
in favour of a method to return the relative difference
between two URLs as a text string
and to generate a new URL object given a base URL and a relative URL string.

=cut

sub IsAbs($)
{ my($this)=@_;

  my@c=caller;die "cs::URL::IsAbs() called from [@c]";
}

=item Context

DEPRECIATED.
Return a URL representing the current context
for the specified I<scheme>.
Use this URL's I<scheme> if the I<scheme> parameter is omitted.
This is a very vague notion,
drawing on the B<HTTP_REFERER> environment variable
as a last resort.

=cut

sub Context($;$)
{ my($this,$scheme)=@_;
  $scheme=$this->{cs::URL::SCHEME} if ! defined $scheme
			  && defined $this->{cs::URL::SCHEME}
			  && length $this->{cs::URL::SCHEME};

  ## warn "this=".cs::Hier::h2a($this,0).", scheme=[$scheme]";

  my($context);

  if (! defined $scheme)
  { if (defined $ENV{HTTP_REFERER}
     && length $ENV{HTTP_REFERER})
    { $context=new cs::URL $ENV{HTTP_REFERER};
    }
  }
  elsif ($scheme eq FILE)
  { $context=_fileContext();
  }

  return undef if ! defined $context;
  $context=new cs::URL $context if ! ref $context;
  $context;
}

sub _fileContext
{ my($dir)=@_;
  ## warn "fileContext(@_): dir=[$dir]";

  if (! defined $dir)
  { ::need(Cwd);
    $dir=cwd();
    if (! defined $dir || ! length $dir)
    { warn "$::cmd: cwd fails, using \"/\"";
      $dir='/';
    }
    else
    { ## warn "cwd=[$dir]";
    }
  }

  "file://localhost$dir";
}

=item Text(I<noanchor>)

Return the textual representation of this URL.
Omit the B<#I<anchor>> part, if any, if the I<noanchor> parameter is true
(it defaults to false).

=cut

sub Text($;$)
{ my($this,$noanchor)=@_;
  $noanchor=0 if ! defined $noanchor;

  my $url;

  ## warn "computing TEXT for ".cs::Hier::h2a($this,1);
  my $SC=$this->{cs::URL::SCHEME};
  $url=lc($SC).":" if length $SC;
  if ($SC eq FILE || $SC eq HTTP || $SC eq HTTPS || $SC eq FTP)
  { $url.='//'.$this->HostPart() if defined $this->{cs::URL::HOST};
  }
  $url.=$this->LocalPart($noanchor);

  ## warn "text=$url\n";

  $url;
}

=item Scheme()

Return the scheme name for this URL.

=cut

sub Scheme($)
{ shift->{cs::URL::SCHEME};
}

=item Host()

Return the host name for this URL.

=cut

sub Host($)
{ shift->{cs::URL::HOST};
}

=item Port()

Return the port number for this URL.

=cut

sub Port($)
{ shift->{cs::URL::PORT};
}

=item Path()

Return the path component of the URL.

=cut

sub Path($)
{ shift->{cs::URL::PATH};
}

=item Query()

Return the query_string component of the URL.

=cut

sub Query($)
{ shift->{cs::URL::QUERY};
}

=item Anchor()

Return the anchor component of the URL.

=cut

sub Anchor($)
{ shift->{cs::URL::ANCHOR};
}

=item HostPart()

Return the B<I<user>@I<host>:I<port>> part of the URL.

=cut

sub HostPart($)
{ my($this)=@_;

  return "" if ! defined $this->{cs::URL::HOST};

  my($hp);

  $hp='';
  $hp.="$this->{USER}\@" if defined $this->{USER};
  $hp.=lc($this->{cs::URL::HOST}) if defined $this->{cs::URL::HOST};
  $hp.=":".lc($this->{cs::URL::PORT}) if defined $this->{cs::URL::PORT}
			      && $this->{cs::URL::PORT}
			      ne urlPort($this->{cs::URL::SCHEME});

  ## warn "HostPart=$hp\n";

  $hp;
}

=item LocalPart(I<noanchor>)

Return the local part (B</path#anchor>) of this URL.
Omit the B<#I<anchor>> part, if any, if the I<noanchor> parameter is true
(it defaults to false).

=cut

sub LocalPart($;$)
{ my($this,$noanchor)=@_;
  $noanchor=0 if ! defined $noanchor;

  my $l = $this->{cs::URL::PATH};

  my $q = $this->Query();
  if (defined $q && length $q)
  { $l.="?$q"; }

  if (! $noanchor)
  { my $a = $this->Anchor();
    if (defined $a && length $a)
    { $l.="#$a";
    }
  }

  $l;
}

=item MatchesCookie(I<cookie>,I<when>)

Given a I<cookie>
as a hashref with B<DOMAIN>, B<PATH> and B<EXPIRES> fields
and a time I<when> (which defaults to now),
return whether the cookie should be associated with this URL.

=cut

sub MatchesCookie($$;$)
{ my($this,$C,$when)=@_;
  $when=time if ! defined $when;

  ## my(@c)=caller;
  ## warn "this=$this, C=$C [@$C] from [@c]";

  substr(lc($this->{cs::URL::HOST}),-length($C->{DOMAIN}))
  eq $C->{DOMAIN}
  &&
  substr($this->{cs::URL::PATH},0,length($C->{PATH}))
  eq $C->{PATH}
  &&
  (! defined $when || $when <= $C->{EXPIRES});
}

=item Get(I<follow>)

Fetch a URL and return a B<cs::MIME> object.
If the optional flag I<follow> is set,
act on B<Redirect> responses etc.
Returns a tuple of (I<endurl>,I<rversion>,I<rcode>,I<rtext>,I<MIME-object>)
where I<endurl> is the URL object whose data was eventually retrieved
and I<MIME-object> is a B<cs::MIME> object
or an empty array on error.

=cut

sub Get($;$$$)
{ my($this,$follow,$sinkfile,$ignerrs)=@_;
  $follow=0 if ! defined $follow;
  $ignerrs=[] if ! ref $ignerrs;

  local(%cs::URL::_Getting);

  $this->_Get($follow,$sinkfile,$ignerrs);
}

sub _Get($$;$$$)
{ my($this,$follow,$sinkfile,$ignerrs)=@_;
  $follow=0 if ! defined $follow;
  $ignerrs=[] if ! ref $ignerrs;

  my($url,$context);

  my %triedAuth;

  my $rqhdrs;

  GET:
  while (1)
  { $url = $this->Text(1);
    $context="$::cmd: GET $url";

    if ($cs::URL::_Getting{$url})
    { warn "$context:\n\tredirection loop detected\n\tURL=$url";
      last GET;
    }

    $cs::URL::_Getting{$url}=1;

    my $scheme = $this->Scheme();
    if (! grep($_ eq $scheme, HTTP, FTP,HTTPS, MMS))
    { warn "$context:\n\tscheme $scheme not implemented";
      last GET;
    }

    my ($phost,$pport) = $this->Proxy();

    my $phttp = ( $scheme eq HTTPS
		? new cs::HTTPS ($this->Host(), $this->Port())
		: new cs::HTTP ($phost,$pport,1)
		);

    if (! defined $phttp)
    { warn "$context:\n\tcan't connect to proxy server $phost:$pport: $!";
      last GET;
    }

    my($rversion,$rcode,$rtext,$M);
    $rqhdrs = cs::HTTP::rqhdr($this);

    if ($::Verbose)
    { warn "GET $url\n";
      $rqhdrs->WriteItem(main::STDERR);
    }

    ($rversion,$rcode,$rtext,$M)=$phttp->Request(GET,$url,$rqhdrs,undef,undef,$sinkfile);

    if (! defined $rversion)
    { warn "$context: nothing from proxy";
      last GET;
    }

    if ($rcode eq $cs::HTTP::M_MOVED || $rcode eq $cs::HTTP::M_FOUND)
    {
      warn "set HTTP_REFERER to $url";
      $ENV{HTTP_REFERER}=$url;
      my $newurl=$M->Hdr(LOCATION);
      chomp($newurl);
      $newurl =~ s/^\s+//g;
      $newurl =~ s/\s+$//g;

      warn "REDIRECT($rcode) to $newurl\n" if $::Verbose;

      $this = new cs::URL($newurl,$this);
      if (! defined $this)
      { warn "$context:\n\tcan't parse URL \"$newurl\"";
	last GET;
      }
    }
    elsif ($rcode eq $cs::HTTP::E_UNAUTH)
    {
      # get challenge info from hdrs
      my ($scheme,$label)=cs::HTTP::Auth::parseWWW_AUTHENTICATE($M);
      if (! defined $scheme)
      { warn "$context:\n\tcan't parse WWW_AUTHENTICATE";
	last GET;
      }

      my $host = $this->Host();

      warn "GETAUTH($rcode): scheme=$scheme, host=$host, label=$label\n" if $::Verbose;

      if ($triedAuth{$url})
      { warn "already tried auth for $url\n";
	last GET;
      }

      my $auth = $this->AuthDB();
      if (! defined $auth)
      { warn "$context:\n\tauthentication challenge but no auth db";
	last GET;
      }

      # get response info
      my $resp = $auth->GetAuth($scheme,$host,$label);
      if (! ref $resp)
      { warn "$context:\n\tno login/password for $scheme/$host/$label";
	last GET;
      }

      if ($::Debug)
      { warn "$context:\n\ttrying auth $resp->{USERID}:$resp->{PASSWORD}\n";
      }

      $auth->HdrsAddAuth($rqhdrs,$scheme,$resp);
      $triedAuth{$url}=1;
      $cs::URL::_Getting{$url}=0;
    }
    elsif ($rcode ne $cs::HTTP::R_OK)
    { if (! grep($_ eq $rcode, @$ignerrs))
      { warn "$context:\n\tunexpected response: $rversion $rcode $rtext\n";
      }
      last GET;
    }
    else
    {
      return ($this,$rversion,$rcode,$rtext,$M);
    }

    last GET if ! $follow;
  }

  return ();
}

=item Head()

Fetch a URL and return a B<cs::MIME> object.
Returns a tuple of (I<endurl>,I<rversion>,I<rcode>,I<rtext>,I<MIME-object>)
where I<endurl> is the URL object whose data was retrieved
and I<MIME-object> is a B<cs::MIME> object
or an empty array on error.

=cut

sub Head($)
{ my($this)=@_;

  my($url,$context);

  HEAD:
  while (1)
  { $url = $this->Text();
    $context="$::cmd: HEAD $url";

    my $scheme = $this->Scheme();
    if ($scheme ne HTTP && $scheme ne FTP)
    { warn "$context:\n\tscheme $scheme not implemented";
      return ();
    }

    my $rqhdrs = cs::HTTP::rqhdr($this);

    my ($phost,$pport) = $this->Proxy();

    my $phttp = new cs::HTTP ($phost,$pport,1);

    if (! defined $phttp)
    { warn "$context:\n\tcan't connect to proxy server $phost:$pport: $!";
      return ();
    }

    my($rversion,$rcode,$rtext,$M);

    warn "HEAD $url\n" if $::Verbose;
    ($rversion,$rcode,$rtext,$M)=$phttp->Request(HEAD,$url,$rqhdrs);

    if (! defined $rversion)
    { warn "$context: nothing from proxy";
      return ();
    }

    return ($this,$rversion,$rcode,$rtext,$M);
  }

  return ();
}

=item URLs(I<hashref>,I<inline>)

Return the URLs references by the page associated with the current URL.
The hash referenced by I<hashref> will be filled with URLs and titles
(from the source document - not the taregt URL's B<TITLE> tag),
using the URL for the key and the title for the value.
See the B<cs::HTML::sourceURLs> method for detail.
If the optional parameter I<inline> is true,
return the URLs of inlined components such as images.

=cut

sub URLs($$;$)
{ my($this,$urls,$inline)=@_;
  $inline=0 if ! defined $inline;

  my($endU,$rversion,$rcode,$rtext,$M)=$this->Get(1);
  return () if ! defined $endU;	# fetch failed

  my %urls;

  $this->_URLsFromMIME($inline,$M,$urls);
}

sub _URLsFromMIME($$$$)
{ my($this,$inline,$M,$urls)=@_;

  my $type = $M->Type();
  my $subtype = $M->SubType();

  if ($type eq MULTIPART)
  { my @M = $M->Parts();
    
    for my $subM (@M)
    { $this->_URLsFromMIME($inline,$subM,$urls);
    }

    return;
  }

  if ($type eq TEXT)
  {
    if ($subtype eq HTML)
    { my $src = $M->BodySource(1,1);
      cs::HTML::sourceURLs($urls,$src,$inline,$this);
      return;
    }
  }

  warn "$::cmd: ".$this->Text().":\n\tcan't parse [$type/$subtype]\n";
}

=item Proxy()

Return an array of (I<host>,I<port>) as the proxy to contact
for this URL.
Currently dissects the B<WEBPROXY> environment variable.

=cut

sub Proxy($)
{ my($this)=@_;

  my @proxy;

  if (defined $ENV{WEBPROXY} && length $ENV{WEBPROXY})
  { if ($ENV{WEBPROXY} =~ /:/)	{ @proxy=($`,$'); }
    else			{ @proxy=($ENV{WEBPROXY},80); }
  }

  @proxy;
}

=item AuthDB()

Return a B<cs::HTTP::Auth> object
containing the authentication tokens we possess.

=cut

sub AuthDB($)
{ shift;
  return new cs::HTTP::Auth(@_);
}

=back

=head1 ENVIRONMENT

B<WEBPROXY> - the HTTP proxy service to use for requests,
of the form B<I<host>:I<port>>.

=head1 AUTHOR

Cameron Simpson E<lt>cs@zip.com.auE<gt>

=cut

1;

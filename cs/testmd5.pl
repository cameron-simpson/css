use cs::MD5;
use cs::Hier;

$str='now is the time';
$md5=cs::MD5::md5string($str);

print "md5=[$md5]\n";

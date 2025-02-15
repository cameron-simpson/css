#!/usr/bin/env python3

from typing import Mapping, Union

def content_length(headers: Mapping[str, str]) -> Union[None, int]:
  ''' Return the value of the `Content-Length` header, or `None`.
  '''
  length_s = headers.get('content-length')
  length = None if length_s is None else int(length_s)
  return length

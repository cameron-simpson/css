#!/usr/bin/env python3

''' A SiteMap which caches docker.io blobs.
'''

from dataclasses import dataclass

from cs.app.pilfer.sitemap import FlowState, on, SiteMap
from cs.tagset import TagSet

@dataclass
class DockerIO(SiteMap):

  TYPE_ZONE = 'dockerio'

  # https://registry-1.docker.io/v2/linuxserver/ffmpeg/blobs/sha256:6e04116828ac8a3a5f3297238a6f2d0246440a95c9827d87cafe43067e9ccc5d
  @on(
      'registry-*.docker.io',
      r'/v2/.*/blobs/[^/]+:[^/]+$',
      cache_key='blobs/{__}',
  )
  def cache_key_image_blob(self, flowstate: FlowState, match: TagSet) -> str:
    return match['cache_key']

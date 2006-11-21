_logNode=document.body
function _log(str) {
  if (_logNode != null) {
    _logNode.appendChild(document.createTextNode(str));
    _logNode.appendChild(document.createElement("BR"));
  }
}

function _logTo(elem) {
  _logNode=elem;
}

function _logWindow() {
  //var w = window.open('javascript:true',"DEBUG_WINDOW","width=400,height=400");
  //_logTo(w.document.body);
  //return w;
}

_cs_browserVersion = parseInt(navigator.appVersion);
_cs_agent = navigator.userAgent.toLowerCase();
document.write("appver = "+navigator.appVersion+", agent = "+navigator.userAgent);
_cs_isGecko = (_cs_agent.indexOf(" gecko/") > 0);
_cs_isIE = (_cs_agent.indexOf(" msie ") > 0);

function csPushOnresize(fn) {
  var old = window.onresize;
  window.onresize = function() {
                      if (old) old();
                      fn();
                    };
}

function csNode(type) {
  return document.createElement(type);
}

function csXY(x,y) {
  return {x: x, y: y};
}

function csSize(width, height) {
  return {width: width, height: height};
}

function box(xy, size) {
  return {x: xy.x, y: xy.y, width: size.width, height: size.height};
}

function csDIV() {
  return csNode('DIV');
}

function csText(str) {
  return document.createTextNode(str);
}

function csIMG(src,onload) {
  var img = csNode('IMG');

  img.style.border=0;
  if (onload) {
    img.onload=onload;
  }
  if (_cs_isIE) {
    img.galleryImg = false;
  }
  img.src = src;

  return img;
}

function csBoxInView(viewport, box) {
  box = {x: box.x, y:box.y, width: box.width, height: box.height};

  if (box.x + box.width > viewport.x + viewport.width)
    box.x = viewport.x + viewport.wdith - box.width;
  if (box.x < 0)
    box.x = 0;

  if (box.y + box.height > viewport.y + viewport.height)
    box.y = viewport.y + viewport.wdith - box.height;
  if (box.y < 0)
    box.y = 0;

  return box;
}

function csScreenXYtoDocXY(xy) {
  var docxy = {x: xy.x, y: xy.y};

  if (_cs_isIE) {
    docxy.x -= window.screenLeft;
    docxy.x += document.body.scrollLeft;
    docxy.y -= window.screenTop;
    docxy.y += document.body.scrollTop;
  } else {
    // BUG: doesn't account for the toolbars
    docxy.x -= window.screenX;
    docxy.x += window.pageXOffset;
    docxy.y -= window.screenY;
    docxy.y += window.pageYOffset;
  }

  return docxy;
}

function csAbsTopLeft(elem) {
  var topLeft = csXY(elem.offsetLeft, elem.offsetTop);
  var parent=elem.offsetParent;

  while (parent && parent != elem) {
    elem=parent;
    topLeft.x += elem.offsetLeft;
    topLeft.y += elem.offsetTop;
    parent=elem.offsetParent;
  }

  return topLeft;
}

function csElementToDocBBox(elem) {
  var abs = csAbsTopLeft(elem);
  return {x: abs.x, y: abs.y, width: elem.offsetWidth, height: elem.offsetHeight};
}

function csLogWindow(width, height) {
  var div = csDIV();
  div.style.overflow="auto";
  if (width && height) {
    csSetSize(div,width,height);
  }
  _logTo(div);
  return div;
}

function csSetZIndex(elem,z) {
  elem.style.zIndex = z;
}

function csSetPosition(elem,xy) {
  elem.style.position='absolute';
  elem.style.left=xy.x;
  elem.style.top=xy.y;
}

function csSetRPosition(elem,dxy) {
  csSetPosition(elem, csXY(elem.offsetLeft + dxy.x, elem.offsetTop + dxy.y));
}

// Set size of one element to the size of another.
function csSetSizeFrom(elem, oElem) {
  csSetSize(elem, csXY(oElem.offsetWidth, oElem.offsetHeight));
}

function csSetSize(elem,width,height) {
  elem.style.width = width;
  elem.style.height = height;
}

function csHotspotsToClientMap(hotspots,mapname) {
  var map = csNode("MAP");
  map.name=mapname;

  var h;
  var a;
  var meta;
  for (var i=0; i<length(hotspots); i++) {
    h=hotspots[i];
    if (h) {
      meta=h[0].attrs;

      a=csNode("AREA");
      a.title=h.title;
      a.alt=h.title;
      a.href=h.href;
      a.shape="RECT";
      a.coords=h[1]+','+h[2]+','+h[3]+','+h[4];
      map.appendChild(a);
    }
  }

  return map;
}

function csAddHotSpot(elem,meta,xy1,xy2,z) {
  var hot = new CSHotSpot(meta,xy1,xy2,z);
  if (elem) {
    elem.appendChild(hot.element);
  }
  return hot;
}

function CSHotSpot(meta,xy1,xy2,z) {
  var me = this;

  if (z == null) z=1;

  this.xy1=xy1;
  this.xy2=xy2;

  var hotdiv = csDIV();
  csSetPosition(hotdiv,xy1);
  csSetSize(hotdiv,xy2.x-xy1.x,xy2.y-xy1.y);
  csSetZIndex(hotdiv,z);
  //hotdiv.style.opacity=0.5;

  hotdiv.style.overflow="hidden";

  //label = csText(label);
  //hotdiv.appendChild(label);

  if (0) {
    var img = document.createElement("IMG");
    img.src="http://docs.python.org/icons/contents.png";
    csSetPosition(img,0,0);
    csSetSize(img,xy2.x-xy1.x,xy2.y-xy1.y);
    hotdiv.appendChild(img);
  }

  if (meta.onclick || meta.attrs.href) {
    hotdiv.onclick = function(e) {
        if (meta.onclick) {
          _log("calling "+meta.onclick);
          meta.onclick(e,me);
        } else if (meta.attrs.href) {
          document.location=meta.attrs.href;
        } else {
          _log("BUG: no onclick or href in meta");
        }
      }
  }

  if (meta.getHoverDiv) {
    hotdiv.onmouseover = function(e) {
        if (!e) e=window.event;
        var popup=me.getHoverDiv(e.screenX, e.screenY);
        if (popup) {
          popup.style.display='block';
        }
      }

    hotdiv.onmouseout = function(e) {
        if (!e) e=window.event;
        var popup=me.getHoverDiv(e.screenX, e.screenY);
        if (popup) {
          popup.style.display='none';
        }
      }
  } else if (meta.attrs.title) {
    hotdiv.title=meta.attrs.title;
    if (_cs_isIE) {
      hotdiv.alt=meta.attrs.title;
    }
  }

  this.meta=meta;
  this.element=hotdiv;
}

CSHotSpot.prototype.getHoverDiv = function(mouseScreenX, mouseScreenY) {

  if (!this.hoverDiv) {
    if (this.meta.getHoverDiv) {
      var hover=this.meta.getHoverDiv();
      hover.style.display='none';
      document.body.appendChild(hover);
      this.hoverDiv = hover;
    }
  }

  if (this.hoverDiv) {
    // position the popup just below the hotspot
    // we do this every time because the hotspot may have moved
    var up  = this.element.parentNode;
    var hotxy = csAbsTopLeft(up);
    var pos = csXY(hotxy.x + this.xy1.x, hotxy.y + this.xy2.y);
    //var pos = csScreenXYtoDocXY(csXY(mouseScreenX, mouseScreenY));

    if (false && pos.x + this.hoverDiv.offsetWidth > up.offsetWidth)
      pos.x = up.offsetWidth - this.hoverDiv.offsetWidth;
    if (false && pos.y + this.hoverDiv.offsetHeight > up.offsetHeight) {
      _log("UP.HEIGHT = "+up.offsetHeight+", up = "+up);
      pos.y = up.offsetHeight - this.hoverDiv.offsetHeight;
    }
    csSetPosition(this.hoverDiv, pos);
  }

  return this.hoverDiv;
}

_cs_anims=[];
_cs_nanim=0;
function runanim(id, fn) {
  var delay = fn();
  if (delay > 0) {
    setTimeout(function(){runanim(id,fn)}, delay);
  }
}

/**
 * Returns a DIV containing the specified element with a mouse handler
 * to pan it.
 */
function CSPan(toPan) {
  _log("new pan div, toPan = "+toPan);

  var outer = csDIV();
  outer.style.position='relative';
  outer.style.overflow='hidden';

  outer.appendChild(toPan);
  csSetPosition(toPan, csXY(0,0));
  csSetZIndex(toPan,0);

  // Place some glass over the object to prevent drag'n'drop causing
  // trouble. Make it full size to cover the outer DIV.
  var glass = csDIV();
  outer.appendChild(glass);
  csSetPosition(glass, csXY(0,0));
  csSetSize(glass,"100%","100%");
  csSetZIndex(glass,1);

  // Place another layer over the glass for hotspots.
  // We pan this layer with the toPan object to keep the hotspots aligned.
  var hotLayer = csDIV();
  outer.appendChild(hotLayer)
  csSetPosition(hotLayer, csXY(0,0));
  csSetZIndex(hotLayer,2);

  var me = this;
  this.onMouseDown = function(e) { me.handleDown(e); };
  this.onMouseMove = function(e) { me.handleMove(e); };
  this.onMouseUp   = function(e) { me.handleUp(e); };
  if (_cs_isIE) {
    toPan.onmousedown=this.onMouseDown;
    //toPan.style.cursor='move';
  } else {
    glass.onmousedown=this.onMouseDown;
    if (_cs_isGecko) glass.style.cursor="-moz-grab";
  }

  this.element=outer;
  this.glass=glass;
  this.hotLayer=hotLayer;
  this.toPan=toPan;
}

CSPan.prototype.addHotSpot = function(attrs,xy1,xy2,z) {
  return csAddHotSpot(this.hotLayer,attrs,xy1,xy2,z);
};

CSPan.prototype.addHotSpots = function(hotspots,z) {
  var spot;
  for (var i=0; i < hotspots.length; i++) {
    spot=hotspots[i];
    if (spot) {
      this.addHotSpot(spot[0], csXY(spot[1],spot[2]), csXY(spot[3],spot[4]));
    }
  }
};

// Set centre point from fraction.
CSPan.prototype.setCentre = function(fxy) {
  _log("fxy="+fxy);
  var newTop = this.element.offsetHeight/2 - fxy.y * this.toPan.offsetHeight;
  var newLeft = this.element.offsetWidth/2 - fxy.x * this.toPan.offsetWidth;
  this.setPosition(csXY(newLeft, newTop));
  this.centreFXY = fxy;
}

// Return centre point as fraction.
CSPan.prototype.getCentre = function() {
  var width = this.toPan.offsetWidth;
  var height = this.toPan.offsetHeight;
  if (width == 0 || height == 0) return null;
  return csXY( (this.element.offsetWidth/2-this.toPan.offsetLeft) / width,
               (this.element.offsetHeight/2-this.toPan.offsetTop) / height);
}

CSPan.prototype.reCentre = function() {
  if (! this.centreFXY) {
    this.centreFXY=this.getCentre();
    if (this.centreFXY == null) {
      _log("no centre yet, no reCentre");
      return;
    }
  }
  this.setCentre(this.centreFXY);
}

CSPan.prototype.setPosition = function(topLeft) {
  if (topLeft.x > 0) topLeft.x = 0;
  if (topLeft.y > 0) topLeft.y = 0;
  csSetPosition(this.toPan, topLeft);
  csSetPosition(this.hotLayer, topLeft);
  this.centreFXY = null;
}

CSPan.prototype.setSize = function(width, height) {
  var me = this;
  var cxy = me.getCentre();
  csSetSize(me.element, width, height);
  me.reCentre();
};

CSPan.prototype.handleDown = function(e) {
  if (!e) e=window.event;

  this.panning=true;
  this.mouseXY = csXY(e.clientX, e.clientY);
  this.toPanTopLeft = csXY(this.toPan.offsetLeft, this.toPan.offsetTop);

  this.glass.style.cursor="-moz-grabbing";
  if (document.addEventListener) {
    document.addEventListener("mousemove", this.onMouseMove,true);
    document.addEventListener("mouseup",   this.onMouseUp,  true);
  } else {
    this.toPan.attachEvent("onmousemove", this.onMouseMove);
    this.toPan.attachEvent("onmouseup",   this.onMouseUp);
    this.toPan.setCapture();
  }
  if (!_cs_isIE)
    this.hotLayer.style.display='none';
};

CSPan.prototype.handleMove = function(e) {
  //_log("move");
  if (!e) e=window.event;

  if (this.panning) {
    // current mouse position
    var newMouseXY = csXY( e.clientX, e.clientY);
    // offset from original mouse down position
    var dxy = csXY( newMouseXY.x - this.mouseXY.x,
                    newMouseXY.y - this.mouseXY.y );
    // new toPan div top left
    var newTopLeft = csXY( this.toPanTopLeft.x + dxy.x, this.toPanTopLeft.y + dxy.y );

    // Don't left the pan get away.
    if (newTopLeft.x+this.toPan.offsetWidth < this.element.offsetWidth) {
      newTopLeft.x=this.element.offsetWidth-this.toPan.offsetWidth;
    }
    if (newTopLeft.x > 0) newTopLeft.x=Math.max(0, this.toPanTopLeft.x);

    if (newTopLeft.y+this.toPan.offsetHeight < this.element.offsetHeight) {
      newTopLeft.y=this.element.offsetHeight-this.toPan.offsetHeight;
    }
    if (newTopLeft.y > 0) newTopLeft.y=Math.max(0, this.toPanTopLeft.y);

    this.setPosition(newTopLeft);
  }
};

CSPan.prototype.handleUp = function(e) {
  if (!e) e=window.event;

  if (this.panning) {
    this.panning=false;
    this.glass.style.cursor="-moz-grab";
    if (document.removeEventListener) {
      document.removeEventListener("mousemove", this.onMouseMove, true);
      document.removeEventListener("mouseup",   this.onMouseUp, true);
    } else {
      this.toPan.detachEvent("onmousemove", this.onMouseMove);
      this.toPan.detachEvent("onmouseup", this.onMouseMove);
      this.toPan.releaseCapture();
    }
    if (!_cs_isIE)
      this.hotLayer.style.display='block';
  }
};

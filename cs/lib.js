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

function csNode(type) {
  return document.createElement(type);
}

function csDIV() {
  return csNode('DIV');
}

function csText(str) {
  return document.createTextNode(str);
}

function csAbsOffsetTop(elem) {
  var parent;
  var oldParent = null;
  var top = elem.offsetTop;
  _log("absOffsetTop("+elem+"): start with top="+top);

  parent=elem.offsetParent;
  _log("parent of "+elem+" = "+parent);
  while (parent && parent != elem) {
    elem=parent;
    top+=elem.offsetTop;
    _log("advance top to "+top);
    _log("elem width = "+elem.offsetWidth+", height = "+elem.offsetHeight);
    parent=elem.offsetParent;
  }
  _log("doc.body ("+document.body+") width = "+document.body.offsetWidth+", height = "+document.body.offsetHeight);
  return top;
}

function csIMG(src) {
  var img = csNode('IMG');
  img.src = src;
  img.style.border=0;
  return img;
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

function csSetPosition(elem,x,y) {
  elem.style.position='absolute';
  elem.style.left=x;
  elem.style.top=y;
}

function csSetRPosition(elem,dx,dy) {
  var px = elem.offsetLeft + dx;
  var py = elem.offsetTop  + dy;
  csSetPosition(elem,px,py);
}

function csSetSizeFrom(elem, oElem) {
  csSetSize(elem, oElem.offsetWidth, oElem.offsetHeight);
}

function csSetSize(elem,width,height) {
  elem.style.width = width;
  _log("height = ["+height+"]");
  elem.style.height = height;
}

function csAddHotSpot(elem,node,x1,y1,x2,y2,z) {
  var hot = new CSHotSpot(node,x1,y1,x2,y2,z);
  if (elem) {
    elem.appendChild(hot.element);
  }
  return hot;
}

function CSHotSpot(node,x1,y1,x2,y2,z) {
  var me = this;

  if (z == null) z=1;

  this.x1=x1;
  this.y1=y1;
  this.x2=x2;
  this.y2=y2;

  var hotdiv = csDIV();
  csSetPosition(hotdiv,x1,y1);
  csSetSize(hotdiv,x2-x1,y2-y1);
  csSetZIndex(hotdiv,z);
  //hotdiv.style.opacity=0.5;

  hotdiv.style.overflow="hidden";

  //label = csText(label);
  //hotdiv.appendChild(label);

  if (0) {
    var img = document.createElement("IMG");
    img.src="http://docs.python.org/icons/contents.png";
    csSetPosition(img,0,0);
    csSetSize(img,x2-x1,y2-y1);
    hotdiv.appendChild(img);
  }

  if (node.onclick || node.attrs.href) {
    hotdiv.onclick = function(e) {
        if (node.onclick) {
          _log("calling "+node.onclick);
          node.onclick(e,me);
        } else if (node.attrs.href) {
          document.location=node.attrs.href;
        } else {
          _log("BUG: no onclick or href in node");
        }
      }
  }

  if (node.getHoverDiv) {
    hotdiv.onmouseover = function(e) {
        _log("mouseover");
        var popup=me.getHoverDiv();
        _log("popup = "+popup);
        if (popup) {
          popup.style.display='block';
        }
      }

    hotdiv.onmouseout = function(e) {
        _log("mouseout");
        var popup=me.getHoverDiv();
        if (popup) {
          popup.style.display='none';
        }
      }
  } else if (node.attrs.title) {
    hotdiv.title=node.attrs.title;
    if (_cs_isIE) {
      hotdiv.alt=node.attrs.title;
    }
  }

  this.node=node;
  this.element=hotdiv;
}

CSHotSpot.prototype.getHoverDiv = function() {
  if (!this.hoverDiv) {
    if (this.node.getHoverDiv) {
      this.hoverDiv=this.node.getHoverDiv();
      csSetPosition(this.hoverDiv,this.x1,this.y1+2+this.element.offsetHeight);
      this.hoverDiv.style.display='none';
      _log("add new hoverDiv to "+this.element);
      this.element.parentNode.appendChild(this.hoverDiv);
    }
  }

  return this.hoverDiv;
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
  csSetPosition(toPan,0,0);
  csSetZIndex(toPan,0);

  // Place some glass over the object to prevent drag'n'drop causing
  // trouble. Make it full size to cover the outer DIV.
  var glass = csDIV();
  outer.appendChild(glass);
  csSetPosition(glass,0,0);
  csSetSize(glass,"100%","100%");
  csSetZIndex(glass,1);
  glass.style.cursor="-moz-grab";

  // Place another layer over the glass for hotspots.
  // We pan this layer with the toPan object to keep the hotspots aligned.
  var hotLayer = csDIV();
  outer.appendChild(hotLayer)
  csSetPosition(hotLayer,0,0);
  csSetZIndex(hotLayer,2);

  var me = this;
  this.onMouseDown = function(e) { me.handleDown(e); };
  this.onMouseMove = function(e) { me.handleMove(e); };
  this.onMouseUp   = function(e) { me.handleUp(e); };
  glass.onmousedown=this.onMouseDown;
  //glass.addEventListener("mousedown", this.onMouseDown, false);

  this.element=outer;
  this.glass=glass;
  this.hotLayer=hotLayer;
  this.toPan=toPan;
}

CSPan.prototype.addHotSpot = function(attrs,x1,y1,x2,y2,z) {
  return csAddHotSpot(this.hotLayer,attrs,x1,y1,x2,y2,z);
};

CSPan.prototype.addHotSpots = function(hotspots,z) {
  var spot;
  for (var i=0; i < hotspots.length; i++) {
    spot=hotspots[i];
    if (spot) {
      this.addHotSpot(spot[0],spot[1],spot[2],spot[3],spot[4]);
    }
  }
};

CSPan.prototype.setSize = function(width, height) {
  csSetSize(this.element, width, height);
};

CSPan.prototype.handleDown = function(e) {
  _log("panStart");
  this.panning=true;
  this.mouseX = e.clientX;
  this.mouseY = e.clientY;
  this.toPanTop  = this.toPan.offsetTop;
  this.toPanLeft = this.toPan.offsetLeft;

  this.glass.style.cursor="-moz-grabbing";
  document.addEventListener("mousemove", this.onMouseMove,true);
  document.addEventListener("mouseup",   this.onMouseUp,  true);
  this.hotLayer.style.display='none';
};

CSPan.prototype.handleMove = function(e) {
  //_log("move");

  if (this.panning) {
    // current mouse position
    var newX = e.clientX;
    var newY = e.clientY;
    // offset from original mouse down position
    var dx = newX - this.mouseX;
    var dy = newY - this.mouseY;
    //_log("move("+dx+","+dy+")");
    // new toPan div top left
    var newTop = this.toPanTop + dy;
    var newLeft = this.toPanLeft + dx;
    // Don't left the pan get away.
    if (newTop+this.toPan.offsetHeight < this.element.offsetHeight) {
      newTop=this.element.offsetHeight-this.toPan.offsetHeight;
    }
    if (newLeft+this.toPan.offsetWidth < this.element.offsetWidth) {
      newLeft=this.element.offsetWidth-this.toPan.offsetWidth;
    }
    if (newTop  > 0) newTop =Math.max(0, this.toPanTop);
    if (newLeft > 0) newLeft=Math.max(0, this.toPanLeft);
    csSetPosition(this.toPan, newLeft, newTop);
    csSetPosition(this.hotLayer, newLeft, newTop);
  }
};

CSPan.prototype.handleUp = function(e) {
  if (this.panning) {
    this.panning=false;
    this.glass.style.cursor="-moz-grab";
    document.removeEventListener("mousemove", this.onMouseMove, true);
    document.removeEventListener("mouseup",   this.onMouseUp, true);
    this.hotLayer.style.display='block';
  }
};

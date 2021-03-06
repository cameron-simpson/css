if (this._cs_libLoaded) {
  _log("ERROR: multiple load of cs/lib.js");
} else {
  _logNode = null;  //document.body;
  _cs_seq = 0;
  _cs_DEBUG = false;

  _cs_singlePixelIMGPrefix = '';
  _cs_browserVersion = parseInt(navigator.appVersion);
  _cs_agent = navigator.userAgent.toLowerCase();
  _cs_isGecko = (_cs_agent.indexOf(" gecko/") > 0);
  _cs_isIE = (_cs_agent.indexOf(" msie ") > 0);
  _cs_isOpera = (_cs_agent.indexOf("opera/") == 0);
  // _cs_isGecko = false;
  // _cs_isIE = true;
  //document.write("appver = "+navigator.appVersion+", agent = "+navigator.userAgent+"<BR>\n");
  //document.write("isIE="+_cs_isIE+", isGecko="+_cs_isGecko+"<BR>\n");

  if (_cs_isIE) {
    self.onerror = function(err, url, line) {
                     _log(err+" at "+url+":"+line);
                   };
  }

  _cs_anims = [];
  _cs_nanim = 0;

  _csPan_useImageMap = false;
  _csPan_dragCursor = null;
  _csPan_draggingCursor = null;
  if (_cs_isGecko) {
    _csPan_dragCursor = "-moz-grab";
    _csPan_draggingCursor = "-moz-grabbing";
  }
  if (_cs_isIE) {
    _csPan_useImageMap = true;
  }

}

function _log(str, node) {
  if (node == null) {
    node = _logNode;
  }
  if (node != null) {
    node.appendChild(document.createTextNode(str));
    node.appendChild(document.createElement("BR"));
  }
}
function _logTo(elem) {
  _logNode = elem;
}

function csSeq() {
  return _cs_seq++;
}

function csSeqId() {
  return "_cs_id_"+csSeq();
}

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

// Insert a node at the current location using document.write()
// and return a reference to it.
function csNodeHere(type) {
  if (type == null) type = "span";
  var id = csSeqId();
  document.write("<"+type+" id=\""+id+"\"></"+type+">");
  return document.getElementById(id);
}

if (!this._cs_libLoaded) {
  _cs_loadedNode = csNodeHere("SPAN");
}

function csThisToString() {
  return csObjectToString(this);
}

function csObjectToString(obj) {
  var s;

  if (obj == null) {
    s = 'null';
  } else {
      var t = typeof(obj);
      if (t == "number") {
      s = ""+obj;
    } else if (typeof(obj) == "string") {
      s = '"'+obj+'"';
    } else if (obj instanceof Array) {
      s = "[";
      var first = true;
      for (var i = 0; i<obj.length; i++) {
        if (first) first = false;
        else       s += ", ";
        s += csObjectToString(obj[i]);
      }
      s += "]";
    } else {
      // General object dump.
      s = "{";
      var first = true;
      var v;
      for (var k in obj) {
        if (k == "toString") continue;
        if (first) first = false;
        else s += ", ";
        s += csObjectToString(k);
        v = obj[k];
        s += ": "+csObjectToString(v);
      }
      s += "}";
    }
  }

  return s;
}
function csStringable(o) {
  if (o == null) {
    _log("csStringable(null) called from "+arguments.callee.caller);
  }
  else {
    if (!(o.toString === csThisToString)) {
      var t = typeof(o);
      if (t != "number" && t != "string" && t != "function") {
        o.toString = csThisToString;
      }
    }
  }
  return o;
}

function csXY(x, y) {
  return csStringable({x: x, y: y});
}

function csSize(width, height) {
  return csStringable({width: width, height: height});
}

function box(xy, size) {
  return csStringable({x: xy.x, y: xy.y, width: size.width, height: size.height});
}

function csDIV(colour, zindex) {
  var div = csNode('DIV');
  if (colour != "TRANSPARENT") {
    if (!colour) colour = document.bgColor;
    var fillImg = csSinglePixelIMG(colour);
    csSetSize(fillImg,"100%","100%");
    csSetPosition(fillImg, csXY(0, 0));
    csSetZIndex(fillImg,-1023);
    div.appendChild(fillImg);
  }
  if (zindex == null) {
    csSetZIndex(div, 0);
  } else {
    csSetZIndex(div, zindex);
  }
  return div;
}

function csText(str, nbsp) {
  if (nbsp) {
    // loop to work around firefox replace() bug:-(
    var ostr = str;
    var left = "";
    var spos;
    while ((spos=str.indexOf(" ")) >= 0) {
      left += str.substr(0, spos)+"\u00A0";
      //_log("left="+left);
      str = str.substr(spos+1);
    }
    str = left+str;
    //_log(ostr+"->"+str);
  }
  return document.createTextNode(str);
}

function csIMG(src, onload) {
  var img = csNode('IMG');

  img.style.border = 0;
  if (onload) {
    img.onload = onload;
  }
  if (_cs_isIE) {
    img.galleryImg = false;
  }
  img.src = src;

  return img;
}

function csSinglePixelIMG(colour) {
  if (colour.substr(0, 1) == "#") {
    colour = "%23"+colour.substr(1);
  }
  var imgfile = colour+"-1x1.png";
  if (_cs_singlePixelIMGPrefix) {
    imgfile = _cs_singlePixelIMGPrefix + imgfile;
  }
  return csIMG(imgfile);
}

/**
 * Set the innerHTML of an element to the supplied string or element-list.
 */
function csSetInnerElements(outer, newElements, nbsp) {
  if (typeof(newElements) == "string") {
    newElements = [newElements];
  }
  outer.innerHTML = "";
  for (var i=0; i<newElements.length; i++) {
    var e = newElements[i];
    if (typeof(e) == "string") {
      e = csText(e, nbsp);
    }
    outer.appendChild(e);
  }
}

/**
 * Convert a token into an HTMLElement.
 */
function csTok(tok, nbsp) {
  var node;

  if (tok == null) {
    node = csText("[NULL]");
  } else {
    var t = typeof(tok);
    if (t == "number" || t == "string") {
      node = csText(tok+"", nbsp);
    } else {
      var node = csNode(tok[0]);
      var attrs =  tok[1];
      if (attrs) {
        for (var attr in attrs) {
          eval("node."+attr.toLowerCase()+"=attrs[attr]");
        }
      }
      for (var j=2; j<tok.length; j++) {
        node.appendChild(csTok(tok[j], nbsp));
      }
    }
  }

  return node;
}

/**
 * Convert a list of tokens into a SPAN of HTMLElements.
 */
function csTok2Span(tokens, nbsp) {
  if (tokens.length == 1)
    return csTok(tokens[0], nbsp);

  var span = csNode("SPAN");
  for (var i = 0; i<tokens.length; i++) {
    span.appendChild(csTok(tokens[i], nbsp));
  }
  return span;
}

function csTokMAILTO(addr, text) {
  if (text == null || text == "") {
    text = addr;
  }
  return ["A", {"HREF": "mailto:"+addr}, text];
}

function csBoxInView(viewport, box) {
  box = csStringable({x: box.x, y:box.y, width: box.width, height: box.height});

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
  var docxy = csStringable({x: xy.x, y: xy.y});

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

// Return the bounding box of the viewport in doc coordinates.
function csViewPort() {
  var xy = csStringable({});
  if (_cs_isIE) {
    xy.x = document.body.scrollLeft;
    xy.y = document.body.scrollTop;
    xy.width = document.body.offsetWidth-24; // hack!
    xy.height = document.body.offsetHeight;
  } else {
    xy.x = window.pageXOffset;
    xy.y = window.pageYOffset;
    xy.width = window.innerWidth;
    xy.height = window.innerHeight;
  }

  return xy;
}

function csAbsTopLeft(elem) {
  var topLeft = csXY(elem.offsetLeft, elem.offsetTop);
  var parent = elem.offsetParent;

  while (parent && parent != elem) {
    elem = parent;
    topLeft.x += elem.offsetLeft;
    topLeft.y += elem.offsetTop;
    parent = elem.offsetParent;
  }

  return topLeft;
}

function csRelToLastAbsoluteTopLeft(elem) {
  var topLeft = csXY(elem.offsetLeft, elem.offsetTop);
  var parent = elem.offsetParent;

  while (parent && parent != elem && parent.style.position != "absolute") {
    topLeft.x += parent.offsetLeft;
    topLeft.y += parent.offsetTop;
    parent = parent.offsetParent;
  }

  return topLeft;
}

function csElementToDocBBox(elem) {
  var abs = csAbsTopLeft(elem);
  return csStringable({x: abs.x, y: abs.y, width: elem.offsetWidth, height: elem.offsetHeight});
}

function csMoveIntoView(div) {
  var vp = csViewPort();
  var box = csElementToDocBBox(div);
  var xy0 = {x: box.x, y: box.y};
  csBoxInView(vp, box);
  var xy1 = {x: box.x, y: box.y};
  var dxy = {x: xy1.x-xy0.x, y: xy1.y - xy0.y};
  if (dxy.x || dxy.y) {
    _log("move div: dx="+dxy.x+", dy="+dxy.y);
    csSetRPosition(div, dxy);
  }
}

function csMkLogWindow(width, height) {
  if (width == null) width = "50%";
  if (height == null) height = "40%";

  var div = csDIV();
  document.body.appendChild(div);
  div.style.overflow = "auto";
  div.style.borderWidth = 1;
  csSetSize(div, width, height);
  csSetPosition(div, csXY("50%","80%"));
  csSetZIndex(div, 1023);
  _logTo(div);
  return div;
}
//csMkLogWindow();

function csSetZIndex(elem, z) {
  elem.style.zIndex = z;
}

function csSetPosition(elem, xy) {
  elem.style.position = 'absolute';
  elem.style.left = xy.x+"px";
  elem.style.top = xy.y+"px";
}

function csSetRPosition(elem, dxy) {
  csSetPosition(elem, csXY(elem.offsetLeft + dxy.x, elem.offsetTop + dxy.y));
}

function csPosNodeRight(elem, leftElem, zindex) {
  var leftPos = csRelToLastAbsoluteTopLeft(leftElem);
  elem.style.position = 'absolute';
  elem.style.top = leftPos.y+"px";
  elem.style.left = (leftPos.x+leftElem.offsetWidth)+"px";
  if (zindex != null) {
    elem.style.zIndex = zindex;
  }
  leftElem.appendChild(elem);
}

function csPosNodeBelow(elem, aboveElem, zindex) {
  var abovePos = csRelToLastAbsoluteTopLeft(aboveElem);
  if (_cs_isIE) {
    // hack from Phil Macey to work around IE not sending onMouseOut
    // force a gap between
    abovePos.x = abovePos.x+5;
    abovePos.y = abovePos.y+5;
  }
  elem.style.position = 'absolute';
  elem.style.left = abovePos.x+"px";
  elem.style.top = (abovePos.y+aboveElem.offsetHeight)+"px";
  if (zindex != null) {
    elem.style.zIndex = zindex;
  }
  aboveElem.appendChild(elem);
}

// Set size of one element to the size of another.
function csSetSizeFrom(elem, oElem) {
  csSetSize(elem, csXY(oElem.offsetWidth, oElem.offsetHeight));
}

function csSetSize(elem, width, height) {
  elem.style.width = width;
  elem.style.height = height;
}

function csClientMapAddHotSpot(map, hot) {
  _log("csClientMapAddHotSpot("+hot+")");
  var a = csNode("AREA");
  a.title = hot.title;
  a.alt = hot.title;
  a.href = hot.href;
  a.shape = "RECT";
  //a.onmouseover = "_log('OVER')";
  a.coords = hot.x+','+hot.y+','+(hot.x+hot.dx)+','+(hot.y+hot.dy);
  map.appendChild(a);
}

function csHotspotsToClientMap(mapname, hotspots) {
  var map = csNode("MAP");
  map.name = mapname;
  _log("new MAP: name="+map.name);

  var hslen = hotspots.length;
  _log("hs2a: "+hslen+" hotspots");
  var h;
  for (var i=0; i<hslen; i++) {
    h = hotspots[i];
    if (h) csClientMapAddHotSpot(h);
  }

  return map;
}

// Create a new DIV using a meta object, with the specified corners and
// optional z-index (default 1).
// Meta: .onclick, function to be called with (e, CSHotSpot).
//       .href, if no .onclick, URL to open if clicked
//       .getHoverDiv, function to be called on mouseover which
//                     creates a DIV to show until mouseout
//       .title, if no .getHoverDiv, a title/alt string for the hotspot
//
// Return: object with .meta, the meta object
//                     .element, the DIV
//
function CSHotSpot(meta, xy1, xy2, z) {
  //_log("CSHotSpot(xy1="+xy1+", xy2="+xy2+")");
  var me = this;

  if (z == null) z = 1;

  this.xy1=xy1;
  this.xy2=xy2;

  var hotdiv = csDIV("TRANSPARENT");
  csSetPosition(hotdiv, xy1);
  //_log("set hot size: "+(xy2.x-xy1.x)+"x"+(xy2.y-xy1.y));
  csSetSize(hotdiv, xy2.x-xy1.x, xy2.y-xy1.y);
  csSetZIndex(hotdiv, z);
  //hotdiv.style.opacity = 0.5;

  hotdiv.style.overflow = "hidden";

  //label = csText(label);
  //hotdiv.appendChild(label);

  if (0) {
    var img = document.createElement("IMG");
    img.src = "http://docs.python.org/icons/contents.png";
    csSetPosition(img, 0, 0);
    csSetSize(img, xy2.x-xy1.x, xy2.y-xy1.y);
    hotdiv.appendChild(img);
  }

  if (meta.onclick || meta.href) {
    hotdiv.onclick = function(e) {
        if (meta.onclick) {
          _log("calling "+meta.onclick);
          meta.onclick(e, me);
        } else if (meta.href) {
          document.location = meta.href;
        } else {
          _log("BUG: no onclick or href in meta");
        }
      }
  }

  if (meta.getHoverDiv) {
    hotdiv.onmouseover = function(e) {
        if (!e) e = window.event;
        var popup = me.getHoverDiv(e.screenX, e.screenY);
        if (popup) {
          popup.style.display = 'block';
        }
      }

    hotdiv.onmouseout = function(e) {
        if (!e) e = window.event;
        var popup = me.getHoverDiv(e.screenX, e.screenY);
        if (popup) {
          popup.style.display = 'none';
        }
      }
  } else if (meta.title) {
    hotdiv.title = meta.title;
    if (_cs_isIE) hotdiv.alt = meta.title;
  }

  this.meta = meta;
  this.element = hotdiv;
}

CSHotSpot.prototype.getHoverDiv = function(mouseScreenX, mouseScreenY) {

  if (!this.hoverDiv) {
    if (this.meta.getHoverDiv) {
      var hover = this.meta.getHoverDiv();
      hover.style.display = 'none';
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

function runanim(id, fn) {
  var delay = fn();
  if (delay > 0) {
    setTimeout(function(){runanim(id, fn)}, delay);
  }
}

///////////////////////////////////////////////////////////////////
// CGI-based RPC infrastructure
//
if (!this._cs_libLoaded) {
  _cs_rpc = csNodeHere();
  _cs_rpc.style.display = 'none';
  _cs_rpc_dflt_cgi = "rpc.cgi";
  _cs_rpc_callbacks = {};
  _cs_rpc_nodes = {};
  _cs_rpc_max = 0;
  _cs_rpc_running = 0;
  _cs_rpc_queue = [];
}

function csRPC(jscgiurl, rpcop, argobj, callback, priority) {
  // Queue requests if too busy.
  var qitem = [jscgiurl, rpcop, argobj, callback];
  if (priority) {
    _cs_rpc_queue.splice(0, 0, qitem);
  } else {
    _cs_rpc_queue.push(qitem);
  }

  csRPCflushQueue();
}

function csRPCflushQueue() {
  var dq;
  var jscgiurl;
  var argobj;
  var callback;
  while (
         _cs_rpc_queue.length > 0
      && ( _cs_rpc_max < 1 || _cs_rpc_running < _cs_rpc_max )
        ) {
    _cs_rpc_running++;
    dq = _cs_rpc_queue.shift();
    jscgiurl = dq[0];
    rpcop = dq[1];
    argobj = dq[2];
    callback = dq[3];

    var seq = csSeq();
    var cbk = "cb"+seq;
    _cs_rpc_callbacks[cbk] = callback;
    jscgiurl += "/"+rpcop+"/"+seq;
    if (argobj) {
      jscgiurl += "/csRPC_doCallback/"+escape(csObjectToString(argobj));
    }

    var rpc = csNode("SCRIPT");
    _cs_rpc_nodes["node"+seq] = rpc;
    _cs_rpc.appendChild(rpc);
    csRPCdispatch(rpc, jscgiurl);
  }
}

function csRPCdispatch(scriptnode, url) {
  window.setTimeout(
        function() {
          scriptnode.src = url;
          //_log("RPC:"+url,_cs_loadedNode);
        }, 1);
}

function csRPC_doCallback(seq, result) {
  //_log("doCallback: seq="+seq, _cs_loadedNode);
  if (result == null) {
    _log("RPC CALLBACK: result=null");
  } else {
    csStringable(result);
    var pres = result.toString();
    if (pres.length > 20) pres = pres.substr(0, 20)+"...";
    //_log("RPC RETURN SEQ = "+seq+", result="+pres, _cs_loadedNode);
  }
  var cbk = "cb"+seq;
  var cb = _cs_rpc_callbacks[cbk];
  delete _cs_rpc_callbacks[cbk];
  _cs_rpc_running--;

  // run the callback
  if (typeof(cb) != "function") {
    _log("BAD CALLBACK seq="+seq+": "+cb);
  } else {
    cb(result);
  }

  // queue up more RPCs if waiting
  csRPCflushQueue();

  // drop junk from the document
  _cs_rpc.removeChild(_cs_rpc_nodes["node"+seq]);
  delete _cs_rpc_nodes["node"+seq];
}

function csRPCbg(jscgiurl, rpcop, argobj, callback) {
  setTimeout(function(){ csRPC(jscgiurl, rpcop, argobj, callback); }, 0);
}

function csAddSuperClass(base, sup) {
  //_log("csAddSuperClass("+base+","+sup+")");
  var assign;
  for (var k in sup.prototype) {
    base.prototype[k] = sup.prototype[k];
  }
}

//////////////////////////////////////////////////
// An object with asynchronous attribute methods.
//
// Access to the asynchronous attribute is done via the
// withAttr(attrname, closure(value) { ... }) method.
// The first caller kicks off a csRPC to obtain 'value'.
// Return from the RPC calls the closure.
// Subsequent callers either get called directly with the 'value'
// if it has been obtained or queued to be called when the rpc returns.
//
function CSAsyncObject() {
  this.asyncAttrs = {};
}
// Declares an async attribute named 'attrname'.
// Users of an async attribute usually use it thus:
//   OBJ.withAttr(attrname, closure(value) { ... });
// So, addAttr() sets up the state to do a csRPC() and call the closure.
// The full argument list is:
//   attrname   The name of the attribute.
//   rpcurl     The base URL of the RPC handler. Default is this.rpcurl.
//   rpcop      The RPC method name. Default is attrname.
//   rpcargs    The arguments for the RPC call, a dict.
//              Default: {key: this.key}, to identify the source object.
//
CSAsyncObject.prototype.addAttr = function(attrname, rpcurl, rpcop, rpcargs) {
  if (!rpcurl) rpcurl = this.rpcurl;
  if (!rpcop) rpcop = attrname;
  if (!rpcargs) rpcargs = {key: this.key};
  if (!this.asyncAttrs) this.asyncAttrs = {};

  var attrs = this.asyncAttrs[attrname] = {};
  attrs.rpc = [rpcurl, rpcop, rpcargs];
};
CSAsyncObject.prototype.withAttr = function(attrname, params, callback) {
  if (typeof(params) != "function") {
    // assume 3-arg call - (attr, {params}, callback) - rpc func, params, callback
    rpcargs = {key: this.key}
    for (param in params) {
      rpcargs[param] = params[param];
    }
    csRPC(this.rpcurl,
          attrname,
          rpcargs,
          callback);
    return;
  }

  // assume 2-args call - (attr, callback) - static attribute with callback
  callback = params;

  var me = this;
  var attr = me.asyncAttrs[attrname];
  if (!attr) _log("me.asyncAttrs["+attrname+"]="+attr);

  if (attr.value) {
    callback(attr.value);
  } else if (attr.callbacks) {
    attr.callbacks.push(callback);
  } else {
    attr.callbacks = [callback];
    csRPC(attr.rpc[0], attr.rpc[1], attr.rpc[2], function(res) { me.setAttr(attrname, res); });
  }
};
CSAsyncObject.prototype.setAttr = function(attrname, value) {
  var attr = this.asyncAttrs[attrname];
  if (!attr) {
    attr = this.asyncAttrs[attrname] = {};
  }
  attr.value = value;
  var callbacks = attr.callbacks;
  if (callbacks) {
    delete attr.callbacks;
    for (var i=0; i<callbacks.length; i++) {
      callbacks[i](value);
    }
  }
};
CSAsyncObject.prototype.delAttr = function(attrname) {
  this.setAttr(attrname, null);
};

/**
 * Create a "hot" <SPAN>, calling the control object
 * on various events.
 * inner: string for inner text, or array of HTMLElements.
 * control: the control object which may supply the following methods:
 *              .onmouseover, .onmouseout
 *              .onclick(hotspan, e)
 */
function CSHotSpan(inner, control, context) {
  var me = this;
  me.context = context;
  //_log("new CSHotSPan: context="+context);

  var span = csNode("SPAN");
  span.style.textDecoration = 'underline';
  me.span = span;

  if (typeof(inner) == "string") {
    inner = [csText(inner)];
  }
  for (var i=0; i<inner.length; i++) {
    span.appendChild(inner[i]);
  }

  span.onmouseover = function(e) {
                     if (control.onmouseover) {
                       if (!e) e = window.event;
                       control.onmouseover(me, e);
                       span.onmouseout = function() {
                                         if (control.onmouseout) {
                                           if (!e) e = window.event;
                                           control.onmouseout(me, e);
                                         }
                                       }
                     }
                   };
  span.onclick = function(e) {
                 if (control.onclick) {
                   if (!e) e = window.event;
                   control.onclick(me, e);
                 }
               };
  span.ondblclick = function(e) {
                    if (control.ondblclick) {
                      if (!e) e = window.event;
                      control.ondblclick(me, e);
                    }
                  };
}

/**
 * Controls a DIV containing the specified element with a mouse handler
 * to pan it.
 */
function CSPan(toPan) {
  _log("new pan div, toPan = "+toPan+", src="+toPan.src);

  var outer = csDIV();
  outer.style.position = 'relative';
  outer.style.overflow = 'hidden';

  var glass = null;
  var hotLayer = null;
  var map = null;
  var mapName = null;

  outer.appendChild(toPan);
  csSetPosition(toPan, csXY(0, 0));
  csSetZIndex(toPan, 0);

  if (_csPan_useImageMap) {
    _log("CSPan: want image map - skipping self-made one");
    //mapName = "_csPan_map"+csSeq();
    //map = csHotspotsToClientMap(mapName,[]);
    //_log("map = "+map);
    //outer.appendChild(map);

    //document.body.appendChild(map);
  } else {
    // Place some glass over the object to prevent drag'n'drop causing
    // trouble. Make it full size to cover the outer DIV.
    glass = csDIV("TRANSPARENT");
    outer.appendChild(glass);
    csSetPosition(glass, csXY(0, 0));
    csSetSize(glass,"100%","100%");
    csSetZIndex(glass, 1);

    // Place another layer over the glass for hotspots.
    // We pan this layer with the toPan object to keep the hotspots aligned.
    hotLayer = csDIV("TRANSPARENT");
    outer.appendChild(hotLayer)
    csSetPosition(hotLayer, csXY(0, 0));
    csSetZIndex(hotLayer, 2);
  }

  var me = this;
  this.onMouseDown = function(e) { me.handleDown(e); };
  this.onMouseMove = function(e) { me.handleMove(e); };
  this.onMouseUp   = function(e) { me.handleUp(e); };
  this.onKeyPress  = function(e) { _log("K"); me.handleKeyPress(e); };

  var mouseElem = (glass ? glass : toPan);
  mouseElem.onmousedown = this.onMouseDown;
  if (_csPan_dragCursor) mouseElem.style.cursor=_csPan_dragCursor;

  var keyElem = hotLayer;       //(glass ? glass : outer);
  if (keyElem) {
    keyElem.onkeypress = this.onKeyPress;
  }

  this.element=outer;
  this.glass=glass;
  this.map=map;
  this.mapName=mapName;
  this.hotLayer=hotLayer;
  this.toPan=toPan;
}

CSPan.prototype.addHotSpot = function(meta, z) {

  csStringable(meta);
  xy1=csXY(meta.x, meta.y);
  xy2=csXY(meta.x+meta.dx, meta.y+meta.dy);
  //_log("xy1="+xy1+", xy2="+xy2);

  var hot = new CSHotSpot(meta, xy1, xy2, z);

  if (this.map) {
    csClientMapAddHotSpot(this.map, meta);
  }

  if (this.hotLayer) {
    var div = hot.element;
    //_log("add hot div: "+div+" width="+(xy2.x-xy1.x));
    csSetPosition(div, xy1);
    csSetSize(div, xy2.x-xy1.x, xy2.y-xy1.y);
    if (z != null) csSetZIndex(div, z);
    this.hotLayer.appendChild(div);
  }

  return hot;
}

CSPan.prototype.addHotSpots = function(hotspots, z) {
  var spot;
  for (var i=0; i < hotspots.length; i++) {
    spot=hotspots[i];
    if (spot) {
      this.addHotSpot(spot, z);
    }
  }
};

// Set centre point from fraction.
CSPan.prototype.setCentre = function(fxy) {
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
  if (this.hotLayer) { csSetPosition(this.hotLayer, topLeft); }
  this.centreFXY = null;
}

CSPan.prototype.setSize = function(width, height) {
  var me = this;
  var cxy = me.getCentre();
  _log("pan.setSize(width="+width+", height="+height+")");
  csSetSize(me.element, width, height);
  me.reCentre();
};

CSPan.prototype.handleKeyPress = function(e) {
  if (!e) e=window.event;

  var keycode = (_is_IE ? e.keyCode : e.which);
  _log("keycode = "+keycode);

  return false;
};

CSPan.prototype.handleDown = function(e) {
  if (!e) e=window.event;

  this.panning=true;
  this.mouseXY = csXY(e.clientX, e.clientY);
  this.toPanTopLeft = csXY(this.toPan.offsetLeft, this.toPan.offsetTop);

  if (document.addEventListener) {
    document.addEventListener("mousemove", this.onMouseMove, true);
    document.addEventListener("mouseup",   this.onMouseUp,  true);

    if (_csPan_draggingCursor) {
      this.savedDocCursor = document.body.style.cursor;
      document.body.style.cursor = _csPan_draggingCursor;
      if (this.glass) this.glass.style.cursor = _csPan_draggingCursor;
    }
  } else {
    this.toPan.attachEvent("onmousemove", this.onMouseMove);
    this.toPan.attachEvent("onmouseup",   this.onMouseUp);
    this.toPan.setCapture();

    if (_csPan_draggingCursor) {
      this.savedPanCursor = this.toPan.style.cursor;
      this.toPan.style.cursor = _csPan_draggingCursor;
    }
  }

  if (this.hotLayer) this.hotLayer.style.display='none';
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
    if (this.glass && _csPan_dragCursor)
      this.glass.style.cursor = _csPan_dragCursor;

    if (document.removeEventListener) {
      document.removeEventListener("mousemove", this.onMouseMove, true);
      document.removeEventListener("mouseup",   this.onMouseUp, true);
      if (_csPan_draggingCursor) {
        document.body.style.cursor = this.savedDocCursor;
        this.savedDocCursor = null;
      }
    } else {
      this.toPan.detachEvent("onmousemove", this.onMouseMove);
      this.toPan.detachEvent("onmouseup", this.onMouseMove);
      this.toPan.releaseCapture();
      if (_csPan_draggingCursor) {
        this.toPan.style.cursor = this.savedPanCursor;
        this.savedPanCursor = null;
      }
    }

    if (this.hotLayer) this.hotLayer.style.display='block';
  }
};

function CSFolder(control, startOpen) {
  var me = this;
  this.control=control;

  var table = csNode("TABLE");
  //table.border=1;

  var titleRow = table.insertRow(0);

  var titleCell = titleRow.insertCell(0);
  titleCell.style.align='left';
  titleCell.style.vAlign='top';

  var innerRow = table.insertRow(1);
  var innerCell = innerRow.insertCell(0);
  titleCell.style.align='left';
  titleCell.style.vAlign='top';
  var titleSpan = csNode("SPAN");
  titleSpan.style.textDecoration='underline';
  titleCell.appendChild(titleSpan);
  titleSpan.onclick=function(cell, e) {
                      me.setOpen(!me.isOpen);
                    };

  this.table=table;
  this.titleCell=titleCell;
  this.titleSpan=titleSpan;
  this.innerRow=innerRow;
  this.innerCell=innerCell;
  this.isOpen=false;
  csShowTableElem(this.innerRow, false);
  this.setOpen(startOpen);
}

CSFolder.prototype.setTitle = function(newtitle) {
  csSetInnerElements(this.titleSpan, newtitle);
}

CSFolder.prototype.setOpen = function(openMode) {
  if (openMode) {
    if (!this.isOpen) {
      this.isOpen=true;
      if (this.control.onopen) {
        this.control.onopen(this);
      }
      csShowTableElem(this.innerRow, true);
    }
  } else {
    if (this.isOpen) {
      this.isOpen=false;
      csShowTableElem(this.innerRow, false);
      if (this.control.onclose) {
        this.control.onclose(this);
      }
    }
  }
};

function csShowTableElem(elem, showit) {
  if (elem) {
    if (_cs_isIE) {
      elem.display=( showit ? 'block' : 'none' );
    } else {
      elem.style.visibility=(showit ? 'visible' : 'collapse' );
    }
  }
}

// A textfield for entering a string, with an accompanying list of helper choices.
// The control object needs to provide the following methods:
//   .onchange(), to apply an entered string
//   .withChoices(), to call a function with the available entry choice strings
function CSEntry(control, prefix) {
  var me = this;
  me.control=control;
  me.value=''
  me.span=csNode('SPAN');
  me.text=csNode('INPUT');
  me.text.type='TEXT';
  me.text.value='';
  me.text.size=10;
  me.text.maxlength=16;
  me.text.onfocus=function() { me.hasFocus=true;  me.showPrefix(); }
  me.text.onblur=function()  { me.hasFocus=false; me.showPrefix(); }
  //Done by the keypress stuff below.
  //me.text.onchange=function() { me.choose(me.text.value); return false; };
  me.text.onkeypress=function(e) {
                       var caught=false;
                       if (!e) e=window.event;
                       var keycode = e.which || e.keyCode;
                       _log("keycode="+keycode);
                       if (e.modifiers) {
                         _log("modifiers="+e.modifiers);
                       }
                       if (keycode >= 32) {
                         caught=true;
                         me.set(me.value+String.fromCharCode(keycode));
                       } else if (keycode == 13) {
                         // enter/return
                         caught=true;
                         me.set(me.value);
                         me.control.choose(me.value);
                       } else if (keycode == 8) {
                         // backspace
                         caught=true;
                         if (me.value.length > 0) {
                           me.set(me.value.substr(0, me.value.length-1));
                         }
                       } else if (keycode == 9) {
                         // TAB completion
                         caught=true;
                         if (me.value.length) {
                           me.control.withChoices(
                             function(choices) {
                               var pfx=me.value;
                               var nchoices=choices.length;
                               while (true) {
                                 var nextch=null;
                                 for (var i=0; i<nchoices; i++) {
                                   var choice=choices[i];
                                   if (choice.length > pfx.length
                                    && choice.substr(0, pfx.length) == pfx) {
                                     var nnextch = choice.substr(pfx.length, 1);
                                     if (nextch == null) {
                                       nextch=nnextch;
                                     } else if (nnextch != nextch) {
                                       nextch=null;
                                       break;
                                     }
                                   }
                                 }
                                 if (nextch == null) {
                                   break;
                                  }
                                  pfx += nextch;
                               }
                               me.set(pfx);
                             });
                         }
                       }

                       me.text.value=me.value;
                       if (_cs_isIE) event.returnValue=!caught;
                       return !caught;
                     };
  me.span.appendChild(me.text);
}
CSEntry.prototype.setTooltip = function(tip) {
  this.text.title=tip;
};
CSEntry.prototype.showPopup = function(doShow) {
  if (_cs_isIE) {
    // FIXME: find out how to hide table rows in IE
    doShow=false;
  }
  if (this.popup) {
    if (doShow) {
      csPosNodeBelow(this.popup, this.span);
    }
    this.popup.style.display=(doShow ? 'block' : 'none');
  }
};
CSEntry.prototype.showPrefix = function() {
  var me=this;
  if (!me.popup) {
    me.popup=csDIV(null, 1);
    me.popup.onmouseover=function(){ _log("mouseover popup"); me.inPopup=true; };
    me.popup.onmouseout =function(){ _log("mouseout  popup"); me.inPopup=false; };
    me.showPopup(false);
    me.control.withChoices(
      function(choices) {
        _log("make new vlist: choices="+choices);
        me.vlist=new CSVList(
                       choices,
                       { choose:
                           function(choice) {
                             me.set(choice);
                             me.control.choose(me.value);
                           },
                         mkChoiceLabel:
                           function(choice) {
                             if (me.control.mkChoiceLabel) {
                               return me.control.mkChoiceLabel(choice, me);
                             }
                             return new CSHotSpan(
                                          choice,
                                          { onclick: function(hotspan) {
                                                       me.set(hotspan.context);
                                                       me.control.choose(hotspan.context);
                                                     }
                                          }, choice).span;
                           }
                       },
                       me.value);
        me._showPrefix();
        me.popup.appendChild(me.vlist.table);
      });
    me.span.appendChild(me.popup);
  } else {
    me._showPrefix();
  }
};
CSEntry.prototype._showPrefix = function() {
  _log("_showpfx("+this.value+")");
  if (this.value.length == 0 || (!this.inPopup && this.hasFocus != null && !this.hasFocus)) {
    this.showPopup(false);
  } else {
    if (this.vlist) {
      this.vlist.showPrefix(this.value);
      this.showPopup(true);
    }
  }
};
CSEntry.prototype.reset = function() {
  this.set('');
};
CSEntry.prototype.set = function(value) {
  this.text.value=value;
  this.value=value;
  this.showPrefix();
};
CSEntry.prototype.choose = function(value) {
  this.set(value);
  this.control.choose(value);
};

// A table for selecting from a list of choices.
// The control supplies these methods:
//   .choose(choice)              Called when a choice is selected.
//   .mkChoiceLabel(choice, this) Optional, returns an in-line element to use
//                                for the choice label. If it catches clicks,
//                                it must call this.choose(choice).
function CSVList(choices, control, prefix) {
  var me=this;
  me.control=control;
  me.table=new csNode('TABLE');
  //me.table.border=1;
  me.table.style.borderCollapse='collapse';
  me.rowMap={};
  var nrows=0;
  for (var i=0; i<choices.length; i++) {
    var choice = choices[i];
    var row = me.table.insertRow(nrows++);
    me.rowMap[choice] = row;
    var cell = row.insertCell(0);
    var labelSpan = control.mkChoiceLabel(choice);
    cell.appendChild(labelSpan);
  }

  if (prefix && prefix.length) {
    me.showPrefix(prefix);
  }
}
CSVList.prototype.choose = function(choice) {
  this.control.choose(choice);
};
CSVList.prototype.onclick = function(hotspan, e) {
  this.choose(hotspan.context);
};
CSVList.prototype.showPrefix = function(prefix) {
  for (var choice in this.rowMap) {
    vis = ( choice.length >= prefix.length && choice.substr(0, prefix.length) == prefix )
          ? 'visible'
          : 'collapse'
          ;
    if (_cs_isIE) {
      this.rowMap[choice].visibility=vis;
    } else {
      this.rowMap[choice].style.visibility=vis;
    }
  }
}

{ var vp = csViewPort();
  _log("viewport = "+vp.x+"x"+vp.y+", "+vp.width+"x"+vp.height);
}

_cs_libLoaded=true;

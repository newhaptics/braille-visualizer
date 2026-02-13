// === frontend/sketch.js ===
let ROWS = 20;
let COLS = 96;
let PADDING = 50;
let GESTURE_COLORS;
let DEFAULT_COLOR;

let cellWidth, cellHeight;

let dotMatrix = [];
let doubleTaps = [];
let fingers = {};

// ── Braille-to-Latin lookup (Grade 1 UEB) ──
const B2L = {};
(function(){
  const letters = 'a b c d e f g h i j k l m n o p q r s t u v w x y z'.split(' ');
  const codes = [1,3,9,25,17,11,27,19,10,26,5,7,13,29,21,15,31,23,14,30,37,39,58,45,61,53];
  codes.forEach((c,i) => B2L[c] = letters[i]);
  const nums = '1 2 3 4 5 6 7 8 9 0'.split(' ');
  const ncodes = [1,3,9,25,17,11,27,19,10,26];
  ncodes.forEach((c,i) => B2L[c + 0x20] = nums[i]);
  B2L[0] = ' '; B2L[2] = ','; B2L[50] = '.'; B2L[18] = ';'; B2L[34] = ':';
  B2L[6] = '!'; B2L[38] = '?'; B2L[36] = '-'; B2L[4] = "'";
  B2L[44] = '#'; B2L[32] = '_'; B2L[48] = '_';
  B2L[0xFF] = '#'; B2L[0xF8] = '|'; B2L[0xE0] = '|';
})();

function brToLatin(s) {
  let out = '', cap = false;
  for (let i = 0; i < s.length; i++) {
    const ch = s.charCodeAt(i);
    if (ch < 0x2800 || ch > 0x28FF) { out += s[i]; cap = false; continue; }
    const code = ch - 0x2800;
    if (code === 0) { out += ' '; continue; }
    if (code === 32) { cap = true; continue; }  // capital indicator
    if (code === 44 || code === 60) { continue; }  // number indicator, letter indicator
    const l = B2L[code];
    if (l) { if (cap) { out += l.toUpperCase(); cap = false; } else out += l; }
    else { out += String.fromCharCode(ch); }
  }
  return out;
}

// connect to WebSocket server from config.js
let socket;

function setup() {
  let canvas = createCanvas(1600 + PADDING, 350 + PADDING);
  canvas.parent("canvas-container");

  cellWidth = (width - PADDING) / COLS;
  cellHeight = (height - PADDING) / ROWS;

  // initialize gesture colors
  DEFAULT_COLOR = color(255, 255, 255, 200);

  GESTURE_COLORS = {
    scrubbing: color(255, 0, 0, 200),
    regression: color(255, 0, 255, 200)
  };

  // initialize empty matrix
  for (let i = 0; i < ROWS; i++) {
    dotMatrix[i] = Array(COLS).fill(0);
  }

  // WebSocket connection
  socket = new WebSocket(WS_HOST);
  socket.onmessage = handleMessage;
  socket.onopen = () => console.log("Connected to WebSocket");
  socket.onclose = () => console.log("Disconnected from WebSocket");
  socket.onerror = (err) => console.error("WebSocket error:", err);
}

function handleMessage(event) {
  const msg = JSON.parse(event.data);

  if (msg.type == "matrix") {
    dotMatrix = msg.mat;
    // Update Latin text display if braille string is included
    if (msg.braille !== undefined) {
      const lines = msg.braille.split('\n');
      const latinEl = document.getElementById('latin-content');
      if (latinEl) latinEl.textContent = lines.map(l => brToLatin(l)).join('\n');
    }
  }

  else if (msg.type == "touch") {
    let finger = fingers[msg.id] || {
      down: false,
      x: null,
      y: null,
      amp: 0,
      area: 0,
      color: DEFAULT_COLOR
    };
    if (msg.action == "down") {
      finger.down = true;
      finger.x = map(msg.x, 0, 1600, (PADDING / 2), width - (PADDING / 2));
      finger.y = map(msg.y, 0, 350, (PADDING / 2), height - (PADDING / 2));
      finger.amp = msg.amp || 0;
      finger.area = msg.area || 0;
    } else if (msg.action == "up") {
      finger.down = false;
      finger.x = null;
      finger.y = null;
      finger.amp = 0;
      finger.area = 0;
      finger.color = getGestureColor(null)
    } else if (msg.action == "move") {
      if (finger.down == true) {
        finger.color = getGestureColor(msg.gesture);
        // Color trail by amplitude
        let trailColor = ampToColor(msg.amp || 0);
        stroke(trailColor);
        strokeWeight(40);
        let x_new = map(msg.x, 0, 1600, (PADDING / 2), width - (PADDING / 2));
        let y_new = map(msg.y, 0, 350, (PADDING / 2), height - (PADDING / 2));
        line(finger.x, finger.y, x_new, y_new);
      }
      finger.x = map(msg.x, 0, 1600, (PADDING / 2), width - (PADDING / 2));
      finger.y = map(msg.y, 0, 350, (PADDING / 2), height - (PADDING / 2));
      finger.amp = msg.amp || 0;
      finger.area = msg.area || 0;
    }
    fingers[msg.id] = finger;
  }

  else if (msg.type == "double tap") {
    let row_idx = msg.row * 5;
    let y_idxs = [row_idx, row_idx + 1, row_idx + 2, row_idx + 3];

    let col_idx = msg.column * 3;
    let x_idxs = [col_idx, col_idx + 1];

    for (let x of x_idxs) {
      for (let y of y_idxs) {
        doubleTaps.push({'x_idx': x, 'y_idx': y, 'life': 300})
      }
    }
  }
}

function getGestureColor(gesture) {
  return GESTURE_COLORS[gesture] || DEFAULT_COLOR;
}

// Map amplitude (0-100+) to a color gradient: blue → cyan → green → yellow → red
function ampToColor(amp) {
  let t = constrain(amp / 100, 0, 1);
  let r, g, b;
  if (t < 0.25) {
    // blue → cyan
    let s = t / 0.25;
    r = 51; g = lerp(51, 255, s); b = 255;
  } else if (t < 0.5) {
    // cyan → green
    let s = (t - 0.25) / 0.25;
    r = 51; g = 255; b = lerp(255, 51, s);
  } else if (t < 0.75) {
    // green → yellow
    let s = (t - 0.5) / 0.25;
    r = lerp(51, 255, s); g = 255; b = 51;
  } else {
    // yellow → red
    let s = (t - 0.75) / 0.25;
    r = 255; g = lerp(255, 51, s); b = 51;
  }
  return color(r, g, b, 200);
}

function draw() {
  noStroke();
  // Fade the trail layer
  fill(0, 0, 0, 20);
  rect(0, 0, width, height);

  // Draw ALL 20x96 dot positions
  for (let i = 0; i < ROWS; i++) {
    for (let j = 0; j < COLS; j++) {
      let x = j * cellWidth + (PADDING / 2);
      let y = i * cellHeight + (PADDING / 2);
      let isGap = (j % 3 == 2) || (i % 5 == 4);

      if (isGap) {
        // Gap dots: smaller, dimmer
        fill("#1a1a1a");
        ellipse(x + cellWidth / 2, y + cellHeight / 2, cellWidth * 0.28, cellHeight * 0.28);
      } else {
        // Braille dots: normal size
        fill(dotMatrix[i][j] ? "white" : "#444");
        ellipse(x + cellWidth / 2, y + cellHeight / 2, cellWidth * 0.56, cellHeight * 0.56);
      }
    }
  }

  // Draw double-tap highlights
  for (let i = doubleTaps.length - 1; i >= 0; i--) {
    let dt = doubleTaps[i];
    let x = dt.x_idx * cellWidth + (PADDING / 2);
    let y = dt.y_idx * cellHeight + (PADDING / 2);
    let alpha = map(dt.life, 0, 300, 0, 200);
    fill(0, 210, 255, alpha);
    ellipse(x + cellWidth / 2, y + cellHeight / 2, cellWidth * 0.56, cellHeight * 0.56);

    dt.life = dt.life - 3;
    if (dt.life <= 0) {
      doubleTaps.splice(i, 1);
    }
    else {
      doubleTaps[i] = dt;
    }
  }

  // Draw active finger indicators: color = amplitude, size = area
  noStroke();
  for (let id in fingers) {
    let f = fingers[id];
    if (f.down && f.x !== null) {
      let c = ampToColor(f.amp);
      // Map area to diameter: min 12px, scales up with area
      let diameter = max(12, 8 + f.area * 2.5);
      fill(c);
      ellipse(f.x, f.y, diameter, diameter);

      // Label: amp / area
      fill(255, 255, 255, 180);
      textSize(10);
      textAlign(CENTER, BOTTOM);
      text(`${f.amp} / ${f.area}`, f.x, f.y - diameter / 2 - 4);
    }
  }
}

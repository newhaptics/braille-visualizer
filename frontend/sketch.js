// === frontend/sketch.js ===
// Resize-aware CodeX visualizer

// ---------- logical dimensions of the device ----------
const TS_WIDTH = 1601;   // touchscreen width (0 -> 1600)
const TS_HEIGHT = 351;   // touchscreen height (0 -> 350)
const ROWS = 21;
const COLS = 96;
const PAD  = 0;         // physical padding around grid (kept in screen px)

// ---------- globals that change when the window is resized ----------
let scale;                         // (uniform in X & Y)
let cellWidth, cellHeight;         // size of one braille dot “cell” on‑screen
let canvasWidth, canvasHeight;     // actual p5 canvas size (includes PAD)
let bgLayer;                       // cached grid layer
redraw = true;                     // becomes true on dot‑matrix or size change

let dotMatrix      = [];    // 20x96 array, updated on "matrix" messages
let fingers        = {};    // map: fingerID -> { x, y, color }
let doubleTaps     = []     // list of double taps

let DEFAULT_COLOR;
let GESTURE_COLORS;

let socket = new WebSocket(WS_HOST);

function setup() {
  createCanvas();   // immediately resize in updateLayout()
  pixelDensity(1);  // avoids blurriness on high‑DPI screens
  updateLayout();   // compute scale & resize everything

  // offscreen buffer
  bgLayer = createGraphics(canvasWidth, canvasHeight);
  bgLayer.noStroke();

  // initialize dotMatrix as all zeros
  for (let i = 0; i < ROWS; i++) {
    dotMatrix[i] = Array(COLS).fill(0);
  }

  // gesture‐based colors
  DEFAULT_COLOR = color(255, 255, 255, 200);
  GESTURE_COLORS = {
    scrubbing:   color(255,   0,   0, 200),
    regression:  color(255,   0, 255, 200)
  };

  // WebSocket handlers
  socket.onmessage = handleMessage;
  socket.onopen    = () => console.log("Connected to WebSocket");
  socket.onclose   = () => console.log("Disconnected from WebSocket");
  socket.onerror   = (err) => console.error("WebSocket error:", err);
}

function windowResized() {
    updateLayout();
}

function updateLayout() {
    const maxWidth = windowWidth - PAD;
    const maxHeight = windowHeight - PAD;

    // uniform scale so dots stay round
    scale = Math.min(maxWidth / TS_WIDTH, maxHeight / TS_HEIGHT);

    // calculate on-screen size
    canvasWidth  = TS_WIDTH  * scale + PAD;
    canvasHeight = TS_HEIGHT * scale + PAD;

    // resize canvas
    resizeCanvas(canvasWidth, canvasHeight, true);
    if (bgLayer) {
        bgLayer.resizeCanvas(canvasWidth, canvasHeight, true);
    }

    // avoid cumulative float error by re-deriving per cell
    cellWidth  = (canvasWidth  - PAD) / COLS;
    cellHeight = (canvasHeight - PAD) / ROWS;

    redraw = true;
}

function deviceToScreen(x, y) {
    return {
        x: x * ((canvasWidth - PAD) / TS_WIDTH) + (PAD / 2),
        y: y * ((canvasHeight - PAD) / TS_HEIGHT) + (PAD / 2)
    };
}

function handleMessage(event) {
  const msg = JSON.parse(event.data);

  if (msg.type === "matrix") {
    // new 20x96 array -> update and mark bgLayer dirty
    dotMatrix = msg.mat;
    redraw = true;
  }
  else if (msg.type === "touch") {
    const fid = msg.id;
    const pos = deviceToScreen(msg.x, msg.y);

    if (msg.action === "down") {
      // finger down -> add to fingers map
      let c  = GESTURE_COLORS[msg.gesture] || DEFAULT_COLOR;
      fingers[fid] = { x: pos.x, y: pos.y, color: c };
    }
    else if (msg.action === "move") {
      // finger move -> update its x,y, color (if needed)
      if (fingers[fid]) {
        let c  = GESTURE_COLORS[msg.gesture] || DEFAULT_COLOR;
        fingers[fid].x     = pos.x;
        fingers[fid].y     = pos.y;
        fingers[fid].color = c;
      }
    }
    else if (msg.action === "up") {
      // finger up -> remove from map
      delete fingers[fid];
    }
  }
  else if (msg.type === "double tap") {
    // map from logical row/col to braille‐dot indices
    let row_idx = msg.row * 5;
    let col_idx = msg.column * 3;
    for (let dy = 0; dy < 4; dy++) {
      for (let dx = 0; dx < 2; dx++) {
        doubleTaps.push({
          x_idx: col_idx + dx,
          y_idx: row_idx + dy,
          life: 300
        });
      }
    }
  }
}

function drawGrid() {
    for (let i = 0; i < ROWS; i++) {
      for (let j = 0; j < COLS; j++) {
        if (i % 5 !== 0 && j % 3 !== 0) { // j % 3 !== 2 && i % 5 !== 0
          const x = (PAD / 2) + (j * cellWidth) + (cellWidth / 2);
          const y = (PAD / 2) + (i * cellHeight) + (cellHeight / 2);
          bgLayer.fill(dotMatrix[i][j] ? 255 : 68);
          bgLayer.ellipse(
            x,
            y,
            cellWidth * 0.56,
            cellHeight * 0.56
          );
        }
      }
    }
}

function drawDoubleTaps() {
    noStroke();
    // iterate backwards so we can splice out dead taps
    for (let i = doubleTaps.length - 1; i >= 0; i--) {
        let t = doubleTaps[i];
        t.life--;
        if (t.life <= 0) {
          doubleTaps.splice(i, 1);
          continue;
        }

        // convert cell coordinates -> screen
        let x = (PAD/2) + (t.x_idx * cellWidth) + (cellWidth/2);
        let y = (PAD/2) + (t.y_idx * cellHeight) + (cellHeight/2);

        // fade out over time
        let alpha = map(t.life, 0, 300, 0, 200);
        fill(255, 255, 0, alpha);
        ellipse(x, y, cellWidth * 0.8, cellHeight * 0.8);
    }
}

function drawFingers() {
  noStroke();
  for (let fid in fingers) {
    const f = fingers[fid];
    fill(f.color);
    ellipse(f.x, f.y, scale * 30, scale * 30);
  }
}

function draw() {
  // if the dotMatrix changed, re‐draw it into bgLayer
  if (redraw) {
    bgLayer.background(0);
    drawGrid();
    redraw = false;
  }

  // blit the cached background (erasing any old circles)
  image(bgLayer, 0, 0);

  // draw double taps
  drawDoubleTaps();

  // draw circles for any fingers that are down
  drawFingers();
}
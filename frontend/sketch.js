// === frontend/sketch.js ===
// Resize-aware CodeX visualizer

// ---------- logical dimensions of the device ----------
const TS_WIDTH = 1600;   // touchscreen width
const TS_HEIGHT = 350;   // touchscreen height
const ROWS = 20;
const COLS = 96;
const PAD  = 50;         // physical padding around grid (kept in screen px)

// ---------- globals that change when the window is resized ----------
let scale;                // (uniform in X & Y)
let cellWidth, cellHeight;         // size of one braille dot “cell” on‑screen
let canvasWidth, canvasHeight;     // actual p5 canvas size (includes PAD)
let bgLayer;              // cached grid layer
redraw = true; // becomes true on dot‑matrix or size change

let dotMatrix      = [];    // 20x96 array, updated on "matrix" messages
let fingers        = {};    // map: fingerID -> { x, y, color }

let DEFAULT_COLOR;
let GESTURE_COLORS;

let socket = new WebSocket(WS_HOST);

function setup() {
  createCanvas(1, 1);            // immediately resize in updateLayout()
  pixelDensity(1);               // avoids blurriness on high‑DPI screens
  updateLayout();                // compute scale & resize everything

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
    canvasWidth = Math.round(TS_WIDTH * scale + PAD);
    canvasHeight = Math.round(TS_HEIGHT * scale + PAD);

    // resize canvas
    resizeCanvas(canvasWidth, canvasHeight, true);
    if (bgLayer) {
        bgLayer.resizeCanvas(canvasWidth, canvasHeight, true);
    }

    // avoid cumulative float error by re-deriving per cell
    cellWidth =  (canvasWidth - PAD)  / COLS;
    cellHeight = (canvasHeight - PAD) / ROWS;

    redraw = true;
}

function deviceToScreen(x, y) {
    return {
        x: (x * scale) + (PAD / 2),
        y: (y * scale) + (PAD / 2)
    };
}

function handleMessage(event) {
  const msg = JSON.parse(event.data);

  if (msg.type === "matrix") {
    // new 20x96 array → update and mark bgLayer dirty
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
}

function draw() {
  // if the dotMatrix changed, re‐draw it into bgLayer
  if (redraw) {
    bgLayer.background(0);
    for (let i = 0; i < ROWS; i++) {
      for (let j = 0; j < COLS; j++) {
        if (j % 3 !== 2 && i % 5 !== 4) {
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
    redraw = false;
  }

  // blit the cached background (erasing any old circles)
  image(bgLayer, 0, 0);

  // draw circles for any "down" fingers that remain in the map
  noStroke();
  for (let fid in fingers) {
    let f = fingers[fid];
    fill(f.color);
    ellipse(
      f.x + cellWidth/2,
      f.y + cellHeight/2,
      scale * 40,
      scale * 40
    );
  }
}
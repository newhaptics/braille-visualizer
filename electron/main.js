const { app, BrowserWindow } = require("electron");
const { spawn, execSync } = require("child_process");
const path = require("path");
const http = require("http");

// ---------------------------------------------------------------------------
// Path helpers – resolve to the right location in dev vs packaged builds
// ---------------------------------------------------------------------------
function resourcePath(...segments) {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, ...segments);
  }
  return path.join(__dirname, "..", ...segments);
}

// ---------------------------------------------------------------------------
// Subprocess management
// ---------------------------------------------------------------------------
let nexusProc = null;
let backendProc = null;

function killStaleProcesses() {
  // Kill any leftover nexusproxy/backend instances from previous runs
  for (const name of ["nexusproxy.exe", "backend.exe"]) {
    try {
      execSync(`taskkill /F /IM ${name}`, { stdio: "ignore" });
      console.log(`[electron] Killed stale ${name}`);
    } catch {
      // No instances found — that's fine
    }
  }
}

function spawnNexusProxy() {
  const exe = resourcePath("nexusproxy.exe");
  console.log("[electron] Starting nexusproxy:", exe);
  nexusProc = spawn(exe, [], {
    stdio: "ignore",
    windowsHide: true,
  });
  nexusProc.on("error", (err) =>
    console.error("[electron] nexusproxy error:", err.message)
  );
  nexusProc.on("exit", (code) =>
    console.log("[electron] nexusproxy exited with code", code)
  );
}

function spawnBackend() {
  let cmd, args, cwd;

  if (app.isPackaged) {
    // Packaged: run the PyInstaller-compiled backend.exe
    cmd = resourcePath("backend", "backend.exe");
    args = [];
    cwd = resourcePath("backend");
  } else {
    // Dev: run Python directly (-u = unbuffered so prints appear immediately)
    cmd = "python";
    args = ["-u", path.join(__dirname, "..", "backend", "main.py")];
    cwd = path.join(__dirname, "..", "backend");
  }

  console.log("[electron] Starting backend:", cmd, args.join(" "));
  backendProc = spawn(cmd, args, {
    cwd,
    stdio: "pipe",
    windowsHide: true,
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
  });

  backendProc.stdout.on("data", (d) => process.stdout.write(d));
  backendProc.stderr.on("data", (d) => process.stderr.write(d));
  backendProc.on("error", (err) =>
    console.error("[electron] backend error:", err.message)
  );
  backendProc.on("exit", (code) =>
    console.log("[electron] backend exited with code", code)
  );
}

// ---------------------------------------------------------------------------
// Wait for the FastAPI backend to be ready before opening the window
// ---------------------------------------------------------------------------
function waitForBackend(url, timeoutMs = 30000) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    function poll() {
      if (Date.now() - start > timeoutMs) {
        return reject(new Error("Backend did not start in time"));
      }
      http
        .get(url, (res) => {
          if (res.statusCode === 200) return resolve();
          setTimeout(poll, 500);
        })
        .on("error", () => setTimeout(poll, 500));
    }
    poll();
  });
}

// ---------------------------------------------------------------------------
// Electron app lifecycle
// ---------------------------------------------------------------------------
let mainWindow = null;

async function createWindow() {
  // 0. Kill leftover processes from previous runs
  killStaleProcesses();

  // 1. Launch subprocesses
  spawnNexusProxy();
  spawnBackend();

  // 2. Wait until the backend is serving
  try {
    await waitForBackend("http://localhost:8000/health");
    console.log("[electron] Backend is ready");
  } catch (err) {
    console.error("[electron]", err.message);
  }

  // 3. Open the main window
  mainWindow = new BrowserWindow({
    width: 1700,
    height: 450,
    title: "Braille Visualizer",
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  mainWindow.loadURL("http://localhost:8000");
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function killSubprocesses() {
  for (const [name, proc] of [
    ["nexusproxy", nexusProc],
    ["backend", backendProc],
  ]) {
    if (proc && !proc.killed) {
      console.log(`[electron] Killing ${name} (pid ${proc.pid})`);
      try {
        // taskkill /F /T kills the process and its entire child tree
        execSync(`taskkill /F /T /PID ${proc.pid}`, { stdio: "ignore" });
      } catch {
        proc.kill();
      }
    }
  }
  nexusProc = null;
  backendProc = null;
}

app.whenReady().then(createWindow);

app.on("window-all-closed", () => {
  app.quit();
});

app.on("before-quit", () => {
  killSubprocesses();
});

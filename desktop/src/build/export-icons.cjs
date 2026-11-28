const { app, BrowserWindow } = require("electron");
const { execFile } = require("node:child_process");
const { mkdtempSync } = require("node:fs");
const { mkdir, readFile, rm, writeFile } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const { dirname, join, resolve } = require("node:path");
const { promisify } = require("node:util");
const { Data } = require("resedit");

async function exportIcons() {
  const root = resolve(__dirname, "../../..");
  const work = mkdtempSync(join(tmpdir(), "colink-icons-"));
  app.setPath("userData", join(work, "user-data"));
  await app.whenReady();
  const window = new BrowserWindow({
    show: false,
    webPreferences: {
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  await window.loadURL("data:text/html,<meta charset=utf-8>");
  const svg = await readFile(
    join(root, "dashboard/public/colink-app-icon.svg"),
    "utf8",
  );
  const source = `data:image/svg+xml;base64,${Buffer.from(svg).toString(
    "base64",
  )}`;
  async function render(size, inset = 0) {
    const png = await window.webContents.executeJavaScript(`(async () => {
      const image = new Image();
      image.src = ${JSON.stringify(source)};
      await image.decode();
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = ${size};
      canvas.getContext("2d").drawImage(image, ${inset}, ${inset}, ${
        size - 2 * inset
      }, ${size - 2 * inset});
      return canvas.toDataURL("image/png").split(",")[1];
    })()`);
    return Buffer.from(png, "base64");
  }
  async function write(file, data) {
    await mkdir(dirname(join(root, file)), { recursive: true });
    await writeFile(join(root, file), data);
    console.log(file);
  }

  const windowsSizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256];
  const linuxSizes = [16, 24, 32, 48, 64, 96, 128, 256, 512, 1024];
  const pngs = new Map();
  for (const size of new Set([...windowsSizes, ...linuxSizes])) {
    pngs.set(size, await render(size));
  }
  const ico = new Data.IconFile();
  ico.icons = windowsSizes.map((size) => ({
    data: Data.RawIconItem.from(pngs.get(size), size, size, 32),
  }));
  await write(
    "desktop/src/build/windows/icon.ico",
    Buffer.from(ico.generate()),
  );
  for (const size of linuxSizes) {
    await write(`desktop/src/build/linux/icons/${size}.png`, pngs.get(size));
  }

  const iconset = join(work, "AppIcon.iconset");
  await mkdir(iconset);
  for (const size of [16, 32, 128, 256, 512]) {
    for (const scale of [1, 2]) {
      const pixels = size * scale;
      await writeFile(
        join(iconset, `icon_${size}x${size}${scale === 2 ? "@2x" : ""}.png`),
        await render(pixels, (pixels * 100) / 1024),
      );
    }
  }
  await promisify(execFile)("iconutil", [
    "-c",
    "icns",
    iconset,
    "-o",
    join(root, "desktop/src/build/darwin/icons.icns"),
  ]);
  console.log("desktop/src/build/darwin/icons.icns");
  await write("desktop/src/build/appicon.png", pngs.get(512));
  await write("desktop/src/build/appicon-macos.png", await render(1024, 100));
  await write("dashboard/public/pwa-192.png", await render(192));
  await write("dashboard/public/pwa-512.png", pngs.get(512));
  await write("dashboard/public/apple-touch-icon.png", await render(180));
  window.destroy();
  await rm(work, { recursive: true, force: true });
}

exportIcons()
  .then(() => app.quit())
  .catch((error) => {
    console.error(error);
    app.exit(1);
  });

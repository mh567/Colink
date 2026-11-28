const path = require("node:path");

const platform =
  process.env.OCTOP_PLATFORM ||
  `${process.platform === "win32" ? "windows" : process.platform}-${
    process.arch === "x64" ? "amd64" : process.arch
  }`;
const version = process.env.OCTOP_VERSION || require("./package.json").version;
const semver = version.replace(
  /(\d)(a|b|rc)(\d+)$/,
  (_, number, stage, count) =>
    `${number}-${{ a: "alpha", b: "beta", rc: "rc" }[stage]}.${count}`,
);

module.exports = {
  appId: "com.colink.enterprise.desktop",
  productName: "Colink",
  executableName: "colink",
  extraMetadata: { version: semver },
  asar: true,
  npmRebuild: false,
  publish: null,
  directories: { output: "release" },
  artifactName: `Colink-desktop-${platform}-${version}.\${ext}`,
  files: [
    "out/main/**",
    "out/preload/**",
    "out/shared/**",
    "renderer/**",
    "package.json",
    "!**/*.test.js",
  ],
  extraResources: [
    {
      from: `../portable/release/Octop-${platform}`,
      to: "portable",
      filter: ["**/*", "!data{,/**}", "!**/__pycache__{,/**}"],
    },
    { from: path.resolve(__dirname, "../../LICENSE"), to: "LICENSE-Octop.txt" },
  ],
  mac: {
    target: ["dmg"],
    category: "public.app-category.productivity",
    icon: "build/appicon-macos.png",
  },
  win: { target: ["nsis"], icon: "build/appicon.png" },
  nsis: {
    oneClick: false,
    perMachine: false,
    allowToChangeInstallationDirectory: true,
    deleteAppDataOnUninstall: false,
    shortcutName: "Colink",
  },
  linux: {
    target: ["deb"],
    category: "Office",
    icon: "build/appicon.png",
    maintainer: "Colink contributors",
  },
};

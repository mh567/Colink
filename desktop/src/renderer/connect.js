"use strict";
const $ = (id) => document.getElementById(id);
let copy;
let busy = false;
window.colinkSetup.state().then((state) => {
  copy = state.copy;
  document.documentElement.lang = navigator.language.startsWith("zh")
    ? "zh"
    : "en";
  $("title").textContent = copy.connectTitle;
  $("label").textContent = copy.serverLabel;
  $("hint").textContent = copy.addressHint;
  $("error").textContent = state.error;
  $("connect").textContent = copy.connect;
  $("local").textContent = copy.local;
  $("server").value = state.server;
  $("server").focus();
});
async function connect(server) {
  if (busy || !copy) return;
  busy = true;
  $("connect").disabled = true;
  $("local").disabled = true;
  $("error").textContent = copy.loading;
  try {
    await window.colinkSetup.connect(server);
  } catch {
    $("error").textContent = copy.failed;
  } finally {
    busy = false;
    $("connect").disabled = false;
    $("local").disabled = false;
  }
}
$("connection").addEventListener("submit", (event) => {
  event.preventDefault();
  void connect($("server").value);
});
$("local").addEventListener("click", () => {
  void connect(null);
});

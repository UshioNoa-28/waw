// jsdom integration test for the ValAim panel capture flow.
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

// Extract PAGE html from valaim/webgui.py
const py = fs.readFileSync(path.join("/home/anna/code/projects/val", "valaim/webgui.py"), "utf8");
const m = py.match(/PAGE = """([\s\S]*?)"""/);
if (!m) { console.error("FAIL: PAGE not found in webgui.py"); process.exit(1); }
const html = m[1];

const sent = [];          // recorded /set payloads
let state = {             // canned /state responses
  bt_host: "", bt_port: 47800, target_classes: "Head", keybind: "", fire_button: "",
  classes: "", trigger: "", sens: 0, aim_gain: 0, aim_lead: 0.1,
  head_bias: 0.55, head_offset_y: 0, fov_radius: 160, conf_threshold: 0.35,
  move_fraction: 0.2, max_step: 45, smoothing: 0.6, deadzone: 2, fire_radius: 12, fps: 60,
  always_on: true, triggerbot: false, debug: false,
  status: "空闲 - 点启动", running: false, raw_sink: false,
};

const dom = new JSDOM(html, {
  runScripts: "dangerously",
  pretendToBeVisual: true,
  beforeParse(window) {
    window.fetch = (url, opts) => {
      if (String(url).startsWith("/set")) {
        sent.push(JSON.parse(opts.body));
        return Promise.resolve({ text: () => Promise.resolve("ok"), json: () => Promise.resolve("ok") });
      }
      if (String(url).startsWith("/start") || String(url).startsWith("/stop") || String(url).startsWith("/save")) {
        return Promise.resolve({ text: () => Promise.resolve("ok") });
      }
      return Promise.resolve({ json: () => Promise.resolve({ ...state }) });
    };
    window.alert = () => {};
  },
});

const { window } = dom;
const { document } = window;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let failures = 0;
function check(name, cond) {
  console.log((cond ? "PASS " : "FAIL ") + name);
  if (!cond) failures++;
}
const last = (k) => [...sent].reverse().find((s) => k in s);

(async () => {
  await sleep(400); // let poll() run a few cycles

  // ---- T1: page alive, status rendered ----
  check("T1 status shows panel text (poll works)", document.getElementById("status").textContent.includes("空闲"));

  // ---- T2: fire-key capture starts -> banner visible, message present ----
  document.getElementById("capBtn").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(50);
  const capbar = document.getElementById("capbar");
  check("T2 capture banner visible", capbar.style.display === "block" && capbar.textContent.includes("鼠标键"));
  check("T2b button shows 采集中", document.getElementById("capBtn").textContent.includes("采集中"));

  // ---- T3: poll must NOT clobber the status while capturing ----
  const stBefore = document.getElementById("status").textContent;
  await sleep(500); // poll ran >=1 more time
  check("T3 status untouched during capture", document.getElementById("status").textContent === stBefore);

  // ---- T4: LEFT click during btn-capture -> refused, capture stays active ----
  let ev = new window.MouseEvent("mousedown", { bubbles: true, cancelable: true });
  Object.defineProperty(ev, "button", { value: 0 });
  window.dispatchEvent(ev);
  await sleep(50);
  check("T4 left refused w/ explanation", capbar.textContent.includes("左键不能"));
  check("T4b capture still active after refusal", document.getElementById("capBtn").textContent.includes("采集中"));

  // ---- T5: right click binds and sends fire_button=right ----
  ev = new window.MouseEvent("mousedown", { bubbles: true, cancelable: true });
  Object.defineProperty(ev, "button", { value: 2 });
  window.dispatchEvent(ev);
  await sleep(50);
  check("T5 fire_button sent", last("fire_button") && last("fire_button").fire_button === "right");
  check("T5b input value updated", document.getElementById("fire_button").value === "right");
  check("T5c banner hidden after success", capbar.style.display === "none");
  check("T5d button restored", document.getElementById("capBtn").textContent === "采集");

  // ---- T6: key capture F8 ----
  document.getElementById("capKey").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(30);
  const kd = new window.KeyboardEvent("keydown", { code: "F8", bubbles: true, cancelable: true });
  window.dispatchEvent(kd);
  await sleep(30);
  check("T6 trigger F8 sent", last("trigger") && last("trigger").trigger === "F8");

  // ---- T7: Esc cancels capture ----
  document.getElementById("capKey").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await sleep(30);
  window.dispatchEvent(new window.KeyboardEvent("keydown", { code: "Escape", bubbles: true, cancelable: true }));
  await sleep(30);
  check("T7 Esc cancels", capbar.style.display === "none" && document.getElementById("capKey").textContent === "采集");

  // ---- T8: after capture ended, poll resumes updating status ----
  state.status = "aim | move +5,+2";
  await sleep(500);
  check("T8 status updates resume", document.getElementById("status").textContent.includes("aim"));

  console.log(failures === 0 ? "\nALL PASS" : `\n${failures} FAILURES`);
  process.exit(failures === 0 ? 0 : 1);
})();

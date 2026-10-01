// jsdom integration test for the ValAim control panel (post-cleanup UI).
const { JSDOM } = require("jsdom");
const fs = require("fs");

const py = fs.readFileSync("/home/anna/code/projects/val/valaim/webgui.py", "utf8");
const m = py.match(/PAGE = """([\s\S]*?)"""/);
if (!m) { console.error("FAIL: PAGE not found in webgui.py"); process.exit(1); }
const html = m[1];

const sent = [];
let state = {
  bt_host: "192.168.1.115", bt_port: 47800, target_classes: "Head", classes: "", sens: 0,
  aim_gain: 0, aim_lead: 0, head_bias: 0.55, head_offset_y: 0,
  fov_radius: 300, conf_threshold: 0.3, move_fraction: 0.7, max_step: 500, smoothing: 0.55,
  deadzone: 4, aim_dz_frac: 0.25, fire_radius: 12, fps: 60, aim_comp: 4, aim_cw: 1, aim_floor: 2,
  burst: true, burst_cooldown: 0.28, burst_gain: 1.0, settle_ms: 90, settle_frac: 1.6,
  arrive_px: 8, resume_px: 32,
  triggerbot: false, debug: false,
  status: "空闲 - 确认手机 App 已连接后点启动。", running: false, raw_sink: true,
};

const dom = new JSDOM(html, {
  url: "http://127.0.0.1:8765/",
  runScripts: "dangerously",
  pretendToBeVisual: true,
  beforeParse(window) {
    window.fetch = (u, o) => {
      if (String(u).endsWith("/state")) {
        return Promise.resolve({ json: () => Promise.resolve({ ...state }), text: () => Promise.resolve("ok") });
      }
      if (String(u).endsWith("/set")) {
        const p = JSON.parse(o.body);
        sent.push(p);
        Object.assign(state, p);
        return Promise.resolve({ json: () => Promise.resolve(state), text: () => Promise.resolve("ok") });
      }
      return Promise.resolve({ json: () => Promise.resolve(state), text: () => Promise.resolve("saved") });
    };
    window.navigator.sendBeacon = () => true;
  },
});
const { window } = dom;
const { document } = window;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failures = 0;
const check = (name, cond) => {
  console.log((cond ? "PASS " : "FAIL ") + name);
  if (!cond) failures++;
};
const last = (k) => [...sent].reverse().find((s) => k in s);

(async () => {
  await sleep(400);

  check("T1 poll renders status", document.getElementById("status").textContent.includes("空闲"));

  // capture machinery fully removed
  check("T2 no capture UI remains",
    !document.getElementById("capKey") && !document.getElementById("capBtn")
    && !document.getElementById("trigger") && !document.getElementById("fire_button")
    && !document.getElementById("always_on") && !document.getElementById("capbar"));

  // new burst controls exist and get filled from /state
  for (const id of ["burst", "burst_cooldown", "burst_gain", "settle_ms", "settle_frac", "arrive_px", "resume_px"]) {
    check(`T3 control ${id} present`, !!document.getElementById(id));
  }
  check("T3b burst checkbox reflects state", document.getElementById("burst").checked === true);
  check("T3c cooldown value filled", document.getElementById("burst_cooldown").value === "0.28");
  check("T3d max_step slider allows 600", document.getElementById("max_step").max === "600");

  // slider input sends patch
  const sl = document.getElementById("settle_ms");
  sl.value = "120";
  sl.dispatchEvent(new window.Event("input", { bubbles: true }));
  await sleep(50);
  check("T4 settle_ms patch sent", last("settle_ms") && last("settle_ms").settle_ms === "120");

  // checkbox change sends bool
  const tb = document.getElementById("triggerbot");
  tb.checked = true;
  tb.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(50);
  check("T5 triggerbot bool patch sent", last("triggerbot") && last("triggerbot").triggerbot === true);

  // burst toggle
  const bu = document.getElementById("burst");
  bu.checked = false;
  bu.dispatchEvent(new window.Event("change", { bubbles: true }));
  await sleep(50);
  check("T6 burst bool patch sent", last("burst") && last("burst").burst === false);

  // text input not clobbered while focused
  const ip = document.getElementById("bt_host");
  ip.focus();
  ip.value = "10.0.0.9";
  state.bt_host = "192.168.1.115";
  await sleep(400);
  check("T7 focused text input not clobbered by poll", ip.value === "10.0.0.9");

  // status resumes after blur
  ip.blur();
  state.status = "运行中";
  await sleep(400);
  check("T8 status updates resume", document.getElementById("status").textContent.includes("运行中"));

  console.log(failures ? `\n${failures} FAILURE(S)` : "\nALL PASS");
  process.exit(failures ? 1 : 0);
})();

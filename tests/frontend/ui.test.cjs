const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { JSDOM } = require("jsdom");
const root = path.resolve(__dirname, "../..");
const html = fs.readFileSync(path.join(root, "web/index.html"), "utf8");
const app = fs.readFileSync(path.join(root, "web/app.js"), "utf8");
const fx = fs.readFileSync(path.join(root, "web/fx.js"), "utf8");
const fixture = JSON.parse(
  fs.readFileSync(path.join(root, "fixtures/demo/replay-run.json")),
);
function setup(t) {
  const dom = new JSDOM(html, {
    url: "http://sidequest.test/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const w = dom.window,
    errors = [],
    calls = [],
    geo = [];
  w.matchMedia = () => ({ matches: false, addEventListener() {} });
  w.scrollTo = () => {};
  w.HTMLElement.prototype.scrollIntoView = () => {};
  w.ResizeObserver = class {
    observe() {}
    disconnect() {}
  };
  Object.defineProperty(w.navigator, "geolocation", {
    value: { getCurrentPosition: (...args) => geo.push(args), clearWatch() {} },
  });
  w.fetch = async (url, options) => {
    calls.push({ url, options });
    return {
      ok: false,
      status: 503,
      json: async () => ({ detail: "No model in this test" }),
    };
  };
  w.addEventListener("error", (e) => errors.push(e.error));
  vm.runInContext(app, dom.getInternalVMContext());
  vm.runInContext(fx, dom.getInternalVMContext());
  t.after(() => {
    dom.window.close();
    assert.deepEqual(errors, []);
  });
  const $ = (id) => w.document.getElementById(id);
  const run = (code) => vm.runInContext(code, dom.getInternalVMContext());
  const event = (id, type) =>
    $(id).dispatchEvent(new w.Event(type, { bubbles: true, cancelable: true }));
  const start = () => {
    $("startLat").value = "44.9778";
    $("startLon").value = "-93.265";
    $("useStart").click();
  };
  return { w, $, run, event, start, calls, geo };
}
test("fresh landing is neutral and never requests location automatically", (t) => {
  const { $, geo } = setup(t);
  assert.equal(geo.length, 0);
  assert.equal($("go").disabled, true);
  assert.equal($("loc").className, "loc");
  assert.equal($("req").value, "");
});
test("manual starting point rejects missing and out-of-range coordinates, accepts zero", (t) => {
  const { $, run } = setup(t);
  $("useStart").click();
  assert.equal($("go").disabled, true);
  assert.equal($("startLat").getAttribute("aria-invalid"), "true");
  $("startLat").value = "91";
  $("startLon").value = "0";
  $("useStart").click();
  assert.equal($("go").disabled, true);
  $("startLat").value = "0";
  $("useStart").click();
  assert.equal($("go").disabled, false);
  assert.equal(run("S.pos.latitude"), 0);
  assert.equal($("startErr").classList.contains("hide"), true);
});
test("GPS denial offers manual recovery and preserves an existing starting point", (t) => {
  const { $, geo, start, run } = setup(t);
  start();
  $("locBtn").click();
  assert.equal(geo.length, 1);
  geo[0][1]({ code: 1 });
  assert.match($("loc").textContent, /previous starting point/);
  assert.equal($("go").disabled, false);
  assert.equal(run("S.pos.latitude"), 44.9778);
});
test("late GPS callback cannot replace a later manual selection", (t) => {
  const { $, geo, start, run } = setup(t);
  $("locBtn").click();
  start();
  geo[0][0]({ coords: { latitude: 1, longitude: 2, accuracy: 10 } });
  assert.equal(run("S.pos.latitude"), 44.9778);
});
test("examples fill the request and group switching preserves input", (t) => {
  const { $, w, event } = setup(t);
  w.document.querySelector(".eg").click();
  assert.match($("req").value, /Coffee/);
  assert.equal(Number($("reqCount").textContent), $("req").value.length);
  $("modeGroup").click();
  assert.equal($("modeGroup").getAttribute("aria-pressed"), "true");
  assert.match($("go").textContent, /Create group/);
  $("modeSolo").click();
  assert.match($("req").value, /Coffee/);
  $("nolimit").checked = true;
  event("nolimit", "change");
  assert.equal($("budget").disabled, true);
});
test("theme and data mode labels follow the actual choice", (t) => {
  const { $, w, event } = setup(t);
  $("themeBtn").click();
  assert.equal(w.document.documentElement.dataset.theme, "dark");
  assert.equal(w.localStorage.getItem("sq-theme"), "dark");
  assert.match($("themeBtn").getAttribute("aria-label"), /light/);
  $("themeBtn").click();
  assert.equal(w.document.documentElement.dataset.theme, "light");
  $("data").value = "demo";
  event("data", "change");
  assert.equal($("modeChip").textContent, "Demo data");
  $("data").value = "live";
  event("data", "change");
  assert.equal($("modeChip").textContent, "Live data");
  $("rain").value = "40";
  event("rain", "change");
  assert.match($("rainSummary").textContent, /40%/);
});
test("empty descriptions and negative budgets are caught before API submission", async (t) => {
  const { $, start, run, calls } = setup(t);
  start();
  await run("startPlan()");
  assert.equal(calls.length, 0);
  assert.equal($("req").getAttribute("aria-invalid"), "true");
  $("req").value = "Find coffee";
  $("budget").value = "-1";
  await run("startPlan()");
  assert.equal(calls.length, 0);
  assert.match($("msg").textContent, /zero or more/);
});
test("failed plan creation returns an actionable form with all chosen constraints intact", async (t) => {
  const { $, start, run, calls } = setup(t);
  start();
  $("req").value = "Find coffee";
  $("budget").value = "20";
  await run("startPlan()");
  assert.equal(calls.length, 1);
  const payload = JSON.parse(calls[0].options.body);
  assert.equal(payload.lat, 44.9778);
  assert.equal(payload.budget_dollars, 20);
  assert.equal($("compose").classList.contains("hide"), false);
  assert.match($("msg").textContent, /not connected/);
  assert.equal($("go").disabled, false);
  assert.equal($("req").value, "Find coffee");
});
test("recorded result renders its real stops, unknowns, and disabled edit state", (t) => {
  const { $, run } = setup(t);
  run(`showResult(${JSON.stringify(fixture.result)}, { replay: true })`);
  assert.equal(
    $("timeline").querySelectorAll(".stop").length,
    fixture.result.proposal.blocks.length,
  );
  assert.match($("timeline").textContent, /Mill Ruins Park/);
  assert.equal($("lateBtn").disabled, true);
  assert.match($("pBadges").textContent, /provisional/i);
  assert.ok($("issues").textContent.length);
});
test("group members and time windows remain accessible and text-safe", (t) => {
  const { $, run } = setup(t);
  run(
    `renderMembers({ me: { role: 'member' }, members: [{ name: '<script> Test', role: 'member', submitted: false, windows: [], interests: ['coffee'] }] })`,
  );
  assert.equal($("gMembers").querySelectorAll("script").length, 0);
  assert.match($("gMembers").textContent, /Waiting for availability/);
  run(`$('gWindows').append(winRow('', ''))`);
  assert.equal(
    $("gWindows").querySelector('[data-k="start"]').getAttribute("aria-label"),
    "Available from",
  );
});
test("mobile section tabs support arrow keys and roving focus", (t) => {
  const { $, w } = setup(t);
  $("tabPlan").dispatchEvent(
    new w.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }),
  );
  assert.equal(w.document.body.dataset.tab, "map");
  assert.equal($("tabMap").getAttribute("aria-selected"), "true");
  assert.equal($("tabPlan").tabIndex, -1);
  assert.equal(w.document.activeElement, $("tabMap"));
});
test("replan diff offers a review without losing the saved itinerary", (t) => {
  const { $, run } = setup(t);
  run(`showResult(${JSON.stringify(fixture.result)}, { replay: true })`);
  run(
    `renderDiff({ awaiting_decision: true, diff: { summary: 'Rain changes one stop', added: [{id:'new',name:'Gallery',when:'15:00'}], removed: [], moved: [], unchanged: ['b1'] } })`,
  );
  assert.match($("diffCard").textContent, /Accept change/);
  assert.match($("diffCard").textContent, /Keep my current plan/);
  assert.match($("timeline").textContent, /Mill Ruins Park/);
});

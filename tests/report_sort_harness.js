// Executes the plugin's real sorting script against a minimal document model
// built from the real rendered report, and reports the resulting row order.
const fs = require("fs");

const HTML = fs.readFileSync(process.argv[2], "utf8");

// Pull the header labels and the tbody rows out of the rendered page.
const headerLabels = [...HTML.matchAll(/<th class="sortable"[^>]*>([^<]*)<\/th>/g)]
  .map((m) => m[1]);
const tbody = HTML.match(/<tbody>([\s\S]*?)<\/tbody>/)[1];
const rowData = [...tbody.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map((m) =>
  [...m[1].matchAll(/<td data-v="([^"]*)">([^<]*)<\/td>/g)].map((c) => ({
    dataV: c[1],
    text: c[2],
  }))
);

if (!headerLabels.length || !rowData.length) {
  console.log("HARNESS-BROKEN: parsed 0 headers or 0 rows");
  process.exit(2);
}

function makeCell(cell) {
  return {
    getAttribute: (name) => (name === "data-v" ? cell.dataV : null),
    textContent: cell.text,
  };
}

const rows = rowData.map((cells) => ({ children: cells.map(makeCell) }));

const body = {
  rows: rows.slice(),
  appendChild(row) {
    const at = this.rows.indexOf(row);
    if (at !== -1) this.rows.splice(at, 1);
    this.rows.push(row);
  },
};

const headers = headerLabels.map((label) => {
  const attrs = { "aria-sort": "none" };
  const listeners = {};
  return {
    label,
    getAttribute: (n) => (n in attrs ? attrs[n] : null),
    setAttribute: (n, v) => {
      attrs[n] = v;
    },
    addEventListener: (type, fn) => {
      (listeners[type] = listeners[type] || []).push(fn);
    },
    fire: (type, event) => (listeners[type] || []).forEach((fn) => fn(event)),
  };
});

const table = {
  tBodies: [body],
  querySelectorAll: (sel) => (sel === "th.sortable" ? headers : []),
};

global.document = { querySelector: (sel) => (sel === "table" ? table : null) };

// eval is deliberate and is the entire purpose of this harness: it executes the
// plugin's own sorting script, read from the plugin source tree, so the code
// being verified is the code that ships. There is no untrusted input here; the
// argument is a path this repository controls. This file is a scratch test
// harness and is not shipped.
eval(fs.readFileSync(process.argv[3], "utf8"));

const order = () => body.rows.map((r) => r.children.map((c) => c.textContent).join("|"));
const firstCol = () => body.rows.map((r) => r.children[0].textContent);
const secondCol = () => body.rows.map((r) => r.children[1].textContent);

console.log("headers parsed :", headerLabels.join(", "));
console.log("initial order  :", order().join("   "));

headers[0].fire("click");
console.log("after 1 click on '" + headerLabels[0] + "' :", firstCol().join(", "),
  "| aria-sort =", headers[0].getAttribute("aria-sort"));
const ascendingNumeric = firstCol();

headers[0].fire("click");
console.log("after 2 clicks :", firstCol().join(", "),
  "| aria-sort =", headers[0].getAttribute("aria-sort"));
const descendingNumeric = firstCol();

headers[1].fire("click");
console.log("after click on '" + headerLabels[1] + "' :", secondCol().join(", "),
  "| aria-sort =", headers[1].getAttribute("aria-sort"),
  "| previous header reset to", headers[0].getAttribute("aria-sort"));
const ascendingText = secondCol();

// Keyboard activation must work too.
let prevented = false;
headers[1].fire("keydown", { key: "Enter", preventDefault: () => (prevented = true) });
console.log("after Enter on '" + headerLabels[1] + "' :", secondCol().join(", "),
  "| preventDefault called =", prevented);

const checks = [
  ["numbers sort as numbers, not as text",
    JSON.stringify(ascendingNumeric) === JSON.stringify(["2", "3", "10"])],
  ["a second click reverses the order",
    JSON.stringify(descendingNumeric) === JSON.stringify(["10", "3", "2"])],
  ["text sorts ignoring letter case",
    JSON.stringify(ascendingText) === JSON.stringify(["alpha", "Bravo", "Charlie"])],
  ["sorting a new column resets the previous one",
    headers[0].getAttribute("aria-sort") === "none"],
  ["the keyboard path sorts and suppresses the default action", prevented === true],
];

console.log("");
let allPassed = true;
for (const [name, passed] of checks) {
  console.log((passed ? "PASS  " : "FAIL  ") + name);
  if (!passed) allPassed = false;
}
process.exit(allPassed ? 0 : 1);

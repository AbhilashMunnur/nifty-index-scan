const PAGES = {
  "nifty-oi": { title: "Nifty OI Scan", key: "nifty_oi", kind: "oi" },
  "sensex-oi": { title: "Sensex OI Scan", key: "sensex_oi", kind: "oi" },
  "nifty-fut": { title: "Nifty future Scan", key: "nifty_fut", kind: "fut" },
  "sensex-fut": { title: "Sensex future Scan", key: "sensex_fut", kind: "fut" },
  "master": { title: "Synthetic futures", kind: "master", key: "master_strategy" },
  "option-buy": { title: "Option-buy BASE", kind: "option-buy", key: "option_buy" },
};

const MASTER_LEDE = {
  master_strategy:
    "Paper from this date. Master PCR base + filler on ATM synthetic CE−PE. Entry 14:05, exit next session 15:00. Filler may book 50% at TP1. Live orders stay off.",
  option_buy:
    "Paper from this date. Master BASE PCR only — buy ATM CE on Buy, ATM PE on Sell. Fixed ₹1 Cr premium sizing with size-slip and STT/charges. Time exit only. Live orders stay off.",
};

const app = document.querySelector("#app");

function flowClass(label) {
  const safe = String(label || "FLAT").toUpperCase().replace(/[^A-Z0-9]+/g, "-");
  return "flow-" + safe;
}

function px(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return value == null ? "" : String(value);
  return number.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function inr(amount) {
  if (amount === null || amount === undefined || Number.isNaN(Number(amount))) return "n/a";
  const value = Number(amount);
  const sign = value < 0 ? "-" : "";
  return sign + "₹" + Math.abs(value).toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function moneyClass(amount) {
  if (amount > 0) return "money up";
  if (amount < 0) return "money down";
  return "money";
}

function dayLabel(iso) {
  const date = new Date(iso + "T12:00:00");
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("en-IN", {
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function pct(rank) {
  if (rank === null || rank === undefined || Number.isNaN(Number(rank))) return "";
  return (Number(rank) * 100).toFixed(1) + "%";
}

function legLine(name, leg) {
  const lots = leg.lots || "n/a";
  const prem = leg.prem == null ? "" : " prem " + leg.prem;
  const delta = leg.dprem == null ? "" : " (" + leg.dprem + ")";
  return `<div class="leg"><span>${name}</span><span class="${flowClass(leg.label)}">${leg.label}</span><span>${lots}${prem}${delta}</span></div>`;
}

function oiCard(card, last) {
  const badge = last ? `<span class="badge">Last scan</span>` : "";
  const note = card.baseline
    ? `<p class="meta">First saved reading. Buy and write counts start from the next scan.</p>`
    : `<p class="meta">Compared to ${card.compared_to || "the previous scan"} · saved ${card.saved_at}</p>`;
  const strikes = (card.strikes || []).map((row) => `
    <div class="strike">
      <div><b>${row.strike}</b> ${row.label}</div>
      ${legLine("CE", row.call)}
      ${legLine("PE", row.put)}
    </div>`).join("");
  const since = card.since_1430 ? `
    <p class="totals">${card.since_1430.totals}</p>
    ${(card.since_1430.strikes || []).map((row) => `
      <div class="strike">
        <div><b>${row.strike}</b> ${row.label}</div>
        ${legLine("CE", row.call)}
        ${legLine("PE", row.put)}
      </div>`).join("")}` : "";
  return `
    <article class="card">
      <header><div class="clock">${card.clock}</div>${badge}</header>
      <p class="meta">Expiry ${card.expiry} · ATM ${card.atm} · lot ${card.lot_size}</p>
      <p class="totals">${card.totals}</p>
      ${note}
      ${strikes}
      ${since}
    </article>`;
}

function dayCard(card) {
  const lines = card.lines || [];
  const title = lines[0] || card.name;
  const contract = lines[1] || "";
  const windowLine = lines[2] || "";
  const lots = lines[3] || "";
  const price = lines[4] || "";
  const note = lines[5] || "";
  return `
    <article class="card">
      <header><div class="day-title">${title}</div></header>
      <p class="meta">${contract}</p>
      <p class="totals ${flowClass(card.label)}">${windowLine}</p>
      <p>${lots}</p>
      <p>${price}</p>
      <p class="meta">${note}</p>
    </article>`;
}

function futCard(card, last) {
  const badge = last ? `<span class="badge">Last scan</span>` : "";
  const since = card.since
    ? `<p class="totals ${flowClass(card.label)}">Since ${card.since} · ${card.label}${card.lots ? " · " + card.lots : ""}</p>`
    : `<p class="totals">First saved reading. The next scan names the side.</p>`;
  return `
    <article class="card">
      <header><div class="clock">${card.clock}</div>${badge}</header>
      <p class="meta">${card.contract} · expiry ${card.expiry}</p>
      ${since}
      <p>${card.price}</p>
      <p>${card.oi}</p>
    </article>`;
}

function listPage(title, cards, render, preface) {
  let day = "";
  const body = cards.length
    ? cards.map((card, index) => {
        const heading = card.day !== day
          ? `<h2 class="day">${dayLabel(card.day)}</h2>`
          : "";
        day = card.day;
        return heading + render(card, index === 0);
      }).join("")
    : `<p class="status">No scans saved yet.</p>`;
  return `
    <a class="back" href="#">&larr; Landing</a>
    <h1>${title}</h1>
    ${preface || ""}
    ${body}`;
}

function paperLabel(book) {
  if (!book || !book.saved) return "Paper book";
  if (!book.flat && book.open) {
    const side = book.open.side || book.open.signal_side || book.open.opt_side || "open";
    return "Open " + side;
  }
  return inr(book.realised);
}

function masterRow(row) {
  const exit = row.event === "Exit";
  const shownDate = exit && row.exit_date ? row.exit_date : row.date;
  const pending = !exit && row.exit_clock
    ? `<p class="meta">Open. Closes from ${row.exit_clock} next session${row.remaining_frac != null ? ` · rem ${(Number(row.remaining_frac) * 100).toFixed(0)}%` : ""}.</p>`
    : "";
  const reason = exit && row.exit_why ? `<p class="meta">${row.exit_why}</p>` : "";
  const result = exit
    ? `<p class="${moneyClass(row.net)}">${inr(row.net)}</p>
       <p class="meta">${row.points} pts · capital ${inr(row.capital_after)}</p>`
    : `<p class="meta">${row.lots} lots · capital before ${inr(row.capital_before)}</p>`;
  return `
    <article class="card ${exit ? "loka-exit" : ""}">
      <header><div class="day-title">${exit ? "exit" : "entry"} ${row.time || ""} -- ${shownDate}</div></header>
      <p>${row.tier || ""} ${row.side || ""} · ${row.script || ""} · strike ${row.strike || ""} · expiry ${row.expiry || ""}</p>
      <p>CE ${row.call_symbol || ""} @ ${px(row.call_price)} · PE ${row.put_symbol || ""} @ ${px(row.put_price)}</p>
      <p class="meta">PCR ${row.pcr == null ? "n/a" : px(row.pcr)} · rank ${pct(row.pcr_rank)}</p>
      ${reason}
      ${pending}
      ${result}
    </article>`;
}

function optionBuyRow(row) {
  const exit = row.event === "Exit";
  const shownDate = exit && row.exit_date ? row.exit_date : row.date;
  const pending = !exit && row.exit_clock
    ? `<p class="meta">Open long ${row.opt_side || ""}. Closes from ${row.exit_clock} next session.</p>`
    : "";
  const reason = exit && row.exit_why ? `<p class="meta">${row.exit_why}</p>` : "";
  const result = exit
    ? `<p class="${moneyClass(row.net)}">${inr(row.net)}</p>
       <p class="meta">${row.points} pts · charges ${inr(row.charges)} · capital ${inr(row.capital_after)}</p>`
    : `<p class="meta">${row.lots} lots · fill ${px(row.price)} · slip ${px(row.slip)} · capital before ${inr(row.capital_before)}</p>`;
  return `
    <article class="card ${exit ? "loka-exit" : ""}">
      <header><div class="day-title">${exit ? "exit" : "entry"} ${row.time || ""} -- ${shownDate}</div></header>
      <p>BASE Buy ${row.opt_side || ""} · signal ${row.signal_side || ""} · ${row.script || ""} · strike ${row.strike || ""}</p>
      <p>${row.symbol || ""} @ ${px(row.price)}</p>
      <p class="meta">PCR ${row.pcr == null ? "n/a" : px(row.pcr)} · rank ${pct(row.pcr_rank)}</p>
      ${reason}
      ${pending}
      ${result}
    </article>`;
}

function decisionCard(decision) {
  if (!decision || !decision.day) return "";
  const scripts = (decision.scripts || []).map((row) => {
    const expiry = row.expiry ? ` · rank uses expiry ${row.expiry}` : "";
    return `<p class="meta">${row.reason || ""} PCR ${px(row.pcr)}${expiry}.</p>`;
  }).join("");
  const synthetic = (decision.synthetic || {}).summary || "";
  const optionBuy = (decision.option_buy || {}).summary || "";
  const later = (decision.scripts_1500 || []).map((row) => {
    const expiry = row.expiry ? ` · rank uses expiry ${row.expiry}` : "";
    return `<p class="meta">3 PM. ${row.reason || ""} PCR ${px(row.pcr)}${expiry}.</p>`;
  }).join("");
  return `
    <article class="card">
      <header><div class="day-title">Paper check ${dayLabel(decision.day)}</div></header>
      <p class="meta">Checked ${decision.checked_at || ""}</p>
      <p><b>Synthetic.</b> ${synthetic}</p>
      <p><b>Option-buy.</b> ${optionBuy}</p>
      ${scripts}
      ${later}
    </article>`;
}

function paperPage(desk, key, title, rowFn) {
  const book = desk[key] || {};
  if (!book.saved) {
    return `
      <a class="back" href="#">&larr; Landing</a>
      <h1>${title}</h1>
      <p class="status">The paper book is not on this page yet.</p>`;
  }
  const lede = (MASTER_LEDE[key] || "").replace("this date", book.started_on || "today");
  const rows = (book.rows || []).map(rowFn).join("")
    || `<p class="status">No trade yet. Signal after 14:00, entry from 14:05.</p>`;
  return `
    <a class="back" href="#">&larr; Landing</a>
    <h1>${title}</h1>
    <p class="lede">${lede}</p>
    ${decisionCard(desk.paper_decision)}
    <p class="${moneyClass(book.realised)}">${inr(book.realised)}</p>
    <p class="meta">Profit and loss · capital ${inr(book.capital)} from ${inr(book.starting_capital)} · saved ${book.updated_at || ""}</p>
    ${rows}`;
}

function latestClock(cards) {
  if (!cards || !cards.length) return "No scan yet";
  return "Last scan " + cards[0].clock;
}

function sessionBlock(days) {
  if (!days || !days.length) return "";
  return `<section class="session"><h2 class="day">Futures day</h2>${days.map(dayCard).join("")}</section>`;
}

function landing(desk) {
  const tiles = [
    ["nifty-oi", "Nifty OI Scan", latestClock(desk.nifty_oi), ""],
    ["sensex-oi", "Sensex OI Scan", latestClock(desk.sensex_oi), ""],
    ["nifty-fut", "Nifty future Scan", latestClock(desk.nifty_fut), ""],
    ["sensex-fut", "Sensex future Scan", latestClock(desk.sensex_fut), ""],
    ["master", "Synthetic futures", paperLabel(desk.master_strategy), "wide"],
    ["option-buy", "Option-buy BASE", paperLabel(desk.option_buy), "wide"],
  ];
  return `
    <h1>Scan desk</h1>
    <p class="lede">Paper strategies: Master synthetic + option-buy only. Newest reading at the top of each scan page. Updated ${desk.generated_at}.</p>
    ${decisionCard(desk.paper_decision)}
    ${sessionBlock(desk.fut_days)}
    <nav class="tiles">
      ${tiles.map(([id, title, sub, extra]) => `
        <a class="tile ${extra}" href="#${id}"><strong>${title}</strong><span>${sub}</span></a>
      `).join("")}
    </nav>
    <p class="foot">Public page. Paper scans only. Live orders stay off.</p>`;
}

function render(desk) {
  const page = PAGES[location.hash.slice(1)];
  if (!page) {
    app.innerHTML = landing(desk);
    return;
  }
  if (page.kind === "master") app.innerHTML = paperPage(desk, page.key, page.title, masterRow);
  else if (page.kind === "option-buy") app.innerHTML = paperPage(desk, page.key, page.title, optionBuyRow);
  else if (page.kind === "oi") app.innerHTML = listPage(page.title, desk[page.key] || [], oiCard);
  else {
    const days = (desk.fut_days || []).filter((card) => card.name === page.title.split(" ")[0].toUpperCase());
    const preface = days.length ? `<h2 class="day">Whole session</h2>${days.map(dayCard).join("")}` : "";
    app.innerHTML = listPage(page.title, desk[page.key] || [], futCard, preface);
  }
  window.scrollTo(0, 0);
}

fetch("data/desk.json?v=" + Date.now(), { cache: "no-store" })
  .then((response) => {
    if (!response.ok) throw new Error("missing desk");
    return response.json();
  })
  .then((desk) => {
    render(desk);
    window.addEventListener("hashchange", () => render(desk));
  })
  .catch(() => {
    app.innerHTML = `<h1>Scan desk</h1><p class="status">The scan file is not on this page yet.</p>`;
  });

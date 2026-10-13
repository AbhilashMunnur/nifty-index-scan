const PAGES = {
  "nifty-oi": { title: "Nifty OI Scan", key: "nifty_oi", kind: "oi" },
  "sensex-oi": { title: "Sensex OI Scan", key: "sensex_oi", kind: "oi" },
  "nifty-fut": { title: "Nifty future Scan", key: "nifty_fut", kind: "fut" },
  "sensex-fut": { title: "Sensex future Scan", key: "sensex_fut", kind: "fut" },
  "positions": { title: "Model positions", kind: "positions" },
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

function listPage(title, cards, render) {
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
    ${body}`;
}

function tradeBlock(trade) {
  return `
    <div class="trade">
      <div>${trade.direction} ${trade.lots} lot(s) ${trade.tradingsymbol}</div>
      <div>${px(trade.entry_price)} → ${px(trade.exit_price)}</div>
      <div class="${moneyClass(trade.pnl)}">${inr(trade.pnl)}</div>
      <p class="meta">${trade.exit_reason || ""} · ${trade.entry_time || ""} → ${trade.exit_time || ""}</p>
    </div>`;
}

function bookBlock(name, book) {
  if (!book || !book.saved) {
    return `<section class="book"><h2>${name}</h2><p>No saved book.</p></section>`;
  }
  const open = book.flat
    ? `<p>Flat. No open paper trade.</p>`
    : `<p>Open ${book.open.direction} ${book.open.lots} lot(s) ${book.open.tradingsymbol} @ ${book.open.entry_price}</p>
       <p>Stop ${book.open.stop_loss_price} · target ${book.open.first_target_price}</p>
       <p class="meta">Entered ${book.open.entry_time || ""}</p>`;
  const closed = (book.closed || []).map(tradeBlock).join("") || `<p class="meta">No closed trades.</p>`;
  return `
    <section class="book">
      <h2>${name}</h2>
      ${open}
      <p class="${moneyClass(book.realised)}">${inr(book.realised)}</p>
      <p class="meta">Realised · book saved ${book.updated_at || ""}</p>
      <h3>Closed</h3>
      ${closed}
    </section>`;
}

function positionsPage(desk) {
  return `
    <a class="back" href="#">&larr; Landing</a>
    <h1>Model positions</h1>
    <p class="lede">Paper book. Live orders stay off.</p>
    ${bookBlock("Nifty", desk.positions.nifty)}
    ${bookBlock("Sensex", desk.positions.sensex)}`;
}

function positionLabel(desk) {
  const books = [desk.positions && desk.positions.nifty, desk.positions && desk.positions.sensex];
  if (books.some((book) => book && book.saved && !book.flat)) return "Open";
  return "Flat";
}

function latestClock(cards) {
  if (!cards || !cards.length) return "No scan yet";
  return "Last scan " + cards[0].clock;
}

function landing(desk) {
  const tiles = [
    ["nifty-oi", "Nifty OI Scan", latestClock(desk.nifty_oi), ""],
    ["sensex-oi", "Sensex OI Scan", latestClock(desk.sensex_oi), ""],
    ["nifty-fut", "Nifty future Scan", latestClock(desk.nifty_fut), ""],
    ["sensex-fut", "Sensex future Scan", latestClock(desk.sensex_fut), ""],
    ["positions", "Model positions", positionLabel(desk), "wide"],
  ];
  return `
    <h1>Scan desk</h1>
    <p class="lede">Newest reading at the top of each page. A scan drops off after two days. Updated ${desk.generated_at}.</p>
    <nav class="tiles">
      ${tiles.map(([id, title, sub, extra]) => `
        <a class="tile ${extra}" href="#${id}"><strong>${title}</strong><span>${sub}</span></a>
      `).join("")}
    </nav>
    <p class="foot">Public page. Paper scans only.</p>`;
}

function render(desk) {
  const page = PAGES[location.hash.slice(1)];
  if (!page) {
    app.innerHTML = landing(desk);
    return;
  }
  if (page.kind === "positions") app.innerHTML = positionsPage(desk);
  else if (page.kind === "oi") app.innerHTML = listPage(page.title, desk[page.key] || [], oiCard);
  else app.innerHTML = listPage(page.title, desk[page.key] || [], futCard);
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

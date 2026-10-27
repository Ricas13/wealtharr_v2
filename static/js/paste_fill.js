/* Fill a fill form from pasted broker-confirmation text. Client-side only:
 * nothing is sent anywhere, nothing is submitted, and every value stays
 * editable. The server still validates and the owner still ticks "this
 * already happened". Unclear text fills nothing rather than guessing. */
(function () {
  "use strict";

  var MONTHS = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8,
                 sep: 9, oct: 10, nov: 11, dec: 12 };

  var SYMBOLS = { "£": "GBP", "$": "USD", "€": "EUR" };

  /* "1,234.56" -> 1234.56; with decimalComma (euro accounts) "1.234,56" -> 1234.56.
   * Anything that could be read two ways ("1.234" on a euro account) is skipped. */
  function number(text, decimalComma) {
    var raw = String(text).replace(/\s/g, "").replace(/[.,]+$/, "");
    if (decimalComma) {
      if (/^\d{1,3}(\.\d{3})*,\d+$/.test(raw)) raw = raw.replace(/\./g, "").replace(",", ".");
      else if (/^\d+,\d+$/.test(raw)) raw = raw.replace(",", ".");
      else if (/^\d{1,3}(\.\d{3})+$/.test(raw)) return null;
    } else if (/^\d{1,3}(,\d{3})+(\.\d+)?$/.test(raw)) {
      raw = raw.replace(/,/g, "");
    }
    if (!/^\d+(\.\d+)?$/.test(raw)) return null;
    var value = parseFloat(raw);
    return isFinite(value) ? value : null;
  }
  function currenciesIn(text) {
    var found = {};
    (text.match(/[£$€]/g) || []).forEach(function (s) { found[SYMBOLS[s]] = true; });
    (text.match(/\b(GBP|USD|EUR)\b/g) || []).forEach(function (c) { found[c] = true; });
    return Object.keys(found);
  }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function iso(y, m, d) {
    if (m < 1 || m > 12 || d < 1 || d > 31) return null;
    var check = new Date(Date.UTC(y, m - 1, d));
    if (check.getUTCMonth() !== m - 1) return null;
    return y + "-" + pad(m) + "-" + pad(d);
  }

  function findDate(text) {
    var m = text.match(/\b(\d{4})-(\d{2})-(\d{2})\b/);
    if (m) return { value: iso(+m[1], +m[2], +m[3]), note: "" };
    m = text.match(/\b(\d{1,2})[\/.](\d{1,2})[\/.](\d{4})\b/);
    if (m) return { value: iso(+m[3], +m[2], +m[1]), note: "Read the date as day/month/year." };
    m = text.match(/\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3})[a-z]*\.?,?\s+(\d{4})\b/);
    if (m && MONTHS[m[2].toLowerCase()]) return { value: iso(+m[3], MONTHS[m[2].toLowerCase()], +m[1]), note: "" };
    m = text.match(/\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b/);
    if (m && MONTHS[m[1].toLowerCase()]) return { value: iso(+m[3], MONTHS[m[1].toLowerCase()], +m[2]), note: "" };
    return null;
  }

  function parse(text, today, currency) {
    currency = currency || "GBP";
    var comma = currency === "EUR";
    var out = { date: null, quantity: null, price: null, fee: null, notes: [] };
    var t = String(text || "").replace(/ /g, " ");

    var d = findDate(t);
    if (d && d.value) {
      if (today && d.value > today) out.notes.push("Ignored a date in the future.");
      else { out.date = d.value; if (d.note) out.notes.push(d.note); }
    }

    var q = t.match(/(?:quantity|shares?|units?|filled|executed|st[uü]ck|anzahl)\s*[:\-]?\s*([\d.,]+)/i) ||
            t.match(/\b([\d.,]*\d)\s+(?:shares?|units?)\b/i);
    if (q) { var qn = number(q[1], comma); if (qn && qn > 0) out.quantity = qn; }

    var p = t.match(/(?:average\s+(?:fill\s+)?price|execution\s+price|fill\s+price|filled\s+at|price(?:\s+per\s+share)?|ausf[uü]hrungskurs|kurs|preis|@)\s*[:\-]?\s*[£$€]?\s*([\d.,]+)\s*(p\b|pence\b|gbx\b)?/i);
    if (p) {
      var pn = number(p[1], comma);
      if (pn && pn > 0) {
        if (p[2] && currency === "GBP") { pn = pn / 100; out.notes.push("Price was in pence; converted to pounds."); }
        else if (p[2]) { pn = null; out.notes.push("The price looks like it is in pence, which doesn't fit a " + currency + " account: not filled."); }
        if (pn) out.price = Math.round(pn * 1e8) / 1e8;
      }
    }

    var total = t.match(/(?:total\s+(?:fees?|charges?|costs?)|gesamtkosten|gesamtgeb[uü]hr(?:en)?)\s*[:\-]?\s*[£$€]?\s*([\d.,]+)/i);
    if (total) {
      var tn = number(total[1], comma);
      if (tn !== null) out.fee = tn;
    } else {
      var re = /(?:fees?|commission|stamp\s+duty|ptm\s+levy|charges?|geb[uü]hr(?:en)?|entgelt|provision)\s*[:\-]?\s*[£$€]?\s*([\d.,]+)/gi, m, sum = 0, n = 0;
      while ((m = re.exec(t))) { var fn = number(m[1], comma); if (fn !== null) { sum += fn; n += 1; } }
      if (n) {
        out.fee = Math.round(sum * 100) / 100;
        if (n > 1) out.notes.push("Added " + n + " fee lines together.");
      }
    }
    var seen = currenciesIn(t);
    var foreign = seen.filter(function (c) { return c !== currency; });
    if (foreign.length && seen.indexOf(currency) === -1) {
      out.price = null;
      out.fee = null;
      out.notes.push("This looks like a " + foreign.join("/") + " confirmation but the account is in " +
                     currency + ": price and fees were not filled.");
    } else if (foreign.length) {
      out.notes.push("The text mentions " + seen.join(" and ") + ": check the price is in " + currency + ".");
    }
    return out;
  }

  window.WealtharrPaste = { parse: parse };

  function setValue(field, value) {
    if (!field || value === null || value === undefined) return false;
    field.value = String(value);
    field.classList.add("w2-pasted");
    field.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  }

  document.querySelectorAll("[data-paste-form]").forEach(function (box) {
    var form = box.closest("form") || box.parentElement.querySelector("form");
    var source = box.querySelector("[data-paste-source]");
    var button = box.querySelector("[data-paste-apply]");
    var status = box.querySelector("[data-paste-status]");
    if (!form || !source || !button) return;
    button.addEventListener("click", function () {
      var today = new Date().toISOString().slice(0, 10);
      var got = parse(source.value, today, box.getAttribute("data-currency") || "GBP");
      var filled = [];
      if (setValue(form.elements.executed_on, got.date)) filled.push("date");
      if (setValue(form.elements.quantity, got.quantity)) filled.push("shares");
      if (setValue(form.elements.unit_price, got.price)) filled.push("price");
      if (setValue(form.elements.fee, got.fee)) filled.push("fees");
      var missing = ["date", "shares", "price", "fees"].filter(function (name) {
        return filled.indexOf(name) === -1;
      });
      var message = filled.length
        ? "Filled " + filled.join(", ") + ". " + (missing.length ? "Not found: " + missing.join(", ") + ". " : "")
        : "Nothing recognisable found. Type the values from your broker instead. ";
      status.textContent = message + got.notes.join(" ") +
        " Check every value against your broker before recording.";
    });
  });

  /* "Use proposed" / "Today" buttons only copy a value when pressed. */
  document.querySelectorAll("[data-fill-from]").forEach(function (button) {
    button.addEventListener("click", function () {
      var form = button.closest("form");
      var field = form && form.elements[button.getAttribute("data-fill-target")];
      var value = button.getAttribute("data-fill-from");
      if (value === "today") value = new Date().toISOString().slice(0, 10);
      if (field) { field.value = value; field.dispatchEvent(new Event("input", { bubbles: true })); field.focus(); }
    });
  });

  /* A link from an alert lands on its action and focuses the first empty field. */
  function focusLinkedAction() {
    if (location.hash.indexOf("#w2-fill-") !== 0) return;
    var card = document.querySelector(location.hash);
    var first = card && card.querySelector("input[name=executed_on]");
    if (first) first.focus();
  }
  if (document.readyState === "complete") focusLinkedAction();
  else window.addEventListener("load", function () { setTimeout(focusLinkedAction, 0); });
}());

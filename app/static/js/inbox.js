/* Trang Tin nhắn (Inbox). Dữ liệu lấy từ /api/inbox/*; cập nhật realtime qua Socket.IO (sự kiện "inbox_message"),
 * mất kết nối thì tự chuyển sang hỏi định kỳ. Nội dung tin nhắn chỉ được phép in đậm (**...**), mã (`...`) và bảng
 * markdown — xem renderRichText() trong app/widget/embed.js (cùng cách làm): luôn dùng createTextNode/textContent
 * cho nội dung thật, chỉ tự thêm thẻ <strong>/<code> nên không có XSS từ khách/bot. */
(function () {
  var cfg = window.INBOX;
  var $ = function (id) { return document.getElementById(id); };
  var root = $('ib');
  var itemsBox = $('ib-items');
  var msgsBox = $('ib-msgs');
  var panel = $('ib-panel');
  var errorBox = $('ib-error');

  var state = { list: [], selectedId: null, lastMsgId: 0, search: '', lastDay: '' };
  var POLL_MS = 10000;
  var SEARCH_DEBOUNCE_MS = 300;
  var REFRESH_DEBOUNCE_MS = 250;

  var WEEKDAYS = ['Chủ nhật', 'Thứ 2', 'Thứ 3', 'Thứ 4', 'Thứ 5', 'Thứ 6', 'Thứ 7'];
  var SENDER_PREFIX = { staff: 'Bạn: ', bot: 'Bot: ', customer: '' };

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  // Giống hệt renderRichText() trong app/widget/embed.js (2 tệp chạy độc lập, không dùng chung bundler
  // nên phải nhân bản) — chỉ **đậm**, `mã` và bảng markdown, luôn qua createTextNode/textContent nên an toàn XSS.
  var RICH_TEXT_RE = /\*\*([^\n]+?)\*\*|`([^\n]+?)`/g;

  function renderInline(container, text) {
    var last = 0, m;
    RICH_TEXT_RE.lastIndex = 0;
    while ((m = RICH_TEXT_RE.exec(text))) {
      if (m.index > last) container.appendChild(document.createTextNode(text.slice(last, m.index)));
      container.appendChild(el(m[1] != null ? 'strong' : 'code', null, m[1] != null ? m[1] : m[2]));
      last = RICH_TEXT_RE.lastIndex;
    }
    if (last < text.length) container.appendChild(document.createTextNode(text.slice(last)));
  }

  // Bảng GFM: chỉ nhận khi có đủ hàng tiêu đề + hàng phân cách (---) cùng số cột, còn lại là văn bản thường.
  function splitTableRow(line) {
    var s = line.trim(), cells = [], cur = '';
    if (s.charAt(0) === '|') s = s.slice(1);
    for (var i = 0; i < s.length; i++) {
      var ch = s.charAt(i);
      if (ch === '\\' && s.charAt(i + 1) === '|') { cur += '|'; i++; }
      else if (ch === '|') { cells.push(cur.trim()); cur = ''; }
      else cur += ch;
    }
    if (cur.trim() !== '') cells.push(cur.trim());
    return cells;
  }

  function parseTableDelimiter(line) {
    if (line.indexOf('|') < 0) return null;
    var cells = splitTableRow(line), align = [];
    for (var i = 0; i < cells.length; i++) {
      if (!/^:?-+:?$/.test(cells[i])) return null;
      var left = cells[i].charAt(0) === ':', right = cells[i].charAt(cells[i].length - 1) === ':';
      align.push(left && right ? 'center' : right ? 'right' : left ? 'left' : '');
    }
    return align.length ? align : null;
  }

  // Danh sách sản phẩm/dịch vụ "**Tên** — mô tả" (từ 2 dòng liền nhau) -> mỗi mục 1 box; giống hệt ITEM_LINE_RE và
  // parseItemRun() trong app/widget/embed.js.
  var ITEM_LINE_RE = /^\s*(?:[-*•]\s+|\d+[.)]\s+)?\*\*([^\n]+?)\*\*(?:\s*[—–]\s*|\s+-\s+)(\S.*)$/;

  function parseItemRun(lines, start) {
    var items = [], i = start, next = start;
    while (i < lines.length) {
      var m = ITEM_LINE_RE.exec(lines[i]);
      if (m) { items.push({ name: m[1].trim(), desc: m[2].trim() }); i++; next = i; }
      else if (lines[i].trim() === '') i++;
      else break;
    }
    return items.length >= 2 ? { items: items, next: next } : null;
  }

  function parseBlocks(text) {
    var lines = text.split('\n'), blocks = [], buf = [], i = 0;
    function flush() { if (buf.length) { blocks.push({ text: buf.join('\n') }); buf = []; } }
    while (i < lines.length) {
      var run = ITEM_LINE_RE.test(lines[i]) ? parseItemRun(lines, i) : null;
      if (run) {
        flush();
        blocks.push({ items: run.items });
        i = run.next;
        continue;
      }
      var align = i + 1 < lines.length && lines[i].indexOf('|') >= 0 ? parseTableDelimiter(lines[i + 1]) : null;
      var head = align ? splitTableRow(lines[i]) : null;
      if (head && head.length === align.length) {
        flush();
        var rows = [];
        i += 2;
        while (i < lines.length && lines[i].trim() !== '' && lines[i].indexOf('|') >= 0) {
          var cells = splitTableRow(lines[i]);
          while (cells.length < head.length) cells.push('');
          rows.push(cells.slice(0, head.length));
          i++;
        }
        blocks.push({ head: head, align: align, rows: rows });
      } else {
        buf.push(lines[i]);
        i++;
      }
    }
    flush();
    return blocks;
  }

  function renderTable(container, block) {
    var table = el('table'), headRow = el('tr');
    function cell(tag, value, align) {
      var node = el(tag);
      if (align) node.style.textAlign = align;
      renderInline(node, value);
      return node;
    }
    block.head.forEach(function (value, c) { headRow.appendChild(cell('th', value, block.align[c])); });
    table.appendChild(el('thead')).appendChild(headRow);
    var body = table.appendChild(el('tbody'));
    block.rows.forEach(function (row) {
      var tr = body.appendChild(el('tr'));
      row.forEach(function (value, c) { tr.appendChild(cell('td', value, block.align[c])); });
    });
    container.appendChild(el('div', 'ib-tbl')).appendChild(table);
  }

  function renderItems(container, block) {
    var box = el('div', 'ib-items');
    block.items.forEach(function (item) {
      var card = box.appendChild(el('div', 'ib-item'));
      renderInline(card.appendChild(el('div', 'ib-item-name')), item.name);
      renderInline(card.appendChild(el('div', 'ib-item-desc')), item.desc);
    });
    container.appendChild(box);
  }

  function renderBlocks(container, blocks) {
    blocks.forEach(function (block, i) {
      if (block.head) { renderTable(container, block); return; }
      if (block.items) { renderItems(container, block); return; }
      // Xuống dòng sát bảng do khối bảng tự tạo khoảng cách nên bỏ đi, tránh dòng trống thừa.
      var text = block.text;
      if (i > 0) text = text.replace(/^\n+/, '');
      if (i < blocks.length - 1) text = text.replace(/\n+$/, '');
      renderInline(container, text);
    });
  }

  function renderRichText(container, raw) {
    renderBlocks(container, parseBlocks(raw == null ? '' : String(raw)));
  }

  // Mục danh sách thường (đánh số / gạch đầu dòng). Giống hệt LIST_LINE_RE trong app/widget/embed.js và _LIST_LINE_RE trong app/dashboard/service.py.
  var LIST_LINE_RE = /^\s*(?:[-*•]\s+|\d+[.)]\s+)\S/;

  // Tách 1 khối chữ thành các phần, mỗi phần 1 bong bóng: mỗi ĐOẠN (ngăn cách bằng dòng trống) 1 phần, mỗi MỤC danh sách 1 phần riêng.
  // Giống hệt splitText() trong app/widget/embed.js và _split_text_parts() trong app/dashboard/service.py.
  function splitText(text) {
    var parts = [], para = [], inItem = false;
    function flush() { var t = para.join('\n').trim(); if (t) parts.push(t); para = []; }
    text.split('\n').forEach(function (line) {
      if (line.trim() === '') { flush(); inItem = false; return; }
      if (LIST_LINE_RE.test(line)) { flush(); inItem = true; }
      else if (inItem && !/^\s/.test(line)) { flush(); inItem = false; }
      para.push(line);
    });
    flush();
    return parts;
  }

  // Tin dài của bot -> nhiều bong bóng riêng (mỗi đoạn, mỗi mục danh sách, mỗi mục "**Tên** — mô tả", mỗi bảng 1 tin). Trả [] nếu chỉ có 1 phần.
  // Giống hệt splitSegments() trong app/widget/embed.js và message_segments() trong app/dashboard/service.py.
  function splitSegments(raw) {
    var segs = [];
    parseBlocks(raw == null ? '' : String(raw)).forEach(function (b) {
      if (b.items) b.items.forEach(function (item) { segs.push({ item: item }); });
      else if (b.head) segs.push({ blocks: [b] });
      else splitText(b.text).forEach(function (t) { segs.push({ blocks: [{ text: t }] }); });
    });
    return segs.length > 1 ? segs : [];
  }

  function renderSegment(container, seg) {
    if (seg.item) {
      renderInline(container.appendChild(el('div', 'ib-item-name')), seg.item.name);
      renderInline(container.appendChild(el('div', 'ib-item-desc')), seg.item.desc);
    } else {
      renderBlocks(container, seg.blocks);
    }
  }

  function api(method, url, body) {
    var opts = { method: method, headers: { 'Accept': 'application/json' } };
    if (method !== 'GET') {
      opts.headers['Content-Type'] = 'application/json';
      opts.headers['X-CSRF-Token'] = cfg.csrfToken;
      opts.body = JSON.stringify(body || {});
    }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) {
          var failure = new Error(data && data.error ? data.error : 'Có lỗi xảy ra (' + r.status + '), vui lòng thử lại.');
          failure.status = r.status;
          throw failure;
        }
        return data;
      });
    });
  }

  // ---- Định dạng thời gian theo giờ máy người dùng (server gửi ISO UTC) ----
  function pad(n) { return n < 10 ? '0' + n : String(n); }
  function hhmm(d) { return pad(d.getHours()) + ':' + pad(d.getMinutes()); }
  function dmy(d) { return pad(d.getDate()) + '/' + pad(d.getMonth() + 1) + '/' + d.getFullYear(); }
  function dayKey(d) { return d.getFullYear() + '-' + d.getMonth() + '-' + d.getDate(); }
  function startOfDay(d) { return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime(); }

  function daysAgo(d) {
    return Math.round((startOfDay(new Date()) - startOfDay(d)) / 86400000);
  }

  function listTime(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    var ago = daysAgo(d);
    if (ago <= 0) return hhmm(d);
    if (ago === 1) return 'Hôm qua';
    if (ago < 7) return WEEKDAYS[d.getDay()];
    return pad(d.getDate()) + '/' + pad(d.getMonth() + 1);
  }

  function dayLabel(iso) {
    var d = new Date(iso);
    var ago = daysAgo(d);
    var prefix = ago <= 0 ? 'Hôm nay' : ago === 1 ? 'Hôm qua' : dmy(d);
    return prefix + ', ' + hhmm(d);
  }

  function chatIcon(color) {
    var ns = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', color);
    var path = document.createElementNS(ns, 'path');
    path.setAttribute('d', 'M12 0C5.4 0 0 5.1 0 11.4c0 3.6 1.8 6.8 4.6 8.9V24l4.2-2.3c1.1.3 2.3.5 3.2.5 6.6 0 12-5.1 12-11.4S18.6 0 12 0z');
    svg.appendChild(path);
    return svg;
  }

  // ---- Danh sách ----
  function renderList() {
    itemsBox.textContent = '';
    if (!state.list.length) {
      itemsBox.appendChild(el('div', 'ib-note', state.search ? 'Không tìm thấy hội thoại phù hợp.' : 'Chưa có hội thoại nào.'));
      return;
    }
    state.list.forEach(function (c) {
      var row = el('button', 'ib-row' + (c.id === state.selectedId ? ' sel' : '') + (c.status === 'closed' ? ' done' : ''));
      row.type = 'button';

      var wrap = el('div', 'ib-avatar-wrap');
      var avatar = el('div', 'ib-avatar', c.initials);
      avatar.style.background = c.color;
      var dot = el('div', 'ib-channel-dot');
      dot.style.background = c.channel_color;
      dot.title = c.channel_label;
      wrap.appendChild(avatar);
      wrap.appendChild(dot);

      var main = el('div', 'ib-row-main');
      var top = el('div', 'ib-row-top');
      top.appendChild(el('span', 'ib-row-name', c.name));
      top.appendChild(el('span', 'ib-row-time', listTime(c.at)));
      main.appendChild(top);
      main.appendChild(el('div', 'ib-row-preview', (SENDER_PREFIX[c.last_sender] || '') + (c.preview || '')));

      row.appendChild(wrap);
      row.appendChild(main);
      if (c.needs_staff) {
        var unread = el('div', 'ib-unread');
        unread.title = 'Đang chờ nhân viên';
        row.appendChild(unread);
      }
      row.addEventListener('click', function () { select(c.id); });
      itemsBox.appendChild(row);
    });
  }

  function loadList() {
    return api('GET', '/api/inbox/conversations?q=' + encodeURIComponent(state.search)).then(function (data) {
      state.list = data.conversations || [];
      renderList();
      return state.list;
    });
  }

  // ---- Hội thoại ----
  function showEmpty(title, desc) {
    $('ib-open').style.display = 'none';
    $('ib-empty').style.display = 'flex';
    $('ib-empty-title').textContent = title;
    $('ib-empty-desc').textContent = desc;
    panel.hidden = true;
  }

  function appendMessages(messages) {
    var nearBottom = msgsBox.scrollHeight - msgsBox.scrollTop - msgsBox.clientHeight < 80;
    messages.forEach(function (m) {
      var d = new Date(m.at);
      var key = dayKey(d);
      if (key !== state.lastDay) {
        state.lastDay = key;
        msgsBox.appendChild(el('div', 'ib-day', dayLabel(m.at)));
      }
      var segs = m.sender === 'bot' ? splitSegments(m.content) : [];
      (segs.length ? segs : [null]).forEach(function (seg, idx) {
        var row = el('div', 'ib-msg ' + m.sender);
        if (m.sender === 'bot') row.appendChild(el('div', 'ib-bot-avatar'));
        if (m.sender === 'staff') row.appendChild(el('div', 'ib-staff-avatar', 'NV'));
        var bubble = el('div', 'ib-bubble');
        if (seg) renderSegment(bubble, seg); else renderRichText(bubble, m.content);
        if (idx === Math.max(segs.length, 1) - 1) {  // giờ chỉ ở tin cuối của nhóm
          bubble.appendChild(el('span', 'ib-time', hhmm(d) + (m.sender === 'staff' ? ' · nhân viên' : m.sender === 'bot' ? ' · trợ lý AI' : '')));
        }
        row.appendChild(bubble);
        msgsBox.appendChild(row);
      });
      state.lastMsgId = Math.max(state.lastMsgId, m.id);
    });
    if (nearBottom || !msgsBox.dataset.scrolled) {
      msgsBox.scrollTop = msgsBox.scrollHeight;
      msgsBox.dataset.scrolled = '1';
    }
  }

  function renderHeader(conv) {
    var avatar = $('ib-head-avatar');
    avatar.textContent = conv.initials;
    avatar.style.background = conv.color;
    $('ib-head-name').textContent = conv.name;
    var channel = $('ib-head-channel');
    channel.textContent = '';
    channel.appendChild(chatIcon(conv.channel_color));
    channel.appendChild(document.createTextNode(conv.channel_label));
    var pill = $('ib-head-status');
    var waiting = conv.status !== 'closed';
    pill.textContent = waiting ? 'Đang chờ' : 'Hoàn tất';
    pill.className = 'ib-pill' + (waiting ? ' waiting' : '');
  }

  function infoRow(label, value) {
    var row = el('div');
    row.appendChild(el('span', null, label));
    row.appendChild(el('span', null, value));
    return row;
  }

  function renderPanel(conv, customer) {
    panel.textContent = '';
    var head = el('div', 'ib-panel-head');
    var avatar = el('div', 'ib-avatar lg', conv.initials);
    avatar.style.background = conv.color;
    head.appendChild(avatar);
    head.appendChild(el('div', 'ib-panel-name', customer.name));
    if (customer.contact) head.appendChild(el('div', 'ib-panel-contact', customer.contact));
    panel.appendChild(head);

    var tags = el('div', 'ib-tags');
    if (customer.stage) tags.appendChild(el('span', 'ib-pill stage ' + customer.stage, customer.stage_label));
    panel.appendChild(tags);

    panel.appendChild(el('div', 'ib-section', 'Thông tin'));
    var info = el('div', 'ib-info');
    info.appendChild(infoRow('Kênh', conv.channel_label));
    if (customer.stage_label) info.appendChild(infoRow('Giai đoạn', customer.stage_label));
    info.appendChild(infoRow('Lần đầu nhắn', customer.first_at ? dmy(new Date(customer.first_at)) : '—'));
    info.appendChild(infoRow('Tổng hội thoại', String(customer.conversation_count)));
    panel.appendChild(info);

    if (customer.profile && customer.profile_query) {
      var profile = el('a', 'ib-btn', 'Xem hồ sơ khách hàng');
      profile.href = cfg.customersUrl + '?q=' + encodeURIComponent(customer.profile_query);
      panel.appendChild(profile);
    }
    var closed = conv.status === 'closed';
    var toggle = el('button', 'ib-btn primary', closed ? 'Mở lại hội thoại' : 'Đánh dấu đã xử lý');
    toggle.type = 'button';
    toggle.addEventListener('click', function () {
      toggle.disabled = true;
      api('PUT', '/api/inbox/conversations/' + conv.id, { status: closed ? 'open' : 'closed' })
        .then(function (data) { data.messages = []; applyDetail(data, false); return loadList(); })   // chỉ cập nhật trạng thái, tin nhắn đã có
        .catch(function (err) { showError(err.message); toggle.disabled = false; });
    });
    panel.appendChild(toggle);
    panel.hidden = false;
  }

  function applyDetail(data, full) {
    if (full) {
      msgsBox.textContent = '';
      delete msgsBox.dataset.scrolled;
      state.lastDay = '';
      state.lastMsgId = 0;
    }
    $('ib-empty').style.display = 'none';
    $('ib-open').style.display = 'flex';
    renderHeader(data.conversation);
    renderPanel(data.conversation, data.customer);
    appendMessages(data.messages || []);
  }

  function showError(message) {
    errorBox.textContent = message || '';
    errorBox.hidden = !message;
  }

  function select(id) {
    state.selectedId = id;
    showError('');
    root.classList.add('show-thread');
    renderList();
    return api('GET', '/api/inbox/conversations/' + id)
      .then(function (data) {
        if (state.selectedId !== id) return;   // người dùng đã chuyển sang hội thoại khác trong lúc tải
        applyDetail(data, true);
      })
      .catch(function (err) {
        showEmpty('Không mở được hội thoại', err.message);
        state.selectedId = null;
      });
  }

  // Lấy tin mới của hội thoại đang mở (id > lastMsgId) và cập nhật tiêu đề/thông tin khách
  function refreshSelected() {
    var id = state.selectedId;
    if (!id) return Promise.resolve();
    return api('GET', '/api/inbox/conversations/' + id + '?after_id=' + state.lastMsgId).then(function (data) {
      if (state.selectedId === id) applyDetail(data, false);
    });
  }

  function refreshAll() {
    return loadList().then(refreshSelected).catch(function () { /* mạng chập chờn: lần sau thử lại */ });
  }

  $('ib-back').addEventListener('click', function () { root.classList.remove('show-thread'); });

  var searchTimer = null;
  $('ib-search').addEventListener('input', function (e) {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      state.search = e.target.value.trim();
      loadList().catch(function (err) { itemsBox.textContent = ''; itemsBox.appendChild(el('div', 'ib-note', err.message)); });
    }, SEARCH_DEBOUNCE_MS);
  });

  // ---- Realtime ----
  var refreshTimer = null;
  function scheduleRefresh() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(refreshAll, REFRESH_DEBOUNCE_MS);
  }

  var pollTimer = null;
  function startPolling() {
    if (!pollTimer) pollTimer = setInterval(function () { if (!document.hidden) refreshAll(); }, POLL_MS);
  }
  function stopPolling() {
    clearInterval(pollTimer);
    pollTimer = null;
  }

  if (window.io) {
    var socket = io({ transports: ['websocket', 'polling'] });
    socket.on('connect', function () {
      socket.emit('join_team', {}, function (res) {
        if (res && res.ok) { stopPolling(); scheduleRefresh(); } else { startPolling(); }
      });
    });
    socket.on('inbox_message', scheduleRefresh);
    socket.on('connect_error', startPolling);
    socket.on('disconnect', startPolling);
  } else {
    startPolling();
  }

  // ---- Khởi động ----
  loadList().then(function (list) {
    if (!list.length) {
      showEmpty('Chưa có tin nhắn nào', 'Hội thoại từ Web Widget sẽ xuất hiện ở đây khi khách bắt đầu chat với trợ lý.');
      return;
    }
    var wanted = list.filter(function (c) { return c.id === cfg.initialId; })[0];
    if (wanted) return select(wanted.id);
    if (window.matchMedia('(min-width: 881px)').matches) return select(list[0].id);   // điện thoại: để người dùng chọn
  }).catch(function (err) {
    showEmpty('Không tải được danh sách', err.message);
  });
})();

/* Web Widget chatbot.
 *
 * Nhúng vào website:  <script src=".../widget/embed.js" data-bot-id="1"></script>
 * Dựng khung chat trong Shadow DOM (CSS của website khách không ảnh hưởng widget và ngược lại).
 * Nội dung tin nhắn chỉ được phép in đậm (**...**), mã (`...`) và bảng markdown — xem renderRichText():
 * luôn tạo text node bằng createTextNode/textContent, chỉ tự thêm thẻ <strong>/<code>/<table>... nên
 * không có XSS từ câu trả lời của AI dù nội dung có chứa "<", ">" hay bất kỳ ký tự nào khác.
 *
 * Cùng 1 bộ dựng này còn được trang Xuất bản dùng cho khung "Xem trước trực tiếp":
 *   var w = AIChatbotWidget.create(config, { container: el, send: fn, open: true });
 *   w.update({ color: '#16A34A', size: 72 });   // mọi tuỳ chọn giao diện đều đi qua update()
 * Vì xem trước chạy đúng mã này nên không thể lệch với widget thật.
 *
 * config: { name, greeting, placeholder, send, error, staff (nhãn tin nhân viên),
 *           icon: { kind: 'svg', value: markup } | { kind: 'image', value: url } — dùng cho CẢ nút chat
 *           và thanh tiêu đề, color (#RRGGBB), size (px), shape ('round'|'rounded'),
 *           position ('left'|'right'), window: { w, h } } */
(function () {
  var DEFAULTS = {
    name: '', greeting: '', placeholder: '', send: 'Send', error: 'Error', staff: 'Staff',
    icon: { kind: 'svg', value: '' }, color: '#1D4ED8', size: 56, shape: 'round', position: 'right', window: { w: 360, h: 540 }
  };
  var OFFSET = 20;      // khoảng cách nút chat tới mép trang/khung
  var GAP = 14;         // khoảng cách giữa nút chat và khung chat
  var MOBILE_MAX = 480; // <= bấy nhiêu px: khung chat chiếm cả chiều rộng (chỉ khi nhúng thật)
  var HISTORY_TURNS = 6;

  function extend(target, source) {
    for (var key in source) { if (Object.prototype.hasOwnProperty.call(source, key)) target[key] = source[key]; }
    return target;
  }

  function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  // In đậm/mã/bảng tối thiểu mà AI hay dùng (**đậm**, `mã`, bảng GFM) — không hỗ trợ heading/link/list vì
  // AI trả lời tự do có thể chứa "*"/"`" bất kỳ; chỉ các dạng phổ biến, rõ ràng này mới đủ an toàn để nhận
  // diện mà không đoán nhầm. Luôn dùng createTextNode/textContent cho nội dung thật, chỉ các thẻ định dạng
  // là do mình tự thêm nên không có XSS dù nội dung chứa gì.
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

  // Bảng markdown kiểu GFM mà AI hay trả về:  | A | B |  /  | --- | :---: |  /  | 1 | 2 |
  // Chỉ nhận diện khi có ĐỦ hàng tiêu đề + hàng phân cách (---) cùng số cột; nếu không thì vẫn là văn bản
  // thường (một dòng có dấu "|" bất kỳ không bị đoán nhầm thành bảng). Quy tắc này giống hệt
  // format_message() trong app/dashboard/service.py và renderRichText() trong app/static/js/inbox.js.
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

  // Hàng phân cách -> mảng căn lề ('left'|'center'|'right'|''), hoặc null nếu dòng này không phải hàng phân cách.
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

  // Tách văn bản thành các khối [{ text }] và [{ head, align, rows }] theo thứ tự xuất hiện.
  function parseBlocks(text) {
    var lines = text.split('\n'), blocks = [], buf = [], i = 0;
    function flush() { if (buf.length) { blocks.push({ text: buf.join('\n') }); buf = []; } }
    while (i < lines.length) {
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
    container.appendChild(el('div', 'tbl')).appendChild(table);
  }

  function renderRichText(container, raw) {
    var blocks = parseBlocks(raw == null ? '' : String(raw));
    blocks.forEach(function (block, i) {
      if (block.head) { renderTable(container, block); return; }
      // Xuống dòng sát bảng do khối bảng tự tạo khoảng cách nên bỏ đi, tránh dòng trống thừa.
      var text = block.text;
      if (i > 0) text = text.replace(/^\n+/, '');
      if (i < blocks.length - 1) text = text.replace(/\n+$/, '');
      renderInline(container, text);
    });
  }

  // Vẽ icon (nút chat + thanh tiêu đề dùng chung hàm này). SVG dựng sẵn luôn vẽ ở glyphPx. Ảnh tải lên
  // dùng imageSize riêng (thường to hơn glyphPx vì ảnh cần rõ chi tiết hơn 1 icon nét đơn giản):
  // imageSize='fill' -> lấp đầy container (dùng ở tiêu đề — container đã cố định kích thước bằng CSS
  // nên không còn bị méo/kéo giãn); imageSize là số -> vẽ đúng số px đó, chừa viền quanh icon để nút
  // chat vẫn thấy màu nền đã chọn thay vì bị ảnh che hết.
  function applyIcon(container, icon, glyphPx, imageSize) {
    container.innerHTML = '';
    if (icon && icon.kind === 'image' && icon.value) {
      var img = el('img');
      img.src = icon.value;
      img.alt = '';
      var dim = imageSize === 'fill' ? '100%' : (imageSize || glyphPx) + 'px';
      img.style.cssText = 'width:' + dim + ';height:' + dim + ';object-fit:cover;border-radius:50%;display:block';
      container.appendChild(img);
    } else {
      container.innerHTML = '<svg viewBox="0 0 24 24" width="' + glyphPx + '" height="' + glyphPx + '" fill="#fff" fill-rule="evenodd">' + ((icon && icon.value) || '') + '</svg>';
    }
  }

  // POST JSON; HTTP lỗi -> Error(message của server, fromServer = true). Lỗi mạng/JSON hỏng ném nguyên.
  function postJson(url, body, headers) {
    return fetch(url, {
      method: 'POST',
      headers: extend({ 'Content-Type': 'application/json' }, headers || {}),
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) {
          var failure = new Error(data && data.error ? data.error : 'Request failed');
          failure.fromServer = true;
          throw failure;
        }
        return data;
      });
    });
  }

  var STYLE = [
    ':host{all:initial}',
    '*{box-sizing:border-box;font-family:"Be Vietnam Pro",system-ui,-apple-system,"Segoe UI",sans-serif}',
    '.wrap{position:relative;display:flex}',
    '.launcher{border:none;background:var(--brand);color:#fff;cursor:pointer;box-shadow:0 8px 24px rgba(15,23,42,.3);display:flex;align-items:center;justify-content:center;padding:0;flex-shrink:0}',
    '.panel{display:none;flex-direction:column;position:absolute;background:#fff;border-radius:18px;overflow:hidden;box-shadow:0 16px 48px rgba(15,23,42,.28)}',
    '.wrap.right .panel{right:0}.wrap.left .panel{left:0}',
    '.panel.open{display:flex}',
    '.head{background:var(--brand);color:#fff;padding:14px 16px;display:flex;align-items:center;gap:10px;font-size:14px;font-weight:700}',
    // Chỉ nhắm đúng span tiêu đề bằng class riêng — dùng selector thẻ chung ".head span" sẽ vô tình
    // khớp luôn .head-icon (cũng là 1 <span> trong .head), khiến icon header bị "flex-grow:1" kéo giãn.
    '.head-title{flex-grow:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.head-icon{width:36px;height:36px;border-radius:50%;background:rgba(255,255,255,.18);display:flex;align-items:center;justify-content:center;flex-shrink:0;overflow:hidden}',
    '.close{background:none;border:none;color:#fff;font-size:20px;line-height:1;cursor:pointer;padding:0 2px}',
    '.msgs{flex-grow:1;overflow-y:auto;padding:14px;background:#F8FAFC;display:flex;flex-direction:column;gap:10px}',
    '.row{display:flex}.row.user{justify-content:flex-end}',
    '.bubble{max-width:82%;padding:9px 12px;font-size:13.5px;line-height:1.5;white-space:pre-wrap;word-break:break-word;background:#fff;border:1px solid #DCE7F5;border-radius:14px 14px 14px 3px;color:#0F172A}',
    '.user .bubble{background:var(--brand);border-color:var(--brand);color:#fff;border-radius:14px 14px 3px 14px}',
    // Bảng markdown: bọc trong .tbl để bảng rộng cuộn ngang trong bong bóng thay vì tràn ra khỏi khung chat
    '.tbl{overflow-x:auto;margin:6px 0}.tbl:first-child{margin-top:0}.tbl:last-child{margin-bottom:0}',
    '.tbl table{border-collapse:collapse;font-size:12.5px;line-height:1.4;white-space:normal}',
    '.tbl th,.tbl td{border:1px solid #DCE7F5;padding:5px 9px;text-align:left;vertical-align:top}',
    '.tbl th{background:#EFF6FF;font-weight:700}',
    '.user .tbl th,.user .tbl td{border-color:rgba(255,255,255,.45)}.user .tbl th{background:rgba(255,255,255,.18)}',
    '.bubble.err{background:#FEF2F2;border-color:#FCA5A5;color:#B91C1C}',
    '.bubble.staff{background:#EFF6FF;border-color:#BFDBFE}',
    '.staff-tag{display:block;font-size:11px;font-weight:700;color:#2563EB;margin-bottom:2px}',
    '.launcher{position:relative}',
    '.launcher.unread::after{content:"";position:absolute;top:0;right:0;width:12px;height:12px;border-radius:50%;background:#EF4444;border:2px solid #fff}',
    '.typing{display:inline-flex;gap:4px;padding:13px 12px}',
    '.typing i{width:6px;height:6px;border-radius:50%;background:#93A4BA;animation:blink 1.2s infinite ease-in-out}',
    '.typing i:nth-child(2){animation-delay:.2s}.typing i:nth-child(3){animation-delay:.4s}',
    '@keyframes blink{0%,80%,100%{opacity:.25}40%{opacity:1}}',
    'form{display:flex;gap:8px;padding:10px 12px;border-top:1px solid #DCE7F5;background:#fff}',
    'input{flex-grow:1;min-width:0;border:1px solid #DCE7F5;border-radius:10px;padding:9px 12px;font-size:13.5px;outline:none;color:#0F172A}',
    'input:focus{border-color:var(--brand)}',
    'button.send{border:none;border-radius:10px;background:var(--brand);color:#fff;font-size:13px;font-weight:700;padding:0 14px;cursor:pointer}',
    'button.send:disabled,input:disabled{opacity:.55;cursor:default}',
    // Điện thoại (chỉ khi nhúng thật): khung chat là bottom sheet, kích thước do CSS điều khiển
    '.wrap.mobile .panel{position:fixed;left:0;right:0;bottom:0;width:auto;height:85vh;border-radius:18px 18px 0 0}'
  ].join('');

  function createWidget(initial, opts) {
    opts = opts || {};
    var cfg = extend(extend({}, DEFAULTS), initial || {});
    var messages = opts.messages || [];   // [{ role: 'user'|'bot', content }]

    var host = el('div');
    var root = host.attachShadow({ mode: 'open' });
    var style = el('style');
    style.textContent = STYLE;
    root.appendChild(style);

    var wrap = el('div', 'wrap');
    var panel = el('div', 'panel');
    var head = el('div', 'head');
    var headIcon = el('span', 'head-icon');
    var title = el('span', 'head-title');
    var closeBtn = el('button', 'close', '×');
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', 'Close');
    head.appendChild(headIcon);
    head.appendChild(title);
    head.appendChild(closeBtn);

    var msgsBox = el('div', 'msgs');
    var form = el('form');
    var input = el('input');
    input.type = 'text';
    input.maxLength = 1000;
    input.autocomplete = 'off';
    var sendBtn = el('button', 'send');
    sendBtn.type = 'submit';
    form.appendChild(input);
    form.appendChild(sendBtn);
    panel.appendChild(head);
    panel.appendChild(msgsBox);
    panel.appendChild(form);

    var launcher = el('button', 'launcher');
    launcher.type = 'button';
    wrap.appendChild(panel);
    wrap.appendChild(launcher);
    root.appendChild(wrap);

    var container = opts.container || null;
    host.style.cssText = 'position:' + (container ? 'absolute' : 'fixed') + ';bottom:' + OFFSET + 'px;z-index:2147483647;';
    (container || document.body).appendChild(host);

    // ---- Nội dung chat ----
    function addBubble(role, text, isError) {
      var row = el('div', 'row ' + (role === 'user' ? 'user' : 'bot'));
      var bubble = el('div', 'bubble' + (isError ? ' err' : '') + (role === 'staff' ? ' staff' : ''));
      if (role === 'staff') bubble.appendChild(el('span', 'staff-tag', cfg.staff));
      renderRichText(bubble, text);
      row.appendChild(bubble);
      msgsBox.appendChild(row);
      msgsBox.scrollTop = msgsBox.scrollHeight;
      return bubble;
    }

    function showTyping() {
      var row = el('div', 'row bot');
      var bubble = el('div', 'bubble typing');
      bubble.innerHTML = '<i></i><i></i><i></i>';
      row.appendChild(bubble);
      msgsBox.appendChild(row);
      msgsBox.scrollTop = msgsBox.scrollHeight;
      return row;
    }

    var greetingBubble = addBubble('bot', '');   // lời chào: cập nhật theo cfg.greeting trong apply()
    messages.forEach(function (m) { addBubble(m.role, String(m.content)); });

    // ---- Áp dụng cấu hình: TẤT CẢ tuỳ chọn giao diện được áp dụng ở đây (chạy lại mỗi lần update) ----
    function apply() {
      var size = clamp(Number(cfg.size) || DEFAULTS.size, 40, 96);
      var color = /^#[0-9a-fA-F]{6}$/.test(cfg.color || '') ? cfg.color : DEFAULTS.color;
      var position = cfg.position === 'left' ? 'left' : 'right';
      var glyph = Math.round(size * 0.46);
      var imageGlyph = Math.round(size * 0.65);   // ảnh tải lên ở nút chat: to hơn SVG (~36px ở nút mặc định 56px)

      host.style.setProperty('--brand', color);
      host.style.left = position === 'left' ? OFFSET + 'px' : 'auto';
      host.style.right = position === 'right' ? OFFSET + 'px' : 'auto';
      wrap.className = 'wrap ' + position + (wrap.classList.contains('mobile') ? ' mobile' : '');

      launcher.style.width = launcher.style.height = size + 'px';
      launcher.style.borderRadius = cfg.shape === 'rounded' ? '30%' : '50%';
      launcher.setAttribute('aria-label', cfg.name);
      applyIcon(launcher, cfg.icon, glyph, imageGlyph);   // ảnh: to hơn SVG nhưng vẫn chừa viền thấy màu nền
      applyIcon(headIcon, cfg.icon, 20, 'fill');          // ảnh: lấp đầy khung 36px ở tiêu đề

      title.textContent = cfg.name;
      input.placeholder = cfg.placeholder;
      sendBtn.textContent = cfg.send;
      greetingBubble.textContent = '';   // apply() có thể chạy lại nhiều lần (đổi màu/size...) -> xoá rồi vẽ lại
      renderRichText(greetingBubble, cfg.greeting);

      layout();
    }

    // Kích thước + vị trí khung chat: không vượt quá chỗ trống của trang (hoặc của khung xem trước)
    function layout() {
      var size = clamp(Number(cfg.size) || DEFAULTS.size, 40, 96);
      var mobile = !container && window.innerWidth <= MOBILE_MAX;
      wrap.classList.toggle('mobile', mobile);
      if (mobile) {
        panel.style.width = panel.style.height = panel.style.bottom = '';
        return;
      }
      var gap = size + GAP;
      var availW = (container ? container.clientWidth : window.innerWidth) - OFFSET * 2;
      var availH = (container ? container.clientHeight : window.innerHeight) - OFFSET - gap - 12;
      panel.style.bottom = gap + 'px';
      panel.style.width = Math.min(cfg.window.w, availW) + 'px';
      panel.style.height = Math.min(cfg.window.h, Math.max(availH, 220)) + 'px';
    }

    function setOpen(open) {
      panel.classList.toggle('open', open);
      if (open) launcher.classList.remove('unread');
      if (open) { msgsBox.scrollTop = msgsBox.scrollHeight; if (!container) input.focus(); }
    }
    launcher.addEventListener('click', function () { setOpen(!panel.classList.contains('open')); });
    closeBtn.addEventListener('click', function () { setOpen(false); });
    window.addEventListener('resize', layout);

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var text = input.value.trim();
      if (!text || input.disabled) return;
      var history = messages.slice(-HISTORY_TURNS);
      input.value = '';
      input.disabled = sendBtn.disabled = true;
      addBubble('user', text);
      messages.push({ role: 'user', content: text });
      var typing = showTyping();

      Promise.resolve(opts.send(text, history))
        .then(function (res) {
          typing.parentNode && typing.parentNode.removeChild(typing);
          if (res.reply == null) return;   // nhân viên đang tiếp quản: bot không trả lời, tin nhân viên sẽ tới sau
          messages.push({ role: 'bot', content: res.reply });
          addBubble('bot', res.reply);
        })
        .catch(function (err) {
          typing.parentNode && typing.parentNode.removeChild(typing);
          addBubble('bot', err && err.fromServer ? err.message : cfg.error, true);
        })
        .then(function () {
          if (opts.onMessages) opts.onMessages(messages);
          input.disabled = sendBtn.disabled = false;
          if (!container) input.focus();
        });
    });

    apply();
    setOpen(!!opts.open);

    return {
      host: host,
      // Đổi 1 hay nhiều tuỳ chọn; giao diện cập nhật ngay
      update: function (partial) { extend(cfg, partial || {}); apply(); },
      open: function () { setOpen(true); },
      close: function () { setOpen(false); },
      // Tin nhân viên trả lời từ Inbox (widget nhúng gọi khi hỏi định kỳ thấy tin mới)
      receiveStaff: function (text) {
        messages.push({ role: 'staff', content: text });
        addBubble('staff', text);
        if (!panel.classList.contains('open')) launcher.classList.add('unread');
        if (opts.onMessages) opts.onMessages(messages);
      },
      // Xoá cuộc trò chuyện, chỉ còn lời chào (nút "Làm mới" ở khung xem trước)
      reset: function () {
        messages.length = 0;
        while (msgsBox.childNodes.length > 1) msgsBox.removeChild(msgsBox.lastChild);
        input.disabled = sendBtn.disabled = false;
      },
      destroy: function () { host.parentNode && host.parentNode.removeChild(host); }
    };
  }

  if (!window.AIChatbotWidget) window.AIChatbotWidget = { create: createWidget, postJson: postJson };

  // ---- Chế độ nhúng vào website khách: tự chạy khi thẻ script có data-bot-id ----
  var script = document.currentScript;
  var botId = script && script.getAttribute('data-bot-id');
  if (!botId || window['__aichatbot_' + botId]) return;
  window['__aichatbot_' + botId] = true;

  var apiBase = new URL(script.src).origin + '/widget/api/' + encodeURIComponent(botId);
  var STORE_KEY = 'aichatbot:' + botId;
  var MAX_STORED = 40;

  var state = { visitorId: '', conversationId: null, lastId: 0, messages: [] };
  try {
    var saved = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    if (saved && typeof saved === 'object') {
      state.visitorId = String(saved.visitorId || '');
      state.conversationId = typeof saved.conversationId === 'number' ? saved.conversationId : null;
      state.lastId = typeof saved.lastId === 'number' ? saved.lastId : 0;
      state.messages = Array.isArray(saved.messages) ? saved.messages : [];
    }
  } catch (e) { /* localStorage bị chặn: chạy không lưu */ }
  if (!state.visitorId) {
    state.visitorId = (window.crypto && crypto.randomUUID) ? crypto.randomUUID().replace(/-/g, '') : String(Date.now()) + Math.random().toString(16).slice(2);
  }

  function persist() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        visitorId: state.visitorId,
        conversationId: state.conversationId,
        lastId: state.lastId,
        messages: state.messages.slice(-MAX_STORED)
      }));
    } catch (e) { /* bỏ qua */ }
  }

  // Hỏi server tin nhân viên trả lời từ Inbox: chỉ khi đã có hội thoại và tab đang hiển thị
  var POLL_MS = 5000;
  function startPolling(widget) {
    var busy = false;
    function poll() {
      if (busy || !state.conversationId || document.hidden) return;
      busy = true;
      var qs = '?conversation_id=' + encodeURIComponent(state.conversationId) +
        '&visitor_id=' + encodeURIComponent(state.visitorId) + '&after_id=' + encodeURIComponent(state.lastId);
      fetch(apiBase + '/staff-messages' + qs)
        .then(function (r) { if (!r.ok) throw new Error('poll ' + r.status); return r.json(); })
        .then(function (data) {
          (data.messages || []).forEach(function (m) { widget.receiveStaff(String(m.content)); });
          if (typeof data.last_id === 'number') state.lastId = Math.max(state.lastId, data.last_id);
          persist();
        })
        .catch(function () { /* mạng chập chờn: lần hỏi sau thử lại */ })
        .then(function () { busy = false; });
    }
    setInterval(poll, POLL_MS);
    document.addEventListener('visibilitychange', poll);
  }

  fetch(apiBase + '/config')
    .then(function (r) { if (!r.ok) throw new Error('config ' + r.status); return r.json(); })
    .then(function (config) {
      var widget = createWidget(config, {
        messages: state.messages,
        send: function (text) {
          return postJson(apiBase + '/messages', {
            message: text, visitor_id: state.visitorId, conversation_id: state.conversationId
          }).then(function (data) {
            state.conversationId = data.conversation_id;
            state.visitorId = data.visitor_id || state.visitorId;
            if (typeof data.last_message_id === 'number') state.lastId = Math.max(state.lastId, data.last_message_id);
            return data;
          });
        },
        onMessages: persist
      });
      startPolling(widget);
    })
    .catch(function (err) {
      // Domain chưa được cấp phép hoặc bot không tồn tại: không hiển thị widget
      if (window.console) console.warn('[aichatbot] widget không khởi tạo được:', err.message);
    });
})();

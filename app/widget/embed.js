/* Web Widget chatbot.
 *
 * Nhúng vào website:  <script src=".../widget/embed.js" data-bot-id="PUBLIC_ID"></script>
 * (PUBLIC_ID = bots.public_id, mã ngẫu nhiên lấy ở Bước 3 — Xuất bản; KHÔNG phải id số của bot)
 * Dựng khung chat trong Shadow DOM (CSS của website khách không ảnh hưởng widget và ngược lại).
 * Nội dung tin nhắn chỉ được phép in đậm (**...**), mã (`...`), bảng markdown và danh sách "**Tên** — mô tả"
 * (dựng thành box) — xem renderRichText():
 * luôn tạo text node bằng createTextNode/textContent, chỉ tự thêm thẻ <strong>/<code>/<table>/<div>... nên
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
 *           position ('left'|'right'), window: { w, h },
 *           attachments: { enabled, accept, max_bytes, max_files } + attach: { button, reading, remove, too_big, unsupported, too_many, failed, truncated } — module
 *           "Đọc tài liệu": chỉ hiện nút đính kèm khi attachments.enabled VÀ createWidget được truyền opts.attach = { upload(file), status(id) } (khung xem trước không truyền) } */
(function () {
  var DEFAULTS = {
    name: '', greeting: '', placeholder: '', send: 'Send', error: 'Error', staff: 'Staff',
    confirm_prompt: 'Do you confirm this action?', confirm_yes: 'Confirm', confirm_no: 'Cancel', confirm_cancelled: 'Action cancelled.', action_failed: 'The action could not be performed.',
    // Tốc độ cảm nhận: câu đệm (hiện ngay khi gửi), dòng tiến trình thật theo mã bước, báo nhận yêu cầu khi lâu, báo có kết quả muộn.
    // async_enabled: kỹ thuật "trả lời bất đồng bộ" (Bước 1, mặc định bật) — tắt thì bỏ qua nhịp chờ/mở lại ô nhập sớm, giữ hành vi chờ cổ điển.
    fillers: [], steps: {}, slow: '', late_reply: '', slow_after_seconds: 10, wait_seconds: 90, async_enabled: true,
    attachments: { enabled: false, accept: '', max_bytes: 0, max_files: 3 },
    attach: { button: 'Attach a file', reading: 'Reading...', remove: 'Remove', too_big: 'File too large (max {mb} MB).', unsupported: 'Unsupported file type.', too_many: 'At most {n} files per message.', failed: 'Upload failed.', truncated: '', only_file: 'Please read this file.' },
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

  // Danh sách sản phẩm/dịch vụ: từ 2 dòng "**Tên** — mô tả" liền nhau (có thể có gạch đầu dòng/số thứ tự, cho phép
  // dòng trống giữa các mục) -> mỗi mục là 1 box. Chỉ nhận dấu gạch dài (— –) hoặc " - " có khoảng trắng hai bên; dấu ":"
  // không nhận để cặp "**Địa chỉ**: ..." không bị dựng nhầm thành box. Cùng quy tắc với _ITEM_LINE_RE trong
  // app/dashboard/service.py và ITEM_LINE_RE trong app/static/js/inbox.js.
  var ITEM_LINE_RE = /^\s*(?:[-*•]\s+|\d+[.)]\s+)?\*\*([^\n]+?)\*\*(?:\s*[—–]\s*|\s+-\s+)(\S.*)$/;

  // Đọc dãy mục bắt đầu ở start -> { items: [{ name, desc }], next } hoặc null nếu chưa đủ 2 mục.
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

  // Tách văn bản thành các khối [{ text }], [{ head, align, rows }] và [{ items }] theo thứ tự xuất hiện.
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
    container.appendChild(el('div', 'tbl')).appendChild(table);
  }

  function renderItems(container, block) {
    var box = el('div', 'items');
    block.items.forEach(function (item) {
      var card = box.appendChild(el('div', 'item'));
      renderInline(card.appendChild(el('div', 'item-name')), item.name);
      renderInline(card.appendChild(el('div', 'item-desc')), item.desc);
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

  // Mục danh sách thường (đánh số / gạch đầu dòng). Giống hệt LIST_LINE_RE trong app/static/js/inbox.js và _LIST_LINE_RE trong app/dashboard/service.py.
  var LIST_LINE_RE = /^\s*(?:[-*•]\s+|\d+[.)]\s+)\S/;

  // Tách 1 khối chữ thành các phần, mỗi phần 1 tin: mỗi ĐOẠN (ngăn cách bằng dòng trống) 1 phần, mỗi MỤC danh sách 1 phần riêng. Dòng thụt vào ngay sau
  // 1 mục là phần tiếp của mục đó; dòng không thụt sau mục (không phải mục mới) mở đoạn mới. Giống hệt _split_text_parts() trong app/dashboard/service.py.
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

  // Tin dài của bot -> nhiều tin riêng (mỗi đoạn, mỗi mục danh sách, mỗi mục "**Tên** — mô tả", mỗi bảng 1 tin) để không dồn cả đoạn dài vào 1 box.
  // Cùng quy tắc với message_segments() trong app/dashboard/service.py và splitSegments() trong app/static/js/inbox.js.
  // Trả [] nếu chỉ có 1 phần (giữ nguyên 1 tin).
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
      renderInline(container.appendChild(el('div', 'item-name')), seg.item.name);
      renderInline(container.appendChild(el('div', 'item-desc')), seg.item.desc);
    } else {
      renderBlocks(container, seg.blocks);
    }
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

  function readJson(r) {
    return r.json().then(function (data) {
      if (!r.ok) {
        var failure = new Error(data && data.error ? data.error : 'Request failed');
        failure.fromServer = true;
        failure.status = r.status;
        throw failure;
      }
      return data;
    });
  }

  // POST JSON; HTTP lỗi -> Error(message của server, fromServer = true). Lỗi mạng/JSON hỏng ném nguyên.
  function postJson(url, body, headers) {
    return fetch(url, {
      method: 'POST',
      headers: extend({ 'Content-Type': 'application/json' }, headers || {}),
      body: JSON.stringify(body)
    }).then(readJson);
  }

  // GET JSON, cùng quy ước lỗi với postJson
  function getJson(url, options) {
    return fetch(url, options || {}).then(readJson);
  }

  var TURN_POLL_MS = 700;         // nhịp hỏi tiến trình của 1 lượt bất đồng bộ (ngắn để dòng tiến trình hiện gần như tức thì)
  var TURN_MAX_FAILURES = 5;      // số lần hỏi liên tiếp hỏng (mạng) thì bỏ cuộc

  // Theo dõi 1 lượt bất đồng bộ tới khi có câu trả lời. `first` = kết quả POST gửi tin: có turn_id thì hỏi tiếp qua poll(turnId, after) -> {events, next, done},
  // mỗi sự kiện step gọi hooks.step(code); sự kiện reply -> resolve({ ...first, reply, last_message_id }); sự kiện error -> reject (fromServer). Không có turn_id
  // (đường đồng bộ cũ, hoặc nhân viên đang tiếp quản: reply = null) thì trả nguyên `first`. Quá hooks.waitMs mà chưa xong -> reject.
  function followTurn(first, poll, hooks) {
    if (!first || !first.turn_id) return Promise.resolve(first);
    hooks = hooks || {};
    return new Promise(function (resolve, reject) {
      var after = 0, failures = 0, deadline = Date.now() + (hooks.waitMs || 90000);
      function later() {
        if (Date.now() > deadline) return reject(new Error('turn timeout'));
        setTimeout(tick, TURN_POLL_MS);
      }
      function tick() {
        poll(first.turn_id, after).then(function (r) {
          failures = 0;
          after = typeof r.next === 'number' ? r.next : after;
          var events = r.events || [];
          for (var i = 0; i < events.length; i++) {
            var e = events[i];
            if (e.type === 'step' && hooks.step) hooks.step(e.code);
            else if (e.type === 'reply') return resolve(extend(extend({}, first), { reply: e.reply, last_message_id: e.message_id != null ? e.message_id : first.last_message_id }));
            else if (e.type === 'error') { var failure = new Error(e.message || 'Error'); failure.fromServer = true; return reject(failure); }
          }
          later();
        }).catch(function (err) {
          if (err && err.status === 404) return reject(err);   // lượt không còn (hết hạn / sai chủ): không hỏi tiếp
          failures++;
          if (failures >= TURN_MAX_FAILURES) return reject(err);
          later();
        });
      }
      tick();
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
    // Danh sách sản phẩm/dịch vụ: mỗi mục 1 box (không kẻ bảng) cho dễ đọc trong khung chat hẹp
    '.items{display:flex;flex-direction:column;gap:6px;margin:6px 0;white-space:normal}.items:first-child{margin-top:0}.items:last-child{margin-bottom:0}',
    '.item{border:1px solid #DCE7F5;border-radius:10px;padding:7px 10px;background:#F8FAFC}',
    '.item-name{font-weight:700}.item-desc{margin-top:2px;line-height:1.45}',
    '.user .item{border-color:rgba(255,255,255,.45);background:rgba(255,255,255,.14)}',
    '.bubble.err{background:#FEF2F2;border-color:#FCA5A5;color:#B91C1C}',
    '.bubble.staff{background:#EFF6FF;border-color:#BFDBFE}',
    // Hộp xác nhận hành động thanh toán (Phase M4): khách phải bấm Đồng ý trong khung chat thì widget mới thực thi
    '.confirm-actions{display:flex;gap:8px;margin-top:8px}',
    '.confirm-btn{border:1px solid var(--brand);border-radius:8px;padding:6px 12px;font-size:12.5px;font-weight:700;cursor:pointer;background:#fff;color:var(--brand)}',
    '.confirm-btn.yes{background:var(--brand);color:#fff}',
    '.confirm-btn:disabled{opacity:.5;cursor:default}',
    '.staff-tag{display:block;font-size:11px;font-weight:700;color:#2563EB;margin-bottom:2px}',
    '.launcher{position:relative}',
    '.launcher.unread::after{content:"";position:absolute;top:0;right:0;width:12px;height:12px;border-radius:50%;background:#EF4444;border:2px solid #fff}',
    '.typing{display:inline-flex;gap:4px;padding:13px 12px}',
    '.typing i{width:6px;height:6px;border-radius:50%;background:#93A4BA;animation:blink 1.2s infinite ease-in-out}',
    '.typing i:nth-child(2){animation-delay:.2s}.typing i:nth-child(3){animation-delay:.4s}',
    // Bong bóng "đang làm việc": câu đệm + các dòng tiến trình thật (dòng hiện tại đậm, dòng đã qua mờ) + 3 chấm
    '.bubble.work{display:flex;flex-direction:column;gap:5px}',
    '.work-step{font-size:12.5px;color:#94A3B8}.work-step.now{color:#0F172A;font-weight:600}',
    '.dots{display:inline-flex;gap:4px;padding-top:2px}',
    '.dots i{width:6px;height:6px;border-radius:50%;background:#93A4BA;animation:blink 1.2s infinite ease-in-out}',
    '.dots i:nth-child(2){animation-delay:.2s}.dots i:nth-child(3){animation-delay:.4s}',
    '@keyframes blink{0%,80%,100%{opacity:.25}40%{opacity:1}}',
    'form{display:flex;gap:8px;padding:10px 12px;border-top:1px solid #DCE7F5;background:#fff}',
    'input{flex-grow:1;min-width:0;border:1px solid #DCE7F5;border-radius:10px;padding:9px 12px;font-size:13.5px;outline:none;color:#0F172A}',
    'input:focus{border-color:var(--brand)}',
    'button.send{border:none;border-radius:10px;background:var(--brand);color:#fff;font-size:13px;font-weight:700;padding:0 14px;cursor:pointer}',
    'button.send:disabled,input:disabled{opacity:.55;cursor:default}',
    // Đính kèm tệp (module "Đọc tài liệu"): nút kẹp giấy + hàng chip tên tệp kèm trạng thái đọc
    '.attach{border:1px solid #DCE7F5;background:#fff;border-radius:10px;width:38px;flex-shrink:0;cursor:pointer;color:#475569;display:flex;align-items:center;justify-content:center;padding:0}',
    '.attach:hover{border-color:var(--brand);color:var(--brand)}.attach:disabled{opacity:.55;cursor:default}',
    '.chips{display:none;flex-wrap:wrap;gap:6px;padding:8px 12px 0;background:#fff;border-top:1px solid #DCE7F5}.chips.has{display:flex}',
    '.chip{display:inline-flex;align-items:center;gap:6px;max-width:100%;border:1px solid #DCE7F5;border-radius:999px;padding:3px 6px 3px 10px;font-size:12px;background:#F8FAFC;color:#0F172A}',
    '.chip .n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:150px}',
    '.chip.reading{color:#64748B}.chip.failed{border-color:#FCA5A5;background:#FEF2F2;color:#B91C1C}',
    '.chip button{border:none;background:none;cursor:pointer;font-size:15px;line-height:1;color:inherit;padding:0 2px}',
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
    // Đính kèm tệp: chỉ tồn tại khi nhúng thật (opts.attach) và bot đã cài module "Đọc tài liệu" (cfg.attachments.enabled) — xem apply()
    var attachBtn = el('button', 'attach');
    attachBtn.type = 'button';
    attachBtn.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path></svg>';
    var fileInput = el('input');
    fileInput.type = 'file';
    fileInput.multiple = true;
    fileInput.style.display = 'none';
    var chips = el('div', 'chips');
    form.appendChild(attachBtn);
    form.appendChild(fileInput);
    form.appendChild(input);
    form.appendChild(sendBtn);
    panel.appendChild(head);
    panel.appendChild(msgsBox);
    panel.appendChild(chips);
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
    function appendSegmentBubble(role, seg, text, isError) {
      var row = el('div', 'row ' + (role === 'user' ? 'user' : 'bot'));
      var bubble = el('div', 'bubble' + (isError ? ' err' : '') + (role === 'staff' ? ' staff' : ''));
      if (role === 'staff') bubble.appendChild(el('span', 'staff-tag', cfg.staff));
      if (seg) renderSegment(bubble, seg); else renderRichText(bubble, text);
      row.appendChild(bubble);
      msgsBox.appendChild(row);
      msgsBox.scrollTop = msgsBox.scrollHeight;
      return bubble;
    }

    // Hiện NGAY toàn bộ các bong bóng của 1 tin (không tuần tự) — dùng khi phục hồi lịch sử lúc mở lại widget, echo tin của khách, và các thông báo
    // ngắn chỉ có 1 bong bóng. Tin trả lời MỚI của bot dùng addBubbleSequential (bên dưới) để tuần tự từng bong bóng thay vì đổ hết cùng lúc.
    function addBubble(role, text, isError) {
      var segs = role === 'bot' && !isError ? splitSegments(text) : [];
      if (!segs.length) return appendSegmentBubble(role, null, text, isError);
      var bubble;
      segs.forEach(function (seg) { bubble = appendSegmentBubble(role, seg, text, isError); });
      return bubble;  // tin cuối của nhóm
    }

    var SEGMENT_DELAY_MIN_MS = 450, SEGMENT_DELAY_MAX_MS = 1800, SEGMENT_DELAY_MS_PER_CHAR = 12;

    // Nhịp chờ trước khi hiện bong bóng KẾ TIẾP, ước theo độ dài bong bóng VỪA hiện (giống tốc độ gõ thật: đoạn dài thì nhịp dài hơn), giới hạn trong
    // [MIN, MAX] để không dồn cục (quá nhanh) lẫn không bắt khách chờ vô ích (đoạn rất dài không nên kéo hiệu ứng quá lâu).
    function segmentDelay(shownText) {
      return clamp(SEGMENT_DELAY_MIN_MS + String(shownText || '').length * SEGMENT_DELAY_MS_PER_CHAR, SEGMENT_DELAY_MIN_MS, SEGMENT_DELAY_MAX_MS);
    }

    // Tin trả lời MỚI của bot có nhiều bong bóng: hiện TUẦN TỰ (bong bóng đầu hiện ngay, các bong bóng sau cách nhau 1 nhịp "đang gõ" ngắn) thay vì
    // đổ hết cùng lúc — cảm giác agent đang gõ từng ý một, giống người thật trả lời nhiều tin nhắn liên tiếp. Chỉ 1 bong bóng (hoặc lỗi/không phải
    // bot) thì hiện ngay như addBubble, không có nhịp chờ nào. Trả Promise resolve khi đã hiện xong hết (không ai cần chờ; tiện cho test).
    function addBubbleSequential(role, text, isError) {
      var segs = role === 'bot' && !isError ? splitSegments(text) : [];
      if (segs.length <= 1) { addBubble(role, text, isError); return Promise.resolve(); }
      return new Promise(function (resolve) {
        var i = 0;
        function next() {
          var bubble = appendSegmentBubble(role, segs[i++], text, isError);
          if (i >= segs.length) { resolve(); return; }
          var typingRow = el('div', 'row bot');
          var typingBubble = el('div', 'bubble typing');
          typingBubble.innerHTML = '<i></i><i></i><i></i>';
          typingRow.appendChild(typingBubble);
          msgsBox.appendChild(typingRow);
          msgsBox.scrollTop = msgsBox.scrollHeight;
          setTimeout(function () {
            typingRow.parentNode && typingRow.parentNode.removeChild(typingRow);
            next();
          }, segmentDelay(bubble.textContent));
        }
        next();
      });
    }

    // Bong bóng "đang làm việc" thay cho 3 chấm trơ: câu đệm hiện NGAY khi gửi (không phải từ server, không lưu vào lịch sử), rồi mỗi bước THẬT agent báo
    // (step(code) -> cfg.steps[code]) thành 1 dòng; 3 chấm nhấp nháy ở cuối. Không có câu đệm/chữ bước trong cấu hình thì chỉ còn 3 chấm như cũ.
    function showWork() {
      var row = el('div', 'row bot');
      var bubble = el('div', 'bubble work');
      var fillers = cfg.fillers || [];
      if (fillers.length) bubble.appendChild(el('div', 'work-filler', String(fillers[Math.floor(Math.random() * fillers.length)])));
      var stepsBox = el('div');
      var dots = el('div', 'dots');
      dots.innerHTML = '<i></i><i></i><i></i>';
      bubble.appendChild(stepsBox);
      bubble.appendChild(dots);
      row.appendChild(bubble);
      msgsBox.appendChild(row);
      msgsBox.scrollTop = msgsBox.scrollHeight;
      var last = null;
      return {
        step: function (code) {
          var text = cfg.steps && cfg.steps[code];
          if (!text || code === last) return;
          last = code;
          var previous = stepsBox.lastChild;
          if (previous) previous.className = 'work-step';
          stepsBox.appendChild(el('div', 'work-step now', String(text)));
          msgsBox.scrollTop = msgsBox.scrollHeight;
        },
        // Lượt quá lâu: báo đã nhận yêu cầu, xong sẽ gửi (trả lời bất đồng bộ) — khách được làm việc khác trong lúc chờ
        slow: function () {
          if (cfg.slow) bubble.insertBefore(el('div', 'work-filler', String(cfg.slow)), stepsBox);
          msgsBox.scrollTop = msgsBox.scrollHeight;
        },
        remove: function () { row.parentNode && row.parentNode.removeChild(row); }
      };
    }

    // Hộp xác nhận trong khung chat: resolve(true) khi khách bấm Đồng ý, resolve(false) khi bấm Huỷ. Không bao giờ tự đồng ý.
    function askConfirm(label, params) {
      return new Promise(function (resolve) {
        var lines = [cfg.confirm_prompt, String(label || '')];
        Object.keys(params || {}).forEach(function (k) { lines.push(k + ': ' + params[k]); });
        var bubble = addBubble('bot', lines.join('\n'));
        var box = el('div', 'confirm-actions');
        var yes = el('button', 'confirm-btn yes', cfg.confirm_yes), no = el('button', 'confirm-btn', cfg.confirm_no);
        yes.type = no.type = 'button';
        function choose(value) { yes.disabled = no.disabled = true; resolve(value); }
        yes.addEventListener('click', function () { choose(true); });
        no.addEventListener('click', function () { choose(false); });
        box.appendChild(yes); box.appendChild(no);
        bubble.appendChild(box);
        if (!panel.classList.contains('open')) launcher.classList.add('unread');
        msgsBox.scrollTop = msgsBox.scrollHeight;
      });
    }

    // ---- Tệp đính kèm (module "Đọc tài liệu") ----
    // files: [{ name, id, status: 'reading'|'ready'|'failed', error }]. Tệp được tải lên NGAY khi chọn; server đọc nền (PDF/ảnh có thể mất vài chục giây), widget hỏi trạng
    // thái tới khi xong. Chỉ tệp 'ready' mới được gửi kèm tin (attachment_ids); tin không gửi được khi còn tệp đang đọc.
    var files = [];
    var ATTACH_POLL_MS = 1500, ATTACH_MAX_FAILURES = 5, ATTACH_MAX_WAIT_MS = 20 * 60 * 1000;

    function attachEnabled() { return !!(opts.attach && cfg.attachments && cfg.attachments.enabled); }
    function attachText(key, vars) {
      var text = String((cfg.attach && cfg.attach[key]) || '');
      Object.keys(vars || {}).forEach(function (k) { text = text.replace('{' + k + '}', vars[k]); });
      return text;
    }
    function isReading(f) { return f.status === 'reading'; }
    function readyFiles() { return files.filter(function (f) { return f.status === 'ready'; }); }

    function renderChips() {
      chips.innerHTML = '';
      files.forEach(function (f) {
        var chip = el('span', 'chip ' + f.status);
        chip.appendChild(el('span', 'n', f.name));
        var note = f.status === 'reading' ? attachText('reading') : f.status === 'failed' ? (f.error || attachText('failed')) : (f.truncated ? attachText('truncated') : '');
        if (note) chip.appendChild(el('span', null, '· ' + note));
        var remove = el('button', null, '×');
        remove.type = 'button';
        remove.setAttribute('aria-label', attachText('remove'));
        remove.addEventListener('click', function () { files = files.filter(function (x) { return x !== f; }); f.removed = true; renderChips(); });
        chip.appendChild(remove);
        chips.appendChild(chip);
      });
      chips.classList.toggle('has', files.length > 0);
      msgsBox.scrollTop = msgsBox.scrollHeight;
    }

    function pollFile(f) {
      var failures = 0, deadline = Date.now() + ATTACH_MAX_WAIT_MS;
      function fail(message) { f.status = 'failed'; f.error = message; renderChips(); }
      function tick() {
        if (f.removed) return;
        if (Date.now() > deadline) return fail(attachText('failed'));
        opts.attach.status(f.id).then(function (r) {
          failures = 0;
          if (f.removed) return;
          if (r.status === 'ready') { f.status = 'ready'; f.truncated = !!r.truncated; renderChips(); }
          else if (r.status === 'failed') fail(r.error);
          else setTimeout(tick, ATTACH_POLL_MS);
        }).catch(function (err) {
          if (err && err.status === 404) return fail(attachText('failed'));
          if (++failures >= ATTACH_MAX_FAILURES) return fail(attachText('failed'));
          setTimeout(tick, ATTACH_POLL_MS);
        });
      }
      tick();
    }

    // Kiểm tra phía trình duyệt CHỈ để phản hồi nhanh; server kiểm lại tất cả (cỡ, đuôi, chữ ký nội dung, giới hạn số tệp)
    function addFiles(list) {
      var accept = String((cfg.attachments && cfg.attachments.accept) || '').toLowerCase().split(',');
      Array.prototype.forEach.call(list, function (file) {
        var dot = file.name.lastIndexOf('.'), ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : '';
        if (accept.indexOf(ext) < 0) { addBubble('bot', attachText('unsupported') + ' (' + file.name + ')', true); return; }
        if (cfg.attachments.max_bytes && file.size > cfg.attachments.max_bytes) { addBubble('bot', attachText('too_big', { mb: Math.floor(cfg.attachments.max_bytes / 1048576) }) + ' (' + file.name + ')', true); return; }
        if (files.length >= (cfg.attachments.max_files || 3)) { addBubble('bot', attachText('too_many', { n: cfg.attachments.max_files || 3 }), true); return; }
        var f = { name: file.name, id: null, status: 'reading', error: '' };
        files.push(f);
        renderChips();
        Promise.resolve(opts.attach.upload(file)).then(function (r) {
          f.id = r.id;
          if (r.status === 'ready') { f.status = 'ready'; renderChips(); } else pollFile(f);
        }).catch(function (err) {
          f.status = 'failed';
          f.error = err && err.fromServer ? err.message : attachText('failed');
          renderChips();
        });
      });
    }
    attachBtn.addEventListener('click', function () { if (!attachBtn.disabled) fileInput.click(); });
    fileInput.addEventListener('change', function () { addFiles(fileInput.files); fileInput.value = ''; });

    // Ô nhập + nút gửi + nút đính kèm cùng khoá khi đang chờ câu trả lời (thay cho các dòng "input.disabled = sendBtn.disabled = ..." rải rác)
    function setBusy(busy) { input.disabled = sendBtn.disabled = attachBtn.disabled = busy; }

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
      var canAttach = attachEnabled();
      attachBtn.style.display = canAttach ? 'flex' : 'none';
      attachBtn.title = attachText('button');
      attachBtn.setAttribute('aria-label', attachText('button'));
      if (canAttach) fileInput.accept = String(cfg.attachments.accept || '');
      else if (files.length) { files = []; renderChips(); }
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
      if (files.some(isReading)) return;   // còn tệp đang đọc: chờ xong (chip đang báo "Đang đọc tệp...")
      var attached = readyFiles();
      if ((!text && !attached.length) || input.disabled) return;
      var history = messages.slice(-HISTORY_TURNS);
      var shown = text, label = attached.length ? '📎 ' + attached.map(function (f) { return f.name; }).join(', ') : '';
      if (label) shown = text ? text + '\n\n' + label : label;   // cùng nhãn server lưu vào tin (widget/service._accept_message) nên khớp khi tải lại lịch sử
      var sentFiles = files;
      files = [];
      renderChips();
      input.value = '';
      setBusy(true);
      addBubble('user', shown);
      messages.push({ role: 'user', content: shown });
      var work = showWork();
      var wasSlow = false;
      // Quá chậm: báo đã nhận yêu cầu và mở lại ô nhập — khách hỏi tiếp/lướt web, câu trả lời sẽ được gửi lại khi xong (lượt sau xếp hàng ở server).
      // Kỹ thuật "trả lời bất đồng bộ" tắt (cfg.async_enabled=false) -> không đặt nhịp này: ô nhập chờ đóng như bình thường tới khi có câu trả lời.
      var slowTimer = cfg.async_enabled ? setTimeout(function () {
        wasSlow = true;
        work.slow();
        setBusy(false);
      }, Math.max(1, Number(cfg.slow_after_seconds) || 10) * 1000) : null;

      // hooks: send() nhận {step, waitMs} để báo tiến trình thật lên bong bóng và biết hạn chờ tối đa của 1 lượt
      Promise.resolve(opts.send(text, history, {
        step: work.step, waitMs: (Number(cfg.wait_seconds) || 90) * 1000, attachments: attached.map(function (f) { return f.id; })
      }))
        .then(function (res) {
          clearTimeout(slowTimer);
          work.remove();
          if (res.reply == null) return;   // nhân viên đang tiếp quản (hoặc tin này đã hiện qua đường dự phòng): không thêm tin bot
          if (wasSlow && cfg.late_reply) addBubble('bot', cfg.late_reply);   // báo kết quả muộn (không lưu vào lịch sử)
          messages.push({ role: 'bot', content: res.reply });
          addBubbleSequential('bot', res.reply);   // nhiều đoạn -> hiện tuần tự; không chờ hiện xong mới mở lại ô nhập (bên dưới)
          if (!panel.classList.contains('open')) launcher.classList.add('unread');   // khách đã đóng khung chat: báo có tin mới
        })
        .catch(function (err) {
          clearTimeout(slowTimer);
          work.remove();
          addBubble('bot', err && err.fromServer ? err.message : cfg.error, true);
          // Server từ chối tin (4xx: tệp chưa hợp lệ, giới hạn tần suất...) thì CHƯA dùng tệp: trả lại danh sách tệp để khách gửi lại. Lỗi khác (5xx, mạng) tệp có thể đã được ghi nhận nên không trả lại.
          if (err && err.fromServer && err.status >= 400 && err.status < 500 && sentFiles.length && !files.length) { files = sentFiles; renderChips(); }
        })
        .then(function () {
          if (opts.onMessages) opts.onMessages(messages);
          setBusy(false);
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
      // Câu trả lời của bot đến muộn qua đường dự phòng (khách tải lại trang / mạng rớt giữa chừng khi lượt bất đồng bộ đang chạy)
      receiveBot: function (text) {
        messages.push({ role: 'bot', content: text });
        addBubbleSequential('bot', text);
        if (!panel.classList.contains('open')) launcher.classList.add('unread');
        if (opts.onMessages) opts.onMessages(messages);
      },
      // Hành động trên website (Phase M): hộp xác nhận + thông báo ngắn (không lưu vào lịch sử hội thoại)
      confirm: askConfirm,
      notice: function (text) { addBubble('bot', text); },
      // Xoá cuộc trò chuyện, chỉ còn lời chào (nút "Làm mới" ở khung xem trước)
      reset: function () {
        messages.length = 0;
        while (msgsBox.childNodes.length > 1) msgsBox.removeChild(msgsBox.lastChild);
        files = [];
        renderChips();
        setBusy(false);
      },
      destroy: function () { host.parentNode && host.parentNode.removeChild(host); }
    };
  }

  if (!window.AIChatbotWidget) window.AIChatbotWidget = { create: createWidget, postJson: postJson, getJson: getJson, followTurn: followTurn };

  // ---- Chế độ nhúng vào website khách: tự chạy khi thẻ script có data-bot-id ----
  var script = document.currentScript;
  var botId = script && script.getAttribute('data-bot-id');
  if (!botId || window['__aichatbot_' + botId]) return;
  window['__aichatbot_' + botId] = true;

  var apiBase = new URL(script.src).origin + '/widget/api/' + encodeURIComponent(botId);
  var STORE_KEY = 'aichatbot:' + botId;
  var MAX_STORED = 40;

  var MAX_SHOWN = 100;
  // shown = id các tin server đã hiện trong khung chat (khử trùng: 1 tin có thể tới cả qua kênh hỏi lượt lẫn kênh hỏi định kỳ)
  var state = { visitorId: '', conversationId: null, lastId: 0, messages: [], shown: [] };
  try {
    var saved = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    if (saved && typeof saved === 'object') {
      state.visitorId = String(saved.visitorId || '');
      state.conversationId = typeof saved.conversationId === 'number' ? saved.conversationId : null;
      state.lastId = typeof saved.lastId === 'number' ? saved.lastId : 0;
      state.messages = Array.isArray(saved.messages) ? saved.messages : [];
      state.shown = Array.isArray(saved.shown) ? saved.shown.filter(function (n) { return typeof n === 'number'; }) : [];
    }
  } catch (e) { /* localStorage bị chặn: chạy không lưu */ }
  var pendingTurns = 0;   // số lượt bất đồng bộ đang chờ: trong lúc đó kênh hỏi lượt lo việc nhận câu trả lời, kênh hỏi định kỳ tạm nghỉ

  // true nếu id CHƯA từng hiện (và ghi nhận là đã hiện); false = đã hiện rồi, bỏ qua
  function markShown(id) {
    if (typeof id !== 'number') return true;
    if (state.shown.indexOf(id) !== -1) return false;
    state.shown.push(id);
    if (state.shown.length > MAX_SHOWN) state.shown.shift();
    return true;
  }
  if (!state.visitorId) {
    state.visitorId = (window.crypto && crypto.randomUUID) ? crypto.randomUUID().replace(/-/g, '') : String(Date.now()) + Math.random().toString(16).slice(2);
  }

  function persist() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        visitorId: state.visitorId,
        conversationId: state.conversationId,
        lastId: state.lastId,
        messages: state.messages.slice(-MAX_STORED),
        shown: state.shown
      }));
    } catch (e) { /* bỏ qua */ }
  }

  // Hỏi server tin nhân viên trả lời từ Inbox: chỉ khi đã có hội thoại và tab đang hiển thị
  var POLL_MS = 5000;
  function startPolling(widget) {
    var busy = false;
    function poll() {
      if (busy || !state.conversationId || document.hidden || pendingTurns > 0) return;
      busy = true;
      // include_bot=1: nhận cả câu trả lời của bot mà kênh hỏi lượt đã lỡ (tải lại trang / mạng rớt trong lúc lượt bất đồng bộ đang chạy)
      var qs = '?conversation_id=' + encodeURIComponent(state.conversationId) +
        '&visitor_id=' + encodeURIComponent(state.visitorId) + '&after_id=' + encodeURIComponent(state.lastId) + '&include_bot=1';
      fetch(apiBase + '/staff-messages' + qs)
        .then(function (r) { if (!r.ok) throw new Error('poll ' + r.status); return r.json(); })
        .then(function (data) {
          (data.messages || []).forEach(function (m) {
            if (!markShown(m.id)) return;
            if (m.sender === 'bot') widget.receiveBot(String(m.content)); else widget.receiveStaff(String(m.content));
          });
          if (typeof data.last_id === 'number') state.lastId = Math.max(state.lastId, data.last_id);
          persist();
        })
        .catch(function () { /* mạng chập chờn: lần hỏi sau thử lại */ })
        .then(function () { busy = false; });
    }
    setInterval(poll, POLL_MS);
    document.addEventListener('visibilitychange', poll);
  }

  // ---- Hành động trên website của khách (Phase M): server đẩy lệnh qua Socket.IO (namespace /widget), widget thực thi trên DOM rồi báo kết quả qua HTTP POST ----
  var handledActions = {};

  function loadSocketIo() {
    // Dùng bản socket.io client do chính server widget phục vụ; giữ nguyên window.io của website khách (không ghi đè thư viện của họ)
    return new Promise(function (resolve, reject) {
      if (window.__aichatbotSio) return resolve(window.__aichatbotSio);
      var previous = window.io, tag = document.createElement('script');
      tag.async = true;
      tag.src = new URL(script.src).origin + '/static/js/socket.io.min.js';
      tag.onload = function () {
        var lib = window.io;
        window.io = previous;
        if (typeof lib !== 'function') return reject(new Error('socket.io client missing'));
        window.__aichatbotSio = lib;
        resolve(lib);
      };
      tag.onerror = function () { reject(new Error('cannot load socket.io client')); };
      document.head.appendChild(tag);
    });
  }

  function normHost(host) { return String(host || '').toLowerCase().replace(/^www\./, ''); }
  function sameSite(host, expect) {
    host = normHost(host); expect = normHost(expect);
    return !!expect && (host === expect || host.slice(-(expect.length + 1)) === '.' + expect);
  }

  function query(root, selector) {
    try { return root.querySelector(selector); } catch (e) { return null; }
  }

  // Đặt giá trị như người dùng gõ: dùng setter gốc của prototype (framework như React theo dõi giá trị) rồi phát input/change
  function setValue(node, value) {
    var proto = node.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : node.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    var desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if (desc && desc.set) desc.set.call(node, value); else node.value = value;
    node.dispatchEvent(new Event('input', { bubbles: true }));
    node.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function fillFields(spec, params, scope) {
    var filled = 0;
    var fields = spec.fields || [];
    for (var i = 0; i < fields.length; i++) {
      var value = params[fields[i].value_from_slot];
      if (value == null || value === '') continue;
      var node = (scope && query(scope, fields[i].selector)) || query(document, fields[i].selector);
      if (!node) return { status: 'failed', reason: 'element_not_found' };
      setValue(node, String(value));
      filled++;
    }
    return { status: 'done', filled: filled };
  }

  // Thực thi 1 hành động theo selector_spec đã được server kiểm chứng. Trả { status, reason, data, navigate, click }. Lỗi JS bất ngờ được BÁO về (reason 'error'), không nuốt.
  function runAction(msg) {
    var spec = msg.spec || {}, params = msg.params || {};
    try {
      if (msg.type === 'navigate') {
        if (spec.selector) {
          var link = query(document, spec.selector);
          if (!link) return { status: 'failed', reason: 'element_not_found' };
          var target = link.href ? new URL(link.href, location.href) : null;
          if (target && (!/^https?:$/.test(target.protocol) || !sameSite(target.hostname, msg.expect_host))) return { status: 'failed', reason: 'domain_mismatch' };
          return { status: 'done', click: link };
        }
        var url = new URL(spec.href, location.href);
        if (!/^https?:$/.test(url.protocol) || !sameSite(url.hostname, msg.expect_host)) return { status: 'failed', reason: 'domain_mismatch' };
        return { status: 'done', navigate: url.href };
      }
      if (msg.type === 'read_info') {
        var reader = query(document, spec.selector);
        if (!reader) return { status: 'failed', reason: 'element_not_found' };
        var attr = spec.attribute || 'text';
        var text = attr === 'text' ? (reader.textContent || '') : (reader.getAttribute(attr) || '');
        return { status: 'done', data: text.replace(/\s+/g, ' ').trim().slice(0, 500) };
      }
      if (msg.type === 'fill_form') {
        var form = query(document, spec.form_selector);
        if (!form) return { status: 'failed', reason: 'element_not_found' };
        var filledForm = fillFields(spec, params, form);
        if (filledForm.status !== 'done') return filledForm;
        if (spec.submit) {
          var submitter = spec.submit_selector ? (query(form, spec.submit_selector) || query(document, spec.submit_selector)) : null;
          if (spec.submit_selector && !submitter) return { status: 'failed', reason: 'element_not_found' };
          if (submitter) submitter.click(); else if (form.requestSubmit) form.requestSubmit(); else form.submit();
        }
        return { status: 'done' };
      }
      // click / add_to_cart
      var button = query(document, spec.selector);
      if (!button) return { status: 'failed', reason: 'element_not_found' };
      var pre = fillFields(spec, params, null);
      if (pre.status !== 'done') return pre;
      button.click();
      return { status: 'done' };
    } catch (e) {
      return { status: 'failed', reason: 'error', data: String((e && e.message) || e).slice(0, 200) };
    }
  }

  function reportAction(msg, result) {
    return postJson(apiBase + '/actions/' + encodeURIComponent(msg.id) + '/result', {
      visitor_id: state.visitorId, token: msg.token, status: result.status, reason: result.reason || null, data: result.data || null
    }).catch(function (err) { if (window.console) console.warn('[aichatbot] không gửi được kết quả hành động:', err.message); });
  }

  function handleAction(widget, config, msg) {
    if (!msg || typeof msg.id !== 'number' || handledActions[msg.id]) return;
    handledActions[msg.id] = true;
    // Chỉ thực thi trên đúng website đã được phân tích (selector chỉ có nghĩa ở đó); website khác thì báo về, không chạm DOM
    if (!sameSite(location.hostname, msg.expect_host)) { reportAction(msg, { status: 'failed', reason: 'domain_mismatch' }); return; }
    var approved = msg.confirm ? widget.confirm(msg.label, msg.params) : Promise.resolve(true);
    approved.then(function (ok) {
      if (!ok) { widget.notice(config.confirm_cancelled); return reportAction(msg, { status: 'failed', reason: 'cancelled_by_customer' }); }
      var result = runAction(msg);
      if (result.status !== 'done' && result.reason !== 'domain_mismatch') widget.notice(config.action_failed);
      // Báo kết quả TRƯỚC khi điều hướng (đổi trang sẽ huỷ các request đang chạy)
      return reportAction(msg, result).then(function () {
        if (result.status === 'done' && result.click) result.click.click();
        else if (result.status === 'done' && result.navigate) window.location.assign(result.navigate);
      });
    });
  }

  function startActions(widget, config) {
    loadSocketIo().then(function (io) {
      var socket = io(new URL(script.src).origin + '/widget', {
        transports: ['websocket', 'polling'], auth: { public_id: botId, visitor_id: state.visitorId }
      });
      socket.on('widget_action', function (msg) { handleAction(widget, config, msg); });
    }).catch(function (err) { if (window.console) console.warn('[aichatbot] hành động trên website không khả dụng:', err.message); });
  }

  fetch(apiBase + '/config')
    .then(function (r) { if (!r.ok) throw new Error('config ' + r.status); return r.json(); })
    .then(function (config) {
      var widget = createWidget(config, {
        messages: state.messages,
        // async: true = server lưu tin rồi trả ngay mã lượt; tiến trình thật + câu trả lời lấy qua followTurn (không giữ kết nối chờ cả chục giây).
        // Kỹ thuật "trả lời bất đồng bộ" tắt ở Bước 1 (config.async_enabled === false) -> không gửi cờ này: server đi đường đồng bộ cũ (chờ tới khi
        // có câu trả lời mới trả về), followTurn() bên dưới tự nhận ra response không có turn_id và trả nguyên vẹn, không hỏi thêm lượt nào.
        send: function (text, history, hooks) {
          pendingTurns++;
          var body = { message: text, visitor_id: state.visitorId, conversation_id: state.conversationId };
          if (hooks && hooks.attachments && hooks.attachments.length) body.attachment_ids = hooks.attachments;
          if (config.async_enabled !== false) body.async = true;
          return postJson(apiBase + '/messages', body).then(function (data) {
            state.conversationId = data.conversation_id;
            state.visitorId = data.visitor_id || state.visitorId;
            if (typeof data.last_message_id === 'number') state.lastId = Math.max(state.lastId, data.last_message_id);
            return followTurn(data, function (turnId, after) {
              return getJson(apiBase + '/turns/' + encodeURIComponent(turnId) + '?visitor_id=' + encodeURIComponent(state.visitorId) + '&after=' + after);
            }, hooks);
          }).then(function (res) {
            // Có turn_id = câu trả lời đi qua kênh hỏi lượt: ghi nhận đã hiện; đã hiện qua kênh khác rồi thì không thêm lần nữa
            if (res.turn_id && res.reply != null && !markShown(res.last_message_id)) return extend(extend({}, res), { reply: null });
            return res;
          }).then(function (res) { pendingTurns--; return res; }, function (err) { pendingTurns--; throw err; });
        },
        onMessages: persist,
        // Module "Đọc tài liệu": tải tệp lên (multipart) rồi hỏi trạng thái đọc; visitor_id/conversation_id để server gắn tệp đúng chủ
        attach: {
          upload: function (file) {
            var form = new FormData();
            form.append('file', file);
            form.append('visitor_id', state.visitorId);
            if (state.conversationId) form.append('conversation_id', state.conversationId);
            return fetch(apiBase + '/attachments', { method: 'POST', body: form }).then(readJson);
          },
          status: function (id) { return getJson(apiBase + '/attachments/' + encodeURIComponent(id) + '?visitor_id=' + encodeURIComponent(state.visitorId)); }
        }
      });
      startPolling(widget);
      startActions(widget, config);
    })
    .catch(function (err) {
      // Domain chưa được cấp phép hoặc bot không tồn tại: không hiển thị widget
      if (window.console) console.warn('[aichatbot] widget không khởi tạo được:', err.message);
    });
})();

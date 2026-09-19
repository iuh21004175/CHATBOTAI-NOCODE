/* Web Widget chatbot.
 *
 * Nhúng vào website:  <script src=".../widget/embed.js" data-bot-id="1"></script>
 * Dựng khung chat trong Shadow DOM (CSS của website khách không ảnh hưởng widget và ngược lại).
 * Mọi nội dung hiển thị đều đi qua textContent nên không có XSS từ câu trả lời của AI.
 *
 * Cùng 1 bộ dựng này còn được trang Xuất bản dùng cho khung "Xem trước trực tiếp":
 *   var w = AIChatbotWidget.create(config, { container: el, send: fn, open: true });
 *   w.update({ color: '#16A34A', size: 72 });   // mọi tuỳ chọn giao diện đều đi qua update()
 * Vì xem trước chạy đúng mã này nên không thể lệch với widget thật.
 *
 * config: { name, greeting, placeholder, send, error, icon (markup SVG), color (#RRGGBB),
 *           size (px), shape ('round'|'rounded'), position ('left'|'right'), window: { w, h } } */
(function () {
  var DEFAULTS = {
    name: '', greeting: '', placeholder: '', send: 'Send', error: 'Error',
    icon: '', color: '#1D4ED8', size: 56, shape: 'round', position: 'right', window: { w: 360, h: 540 }
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
    '.head span{flex-grow:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.close{background:none;border:none;color:#fff;font-size:20px;line-height:1;cursor:pointer;padding:0 2px}',
    '.msgs{flex-grow:1;overflow-y:auto;padding:14px;background:#F8FAFC;display:flex;flex-direction:column;gap:10px}',
    '.row{display:flex}.row.user{justify-content:flex-end}',
    '.bubble{max-width:82%;padding:9px 12px;font-size:13.5px;line-height:1.5;white-space:pre-wrap;word-break:break-word;background:#fff;border:1px solid #DCE7F5;border-radius:14px 14px 14px 3px;color:#0F172A}',
    '.user .bubble{background:var(--brand);border-color:var(--brand);color:#fff;border-radius:14px 14px 3px 14px}',
    '.bubble.err{background:#FEF2F2;border-color:#FCA5A5;color:#B91C1C}',
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
    var title = el('span');
    var closeBtn = el('button', 'close', '×');
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', 'Close');
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
      var bubble = el('div', 'bubble' + (isError ? ' err' : ''), text);
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

      host.style.setProperty('--brand', color);
      host.style.left = position === 'left' ? OFFSET + 'px' : 'auto';
      host.style.right = position === 'right' ? OFFSET + 'px' : 'auto';
      wrap.className = 'wrap ' + position + (wrap.classList.contains('mobile') ? ' mobile' : '');

      launcher.style.width = launcher.style.height = size + 'px';
      launcher.style.borderRadius = cfg.shape === 'rounded' ? '30%' : '50%';
      launcher.setAttribute('aria-label', cfg.name);
      // markup SVG do server cấp (bảng icon cố định), không phải dữ liệu người dùng nhập
      launcher.innerHTML = '<svg viewBox="0 0 24 24" width="' + glyph + '" height="' + glyph + '" fill="#fff" fill-rule="evenodd">' + (cfg.icon || '') + '</svg>';

      title.textContent = cfg.name;
      input.placeholder = cfg.placeholder;
      sendBtn.textContent = cfg.send;
      greetingBubble.textContent = cfg.greeting;

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

  var state = { visitorId: '', conversationId: null, messages: [] };
  try {
    var saved = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    if (saved && typeof saved === 'object') {
      state.visitorId = String(saved.visitorId || '');
      state.conversationId = typeof saved.conversationId === 'number' ? saved.conversationId : null;
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
        messages: state.messages.slice(-MAX_STORED)
      }));
    } catch (e) { /* bỏ qua */ }
  }

  fetch(apiBase + '/config')
    .then(function (r) { if (!r.ok) throw new Error('config ' + r.status); return r.json(); })
    .then(function (config) {
      createWidget(config, {
        messages: state.messages,
        send: function (text) {
          return postJson(apiBase + '/messages', {
            message: text, visitor_id: state.visitorId, conversation_id: state.conversationId
          }).then(function (data) {
            state.conversationId = data.conversation_id;
            state.visitorId = data.visitor_id || state.visitorId;
            return data;
          });
        },
        onMessages: persist
      });
    })
    .catch(function (err) {
      // Domain chưa được cấp phép hoặc bot không tồn tại: không hiển thị widget
      if (window.console) console.warn('[aichatbot] widget không khởi tạo được:', err.message);
    });
})();

/* ui-kit.js — thành phần giao diện dùng chung (jQuery) cho app do AI sinh (AB0). LLM chỉ GỌI các hàm này, không viết lại. Mọi dữ liệu người dùng được
 * chèn bằng .text() / thuộc tính an toàn — không bao giờ .html() với dữ liệu — nên các màn hình ráp từ UI Kit tự miễn nhiễm XSS. */
(function (window, $) {
  'use strict';

  function el(tag, props, text) {
    var $e = $('<' + tag + '>');
    if (props) { $e.attr(props); }
    if (text !== undefined && text !== null) { $e.text(text); }
    return $e;
  }

  var ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  function escapeHtml(value) { return String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, function (c) { return ESC[c]; }); }

  function toast(message, type) {
    var $t = el('div', { 'class': 'pf-toast pf-toast-' + (type || 'info'), role: 'status' }, message);
    $('#pf-toasts').append($t);
    setTimeout(function () { $t.fadeOut(200, function () { $t.remove(); }); }, 3500);
  }

  function errorMessage(err) { return (err && err.message) || 'Có lỗi xảy ra.'; }

  function modal(options) {
    var $overlay = el('div', { 'class': 'pf-overlay' });
    var $box = el('div', { 'class': 'pf-modal', role: 'dialog', 'aria-modal': 'true' });
    if (options.width) { $box.css('max-width', options.width); }
    var $head = el('div', { 'class': 'pf-modal-head' });
    $head.append(el('div', { 'class': 'pf-modal-title' }, options.title || ''));
    var $close = el('button', { type: 'button', 'class': 'pf-btn pf-btn-ghost', 'aria-label': 'Đóng' }, '×');
    $head.append($close);
    $box.append($head).append(el('div', { 'class': 'pf-modal-body' }).append(options.content));
    $overlay.append($box);
    function close() { $overlay.remove(); $(document).off('keydown.pfmodal'); if (options.onClose) { options.onClose(); } }
    $close.on('click', close);
    $overlay.on('mousedown', function (e) { if (e.target === $overlay[0]) { close(); } });
    $(document).on('keydown.pfmodal', function (e) { if (e.key === 'Escape') { close(); } });
    $('body').append($overlay);
    $overlay.find('input,select,textarea').first().trigger('focus');
    return { close: close };
  }

  function confirmDialog(message) {
    var deferred = $.Deferred();
    var $content = el('div');
    $content.append(el('p', null, message));
    var $actions = el('div', { 'class': 'pf-actions' });
    var $no = el('button', { type: 'button', 'class': 'pf-btn' }, 'Huỷ');
    var $yes = el('button', { type: 'button', 'class': 'pf-btn pf-btn-danger' }, 'Đồng ý');
    $actions.append($no).append($yes);
    $content.append($actions);
    var settled = false;
    var m = modal({ title: 'Xác nhận', content: $content, width: '420px', onClose: function () { if (!settled) { settled = true; deferred.resolve(false); } } });
    $no.on('click', function () { m.close(); });
    $yes.on('click', function () { settled = true; deferred.resolve(true); m.close(); });
    return deferred.promise();
  }

  function formatValue(value, field) {
    if (value === null || value === undefined || value === '') { return ''; }
    switch (field && field.type) {
      case 'boolean': return value ? 'Có' : 'Không';
      case 'decimal': case 'integer': var n = Number(value); return isNaN(n) ? String(value) : n.toLocaleString('vi-VN');
      case 'date': return String(value).slice(0, 10).split('-').reverse().join('/');
      case 'datetime': var d = new Date(value); return isNaN(d.getTime()) ? String(value) : d.toLocaleString('vi-VN');
      default: return String(value);
    }
  }

  function collectionOf(schema, name) {
    for (var i = 0; i < schema.collections.length; i++) { if (schema.collections[i].name === name) { return schema.collections[i]; } }
    return null;
  }

  // Nhãn hiển thị của bản ghi được ref tới = giá trị field chữ đầu tiên (hoặc #id). Tải tối đa 100 bản ghi/collection, cache theo trang.
  var refCache = {};
  function refOptions(schema, refName, force) {
    if (force || !refCache[refName]) {
      refCache[refName] = PF.records.list(refName, { per_page: 100, sort: 'id', order: 'asc' }).then(function (page) {
        var coll = collectionOf(schema, refName);
        var labelField = null;
        $.each(coll ? coll.fields : [], function (_, f) { if (!labelField && (f.type === 'string' || f.type === 'text')) { labelField = f.name; } });
        return $.map(page.items, function (item) { return { id: item.id, label: (labelField && item[labelField]) ? String(item[labelField]) : '#' + item.id }; });
      });
    }
    return refCache[refName];
  }

  function buildForm(collection, options) {
    options = options || {};
    var $form = el('form', { 'class': 'pf-form', novalidate: 'novalidate' });
    var $actions = el('div', { 'class': 'pf-actions' });
    PF.schema().then(function (schema) {
      var coll = collectionOf(schema, collection);
      if (!coll) { $form.append(el('p', { 'class': 'pf-error' }, 'Không tìm thấy dữ liệu "' + collection + '".')); return; }
      var inputs = {};
      $.each(coll.fields, function (_, f) {
        var $row = el('label', { 'class': 'pf-field' });
        $row.append(el('span', { 'class': 'pf-label' }, f.label + (f.required ? ' *' : '')));
        var $input;
        var value = options.record ? options.record[f.name] : null;
        if (f.type === 'text') { $input = el('textarea', { rows: 3 }); }
        else if (f.type === 'boolean') { $input = el('input', { type: 'checkbox' }); }
        else if (f.type === 'ref') { $input = el('select'); $input.append(el('option', { value: '' }, '— Chọn —')); }
        else {
          var htmlType = { integer: 'number', decimal: 'number', date: 'date', datetime: 'datetime-local' }[f.type] || 'text';
          $input = el('input', { type: htmlType });
          if (f.type === 'decimal') { $input.attr('step', 'any'); }
          if (f.type === 'integer') { $input.attr('step', '1'); }
        }
        if (f.type === 'ref') {
          refOptions(schema, f.ref, true).then(function (opts) {
            $.each(opts, function (_i, o) { $input.append(el('option', { value: o.id }, o.label)); });
            if (value !== null && value !== undefined) { $input.val(String(value)); }
          });
        } else if (f.type === 'boolean') { $input.prop('checked', !!value); }
        else if (value !== null && value !== undefined) { $input.val(f.type === 'datetime' ? String(value).slice(0, 16) : String(value)); }
        $row.append($input).append(el('span', { 'class': 'pf-error' }));
        $form.append($row);
        inputs[f.name] = { field: f, $input: $input, $error: $row.find('.pf-error') };
      });
      var $submit = el('button', { type: 'submit', 'class': 'pf-btn pf-btn-primary' }, options.submitLabel || 'Lưu');
      if (options.onCancel) { $actions.append(el('button', { type: 'button', 'class': 'pf-btn' }, 'Huỷ').on('click', options.onCancel)); }
      $actions.append($submit);
      $form.append($actions);
      $form.on('submit', function (e) {
        e.preventDefault();
        var data = {};
        $.each(inputs, function (name, ref) {
          ref.$error.text('');
          data[name] = ref.field.type === 'boolean' ? ref.$input.prop('checked') : ref.$input.val();
        });
        $submit.prop('disabled', true);
        $.when(options.onSubmit(data)).fail(function (err) {
          $.each((err && err.errors) || {}, function (name, message) { if (inputs[name]) { inputs[name].$error.text(message); } });
          toast(errorMessage(err), 'error');
        }).always(function () { $submit.prop('disabled', false); });
      });
    });
    return $form;
  }

  function crudTable($container, options) {
    var collection = options.collection;
    var state = { page: 1, q: '', sort: 'id', order: 'desc' };
    var pageSize = options.pageSize || 20;
    var canCreate = options.canCreate !== false && PF.can(collection, 'create'), canEdit = options.canEdit !== false && PF.can(collection, 'update'),
        canDelete = options.canDelete !== false && PF.can(collection, 'delete');
    var $root = el('div', { 'class': 'pf-crud' });
    var $toolbar = el('div', { 'class': 'pf-toolbar' });
    var $search = el('input', { type: 'search', placeholder: 'Tìm kiếm...', 'class': 'pf-search' });
    $toolbar.append($search);
    var $add = el('button', { type: 'button', 'class': 'pf-btn pf-btn-primary' }, '+ Thêm mới');
    if (canCreate) { $toolbar.append($add); }
    var $tableWrap = el('div', { 'class': 'pf-table-wrap' });
    var $pager = el('div', { 'class': 'pf-pager' });
    $root.append($toolbar).append($tableWrap).append($pager);
    $container.append($root);

    var schema, coll, columns, refLabels = {};

    function cellText(item, f) {
      if (f.type === 'ref') { var map = refLabels[f.ref] || {}; return map[item[f.name]] || (item[f.name] ? '#' + item[f.name] : ''); }
      return formatValue(item[f.name], f);
    }

    function openForm(record) {
      var m;
      var $form = buildForm(collection, {
        record: record, onCancel: function () { m.close(); },
        onSubmit: function (data) {
          var call = record ? PF.records.update(collection, record.id, data) : PF.records.create(collection, data);
          return call.then(function () { m.close(); toast('Đã lưu', 'success'); load(); });
        }
      });
      m = modal({ title: (record ? 'Sửa ' : 'Thêm ') + coll.label.toLowerCase(), content: $form });
    }

    function render(page) {
      var $table = el('table', { 'class': 'pf-table' });
      var $head = el('tr');
      $.each(columns, function (_, f) {
        var mark = state.sort === f.name ? (state.order === 'asc' ? ' ▲' : ' ▼') : '';
        $head.append(el('th', { 'class': 'pf-sortable', tabindex: 0 }, f.label + mark).on('click', function () {
          state.order = state.sort === f.name && state.order === 'asc' ? 'desc' : 'asc'; state.sort = f.name; state.page = 1; load();
        }));
      });
      if (canEdit || canDelete) { $head.append(el('th', null, '')); }
      $table.append(el('thead').append($head));
      var $body = el('tbody');
      if (!page.items.length) { $body.append(el('tr').append(el('td', { colspan: columns.length + 1, 'class': 'pf-muted' }, 'Chưa có dữ liệu.'))); }
      $.each(page.items, function (_, item) {
        var $tr = el('tr');
        $.each(columns, function (_c, f) { $tr.append(el('td', null, cellText(item, f))); });
        if (canEdit || canDelete) {
          var $cell = el('td', { 'class': 'pf-row-actions' });
          if (canEdit) { $cell.append(el('button', { type: 'button', 'class': 'pf-btn' }, 'Sửa').on('click', function () { openForm(item); })); }
          if (canDelete) {
            $cell.append(el('button', { type: 'button', 'class': 'pf-btn pf-btn-danger' }, 'Xoá').on('click', function () {
              UI.confirm('Xoá bản ghi này?').then(function (ok) {
                if (ok) { PF.records.remove(collection, item.id).then(function () { toast('Đã xoá', 'success'); load(); }, function (err) { toast(errorMessage(err), 'error'); }); }
              });
            }));
          }
          $tr.append($cell);
        }
        $body.append($tr);
      });
      $table.append($body);
      $tableWrap.empty().append($table);
      var pages = Math.max(1, Math.ceil(page.total / page.per_page));
      $pager.empty().append(el('span', { 'class': 'pf-muted' }, page.total + ' bản ghi'));
      $pager.append(el('button', { type: 'button', 'class': 'pf-btn' }, '‹').prop('disabled', page.page <= 1).on('click', function () { state.page--; load(); }));
      $pager.append(el('span', null, 'Trang ' + page.page + '/' + pages));
      $pager.append(el('button', { type: 'button', 'class': 'pf-btn' }, '›').prop('disabled', page.page >= pages).on('click', function () { state.page++; load(); }));
    }

    function load() {
      return PF.records.list(collection, { page: state.page, per_page: pageSize, q: state.q, sort: state.sort, order: state.order }).then(function (page) {
        var refs = {};
        $.each(columns, function (_, f) { if (f.type === 'ref') { refs[f.ref] = true; } });
        var waits = $.map(Object.keys(refs), function (name) {
          return refOptions(schema, name, true).then(function (opts) { refLabels[name] = {}; $.each(opts, function (_i, o) { refLabels[name][o.id] = o.label; }); });
        });
        return $.when.apply($, waits).then(function () { render(page); });
      }, function (err) { $tableWrap.empty().append(el('p', { 'class': 'pf-error' }, errorMessage(err))); });
    }

    PF.schema().then(function (s) {
      schema = s; coll = collectionOf(s, collection);
      if (!coll) { $root.append(el('p', { 'class': 'pf-error' }, 'Không tìm thấy dữ liệu "' + collection + '".')); return; }
      columns = $.grep(coll.fields, function (f) { return f.type !== 'text' && (!options.columns || options.columns.indexOf(f.name) >= 0); });
      if (options.columns) { columns.sort(function (a, b) { return options.columns.indexOf(a.name) - options.columns.indexOf(b.name); }); }
      $add.on('click', function () { openForm(null); });
      var timer = null;
      $search.on('input', function () { clearTimeout(timer); timer = setTimeout(function () { state.q = $search.val(); state.page = 1; load(); }, 300); });
      load();
    }, function (err) { $root.append(el('p', { 'class': 'pf-error' }, errorMessage(err))); });
    return { reload: function () { return load && load(); } };
  }

  function stats($container, items) {
    var $grid = el('div', { 'class': 'pf-stats' });
    $container.append($grid);
    $.each(items || [], function (_, item) {
      var $card = el('div', { 'class': 'pf-stat' });
      var $value = el('div', { 'class': 'pf-stat-value' }, '…');
      $card.append($value).append(el('div', { 'class': 'pf-stat-label' }, item.label));
      $grid.append($card);
      PF.records.aggregate(item.collection, { agg: item.agg, field: item.field }).then(function (res) {
        $value.text(formatValue(res.value, { type: 'decimal' }) || '0');
      }, function (err) { $value.text('—'); $card.attr('title', errorMessage(err)); });
    });
    return $grid;
  }

  function card($container, title) {
    var $c = el('section', { 'class': 'pf-card' });
    if (title) { $c.append(el('h2', { 'class': 'pf-card-title' }, title)); }
    var $body = el('div', { 'class': 'pf-card-body' });
    $c.append($body);
    $container.append($c);
    return $body;
  }

  // Ẩn mục menu của màn hình table mà người xem không có quyền view (chỉ hiển thị; server vẫn chặn API).
  $(function () { $('.pf-nav-link[data-collection]').each(function () { if (!PF.can($(this).attr('data-collection'), 'view')) { $(this).hide(); } }); });

  window.UI = { escape: escapeHtml, toast: toast, modal: modal, confirm: confirmDialog, form: buildForm, crudTable: crudTable, stats: stats, card: card, format: formatValue };
})(window, window.jQuery);

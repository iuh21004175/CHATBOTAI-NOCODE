/* platform-sdk.js — lớp gọi backend CỐ ĐỊNH của nền tảng cho app do AI sinh (AB0). Code LLM sinh KHÔNG được sửa/thay file này và không bao giờ nối thẳng DB:
 * mọi thao tác dữ liệu đi qua đây tới /apps/<id>/_api/..., nơi server kiểm tra đăng nhập, quyền, kiểu dữ liệu và hạn mức.
 * Token CSRF lấy từ <meta name="pf-csrf"> do server điền khi phục vụ trang (code sinh ra không đọc/gửi token trực tiếp). */
(function (window, $) {
  'use strict';
  var appId = $('meta[name="pf-app"]').attr('content');
  var csrf = $('meta[name="pf-csrf"]').attr('content');
  var base = '/apps/' + encodeURIComponent(appId) + '/_api';

  function request(method, path, data) {
    var deferred = $.Deferred();
    var options = { url: base + path, method: method, dataType: 'json', headers: { 'X-CSRF-Token': csrf } };
    if (method === 'GET') {
      options.data = data;
    } else {
      options.data = JSON.stringify(data || {});
      options.contentType = 'application/json';
    }
    $.ajax(options).done(function (body) {
      deferred.resolve(body);
    }).fail(function (xhr) {
      var body = xhr.responseJSON || {};
      var error = { status: xhr.status, message: body.message || 'Không kết nối được máy chủ.', errors: body.errors || {} };
      if (xhr.status === 401) {
        window.location.href = '/login';  // hết phiên: về trang đăng nhập của nền tảng
      }
      deferred.reject(error);
    });
    return deferred.promise();
  }

  function listParams(params) {
    var query = $.extend({}, params || {});
    if (query.filters) { query.filters = JSON.stringify(query.filters); }
    return query;
  }

  var schemaPromise = null;
  var perms = null;
  try { perms = JSON.parse($('meta[name="pf-perms"]').attr('content')); } catch (e) { perms = null; }  // meta thiếu/không phải JSON -> xem ghi chú ở PF.can

  window.PF = {
    records: {
      list: function (collection, params) { return request('GET', '/' + encodeURIComponent(collection), listParams(params)); },
      get: function (collection, id) { return request('GET', '/' + encodeURIComponent(collection) + '/' + encodeURIComponent(id)); },
      create: function (collection, data) { return request('POST', '/' + encodeURIComponent(collection), data); },
      update: function (collection, id, data) { return request('PUT', '/' + encodeURIComponent(collection) + '/' + encodeURIComponent(id), data); },
      remove: function (collection, id) { return request('DELETE', '/' + encodeURIComponent(collection) + '/' + encodeURIComponent(id)); },
      aggregate: function (collection, params) { return request('GET', '/' + encodeURIComponent(collection) + '/_aggregate', listParams(params)); }
    },
    schema: function () {
      if (!schemaPromise) { schemaPromise = request('GET', '/_schema'); }
      return schemaPromise;
    },
    auth: { me: function () { return request('GET', '/_me'); } },
    // Quyền của người đang xem, do SERVER điền vào <meta name="pf-perms"> lúc phục vụ trang: CHỈ để ẩn nút/menu. Mọi API vẫn do server kiểm tra lại nên sửa
    // giá trị này ở trình duyệt không cấp thêm quyền. Thiếu meta (trang sinh trước AB1) -> true: không ẩn gì, server vẫn chặn.
    can: function (collection, action) {
      if (perms === null) { return true; }
      return $.inArray(action, perms[collection] || []) >= 0;
    }
  };
})(window, window.jQuery);
